"""Review issues and collector responses with transactional notification events."""
import hashlib
import json
import re
import secrets
import time
from .auth import AuthError
from .events import queue_event, queue_role

STATUS = {'pending':'待核验','needs_action':'待采集者处理','disputed':'采集者已驳回'}


class Review:
    def __init__(self, collection):
        self.collection=collection
        self.store=collection.store
        with self.store.connect() as db:
            db.executescript('''
            CREATE TABLE IF NOT EXISTS issues(
              id TEXT PRIMARY KEY, poi_id TEXT NOT NULL REFERENCES pois(id),
              reviewer TEXT NOT NULL REFERENCES users(id), based_revision INTEGER NOT NULL,
              description TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'open',
              response TEXT, created_ms REAL NOT NULL, responded_ms REAL);
            CREATE UNIQUE INDEX IF NOT EXISTS one_open_issue ON issues(poi_id) WHERE status='open';
            CREATE TABLE IF NOT EXISTS event_outbox(
              id TEXT PRIMARY KEY, event_type TEXT NOT NULL, recipient TEXT NOT NULL REFERENCES users(id),
              payload TEXT NOT NULL, created_ms REAL NOT NULL, delivered_ms REAL);
            ''')

    @staticmethod
    def description(value):
        if not isinstance(value,str) or not value.strip() or len(value)>1000:
            raise AuthError('请填写1—1000字的说明')
        return value.strip()

    @staticmethod
    def event(db,kind,recipient,payload):
        queue_event(db,kind,recipient,payload)

    def require_reviewer(self,uid):
        if not uid or not self.collection.has_role(uid,'reviewer'): raise AuthError('需要核验者权限')

    @staticmethod
    def validate_reference(poi_id,revision,issue_id=None):
        if not isinstance(poi_id,str) or not re.fullmatch(r'[a-f0-9]{32}',poi_id): raise AuthError('POI编号无效')
        if type(revision) is not int or not 1<=revision<=2147483647: raise AuthError('版本无效')
        if issue_id is not None and (not isinstance(issue_id,str) or not re.fullmatch(r'[a-f0-9]{32}',issue_id)): raise AuthError('错误编号无效')

    def queue(self,uid):
        self.require_reviewer(uid)
        with self.store.connect() as db:
            return [dict(r) for r in db.execute('SELECT * FROM pois WHERE owner<>? ORDER BY created_ms DESC LIMIT 100',(uid,))]

    def detail(self,uid,poi_id):
        with self.store.connect() as db:
            row=db.execute('SELECT * FROM pois WHERE id=?',(poi_id,)).fetchone()
            if not uid or not row or not (row['owner']==uid or self.collection.has_role(uid,'reviewer')):
                raise AuthError('记录不存在或无权访问')
            return {'poi':dict(row),'issues':[dict(r) for r in db.execute('SELECT * FROM issues WHERE poi_id=? ORDER BY created_ms',(poi_id,))],
                    'versions':[json.loads(r['snapshot']) for r in db.execute('SELECT snapshot FROM poi_versions WHERE poi_id=? ORDER BY revision',(poi_id,))]}

    def report(self,uid,poi_id,revision,description):
        self.require_reviewer(uid)
        description=self.description(description)
        self.validate_reference(poi_id,revision)
        with self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row=db.execute('SELECT * FROM pois WHERE id=?',(poi_id,)).fetchone()
            if not row or row['owner']==uid: raise AuthError('不能核验本人或不存在的POI')
            if row['status']!='pending' or row['revision']!=revision: raise AuthError('记录已变化，请刷新后处理')
            issue_id=secrets.token_hex(16)
            db.execute('INSERT INTO issues(id,poi_id,reviewer,based_revision,description,created_ms) VALUES(?,?,?,?,?,?)',
                       (issue_id,poi_id,uid,revision,description,time.time()*1000))
            db.execute("UPDATE pois SET status='needs_action' WHERE id=?",(poi_id,))
            self.event(db,'poi.error_reported',row['owner'],{'poi_id':poi_id,'issue_id':issue_id,'revision':revision})
        return issue_id

    def respond(self,uid,poi_id,value):
        self.collection._require(uid)
        if not isinstance(value,dict): raise AuthError('处理内容无效')
        action=value.get('action')
        if action not in ('update','recollect','reject'): raise AuthError('请选择更新、重新采集或驳回')
        self.validate_reference(poi_id,value.get('revision'),value.get('issue_id',''))
        reason=self.description(value.get('reason'))
        with self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row=db.execute('SELECT * FROM pois WHERE id=? AND owner=?',(poi_id,uid)).fetchone()
            issue=db.execute("SELECT * FROM issues WHERE id=? AND poi_id=? AND status='open'",(value.get('issue_id'),poi_id)).fetchone()
            if not row or not issue or row['revision']!=value['revision'] or row['status']!='needs_action':
                raise AuthError('记录已变化或无权处理，请刷新')
            fields={}
            photo=None
            if action=='update':
                if any(k in value for k in ('photo_base64','capture_id','latitude','longitude','accuracy','captured_ms','located_ms')):
                    raise AuthError('照片与位置需通过重新采集修改')
                fields=self.collection.metadata(value)
            elif action=='recollect':
                fields,_,photo=self.collection.prepare(value)
                if self.collection.capture_used(db,uid,fields['capture_id']): raise AuthError('请重新拍照定位，不得复用已有拍摄标识')
            if action=='reject':
                db.execute("UPDATE pois SET status='disputed' WHERE id=?",(poi_id,))
            else:
                version=row['revision']+1
                old=db.execute('SELECT photo_name FROM poi_versions WHERE poi_id=? AND revision=?',(poi_id,row['revision'])).fetchone()
                photo_name=f'{poi_id}-v{version}.jpg' if photo else old['photo_name']
                merged={**dict(row),**fields}
                keys=['name','description','category','capture_id','latitude','longitude','accuracy','captured_ms','located_ms','photo_sha256']
                fingerprint=hashlib.sha256(json.dumps({k:merged[k] for k in keys},sort_keys=True,ensure_ascii=False).encode()).hexdigest()
                path=self.collection.photos/photo_name if photo else None
                created=False
                try:
                    if path:
                        with path.open('xb') as f:
                            created=True
                            f.write(photo)
                    changes={**fields,'revision':version,'status':'pending','fingerprint':fingerprint}
                    db.execute('UPDATE pois SET '+','.join(k+'=?' for k in changes)+' WHERE id=?',list(changes.values())+[poi_id])
                    self.collection.snapshot(db,db.execute('SELECT * FROM pois WHERE id=?',(poi_id,)).fetchone(),photo_name)
                    db.execute('UPDATE issues SET status=?,response=?,responded_ms=? WHERE id=?',(action,reason,time.time()*1000,issue['id']))
                    self.event(db,'poi.resubmitted',issue['reviewer'],{'poi_id':poi_id,'issue_id':issue['id'],'revision':version,'action':action})
                    queue_role(db,'reviewer','poi.ready_for_review',{'poi_id':poi_id,'revision':version,'action':action},exclude=(uid,issue['reviewer']))
                    db.commit()
                except Exception:
                    if created: path.unlink(missing_ok=True)
                    raise
                return version
            db.execute("UPDATE issues SET status='reject',response=?,responded_ms=? WHERE id=?",(reason,time.time()*1000,issue['id']))
            self.event(db,'poi.rejected',issue['reviewer'],{'poi_id':poi_id,'issue_id':issue['id'],'revision':row['revision']})
            return row['revision']

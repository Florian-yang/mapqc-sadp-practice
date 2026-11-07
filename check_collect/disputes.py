"""Independent recapture and final review for disputed POIs."""
import hashlib
import json
import secrets
import time
from .auth import AuthError
from .events import queue_event

DECISIONS={'error_confirmed':'原错误标识成立','error_rejected':'原错误标识不成立'}


class Disputes:
    def __init__(self,collection):
        self.collection=collection
        self.store=collection.store
        with self.store.connect() as db:
            db.execute('''CREATE TABLE IF NOT EXISTS disputes(
              id TEXT PRIMARY KEY, issue_id TEXT NOT NULL UNIQUE REFERENCES issues(id),
              poi_id TEXT NOT NULL REFERENCES pois(id), opener TEXT NOT NULL REFERENCES users(id),
              assignee TEXT NOT NULL REFERENCES users(id), reason TEXT NOT NULL,
              based_revision INTEGER NOT NULL, status TEXT NOT NULL DEFAULT 'assigned',
              created_ms REAL NOT NULL, decision TEXT, resolution TEXT,
              capture_id TEXT, final_revision INTEGER, completed_ms REAL,
              UNIQUE(assignee,capture_id))''')

    @staticmethod
    def text(value):
        if not isinstance(value,str) or not value.strip() or len(value)>1000: raise AuthError('请填写1—1000字的说明')
        return value.strip()

    @staticmethod
    def role(db,uid,role):
        return bool(db.execute('SELECT 1 FROM user_roles WHERE user_id=? AND role=?',(uid,role)).fetchone())

    def participant(self,db,uid,row):
        return bool(uid and ((uid==row['owner'] and self.role(db,uid,'collector')) or
                            (uid==row['reviewer'] and self.role(db,uid,'reviewer'))))

    def file(self,uid,issue_id,reason):
        reason=self.text(reason)
        if not isinstance(issue_id,str):raise AuthError('错误编号无效')
        with self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row=db.execute('SELECT i.*,p.owner,p.status AS poi_status,p.revision FROM issues i JOIN pois p ON p.id=i.poi_id WHERE i.id=?',(issue_id,)).fetchone()
            if not row or not self.participant(db,uid,row):raise AuthError('无权提交该异议')
            previous=db.execute('SELECT * FROM disputes WHERE issue_id=?',(issue_id,)).fetchone()
            if previous:
                if previous['opener']==uid and previous['reason']==reason:return previous['id']
                raise AuthError('该错误已提交异议，请查看处理记录')
            if row['status']!='reject' or row['poi_status']!='disputed':raise AuthError('请先复核并驳回错误标识')
            candidates=db.execute("SELECT user_id FROM user_roles WHERE role='third_party' AND user_id NOT IN (?,?) ORDER BY user_id",(row['owner'],row['reviewer'])).fetchall()
            if not candidates:raise AuthError('暂无独立第三方工作人员，请维护者配置')
            case_id=secrets.token_hex(16);assignee=candidates[0]['user_id']
            db.execute('INSERT INTO disputes(id,issue_id,poi_id,opener,assignee,reason,based_revision,created_ms) VALUES(?,?,?,?,?,?,?,?)',
                       (case_id,issue_id,row['poi_id'],uid,assignee,reason,row['revision'],time.time()*1000))
            db.execute("UPDATE pois SET status='under_arbitration' WHERE id=?",(row['poi_id'],))
            queue_event(db,'dispute.assigned',assignee,{'dispute_id':case_id,'poi_id':row['poi_id']})
        return case_id

    def _accessible(self,db,uid,case_id):
        if not isinstance(case_id,str):raise AuthError('异议不存在或无权访问')
        row=db.execute('SELECT d.*,p.owner,i.reviewer FROM disputes d JOIN pois p ON p.id=d.poi_id JOIN issues i ON i.id=d.issue_id WHERE d.id=?',(case_id,)).fetchone()
        if not row or not (self.participant(db,uid,row) or
                           (uid==row['assignee'] and self.role(db,uid,'third_party'))):raise AuthError('异议不存在或无权访问')
        return row

    def detail(self,uid,case_id):
        with self.store.connect() as db:
            row=self._accessible(db,uid,case_id)
            return {'case':dict(row),'poi':dict(db.execute('SELECT * FROM pois WHERE id=?',(row['poi_id'],)).fetchone()),
                    'issue':dict(db.execute('SELECT * FROM issues WHERE id=?',(row['issue_id'],)).fetchone()),
                    'versions':[json.loads(r['snapshot']) for r in db.execute('SELECT snapshot FROM poi_versions WHERE poi_id=? ORDER BY revision',(row['poi_id'],))]}

    def list_owned(self,uid):
        with self.store.connect() as db:
            if not uid or not any(self.role(db,uid,r) for r in ('collector','reviewer','third_party')):raise AuthError('请先取得工作角色')
            ids=[r['id'] for r in db.execute('SELECT d.id FROM disputes d JOIN pois p ON p.id=d.poi_id JOIN issues i ON i.id=d.issue_id WHERE p.owner=? OR i.reviewer=? OR d.assignee=? ORDER BY d.created_ms DESC LIMIT 100',(uid,uid,uid))]
            result=[]
            for case_id in ids:
                try:result.append(dict(self._accessible(db,uid,case_id)))
                except AuthError:pass
            return result

    def capture_context(self,uid,case_id):
        detail=self.detail(uid,case_id)
        row=detail['case']
        if uid!=row['assignee'] or not self.collection.has_role(uid,'third_party') or row['status']!='assigned':raise AuthError('仅指定第三方可提交重采核验')
        return detail

    def finalize(self,uid,case_id,value):
        if not isinstance(value,dict):raise AuthError('核验内容无效')
        decision=value.get('decision')
        if not isinstance(decision,str) or decision not in DECISIONS:raise AuthError('请选择核验结论')
        reason=self.text(value.get('reason'))
        revision=value.get('revision')
        if type(revision) is not int or not 1<=revision<=2147483647:raise AuthError('版本无效')
        with self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            case=self._accessible(db,uid,case_id)
            row=db.execute('SELECT * FROM pois WHERE id=?',(case['poi_id'],)).fetchone()
            if (uid!=case['assignee'] or not self.role(db,uid,'third_party') or uid in (case['owner'],case['reviewer'])
                or case['status']!='assigned' or row['status']!='under_arbitration'
                or revision!=case['based_revision'] or revision!=row['revision']):raise AuthError('任务已变化或无权裁决，请刷新')
            fields,_,photo=self.collection.prepare(value)
            if (self.collection.capture_used(db,row['owner'],fields['capture_id']) or self.collection.capture_used(db,uid,fields['capture_id'])
                or db.execute('SELECT 1 FROM disputes WHERE assignee=? AND capture_id=?',(uid,fields['capture_id'])).fetchone()):raise AuthError('请重新拍照定位，不得复用已有采集')
            version=revision+1
            path=self.collection.photos/f'{row["id"]}-v{version}.jpg'
            created=False
            try:
                with path.open('xb') as f:
                    created=True;f.write(photo)
                fingerprint=hashlib.sha256(json.dumps(fields,sort_keys=True,ensure_ascii=False).encode()).hexdigest()
                changes={**fields,'revision':version,'status':'finalized','fingerprint':fingerprint}
                db.execute('UPDATE pois SET '+','.join(k+'=?' for k in changes)+' WHERE id=?',list(changes.values())+[row['id']])
                snapshot=dict(db.execute('SELECT * FROM pois WHERE id=?',(row['id'],)).fetchone())
                self.collection.snapshot(db,{**snapshot,'captured_by':uid,'dispute_id':case_id},path.name)
                db.execute("UPDATE disputes SET status='finalized',decision=?,resolution=?,capture_id=?,final_revision=?,completed_ms=? WHERE id=?",(decision,reason,fields['capture_id'],version,time.time()*1000,case_id))
                db.execute("UPDATE issues SET status='finalized' WHERE id=?",(case['issue_id'],))
                for recipient in (case['owner'],case['reviewer']):
                    queue_event(db,'dispute.finalized',recipient,{'dispute_id':case_id,'poi_id':row['id'],'revision':version,'decision':decision})
                db.commit()
            except Exception:
                if created:path.unlink(missing_ok=True)
                raise
        return version

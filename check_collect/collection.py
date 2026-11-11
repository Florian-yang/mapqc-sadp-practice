"""POI capture validation and versioned photo storage."""
import base64
import binascii
import hashlib
import io
import json
import math
from pathlib import Path
import re
import secrets
import time
import warnings
from PIL import Image, UnidentifiedImageError
from .auth import AuthError
from .events import ensure_schema, queue_role

CATEGORIES = {'house':'房屋','shop':'商铺','bus_stop':'公交站','greenery':'绿化','scenic':'景点','other':'其他'}
MAX_BODY = 6 * 1024 * 1024


class Collection:
    def __init__(self, store):
        self.store = store
        self.photos = Path(store.path).resolve().parent/'photos'
        self.photos.mkdir(exist_ok=True)
        with store.connect() as db:
            db.executescript('''
            BEGIN IMMEDIATE;
            CREATE TABLE IF NOT EXISTS user_roles(
              user_id TEXT NOT NULL REFERENCES users(id), role TEXT NOT NULL,
              PRIMARY KEY(user_id,role));
            CREATE TABLE IF NOT EXISTS pois(
              id TEXT PRIMARY KEY, owner TEXT NOT NULL REFERENCES users(id),
              capture_id TEXT NOT NULL, name TEXT NOT NULL, category TEXT NOT NULL,
              description TEXT NOT NULL, latitude REAL NOT NULL, longitude REAL NOT NULL,
              accuracy REAL NOT NULL, captured_ms REAL NOT NULL, located_ms REAL NOT NULL,
              created_ms REAL NOT NULL, photo_sha256 TEXT NOT NULL, fingerprint TEXT NOT NULL,
              status TEXT NOT NULL DEFAULT 'pending', UNIQUE(owner,capture_id));
            ''')
            columns = {r['name'] for r in db.execute('PRAGMA table_info(pois)')}
            if 'revision' not in columns:
                db.execute('ALTER TABLE pois ADD COLUMN revision INTEGER NOT NULL DEFAULT 1')
            db.execute('''CREATE TABLE IF NOT EXISTS poi_versions(
              poi_id TEXT NOT NULL REFERENCES pois(id), revision INTEGER NOT NULL,
              snapshot TEXT NOT NULL, photo_name TEXT NOT NULL, created_ms REAL NOT NULL,
              PRIMARY KEY(poi_id,revision))''')
            for row in db.execute('SELECT * FROM pois WHERE NOT EXISTS (SELECT 1 FROM poi_versions v WHERE v.poi_id=pois.id)'):
                self.snapshot(db, row, row['id']+'.jpg')
            ensure_schema(db)

    def has_role(self, uid, role):
        with self.store.connect() as db:
            return bool(db.execute('SELECT 1 FROM user_roles WHERE user_id=? AND role=?',(uid,role)).fetchone())

    @staticmethod
    def snapshot(db, row, photo_name):
        db.execute('INSERT INTO poi_versions VALUES(?,?,?,?,?)',
                   (row['id'],row['revision'],json.dumps(dict(row),ensure_ascii=False),photo_name,time.time()*1000))

    def is_collector(self, uid):
        return self.has_role(uid,'collector')

    def enroll(self, uid):
        # Collecting is an opt-in ordinary role; no reviewer/admin self-assignment.
        with self.store.connect() as db:
            db.execute('INSERT OR IGNORE INTO user_roles VALUES(?,?)',(uid,'collector'))

    def _require(self, uid):
        if not uid or not self.is_collector(uid):
            raise AuthError('请先登录并加入采集者')

    def create(self, uid, value):
        self._require(uid)
        fields,fingerprint,photo=self.prepare(value)
        now=time.time()*1000
        with self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            previous=db.execute('SELECT id,fingerprint FROM pois WHERE owner=? AND capture_id=?',(uid,fields['capture_id'])).fetchone()
            if previous:
                if previous['fingerprint']!=fingerprint: raise AuthError('同一拍摄标识不能替换照片或位置')
                return previous['id']
            if self.capture_used(db,uid,fields['capture_id']): raise AuthError('拍摄标识已使用，请重新拍照')
            poi_id=secrets.token_hex(16)
            path=self.photos/(poi_id+'.jpg')
            created=False
            try:
                with path.open('xb') as f:
                    created=True
                    f.write(photo)
                keys=list(fields)
                db.execute('INSERT INTO pois(id,owner,'+','.join(keys)+',created_ms,fingerprint) VALUES('+','.join('?' for _ in range(len(keys)+4))+')',[poi_id,uid]+[fields[k] for k in keys]+[now,fingerprint])
                self.snapshot(db,db.execute('SELECT * FROM pois WHERE id=?',(poi_id,)).fetchone(),path.name)
                queue_role(db,'reviewer','poi.ready_for_review',{'poi_id':poi_id,'revision':1},exclude=(uid,))
                db.commit()
            except Exception:
                if created: path.unlink(missing_ok=True)
                raise
            return poi_id

    @staticmethod
    def capture_used(db,uid,capture_id):
        return any(json.loads(r['snapshot'])['capture_id']==capture_id for r in db.execute(
            "SELECT v.snapshot FROM poi_versions v JOIN pois p ON p.id=v.poi_id WHERE p.owner=? OR json_extract(v.snapshot,'$.captured_by')=?",(uid,uid)))

    @staticmethod
    def metadata(value):
        fields={}
        for key,limit in [('name',120),('description',1000),('category',32)]:
            v=value.get(key)
            if not isinstance(v,str) or not v.strip() or len(v)>limit: raise AuthError('请填写有效的名称、类别与描述')
            fields[key]=v.strip()
        if fields['category'] not in CATEGORIES: raise AuthError('POI类别无效')
        return fields

    def prepare(self, value):
        if not isinstance(value,dict): raise AuthError('采集内容必须为对象')
        fields = self.metadata(value)
        fields['capture_id']=value.get('capture_id','')
        if not isinstance(fields['capture_id'],str): raise AuthError('拍摄标识无效')
        if not re.fullmatch(r'[a-zA-Z0-9-]{16,64}',fields['capture_id']): raise AuthError('拍摄标识无效')
        now=time.time()*1000
        for key,low,high in [('latitude',-90,90),('longitude',-180,180),('accuracy',0.01,10000),('captured_ms',now-600000,now+30000),('located_ms',now-600000,now+30000)]:
            v=value.get(key)
            if type(v) not in (float,int) or not math.isfinite(v) or not low<=v<=high: raise AuthError('自动位置、精度或拍摄时间无效，请重新拍照定位')
            fields[key]=v
        if abs(fields['captured_ms']-fields['located_ms'])>20000:
            raise AuthError('定位与拍照间隔过长，请重新拍照')
        encoded=value.get('photo_base64')
        if not isinstance(encoded,str) or len(encoded)>MAX_BODY: raise AuthError('照片缺失或过大')
        try:
            raw=base64.b64decode(encoded,validate=True)
            if not 1<=len(raw)<=4*1024*1024: raise ValueError()
            with warnings.catch_warnings():
                warnings.simplefilter('error',Image.DecompressionBombWarning)
                with Image.open(io.BytesIO(raw)) as im:
                    if im.format not in ('JPEG','PNG') or im.width*im.height>12000000: raise ValueError()
                    im.load()
                    output=io.BytesIO();im.convert('RGB').save(output,'JPEG',quality=88)
            photo=output.getvalue()
        except (ValueError,OSError,binascii.Error,UnidentifiedImageError,Image.DecompressionBombWarning,Image.DecompressionBombError):
            raise AuthError('照片不是可读取的JPEG/PNG，或尺寸过大') from None
        fields['photo_sha256']=hashlib.sha256(photo).hexdigest()
        fingerprint=hashlib.sha256(json.dumps(fields,sort_keys=True,ensure_ascii=False).encode()).hexdigest()
        return fields,fingerprint,photo

    def get_owned(self,uid,poi_id):
        self._require(uid)
        if not isinstance(poi_id,str) or not re.fullmatch(r'[a-f0-9]{32}',poi_id):
            raise AuthError('记录不存在或无权访问')
        with self.store.connect() as db:
            row=db.execute('SELECT id,status FROM pois WHERE id=? AND owner=?',(poi_id,uid)).fetchone()
            if not row: raise AuthError('记录不存在或无权访问')
            return dict(row)

    def list_owned(self,uid):
        self._require(uid)
        with self.store.connect() as db:
            return [dict(r) for r in db.execute('SELECT id,name,category,description,latitude,longitude,accuracy,captured_ms,located_ms,created_ms,photo_sha256,status,revision FROM pois WHERE owner=? ORDER BY created_ms DESC LIMIT 100',(uid,))]

    def photo(self,uid,poi_id,revision=None):
        if not re.fullmatch(r'[a-f0-9]{32}',poi_id): raise AuthError('照片不存在或无权访问')
        if revision is not None and (type(revision) is not int or not 1<=revision<=2147483647): raise AuthError('照片版本无效')
        with self.store.connect() as db:
            row=db.execute('SELECT owner,revision FROM pois WHERE id=?',(poi_id,)).fetchone()
            if not row or not self.can_read(db,uid,poi_id,row['owner']): raise AuthError('照片不存在或无权访问')
            version=db.execute('SELECT photo_name FROM poi_versions WHERE poi_id=? AND revision=?',(poi_id,revision if revision is not None else row['revision'])).fetchone()
            if not version: raise AuthError('照片版本不存在')
        return (self.photos/version['photo_name']).read_bytes()

    def can_read(self,db,uid,poi_id,owner):
        if not uid:return False
        if uid==owner or self.has_role(uid,'reviewer'):return True
        if not self.has_role(uid,'third_party'):return False
        if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='disputes'").fetchone():return False
        return bool(db.execute('SELECT 1 FROM disputes WHERE poi_id=? AND assignee=?',(poi_id,uid)).fetchone())

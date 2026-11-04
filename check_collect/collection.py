"""POI capture validation and collector-owned storage; review is a later milestone."""
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

CATEGORIES = {'house':'房屋','shop':'商铺','bus_stop':'公交站','greenery':'绿化','scenic':'景点','other':'其他'}
MAX_BODY = 6 * 1024 * 1024


class Collection:
    def __init__(self, store):
        self.store = store
        self.photos = Path(store.path).resolve().parent/'photos'
        self.photos.mkdir(exist_ok=True)
        with store.connect() as db:
            db.executescript('''
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

    def is_collector(self, uid):
        with self.store.connect() as db:
            return bool(db.execute('SELECT 1 FROM user_roles WHERE user_id=? AND role=?',(uid,'collector')).fetchone())

    def enroll(self, uid):
        # Collecting is an opt-in ordinary role; no reviewer/admin self-assignment.
        with self.store.connect() as db:
            db.execute('INSERT OR IGNORE INTO user_roles VALUES(?,?)',(uid,'collector'))

    def _require(self, uid):
        if not uid or not self.is_collector(uid):
            raise AuthError('请先登录并加入采集者')

    def create(self, uid, value):
        self._require(uid)
        if not isinstance(value,dict): raise AuthError('采集内容必须为对象')
        fields = {}
        for key,limit in [('name',120),('description',1000),('category',32),('capture_id',64)]:
            v=value.get(key)
            if not isinstance(v,str) or not v.strip() or len(v)>limit: raise AuthError('请填写有效的名称、类别与描述')
            fields[key]=v.strip()
        if fields['category'] not in CATEGORIES: raise AuthError('POI类别无效')
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
        with self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            previous=db.execute('SELECT id,fingerprint FROM pois WHERE owner=? AND capture_id=?',(uid,fields['capture_id'])).fetchone()
            if previous:
                if previous['fingerprint']!=fingerprint: raise AuthError('同一拍摄标识不能替换照片或位置')
                return previous['id']
            poi_id=secrets.token_hex(16)
            path=self.photos/(poi_id+'.jpg')
            try:
                with path.open('xb') as f: f.write(photo)
                keys=['capture_id','name','category','description','latitude','longitude','accuracy','captured_ms','located_ms','photo_sha256']
                db.execute('INSERT INTO pois(id,owner,'+','.join(keys)+',created_ms,fingerprint) VALUES('+','.join('?' for _ in range(len(keys)+4))+')',[poi_id,uid]+[fields[k] for k in keys]+[now,fingerprint])
                db.commit()
            except Exception:
                path.unlink(missing_ok=True)
                raise
            return poi_id

    def list_owned(self,uid):
        self._require(uid)
        with self.store.connect() as db:
            return [dict(r) for r in db.execute('SELECT id,name,category,description,latitude,longitude,accuracy,captured_ms,located_ms,created_ms,photo_sha256,status FROM pois WHERE owner=? ORDER BY created_ms DESC LIMIT 100',(uid,))]

    def photo(self,uid,poi_id):
        self._require(uid)
        if not re.fullmatch(r'[a-f0-9]{32}',poi_id): raise AuthError('照片不存在或无权访问')
        with self.store.connect() as db:
            if not db.execute('SELECT 1 FROM pois WHERE id=? AND owner=?',(poi_id,uid)).fetchone(): raise AuthError('照片不存在或无权访问')
        return (self.photos/(poi_id+'.jpg')).read_bytes()

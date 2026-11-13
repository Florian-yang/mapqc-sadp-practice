"""System notices and per-user delivery inbox."""
import argparse
import json
import re
import secrets
import time
from .auth import AuthError, Settings, Store
from .events import ensure_schema, queue_role




class Notifications:
    def __init__(self,store):
        self.store=store
        with store.connect() as db:
            ensure_schema(db)
            db.execute('''CREATE TABLE IF NOT EXISTS notification_inbox(
              event_id TEXT PRIMARY KEY REFERENCES event_outbox(id), recipient TEXT NOT NULL REFERENCES users(id),
              event_type TEXT NOT NULL, payload TEXT NOT NULL, received_ms REAL NOT NULL, read_ms REAL)''')

    def announce(self,role,text):
        if role not in ('collector','reviewer'): raise AuthError('通知对象只能是采集者或核验者')
        if not isinstance(text,str) or not text.strip() or len(text)>1000: raise AuthError('通知须为1—1000字')
        with self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            return queue_role(db,role,'system.notice',{'text':text.strip(),'role':role,'broadcast_id':secrets.token_hex(16)})

    def list_owned(self,uid):
        if not uid: raise AuthError('请先登录')
        with self.store.connect() as db:
            return [{**dict(r),'payload':json.loads(r['payload'])} for r in db.execute(
                'SELECT * FROM notification_inbox WHERE recipient=? ORDER BY received_ms DESC,event_id LIMIT 100',(uid,))]

    def unread_count(self,uid):
        if not uid: raise AuthError('请先登录')
        with self.store.connect() as db:
            return db.execute('SELECT COUNT(*) FROM notification_inbox WHERE recipient=? AND read_ms IS NULL',(uid,)).fetchone()[0]

    def mark_read(self,uid,event_id):
        if not uid or not isinstance(event_id,str) or not re.fullmatch('[a-f0-9]{32}',event_id): raise AuthError('通知不存在或无权访问')
        with self.store.connect() as db:
            if not db.execute('UPDATE notification_inbox SET read_ms=COALESCE(read_ms,?) WHERE event_id=? AND recipient=?',
                              (time.time()*1000,event_id,uid)).rowcount: raise AuthError('通知不存在或无权访问')




def main():
    parser=argparse.ArgumentParser(description='可信本机维护者发送系统角色通知')
    parser.add_argument('role',choices=['collector','reviewer'])
    parser.add_argument('text')
    args=parser.parse_args()
    from .collection import Collection
    store=Store(Settings.from_env().database);Collection(store)
    ids=Notifications(store).announce(args.role,args.text)
    print(f'已加入待发送箱：{len(ids)}名接收者；运行消息服务后查看送达结果。')


if __name__=='__main__': main()

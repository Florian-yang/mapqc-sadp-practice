"""NATS JetStream adapter. SQLite holds outbox/inbox, not the transport."""
import argparse
import asyncio
import json
import os
import time
from urllib.parse import urlsplit
import nats
from nats.js.api import StreamConfig, ConsumerConfig, RetentionPolicy, StorageType, DiscardPolicy, AckPolicy, DeliverPolicy
from nats.js.errors import NotFoundError
from .auth import Settings, Store, AuthError
from .notifications import Notifications

STREAM='CHECK_COLLECT_NOTICES'
SUBJECT='checkcollect.events.>'
DURABLE='check-collect-inbox'


def wire(row):
    return json.dumps({k:row[k] for k in ('id','event_type','recipient','payload','created_ms')},
                      ensure_ascii=False,sort_keys=True,separators=(',',':')).encode('utf-8')


class Messaging:
    def __init__(self,store,url='nats://127.0.0.1:14222'):
        address=urlsplit(url)
        if address.scheme!='nats' or address.hostname not in ('localhost','127.0.0.1','::1'):
            raise ValueError('当前消息适配仅支持本机NATS，不对公网开放')
        self.store=store
        self.url=url
        self.nc=None
        self.js=None
        self.subscription=None
        Notifications(store)

    async def connect(self):
        async def quiet_error(_): pass
        self.nc=await nats.connect(self.url,connect_timeout=1,allow_reconnect=False,error_cb=quiet_error)
        self.js=self.nc.jetstream(timeout=2)
        try:
            info=await self.js.stream_info(STREAM)
        except NotFoundError:
            info=await self.js.add_stream(config=StreamConfig(name=STREAM,subjects=[SUBJECT],
                retention=RetentionPolicy.WORK_QUEUE,storage=StorageType.FILE,discard=DiscardPolicy.NEW,
                max_bytes=64*1024*1024,duplicate_window=120))
        if (info.config.subjects!=[SUBJECT] or info.config.retention!=RetentionPolicy.WORK_QUEUE
            or info.config.storage!=StorageType.FILE or info.config.discard!=DiscardPolicy.NEW
            or info.config.max_age or info.config.no_ack or info.config.allow_msg_ttl):
            raise AuthError('已有NATS流配置不同，停止以保护现有数据')
        try:
            consumer=await self.js.consumer_info(STREAM,DURABLE)
        except NotFoundError:
            consumer=await self.js.add_consumer(STREAM,config=ConsumerConfig(durable_name=DURABLE,
                filter_subject=SUBJECT,ack_policy=AckPolicy.EXPLICIT,ack_wait=2,max_ack_pending=100,max_deliver=-1))
        if (consumer.config.ack_policy!=AckPolicy.EXPLICIT or consumer.config.filter_subject!=SUBJECT
            or consumer.config.deliver_policy!=DeliverPolicy.ALL or consumer.config.max_deliver not in (None,-1)
            or consumer.config.headers_only or consumer.config.inactive_threshold):
            raise AuthError('已有消息消费者配置不同')
        self.subscription=await self.js.pull_subscribe_bind(stream=STREAM,durable=DURABLE)

    async def close(self):
        if self.nc: await self.nc.close()

    async def publish_pending(self,limit=100):
        with self.store.connect() as db:
            rows=[dict(r) for r in db.execute('SELECT * FROM event_outbox WHERE published_ms IS NULL AND delivered_ms IS NULL ORDER BY created_ms,id LIMIT ?',(limit,))]
        count=0
        for row in rows:
            try:
                await self.js.publish('checkcollect.events.user.'+row['recipient'],wire(row),headers={'Nats-Msg-Id':row['id']})
            except Exception as exc:
                with self.store.connect() as db:
                    db.execute('UPDATE event_outbox SET publish_attempts=publish_attempts+1,last_error=? WHERE id=?',(type(exc).__name__,row['id']))
                raise
            with self.store.connect() as db:
                db.execute('UPDATE event_outbox SET published_ms=COALESCE(published_ms,?),publish_attempts=publish_attempts+1,last_error=NULL WHERE id=?',
                           (time.time()*1000,row['id']))
            count+=1
        return count

    def save_inbox(self,message):
        value=json.loads(message.data)
        if not isinstance(value,dict) or not isinstance(value.get('id'),str): raise AuthError('消息格式无效')
        with self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row=db.execute('SELECT * FROM event_outbox WHERE id=?',(value['id'],)).fetchone()
            if not row or message.subject!='checkcollect.events.user.'+row['recipient'] or message.data!=wire(row):
                raise AuthError('消息与业务事件不一致')
            db.execute('INSERT OR IGNORE INTO notification_inbox(event_id,recipient,event_type,payload,received_ms) VALUES(?,?,?,?,?)',
                       (row['id'],row['recipient'],row['event_type'],row['payload'],time.time()*1000))
            db.execute('UPDATE event_outbox SET delivered_ms=COALESCE(delivered_ms,?) WHERE id=?',(time.time()*1000,row['id']))

    async def consume(self,limit=100,timeout=.3):
        try: messages=await self.subscription.fetch(batch=limit,timeout=timeout)
        except (asyncio.TimeoutError,nats.errors.TimeoutError): return 0
        count=0
        for message in messages:
            try:
                self.save_inbox(message)
                await message.ack_sync(timeout=2)
            except (AuthError,ValueError):
                await message.term()
                raise
            except Exception:
                await message.nak(delay=1)
                raise
            count+=1
        return count

    async def run_once(self):
        # Drain existing messages first so a full stream can accept new ones.
        received=await self.consume(timeout=.05)
        return {'published':await self.publish_pending(),'received':received+await self.consume()}


async def run(once=False):
    from .collection import Collection
    store=Store(Settings.from_env().database);Collection(store)
    while True:
        worker=Messaging(store,os.getenv('CHECK_COLLECT_NATS_URL','nats://127.0.0.1:14222'))
        try:
            await worker.connect()
            while True:
                result=await worker.run_once()
                if once: print(json.dumps(result));return
                await asyncio.sleep(.5)
        except Exception as exc:
            print('消息处理暂未完成：'+type(exc).__name__,flush=True)
            if once: raise SystemExit(1)
        finally:
            await worker.close()
        await asyncio.sleep(3)


def main():
    parser=argparse.ArgumentParser(description='处理本项目NATS通知与待发送事件')
    parser.add_argument('--once',action='store_true')
    args=parser.parse_args()
    try: asyncio.run(run(args.once))
    except KeyboardInterrupt: pass


if __name__=='__main__': main()

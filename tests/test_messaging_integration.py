"""Actual NATS/JetStream TCP integration; requires an external server executable."""
import asyncio
import base64
import http.client
import io
import json
import os
from pathlib import Path
import socket
import subprocess
import tempfile
import threading
import time
import unittest
from nats.js.api import DiscardPolicy
from http.server import ThreadingHTTPServer
from unittest.mock import patch
from PIL import Image
from check_collect.auth import Store, Settings, AuthError
from check_collect.collection import Collection
from check_collect.review import Review
from check_collect.roles import grant_reviewer, grant_role
from check_collect.chat import Chat
from check_collect.disputes import Disputes
from check_collect.events import queue_event
from check_collect.notifications import Notifications
from check_collect.messaging import Messaging, STREAM, wire
from check_collect.server import application

SERVER=os.getenv('NATS_SERVER_EXE','')


@unittest.skipUnless(SERVER and Path(SERVER).is_file(),'Set NATS_SERVER_EXE for real broker integration; a skip is not acceptance')
class MessagingIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        asyncio.get_running_loop().set_debug(False)
        asyncio.get_running_loop().slow_callback_duration=10
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        self.addCleanup(self.temp.cleanup)
        self.log=(self.root/'nats.txt').open('ab');self.addCleanup(self.log.close)
        self.process=None
        self.workers=[]
        self.broker_pids=[]
        self.addAsyncCleanup(self.cleanup_runtime)
        with socket.socket() as probe:
            probe.bind(('127.0.0.1',0));self.port=probe.getsockname()[1]
        self.store=Store(str(self.root/'test.sqlite'))
        self.collection=Collection(self.store);self.review=Review(self.collection);self.notices=Notifications(self.store)
        users=[self.store.local_account(name,'synthetic-password-123',True) for name in ['collector1','collector2','reviewer1','reviewer2','ordinary']]
        self.owner,self.owner_token=users[0];self.other,self.other_token=users[1]
        self.reviewer,self.review_token=users[2];self.reviewer2,_=users[3];self.ordinary,self.ordinary_token=users[4]
        self.collection.enroll(self.owner);self.collection.enroll(self.other)
        grant_reviewer(self.store,self.reviewer);grant_reviewer(self.store,self.reviewer2)
        await self.start_broker()
        self.worker=await self.new_worker()

    async def start_broker(self):
        self.process=subprocess.Popen([SERVER,'-js','-a','127.0.0.1','-p',str(self.port),'-sd',str(self.root/'jetstream')],
            stdout=self.log,stderr=subprocess.STDOUT,creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
        self.broker_pids.append(self.process.pid)
        for _ in range(100):
            if self.process.poll() is not None: self.fail('NATS exited: '+(self.root/'nats.txt').read_text(errors='replace'))
            with socket.socket() as probe:
                if probe.connect_ex(('127.0.0.1',self.port))==0: return
            await asyncio.sleep(.05)
        self.fail('NATS did not start')

    async def stop_broker(self):
        if self.process and self.process.poll() is None:
            self.process.terminate();self.process.wait(timeout=5)

    async def cleanup_runtime(self):
        for worker in self.workers: await worker.close()
        await self.stop_broker()

    async def asyncTearDown(self):
        receipt=os.getenv('CHECK_COLLECT_BROKER_RECEIPT')
        if not receipt:return
        rows=self.event_rows()
        with self.store.connect() as db:
            inbox=db.execute('SELECT count(*) FROM notification_inbox').fetchone()[0]
        info=await self.workers[-1].js.stream_info(STREAM)
        consumer=await self.workers[-1].js.consumer_info(STREAM,'check-collect-inbox')
        record={'scenario':self._testMethodName,'broker_pids':self.broker_pids,'outbox_events':len(rows),
                'published_events':sum(r['published_ms'] is not None for r in rows),
                'delivered_events':sum(r['delivered_ms'] is not None for r in rows),'inbox_rows':inbox,
                'stream_messages_remaining':info.state.messages,'stream_last_sequence':info.state.last_seq,
                'consumer_ack_floor':consumer.ack_floor.stream_seq,'consumer_ack_pending':consumer.num_ack_pending}
        with Path(receipt).open('a',encoding='utf-8') as f:f.write(json.dumps(record)+'\n')

    async def new_worker(self):
        worker=Messaging(self.store,f'nats://127.0.0.1:{self.port}')
        self.workers.append(worker);await worker.connect();return worker

    def capture(self):
        image=io.BytesIO();Image.new('RGB',(20,20),(120,80,30)).save(image,'JPEG');now=time.time()*1000
        return dict(name='合成POI',category='shop',description='自动测试样例',capture_id='synthetic-capture-12345',
                    latitude=30.,longitude=114.,accuracy=10.,captured_ms=now,located_ms=now,photo_base64=base64.b64encode(image.getvalue()).decode())

    def point_notice(self):
        with self.store.connect() as db:
            return queue_event(db,'system.notice',self.owner,{'text':'合成系统通知'})

    def event_rows(self):
        with self.store.connect() as db:return [dict(r) for r in db.execute('SELECT * FROM event_outbox ORDER BY created_ms,id')]

    async def test_role_broadcast_point_delivery_and_http_ownership(self):
        self.assertEqual(len(self.notices.announce('collector','<script>暂停采集</script>')),2)
        self.assertEqual(len(self.notices.announce('reviewer','有新任务待检')),2)
        value=self.capture();poi=self.collection.create(self.owner,value)
        self.assertEqual(self.collection.create(self.owner,value),poi)
        self.review.report(self.reviewer,poi,1,'照片不清楚')
        self.assertEqual(await self.worker.run_once(),{'published':7,'received':7})
        self.assertEqual([len(self.notices.list_owned(uid)) for uid in [self.owner,self.other,self.reviewer,self.reviewer2,self.ordinary]],[2,1,2,2,0])
        self.assertTrue(all(r['published_ms'] and r['delivered_ms'] for r in self.event_rows()))
        config=Settings('http://127.0.0.1:8765',database=self.store.path)
        server=ThreadingHTTPServer(('127.0.0.1',0),application(config))
        threading.Thread(target=server.serve_forever,daemon=True).start()
        self.addCleanup(server.server_close);self.addCleanup(server.shutdown)
        def request(method,path,token=None,origin=None):
            headers={'Origin':origin or config.origin}
            if token: headers['Cookie']='cc_session='+token
            connection=http.client.HTTPConnection('127.0.0.1',server.server_port,timeout=3)
            connection.request(method,path,headers=headers);response=connection.getresponse();body=response.read().decode();connection.close()
            return response.status,body
        status,page=request('GET','/notifications',self.owner_token)
        self.assertEqual(status,200);self.assertIn('&lt;script&gt;',page);self.assertNotIn('<script>暂停',page)
        self.assertEqual(request('GET','/api/notifications')[0],401)
        event=self.notices.list_owned(self.owner)[0]['event_id']
        self.assertEqual(request('POST',f'/notifications/{event}/read',self.other_token)[0],403)
        self.assertEqual(request('POST',f'/notifications/{event}/read',self.owner_token,'https://foreign.example')[0],403)
        self.assertEqual(request('POST',f'/notifications/{event}/read',self.owner_token)[0],303)
        self.assertIsNotNone(next(r for r in self.notices.list_owned(self.owner) if r['event_id']==event)['read_ms'])
        self.assertEqual(len(json.loads(request('GET','/api/notifications',self.other_token)[1])),1)

    async def test_publish_ack_loss_retries_are_deduplicated(self):
        self.point_notice();row=self.event_rows()[0]
        first=await self.worker.js.publish('checkcollect.events.user.'+row['recipient'],wire(row),headers={'Nats-Msg-Id':row['id']})
        self.assertFalse(first.duplicate)
        # Simulate a publisher dying before recording the broker acknowledgement.
        self.assertEqual(await self.worker.publish_pending(),1)
        info=await self.worker.js.stream_info(STREAM);self.assertEqual(info.state.messages,1)
        self.assertEqual(await self.worker.consume(),1)
        self.assertEqual(len(self.notices.list_owned(self.owner)),1)

    async def test_consumer_restart_after_commit_before_ack_does_not_duplicate(self):
        self.point_notice();await self.worker.publish_pending()
        message=(await self.worker.subscription.fetch(batch=1,timeout=1))[0]
        self.worker.save_inbox(message)  # Commit, deliberately omit acknowledgement.
        await self.worker.close();await asyncio.sleep(2.2)
        replacement=await self.new_worker()
        again=(await replacement.subscription.fetch(batch=1,timeout=1))[0]
        self.assertGreaterEqual(again.metadata.num_delivered,2)
        replacement.save_inbox(again);await again.ack_sync()
        self.assertEqual(len(self.notices.list_owned(self.owner)),1)

    async def test_broker_outage_retry_and_file_store_restart(self):
        self.point_notice();await self.stop_broker()
        with self.assertRaises(Exception):await self.worker.publish_pending()
        row=self.event_rows()[0];self.assertIsNone(row['published_ms']);self.assertIsNone(row['delivered_ms'])
        self.assertGreaterEqual(row['publish_attempts'],1)
        await self.worker.close();await self.start_broker();replacement=await self.new_worker()
        self.assertEqual(await replacement.publish_pending(),1)
        await replacement.close();await self.stop_broker();await self.start_broker()
        after_restart=await self.new_worker()
        self.assertEqual(await after_restart.consume(),1)
        self.assertEqual(len(self.notices.list_owned(self.owner)),1)
        self.assertIsNotNone(self.event_rows()[0]['delivered_ms'])

    async def test_inbox_failure_is_retried_without_premature_ack(self):
        self.point_notice();await self.worker.publish_pending()
        with patch.object(self.worker,'save_inbox',side_effect=RuntimeError('synthetic database failure')):
            with self.assertRaises(RuntimeError):await self.worker.consume()
        self.assertEqual(self.notices.list_owned(self.owner),[])
        self.assertIsNone(self.event_rows()[0]['delivered_ms'])
        await asyncio.sleep(1.2)
        self.assertEqual(await self.worker.consume(),1)
        self.assertEqual(len(self.notices.list_owned(self.owner)),1)

    async def test_tampered_payload_is_rejected_without_delivery(self):
        self.point_notice();row=self.event_rows()[0];value=json.loads(wire(row));value['recipient']=self.other
        await self.worker.js.publish('checkcollect.events.user.'+self.other,json.dumps(value).encode())
        with self.assertRaises(AuthError):await self.worker.consume()
        self.assertEqual(self.notices.list_owned(self.owner),[]);self.assertEqual(self.notices.list_owned(self.other),[])
        self.assertIsNone(self.event_rows()[0]['delivered_ms'])
        self.assertEqual(await self.worker.run_once(),{'published':1,'received':1})

    async def test_old_outbox_migration_and_role_snapshot(self):
        with self.store.connect() as db:
            db.execute('DROP TABLE notification_inbox');db.execute('DROP TABLE event_outbox')
            db.execute('CREATE TABLE event_outbox(id TEXT PRIMARY KEY,event_type TEXT,recipient TEXT,payload TEXT,created_ms REAL,delivered_ms REAL)')
            old=queue_event(db,'system.notice',self.owner,{'text':'旧版本待发送通知'})
        self.notices=Notifications(self.store)
        self.notices.announce('collector','快照广播')
        self.collection.enroll(self.ordinary)
        self.assertEqual(await self.worker.run_once(),{'published':3,'received':3})
        self.assertEqual(len(self.notices.list_owned(self.ordinary)),0)
        self.assertIn(old,[r['event_id'] for r in self.notices.list_owned(self.owner)])

    async def test_resubmission_notifies_all_reviewers_once(self):
        poi=self.collection.create(self.owner,self.capture())
        self.assertEqual(await self.worker.run_once(),{'published':2,'received':2})
        issue=self.review.report(self.reviewer,poi,1,'说明缺少信息')
        self.assertEqual(await self.worker.run_once(),{'published':1,'received':1})
        response=dict(action='update',revision=1,issue_id=issue,reason='已补全',name='合成POI',category='shop',description='补全后的合成说明')
        self.review.respond(self.owner,poi,response)
        with self.assertRaises(AuthError):self.review.respond(self.owner,poi,response)
        self.assertEqual(await self.worker.run_once(),{'published':2,'received':2})
        self.assertEqual(len(self.notices.list_owned(self.reviewer)),2)
        self.assertEqual(len(self.notices.list_owned(self.reviewer2)),2)

    async def test_full_stream_is_drained_before_retrying_new_publication(self):
        info=await self.worker.js.stream_info(STREAM)
        info.config.max_msgs=1
        await self.worker.js.update_stream(config=info.config)
        self.point_notice();self.assertEqual(await self.worker.publish_pending(),1)
        self.point_notice()
        with self.assertRaises(Exception):await self.worker.publish_pending()
        # A normal iteration must free broker capacity and finish both inbox entries.
        self.assertEqual(await self.worker.run_once(),{'published':1,'received':2})
        self.assertEqual(len(self.notices.list_owned(self.owner)),2)
        self.assertTrue(all(r['delivered_ms'] is not None for r in self.event_rows()))

    async def test_incompatible_existing_stream_and_consumer_are_refused(self):
        info=await self.worker.js.stream_info(STREAM)
        for field,bad_value in [('discard',DiscardPolicy.OLD),('max_age',300)]:
            original=getattr(info.config,field)
            setattr(info.config,field,bad_value);await self.worker.js.update_stream(config=info.config)
            with self.assertRaises(AuthError):await self.new_worker()
            setattr(info.config,field,original);await self.worker.js.update_stream(config=info.config)
        consumer=await self.worker.js.consumer_info(STREAM,'check-collect-inbox')
        consumer.config.max_deliver=2
        await self.worker.js.add_consumer(STREAM,config=consumer.config)
        with self.assertRaises(AuthError):await self.new_worker()
        consumer.config.max_deliver=-1
        await self.worker.js.add_consumer(STREAM,config=consumer.config)
        await self.new_worker()

    async def test_chat_and_final_arbitration_notifications_reach_parties(self):
        chat=Chat(self.collection);disputes=Disputes(self.collection)
        staff,_=self.store.local_account('third_party','synthetic-password-123',True)
        grant_role(self.store,staff,'third_party')
        poi=self.collection.create(self.owner,self.capture())
        issue=self.review.report(self.reviewer,poi,1,'招牌不清楚')
        room=chat.open_private(self.owner,issue)
        chat.send(self.owner,room,'请说明错误位置','private-owner-1')
        chat.send(self.reviewer,room,'需要重拍招牌','private-reviewer-1')
        chat.send(self.owner,'collectors','分享采集技巧','collector-group-1')
        chat.send(self.reviewer,'reviewers','分享核验技巧','reviewer-group-1')
        self.review.respond(self.owner,poi,dict(action='reject',issue_id=issue,revision=1,reason='双方未能达成一致'))
        case=disputes.file(self.owner,issue,'请第三方现场核验')
        value={**self.capture(),'capture_id':'third-party-recapture-0002','revision':1,'decision':'error_confirmed','reason':'根据重采结果确认原错误标识'}
        disputes.finalize(staff,case,value)
        with self.assertRaises(AuthError):disputes.finalize(staff,case,value)
        self.assertEqual(await self.worker.run_once(),{'published':11,'received':11})
        self.assertEqual([len(self.notices.list_owned(uid)) for uid in [self.owner,self.other,self.reviewer,self.reviewer2,staff,self.ordinary]],[3,1,4,2,1,0])
        for uid in (self.owner,self.reviewer):
            final=[r for r in self.notices.list_owned(uid) if r['event_type']=='dispute.finalized']
            self.assertEqual(len(final),1);self.assertEqual(final[0]['payload']['decision'],'error_confirmed')
            self.assertIn('/dispute/'+case,self.notices.page(uid))
        self.assertEqual(disputes.detail(staff,case)['case']['status'],'finalized')

    async def test_chat_role_revocation_does_not_leak_text_in_notification(self):
        chat=Chat(self.collection)
        secret='合成的群内讨论内容'
        chat.send(self.owner,'collectors',secret,'group-privacy-1')
        with self.store.connect() as db:db.execute("DELETE FROM user_roles WHERE user_id=? AND role='collector'",(self.other,))
        self.assertEqual(await self.worker.run_once(),{'published':1,'received':1})
        self.assertNotIn(secret,json.dumps(self.notices.list_owned(self.other),ensure_ascii=False))
        self.assertNotIn(secret,self.notices.page(self.other))
        with self.assertRaises(AuthError):chat.messages(self.other,'collectors')

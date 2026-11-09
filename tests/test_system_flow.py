"""Whole local workflow over HTTP and real NATS; photos, GPS and platform replies are synthetic."""
import asyncio
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import socket
import subprocess
import tempfile
import threading
import unittest

import test_collaboration_http as collaboration
import test_geoplatform_http as geoplatform
from check_collect.auth import Settings, Store
from check_collect.collection import Collection
from check_collect.messaging import Messaging, STREAM
from check_collect.notifications import Notifications
from check_collect.ocr import OcrSpace
from check_collect.maps import OsrmRouter
from check_collect.roles import grant_role
from check_collect.server import application


SERVER = os.getenv('NATS_SERVER_EXE', '')


@unittest.skipUnless(SERVER and Path(SERVER).is_file(), 'Set NATS_SERVER_EXE; a skipped local flow is not acceptance')
class SystemFlowTests(unittest.IsolatedAsyncioTestCase):
    request = collaboration.CollaborationHTTPTests.request
    capture = collaboration.CollaborationHTTPTests.capture
    form_request_id = collaboration.CollaborationHTTPTests.form_request_id

    async def asyncSetUp(self):
        asyncio.get_running_loop().set_debug(False)
        asyncio.get_running_loop().slow_callback_duration = 10
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.store = Store(str(self.root / 'system.sqlite'))
        Collection(self.store)
        self.tokens, self.serial, self.ocr_requests = {}, 0, []
        self.route_protocol = geoplatform.ProtocolTransport()

        def ocr_protocol(request):
            self.ocr_requests.append(request)
            return {'IsErroredOnProcessing': False, 'OCRExitCode': 1,
                    'ParsedResults': [{'FileParseExitCode': 1, 'ParsedText': 'SYNTHETIC SHOP 合成商店'}]}

        self.server = ThreadingHTTPServer(('127.0.0.1', 0), BaseHTTPRequestHandler)
        self.settings = Settings(f'http://127.0.0.1:{self.server.server_port}', database=self.store.path)
        self.server.RequestHandlerClass = application(self.settings,
            router=OsrmRouter(self.route_protocol), ocr_provider=OcrSpace('synthetic-local-key', ocr_protocol))
        self.http_thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.http_thread.start()
        self.addCleanup(self.stop_http)
        for name in ('owner', 'other', 'reviewer', 'staff'):
            result = self.request('POST', '/auth/local/register',
                {'username': 'flow_' + name, 'password': 'synthetic-password-123'})
            self.assertEqual(result.status, 303, result.text)
            cookie = SimpleCookie(result.headers['Set-Cookie'])
            token = cookie['cc_session'].value
            uid = self.store.user(token)
            self.assertIsNotNone(uid)
            setattr(self, name, uid)
            self.tokens[uid] = token
        for uid in (self.owner, self.other):
            self.assertEqual(self.request('POST', '/profile/collector', uid=uid).status, 303)
        grant_role(self.store, self.reviewer, 'reviewer')
        grant_role(self.store, self.staff, 'third_party')

        self.log = (self.root / 'nats.txt').open('ab')
        self.addCleanup(self.log.close)
        with socket.socket() as probe:
            probe.bind(('127.0.0.1', 0))
            port = probe.getsockname()[1]
        self.process = subprocess.Popen([SERVER, '-js', '-a', '127.0.0.1', '-p', str(port), '-sd', str(self.root / 'jetstream')],
            stdout=self.log, stderr=subprocess.STDOUT,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
        self.addCleanup(self.stop_broker)
        for _ in range(100):
            self.assertIsNone(self.process.poll(), 'NATS exited before local flow')
            with socket.socket() as probe:
                if probe.connect_ex(('127.0.0.1', port)) == 0:
                    break
            await asyncio.sleep(.05)
        else:
            self.fail('NATS did not start')
        self.worker = Messaging(self.store, f'nats://127.0.0.1:{port}')
        self.addAsyncCleanup(self.worker.close)
        await self.worker.connect()

    def stop_http(self):
        self.server.shutdown()
        self.server.server_close()
        self.http_thread.join(timeout=3)

    def stop_broker(self):
        if self.process.poll() is None:
            self.process.terminate()
            self.process.wait(timeout=5)

    async def flush(self):
        result = await self.worker.run_once()
        self.assertEqual(result['published'], result['received'])
        return result['received']

    def json_request(self, method, path, uid, value=None):
        result = self.request(method, path, value, uid, json_body=value is not None)
        self.assertEqual(result.status, 200, result.text)
        return result.json()

    def map_item(self, poi, status, uid=None):
        data = self.json_request('GET', '/api/map?category=other&status=' + status, uid or self.owner)
        self.assertEqual([feature['id'] for feature in data['features']], [poi])
        return data['features'][0]['properties']

    async def test_http_to_broker_full_dispute_map_and_ocr_flow(self):
        notices = Notifications(self.store)
        self.assertEqual(len(notices.announce('collector', '合成系统通知：暂停采集')), 2)
        self.assertEqual(len(notices.announce('reviewer', '合成系统通知：核验工作提示')), 1)
        await self.flush()
        for uid in (self.owner, self.other, self.reviewer):
            rows = self.json_request('GET', '/api/notifications', uid)
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]['event_type'], 'system.notice')

        capture = {**self.capture(), 'name': '合成招牌', 'category': 'other'}
        created = self.request('POST', '/api/pois', capture, self.owner, json_body=True)
        self.assertEqual(created.status, 201, created.text)
        poi = created.json()['id']
        await self.flush()
        self.assertEqual(self.map_item(poi, 'pending')['revision'], 1)
        self.assertEqual(self.json_request('GET', '/api/map', self.other)['features'], [])
        reviewer_tasks = self.json_request('GET', '/api/notifications', self.reviewer)
        self.assertIn('poi.ready_for_review', [r['event_type'] for r in reviewer_tasks])

        first_photo = self.request('GET', f'/api/pois/{poi}/photo?revision=1', uid=self.owner).body
        ocr_value = {'language': 'auto', 'expected_revision': 1, 'confirmed_external': True}
        recognized = self.json_request('POST', f'/api/pois/{poi}/ocr', self.owner, ocr_value)
        self.assertEqual(recognized['suggested_category'], 'shop')
        self.assertEqual(self.json_request('POST', f'/api/pois/{poi}/ocr', self.owner, ocr_value), recognized)
        self.assertEqual(len(self.ocr_requests), 1)
        self.assertEqual(self.map_item(poi, 'pending')['category'], 'other')
        routed = self.json_request('POST', '/api/maps/route', self.owner,
                                  {'poi_id': poi, 'latitude': 30.001, 'longitude': 114.001})
        self.assertEqual(routed['provider'], 'OSRM')
        self.assertEqual(routed['geometry']['type'], 'LineString')
        self.assertEqual(len(self.route_protocol.urls), 1)

        reported = self.request('POST', '/review/report',
            {'poi_id': poi, 'revision': 1, 'description': '合成测试：招牌分类需要复核'}, self.reviewer)
        self.assertEqual(reported.status, 303, reported.text)
        issue = self.json_request('GET', '/api/review/' + poi, self.owner)['issues'][0]['id']
        self.assertTrue(self.map_item(poi, 'needs_action')['has_error'])
        await self.flush()
        owner_notices = self.json_request('GET', '/api/notifications', self.owner)
        self.assertIn('poi.error_reported', [r['event_type'] for r in owner_notices])

        opened = self.request('POST', '/chats/open', {'issue_id': issue}, self.owner)
        self.assertEqual(opened.status, 303, opened.text)
        room = opened.headers['Location'].split('/')[-1]
        for uid, text in ((self.owner, '已复核，想确认分类依据'), (self.reviewer, '建议请第三方重新采集')):
            self.assertEqual(self.request('POST', f'/chat/{room}/send',
                {'text': text, 'request_id': self.form_request_id(room, uid)}, uid).status, 303)
        self.assertIn('建议请第三方重新采集', self.request('GET', '/chat/' + room, uid=self.owner).text)
        self.assertEqual(self.request('GET', '/chat/' + room, uid=self.other).status, 403)
        for group, uid, text in (('collectors', self.owner, '合成采集技巧'), ('reviewers', self.reviewer, '合成核验技巧')):
            self.assertEqual(self.request('POST', f'/chat/{group}/send',
                {'text': text, 'request_id': self.form_request_id(group, uid)}, uid).status, 303)
            self.assertIn(text, self.request('GET', '/chat/' + group, uid=uid).text)
        self.assertIn('合成采集技巧', self.request('GET', '/chat/collectors', uid=self.other).text)
        self.assertEqual(self.request('GET', '/chat/reviewers', uid=self.other).status, 403)
        await self.flush()

        rejected = self.request('POST', '/review/respond',
            {'poi_id': poi, 'issue_id': issue, 'revision': 1, 'action': 'reject', 'reason': '合成测试：双方有异议'}, self.owner)
        self.assertEqual(rejected.status, 303, rejected.text)
        self.map_item(poi, 'disputed')
        filed = self.request('POST', '/disputes/file', {'issue_id': issue, 'reason': '合成测试：申请独立核验'}, self.owner)
        self.assertEqual(filed.status, 303, filed.text)
        case = filed.headers['Location'].split('/')[-1]
        detail = self.json_request('GET', '/api/disputes/' + case, self.staff)
        self.assertEqual(detail['case']['assignee'], self.staff)
        self.assertEqual(self.map_item(poi, 'under_arbitration', self.staff)['status'], 'under_arbitration')
        self.assertEqual(self.request('GET', '/api/disputes/' + case, uid=self.other).status, 403)
        await self.flush()
        staff_notices = self.json_request('GET', '/api/notifications', self.staff)
        self.assertEqual([r['event_type'] for r in staff_notices], ['dispute.assigned'])

        final_value = {**self.capture((180, 20, 60)), 'name': '第三方合成招牌', 'category': 'other',
                       'revision': 1, 'decision': 'error_confirmed', 'reason': '合成重采数据确认原错误标识'}
        self.assertEqual(self.request('POST', f'/api/disputes/{case}/finalize', final_value, self.owner, json_body=True).status, 400)
        finalized = self.json_request('POST', f'/api/disputes/{case}/finalize', self.staff, final_value)
        self.assertEqual(finalized, {'id': case, 'revision': 2})
        self.assertFalse(self.map_item(poi, 'finalized')['has_error'])
        self.assertEqual(self.map_item(poi, 'finalized')['revision'], 2)
        await self.flush()
        for uid in (self.owner, self.reviewer):
            result = self.json_request('GET', '/api/disputes/' + case, uid)
            self.assertEqual(result['case']['decision'], 'error_confirmed')
            self.assertEqual([r['revision'] for r in result['versions']], [1, 2])
            self.assertEqual(result['versions'][-1]['captured_by'], self.staff)
            self.assertEqual(self.request('GET', f'/api/pois/{poi}/photo?revision=1', uid=uid).body, first_photo)
            self.assertNotEqual(self.request('GET', f'/api/pois/{poi}/photo?revision=2', uid=uid).body, first_photo)
            messages = self.json_request('GET', '/api/notifications', uid)
            final_notice = [r for r in messages if r['event_type'] == 'dispute.finalized']
            self.assertEqual(len(final_notice), 1)
            event = final_notice[0]['event_id']
            self.assertEqual(self.request('POST', f'/notifications/{event}/read', uid=uid).status, 303)
            read = self.json_request('GET', '/api/notifications', uid)
            self.assertIsNotNone(next(r for r in read if r['event_id'] == event)['read_ms'])
        self.assertEqual(self.request('POST', f'/api/disputes/{case}/finalize', final_value, self.staff, json_body=True).status, 400)
        self.assertEqual(self.request('POST', f'/api/pois/{poi}/ocr', ocr_value, self.owner, json_body=True).status, 400)
        self.assertEqual(len(self.ocr_requests), 1)
        final_ocr = self.json_request('POST', f'/api/pois/{poi}/ocr', self.owner, {**ocr_value, 'expected_revision': 2})
        self.assertEqual(final_ocr['revision'], 2)
        self.assertEqual(final_ocr['suggested_category'], 'shop')
        self.assertEqual(len(self.ocr_requests), 2)
        self.assertEqual(self.map_item(poi, 'finalized')['category'], 'other')
        self.assertEqual(await self.worker.run_once(), {'published': 0, 'received': 0})

        with self.store.connect() as db:
            outbox = [dict(r) for r in db.execute('SELECT published_ms,delivered_ms FROM event_outbox')]
            inbox = db.execute('SELECT count(*) FROM notification_inbox').fetchone()[0]
        self.assertTrue(all(row['published_ms'] and row['delivered_ms'] for row in outbox))
        self.assertEqual(len(outbox), inbox)
        info = await self.worker.js.stream_info(STREAM)
        consumer = await self.worker.js.consumer_info(STREAM, 'check-collect-inbox')
        self.assertEqual(info.state.messages, 0)
        self.assertEqual(consumer.num_ack_pending, 0)
        receipt = {'scenario': self._testMethodName, 'broker_pids': [self.process.pid], 'outbox_events': len(outbox),
            'published_events': len(outbox), 'delivered_events': len(outbox), 'inbox_rows': inbox,
            'stream_messages_remaining': info.state.messages, 'stream_last_sequence': info.state.last_seq,
            'consumer_ack_floor': consumer.ack_floor.stream_seq, 'consumer_ack_pending': consumer.num_ack_pending}
        target = os.getenv('CHECK_COLLECT_BROKER_RECEIPT')
        if target:
            with Path(target).open('a', encoding='utf-8') as output:
                output.write(json.dumps(receipt) + '\n')

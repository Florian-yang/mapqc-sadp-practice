"""Loopback HTTP scenarios; photos/locations are synthetic, no camera or broker."""
import base64
import http.client
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import io
import json
from pathlib import Path
import re
import tempfile
import threading
import time
import unittest
from urllib.parse import urlencode
from PIL import Image

from check_collect.auth import Settings, Store
from check_collect.collection import Collection
from check_collect.roles import grant_role
from check_collect.server import application


class Response:
    def __init__(self, status, headers, body):
        self.status, self.headers, self.body = status, headers, body

    @property
    def text(self):
        return self.body.decode('utf-8')

    def json(self):
        return json.loads(self.body)


class CollaborationHTTPTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = Store(str(Path(self.temp.name) / 'http.sqlite'))
        self.collection = Collection(self.store)
        self.tokens = {}
        for name in ('owner', 'collector', 'reviewer', 'other_reviewer', 'outsider', 'third_one', 'third_two'):
            uid, token = self.store.local_account(name, 'synthetic-password-123', True)
            setattr(self, name, uid)
            self.tokens[uid] = token
        for uid in (self.owner, self.collector):
            self.collection.enroll(uid)
        for uid in (self.reviewer, self.other_reviewer):
            grant_role(self.store, uid, 'reviewer')
        for uid in (self.third_one, self.third_two):
            grant_role(self.store, uid, 'third_party')
        self.assignee, self.unassigned = sorted((self.third_one, self.third_two))
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), BaseHTTPRequestHandler)
        self.settings = Settings(f'http://127.0.0.1:{self.server.server_port}', database=self.store.path)
        self.server.RequestHandlerClass = application(self.settings)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.stop_server)
        self.serial = 0

    def stop_server(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=3)

    def request(self, method, path, data=None, uid=None, json_body=False, origin=True):
        headers = {}
        if origin is True:
            headers['Origin'] = self.settings.origin
        elif origin is not None:
            headers['Origin'] = origin
        if uid:
            headers['Cookie'] = 'cc_session=' + self.tokens[uid]
        body = None
        if data is not None:
            body = (json.dumps(data, ensure_ascii=False) if json_body else urlencode(data)).encode('utf-8')
            headers['Content-Type'] = 'application/json' if json_body else 'application/x-www-form-urlencoded'
        connection = http.client.HTTPConnection('127.0.0.1', self.server.server_port, timeout=5)
        try:
            connection.request(method, path, body, headers)
            result = connection.getresponse()
            return Response(result.status, dict(result.getheaders()), result.read())
        finally:
            connection.close()

    def capture(self, color=(20, 120, 40)):
        self.serial += 1
        photo = io.BytesIO()
        Image.new('RGB', (24, 32), color).save(photo, 'JPEG')
        now = time.time() * 1000
        return dict(name='<b>合成商铺</b>', category='shop', description='合成照片与位置，非真机证据',
                    capture_id=f'http-synthetic-capture-{self.serial:04d}', latitude=30., longitude=114., accuracy=12.,
                    captured_ms=now, located_ms=now, photo_base64=base64.b64encode(photo.getvalue()).decode())

    def reported_poi(self):
        capture = self.capture()
        created = self.request('POST', '/api/pois', capture, self.owner, json_body=True)
        self.assertEqual(created.status, 201, created.text)
        poi = created.json()['id']
        reported = self.request('POST', '/review/report',
            {'poi_id': poi, 'revision': 1, 'description': '<svg onload="bad()">招牌不清楚'}, self.reviewer)
        self.assertEqual(reported.status, 303, reported.text)
        detail = self.request('GET', '/api/review/' + poi, uid=self.owner)
        self.assertEqual(detail.status, 200)
        before_retry = detail.json()
        retried = self.request('POST', '/api/pois', capture, self.owner, json_body=True)
        self.assertEqual(retried.status, 201, retried.text)
        self.assertEqual(retried.json()['id'], poi)
        self.assertEqual(retried.json()['status'], 'needs_action')
        after_retry = self.request('GET', '/api/review/' + poi, uid=self.owner).json()
        self.assertEqual(after_retry, before_retry)
        self.assertEqual(after_retry['poi']['id'], poi)
        self.assertEqual(after_retry['poi']['revision'], 1)
        self.assertEqual(len(after_retry['versions']), 1)
        return poi, after_retry['issues'][0]['id']

    def private_room(self, issue):
        result = self.request('POST', '/chats/open', {'issue_id': issue}, self.owner)
        self.assertEqual(result.status, 303, result.text)
        self.assertRegex(result.headers['Location'], r'^/chat/[a-f0-9]{32}$')
        return result.headers['Location'].split('/')[-1]

    def rejected_case(self, poi, issue, opener=None):
        rejected = self.request('POST', '/review/respond',
            {'poi_id': poi, 'issue_id': issue, 'revision': 1, 'action': 'reject', 'reason': '<i>已复核，仍有异议</i>'}, self.owner)
        self.assertEqual(rejected.status, 303, rejected.text)
        filed = self.request('POST', '/disputes/file',
            {'issue_id': issue, 'reason': '<img src=x onerror="bad()">请第三方现场核验'}, opener or self.owner)
        self.assertEqual(filed.status, 303, filed.text)
        self.assertRegex(filed.headers['Location'], r'^/dispute/[a-f0-9]{32}$')
        return filed.headers['Location'].split('/')[-1]

    def form_request_id(self, room, uid):
        response = self.request('GET', '/chat/' + room, uid=uid)
        self.assertEqual(response.status, 200, response.text)
        match = re.search(r'name="request_id" value="([a-f0-9]{32})"', response.text)
        self.assertIsNotNone(match)
        return match.group(1)

    def test_private_and_both_groups_use_real_http_with_deduplication_and_escaping(self):
        poi, issue = self.reported_poi()
        history = self.request('GET', '/poi/' + poi, uid=self.owner)
        self.assertEqual(history.status, 200)
        self.assertIn('action="/chats/open"', history.text)
        self.assertIn('value="' + issue + '"', history.text)
        room = self.private_room(issue)
        other_open = self.request('POST', '/chats/open', {'issue_id': issue}, self.reviewer)
        self.assertEqual(other_open.status, 303)
        self.assertEqual(other_open.headers['Location'], '/chat/' + room)
        for uid, own_group, forbidden_group in ((self.owner, 'collectors', 'reviewers'), (self.reviewer, 'reviewers', 'collectors')):
            listing = self.request('GET', '/chats', uid=uid)
            self.assertEqual(listing.status, 200)
            self.assertIn('/chat/' + own_group, listing.text)
            self.assertNotIn('/chat/' + forbidden_group, listing.text)
            self.assertIn('/chat/' + room, listing.text)
            self.assertIn('&lt;b&gt;合成商铺&lt;/b&gt;', listing.text)
            self.assertNotIn('<b>合成商铺</b>', listing.text)
        request_id = self.form_request_id(room, self.owner)
        message = {'text': '<script>讨论错误详情</script>', 'request_id': request_id}
        for _ in range(2):
            sent = self.request('POST', f'/chat/{room}/send', message, self.owner)
            self.assertEqual(sent.status, 303, sent.text)
            self.assertEqual(sent.headers['Location'], '/chat/' + room)
        conflicting = self.request('POST', f'/chat/{room}/send', {**message, 'text': '换一条内容'}, self.owner)
        self.assertEqual(conflicting.status, 400)
        conversation = self.request('GET', '/chat/' + room, uid=self.reviewer)
        self.assertEqual(conversation.status, 200)
        self.assertEqual(conversation.text.count('&lt;script&gt;讨论错误详情&lt;/script&gt;'), 1)
        self.assertNotIn('<script>讨论错误', conversation.text)
        self.assertIn('&lt;svg onload=', conversation.text)
        self.assertNotIn('<svg onload=', conversation.text)
        self.assertNotEqual(request_id, self.form_request_id(room, self.owner))
        reply = self.request('POST', f'/chat/{room}/send',
                             {'text': '建议重新拍摄招牌', 'request_id': self.form_request_id(room, self.reviewer)}, self.reviewer)
        self.assertEqual(reply.status, 303)
        self.assertIn('建议重新拍摄招牌', self.request('GET', '/chat/' + room, uid=self.owner).text)
        for group, sender, recipient, wrong_user in (
            ('collectors', self.owner, self.collector, self.reviewer),
            ('reviewers', self.reviewer, self.other_reviewer, self.owner)):
            group_text = group + '工作经验'
            sent = self.request('POST', f'/chat/{group}/send',
                {'text': group_text, 'request_id': self.form_request_id(group, sender)}, sender)
            self.assertEqual(sent.status, 303)
            self.assertIn(group_text, self.request('GET', '/chat/' + group, uid=recipient).text)
            self.assertEqual(self.request('GET', '/chat/' + group, uid=wrong_user).status, 403)
            self.assertNotIn(group_text, self.request('GET', '/chat/' + room, uid=self.owner).text)

    def test_both_final_decisions_recapture_versions_and_freeze_the_case(self):
        for decision, label, opener in (('error_confirmed', '原错误标识成立', self.owner),
                                         ('error_rejected', '原错误标识不成立', self.reviewer)):
            with self.subTest(decision=decision):
                poi, issue = self.reported_poi()
                room = self.private_room(issue)
                self.assertEqual(self.request('POST', f'/chat/{room}/send',
                    {'text': '双方讨论后仍无法达成共识', 'request_id': self.form_request_id(room, self.owner)}, self.owner).status, 303)
                original = self.request('GET', f'/api/pois/{poi}/photo', uid=self.owner).body
                case = self.rejected_case(poi, issue, opener)
                for uid in (self.owner, self.reviewer, self.assignee):
                    listed = self.request('GET', '/disputes', uid=uid)
                    self.assertEqual(listed.status, 200)
                    self.assertIn('/dispute/' + case, listed.text)
                    detail = self.request('GET', '/api/disputes/' + case, uid=uid)
                    self.assertEqual(detail.status, 200)
                    self.assertEqual(detail.json()['case']['assignee'], self.assignee)
                page = self.request('GET', '/dispute/' + case, uid=self.owner)
                self.assertIn('&lt;img src=x onerror=', page.text)
                self.assertNotIn('<img src=x onerror=', page.text)
                self.assertIn('&lt;i&gt;已复核', page.text)
                self.assertFalse(self.collection.is_collector(self.assignee))
                camera = self.request('GET', '/collect?' + urlencode({'dispute_id': case}), uid=self.assignee)
                self.assertEqual(camera.status, 200, camera.text)
                self.assertIn('data-dispute-id="' + case + '"', camera.text)
                self.assertIn('data-revision="1"', camera.text)
                self.assertIn('name="decision"', camera.text)
                final_capture = self.capture((180, 20, 60))
                final = {**final_capture, 'revision': 1, 'decision': decision,
                         'reason': '<strong>已按合成测试位置重新采集并核验</strong>'}
                result = self.request('POST', f'/api/disputes/{case}/finalize', final, self.assignee, json_body=True)
                self.assertEqual(result.status, 200, result.text)
                self.assertEqual(result.json(), {'id': case, 'revision': 2})
                before_retry = self.request('GET', '/api/disputes/' + case, uid=self.owner).json()
                retried = self.request('POST', '/api/pois', final_capture, self.owner, json_body=True)
                self.assertEqual(retried.status, 201, retried.text)
                self.assertEqual(retried.json()['id'], poi)
                self.assertEqual(retried.json()['status'], 'finalized')
                after_retry = self.request('GET', '/api/disputes/' + case, uid=self.owner).json()
                self.assertEqual(after_retry, before_retry)
                self.assertEqual(after_retry['poi']['id'], poi)
                self.assertEqual(after_retry['poi']['revision'], 2)
                self.assertEqual(len(after_retry['versions']), 2)
                for uid in (self.owner, self.reviewer):
                    data = self.request('GET', '/api/disputes/' + case, uid=uid).json()
                    self.assertEqual(data['case']['status'], 'finalized')
                    self.assertEqual(data['case']['decision'], decision)
                    self.assertEqual(data['case']['final_revision'], 2)
                    self.assertEqual(data['poi']['status'], 'finalized')
                    self.assertEqual(data['issue']['status'], 'finalized')
                    self.assertEqual(len(data['versions']), 2)
                    self.assertEqual(data['versions'][-1]['captured_by'], self.assignee)
                    self.assertEqual(data['versions'][-1]['dispute_id'], case)
                    final_page = self.request('GET', '/dispute/' + case, uid=uid)
                    self.assertEqual(final_page.status, 200)
                    self.assertIn('最终结果：' + label, final_page.text)
                    self.assertIn('&lt;strong&gt;已按合成测试', final_page.text)
                    self.assertNotIn('<strong>已按合成测试', final_page.text)
                    photo = self.request('GET', f'/api/pois/{poi}/photo', uid=uid)
                    self.assertEqual(photo.status, 200)
                    self.assertEqual(photo.headers['Content-Type'], 'image/jpeg')
                    self.assertNotEqual(photo.body, original)
                    self.assertEqual(self.request('GET', f'/api/pois/{poi}/photo?revision=1', uid=uid).body, original)
                    self.assertEqual(self.request('GET', f'/api/pois/{poi}/photo?revision=2', uid=uid).body, photo.body)
                    self.assertEqual(self.request('GET', '/poi/' + poi, uid=uid).status, 200)
                repeat = self.request('POST', f'/api/disputes/{case}/finalize', final, self.assignee, json_body=True)
                self.assertEqual(repeat.status, 400)
                self.assertEqual(self.request('GET', '/collect?dispute_id=' + case, uid=self.assignee).status, 403)
                report = self.request('POST', '/review/report',
                    {'poi_id': poi, 'revision': 2, 'description': '尝试覆盖最终结果'}, self.reviewer)
                self.assertEqual(report.status, 400)
                update = self.request('POST', f'/api/review/{poi}/respond',
                    {'issue_id': issue, 'revision': 2, 'action': 'update', 'reason': '尝试修改最终结果',
                     'name': '新名称', 'category': 'shop', 'description': '新描述'}, self.owner, json_body=True)
                self.assertEqual(update.status, 400)
                frozen = self.request('GET', '/api/disputes/' + case, uid=self.owner).json()
                self.assertEqual(frozen['poi']['revision'], 2)
                self.assertEqual(len(frozen['versions']), 2)
                self.assertEqual(frozen['case']['decision'], decision)

    def test_private_and_dispute_apis_deny_unrelated_users_and_missing_sessions(self):
        poi, issue = self.reported_poi()
        room = self.private_room(issue)
        case = self.rejected_case(poi, issue)
        final = {**self.capture(), 'revision': 1, 'decision': 'error_confirmed', 'reason': '越权裁决测试'}
        for path in ('/chats', '/chat/' + room, '/disputes', '/dispute/' + case, '/api/disputes/' + case):
            self.assertEqual(self.request('GET', path).status, 401, path)
        for path, data, as_json in (
            ('/chats/open', {'issue_id': issue}, False),
            (f'/chat/{room}/send', {'text': '越权消息', 'request_id': 'no-session'}, False),
            ('/disputes/file', {'issue_id': issue, 'reason': '越权提交'}, False),
            (f'/api/disputes/{case}/finalize', final, True)):
            self.assertEqual(self.request('POST', path, data, json_body=as_json).status, 401, path)
        for uid in (self.collector, self.other_reviewer, self.outsider, self.unassigned):
            with self.subTest(uid=uid):
                self.assertEqual(self.request('GET', '/chat/' + room, uid=uid).status, 403)
                self.assertEqual(self.request('POST', '/chats/open', {'issue_id': issue}, uid).status, 400)
                self.assertEqual(self.request('POST', f'/chat/{room}/send',
                    {'text': '禁止跨账号写入', 'request_id': 'intruder'}, uid).status, 400)
                for path in ('/dispute/' + case, '/api/disputes/' + case, '/collect?dispute_id=' + case):
                    self.assertEqual(self.request('GET', path, uid=uid).status, 403, path)
                self.assertEqual(self.request('POST', '/disputes/file',
                    {'issue_id': issue, 'reason': '禁止跨账号提交'}, uid).status, 400)
                self.assertEqual(self.request('POST', f'/api/disputes/{case}/finalize', final, uid, json_body=True).status, 400)
        for uid in (self.owner, self.reviewer):
            self.assertEqual(self.request('POST', f'/api/disputes/{case}/finalize', final, uid, json_body=True).status, 400)
            self.assertEqual(self.request('GET', '/collect?dispute_id=' + case, uid=uid).status, 403)
        for uid in (self.collector, self.other_reviewer, self.unassigned):
            self.assertEqual(self.request('GET', '/api/disputes', uid=uid).json(), [])
            self.assertNotIn(case, self.request('GET', '/disputes', uid=uid).text)
        self.assertNotIn('禁止跨账号写入', self.request('GET', '/chat/' + room, uid=self.owner).text)
        self.assertEqual(self.request('GET', '/api/disputes/' + case, uid=self.owner).json()['case']['status'], 'assigned')

    def test_origin_protection_duplicate_fields_and_no_public_privilege_grant(self):
        poi, issue = self.reported_poi()
        room = self.private_room(issue)
        case = self.rejected_case(poi, issue)
        final = {**self.capture(), 'revision': 1, 'decision': 'error_confirmed', 'reason': '来源保护测试'}
        operations = (
            ('/chats/open', {'issue_id': issue}, self.owner, False),
            (f'/chat/{room}/send', {'text': '跨站消息不能保存', 'request_id': 'foreign-origin'}, self.owner, False),
            ('/disputes/file', {'issue_id': issue, 'reason': '跨站申请'}, self.owner, False),
            (f'/api/disputes/{case}/finalize', final, self.assignee, True))
        for origin in ('https://foreign.example', None):
            for path, data, uid, as_json in operations:
                self.assertEqual(self.request('POST', path, data, uid, as_json, origin).status, 403, path)
        duplicated = self.request('POST', '/chats/open', [('issue_id', issue), ('issue_id', issue)], self.owner)
        self.assertEqual(duplicated.status, 400)
        duplicated = self.request('POST', f'/chat/{room}/send',
            [('text', '第一项'), ('text', '第二项'), ('request_id', 'duplicate-fields')], self.owner)
        self.assertEqual(duplicated.status, 400)
        self.assertEqual(self.request('POST', '/chats/open', {'issue_id': issue}, self.owner, json_body=True).status, 400)
        for path in ('/profile/third_party', '/profile/reviewer'):
            self.assertEqual(self.request('POST', path, uid=self.outsider).status, 404)
        self.assertEqual(self.request('POST', '/profile/third_party', {'role': 'third_party'}, self.outsider).status, 400)
        self.assertEqual(self.request('GET', '/collect?dispute_id=' + case, uid=self.outsider).status, 403)
        page = self.request('GET', '/chat/' + room, uid=self.reviewer)
        self.assertNotIn('跨站消息不能保存', page.text)
        self.assertNotIn('第一项', page.text)
        state = self.request('GET', '/api/disputes/' + case, uid=self.owner).json()
        self.assertEqual(state['case']['status'], 'assigned')
        self.assertEqual(state['poi']['revision'], 1)


if __name__ == '__main__':
    unittest.main()

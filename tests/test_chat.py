import base64
from concurrent.futures import ThreadPoolExecutor
import io
import json
from pathlib import Path
import re
import tempfile
import time
import unittest
from unittest.mock import patch
from PIL import Image

from check_collect.auth import AuthError, Store
from check_collect.chat import Chat
from check_collect import chat_pages, events
from check_collect.collection import Collection
from check_collect.review import Review
from check_collect.roles import grant_reviewer


class ChatTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = Store(str(Path(self.temp.name) / 'chat.sqlite'))
        self.collection = Collection(self.store)
        self.review = Review(self.collection)
        self.chat = Chat(self.collection)
        self.owner, self.collector, self.reviewer, self.other_reviewer, self.outsider, self.third = [c * 32 for c in 'abcdef']
        with self.store.connect() as db:
            db.executemany('INSERT INTO users VALUES(?,?)', [(uid, int(time.time())) for uid in
                (self.owner, self.collector, self.reviewer, self.other_reviewer, self.outsider, self.third)])
            db.execute('INSERT INTO user_roles VALUES(?,?)', (self.third, 'arbiter'))
        self.collection.enroll(self.owner)
        self.collection.enroll(self.collector)
        grant_reviewer(self.store, self.reviewer)
        grant_reviewer(self.store, self.other_reviewer)
        photo = io.BytesIO()
        Image.new('RGB', (24, 32), (20, 120, 40)).save(photo, 'JPEG')
        now = time.time() * 1000
        self.poi = self.collection.create(self.owner, dict(
            name='<script>测试商铺</script>', category='shop', description='合成测试POI',
            capture_id='chat-synthetic-capture-0001', latitude=30., longitude=114., accuracy=12.,
            captured_ms=now, located_ms=now, photo_base64=base64.b64encode(photo.getvalue()).decode()))
        self.issue = self.review.report(self.reviewer, self.poi, 1, '招牌<svg onload="bad()">不清楚')
        with self.store.connect() as db:
            db.execute('DELETE FROM event_outbox')

    def open(self):
        return self.chat.open_private(self.owner, self.issue)

    def outbox(self):
        with self.store.connect() as db:
            return [dict(row) for row in db.execute('SELECT * FROM event_outbox ORDER BY created_ms,id')]

    def counts(self):
        with self.store.connect() as db:
            return tuple(db.execute('SELECT count(*) FROM ' + table).fetchone()[0]
                         for table in ('chat_rooms', 'chat_messages', 'event_outbox'))

    def revoke(self, uid, role):
        with self.store.connect() as db:
            db.execute('DELETE FROM user_roles WHERE user_id=? AND role=?', (uid, role))

    def test_exactly_two_fixed_groups_and_current_role_membership(self):
        self.assertEqual([r['id'] for r in self.chat.list_rooms(self.owner)], ['collectors'])
        self.assertEqual([r['id'] for r in self.chat.list_rooms(self.reviewer)], ['reviewers'])
        self.collection.enroll(self.reviewer)
        self.assertEqual([r['id'] for r in self.chat.list_rooms(self.reviewer)], ['collectors', 'reviewers'])
        for uid in (None, self.outsider, self.third, 'missing-user'):
            with self.assertRaises(AuthError):
                self.chat.list_rooms(uid)
        for uid, room in ((self.owner, 'reviewers'), (self.reviewer, 'unknown-group'), (self.collector, 'reviewers')):
            with self.assertRaises(AuthError):
                self.chat.messages(uid, room)
            with self.assertRaises(AuthError):
                self.chat.send(uid, room, '越权', 'wrong-group')
        self.assertEqual(self.counts(), (0, 0, 0))

    def test_private_room_is_per_issue_and_only_original_participants(self):
        room = self.open()
        self.assertEqual(self.chat.open_private(self.reviewer, self.issue), room)
        self.assertEqual(self.open(), room)
        self.assertEqual(self.chat.room(self.reviewer, room)['issue_id'], self.issue)
        for uid in (self.collector, self.other_reviewer, self.third, self.outsider, None):
            with self.assertRaises(AuthError):
                self.chat.open_private(uid, self.issue)
            with self.assertRaises(AuthError):
                self.chat.room(uid, room)
            with self.assertRaises(AuthError):
                self.chat.messages(uid, room)
            with self.assertRaises(AuthError):
                self.chat.send(uid, room, '不能读取或写入', 'unauthorized')
        self.assertEqual([r['id'] for r in self.chat.list_rooms(self.collector)], ['collectors'])
        self.assertEqual([r['id'] for r in self.chat.list_rooms(self.other_reviewer)], ['reviewers'])
        self.assertEqual(self.counts(), (1, 0, 0))

    def test_revoked_role_removes_read_write_open_and_duplicate_access(self):
        room = self.open()
        self.chat.send(self.owner, room, '先前可访问', 'before-revoke')
        self.revoke(self.owner, 'collector')
        for operation in (lambda: self.chat.list_rooms(self.owner), lambda: self.chat.room(self.owner, room),
                          lambda: self.chat.messages(self.owner, room), lambda: self.chat.open_private(self.owner, self.issue),
                          lambda: self.chat.send(self.owner, room, '先前可访问', 'before-revoke')):
            with self.assertRaises(AuthError):
                operation()
        self.collection.enroll(self.reviewer)
        self.revoke(self.reviewer, 'reviewer')
        self.assertEqual([r['id'] for r in self.chat.list_rooms(self.reviewer)], ['collectors'])
        with self.assertRaises(AuthError):
            self.chat.messages(self.reviewer, room)
        with self.assertRaises(AuthError):
            self.chat.send(self.reviewer, 'reviewers', '旧角色已撤回', 'revoked')
        self.assertEqual(self.counts(), (1, 1, 1))

    def test_invalid_references_cannot_open_user_to_user_messages(self):
        for issue in ('', self.owner, 'collectors', '../other', '0' * 32, None, 42):
            with self.assertRaises(AuthError):
                self.chat.open_private(self.owner, issue)
        for room in ('', self.owner, '0' * 32, '../reviewers', None, 42):
            with self.assertRaises(AuthError):
                self.chat.messages(self.owner, room)
        self.assertEqual(self.counts(), (0, 0, 0))

    def test_private_messages_notify_only_fixed_counterpart(self):
        room = self.open()
        first = self.chat.send(self.owner, room, '  请说明错误\n谢谢  ', 'owner-first')
        second = self.chat.send(self.reviewer, room, '建议重新拍摄招牌', 'reviewer-first')
        self.assertEqual([m['id'] for m in self.chat.messages(self.owner, room)], [first, second])
        self.assertEqual(self.chat.messages(self.reviewer, room)[0]['text'], '请说明错误\n谢谢')
        outbox = self.outbox()
        self.assertEqual([e['recipient'] for e in outbox], [self.reviewer, self.owner])
        self.assertEqual({e['event_type'] for e in outbox}, {'chat.message'})
        self.assertEqual([json.loads(e['payload']) for e in outbox],
                         [{'room_id': room, 'message_id': first}, {'room_id': room, 'message_id': second}])

    def test_group_notifications_snapshot_current_role_and_exclude_sender(self):
        first = self.chat.send(self.owner, 'collectors', '工作经验', 'group-first')
        self.collection.enroll(self.outsider)
        second = self.chat.send(self.reviewer, 'reviewers', '核验经验', 'review-first')
        outbox = self.outbox()
        self.assertEqual([(e['recipient'], json.loads(e['payload'])['message_id']) for e in outbox],
                         [(self.collector, first), (self.other_reviewer, second)])
        self.assertEqual(self.chat.messages(self.outsider, 'collectors')[0]['id'], first)
        self.assertEqual(self.chat.messages(self.reviewer, 'reviewers')[0]['id'], second)
        self.assertEqual(len(self.chat.messages(self.owner, 'collectors')), 1)

    def test_duplicate_request_is_per_sender_and_conflicting_reuse_is_rejected(self):
        room = self.open()
        message = self.chat.send(self.owner, room, '原消息', 'stable-id')
        self.assertEqual(self.chat.send(self.owner, room, ' 原消息 ', 'stable-id'), message)
        with self.assertRaises(AuthError):
            self.chat.send(self.owner, room, '改写消息', 'stable-id')
        with self.assertRaises(AuthError):
            self.chat.send(self.owner, 'collectors', '原消息', 'stable-id')
        other = self.chat.send(self.reviewer, room, '原消息', 'stable-id')
        self.assertNotEqual(other, message)
        self.assertEqual(self.counts(), (1, 2, 2))

    def test_concurrent_open_and_send_create_one_room_message_and_event(self):
        with ThreadPoolExecutor(max_workers=4) as pool:
            rooms = list(pool.map(lambda _: self.open(), range(4)))
        self.assertEqual(len(set(rooms)), 1)
        with ThreadPoolExecutor(max_workers=4) as pool:
            messages = list(pool.map(lambda _: self.chat.send(self.owner, rooms[0], '重复点击', 'same-send'), range(4)))
        self.assertEqual(len(set(messages)), 1)
        self.assertEqual(self.counts(), (1, 1, 1))

    def test_event_failure_rolls_back_message_and_retry_can_succeed(self):
        room = self.open()
        with patch('check_collect.chat.queue_event', side_effect=RuntimeError('injected outbox failure')):
            with self.assertRaises(RuntimeError):
                self.chat.send(self.owner, room, '应原子保存', 'retry-after-failure')
        self.assertEqual(self.counts(), (1, 0, 0))
        message = self.chat.send(self.owner, room, '应原子保存', 'retry-after-failure')
        self.assertEqual(self.chat.messages(self.reviewer, room)[0]['id'], message)

    def test_partial_group_event_failure_rolls_back_all_recipients(self):
        self.collection.enroll(self.reviewer)
        real_queue_event = events.queue_event
        calls = []

        def fail_second(*args, **kwargs):
            calls.append(args)
            if len(calls) == 2:
                raise RuntimeError('injected second recipient failure')
            return real_queue_event(*args, **kwargs)

        with patch('check_collect.events.queue_event', side_effect=fail_second):
            with self.assertRaises(RuntimeError):
                self.chat.send(self.owner, 'collectors', '不可部分保存', 'atomic-group')
        self.assertEqual(len(calls), 2)
        self.assertEqual(self.counts(), (0, 0, 0))

    def test_messages_and_deduplication_survive_store_reopen(self):
        room = self.open()
        message = self.chat.send(self.owner, room, '持久化消息', 'persisted-id')
        reopened = Chat(Collection(Store(self.store.path)))
        self.assertEqual(reopened.open_private(self.reviewer, self.issue), room)
        self.assertEqual(reopened.messages(self.reviewer, room)[0]['id'], message)
        self.assertEqual(reopened.send(self.owner, room, '持久化消息', 'persisted-id'), message)
        self.assertEqual(self.counts(), (1, 1, 1))

    def test_last_hundred_are_ascending_even_with_equal_timestamps(self):
        room = self.open()
        self.chat.send(self.owner, room, '私聊不混入群聊', 'separate')
        with patch('check_collect.chat.time.time', return_value=1):
            for index in range(105):
                self.chat.send(self.owner, 'collectors', str(index), 'page-' + str(index))
        messages = self.chat.messages(self.collector, 'collectors')
        self.assertEqual([m['text'] for m in messages], [str(i) for i in range(5, 105)])
        self.assertEqual(len(self.chat.messages(self.reviewer, room)), 1)

    def test_text_and_request_id_validation_do_not_write(self):
        for text in (None, 42, '', ' \t\n', '字' * 1001):
            with self.assertRaises(AuthError):
                self.chat.send(self.owner, 'collectors', text, 'invalid-text')
        for request_id in (None, 42, '', 'x' * 129, '<bad>', 'two ids'):
            with self.assertRaises(AuthError):
                self.chat.send(self.owner, 'collectors', '有效消息', request_id)
        self.assertEqual(self.counts(), (0, 0, 0))
        self.chat.send(self.owner, 'collectors', '  ' + '字' * 1000 + '\n', 'length-boundary')
        self.assertEqual(len(self.chat.messages(self.owner, 'collectors')[0]['text']), 1000)

    def test_pages_escape_content_and_make_fresh_stable_form_ids(self):
        room = self.open()
        self.chat.send(self.owner, room, '<script>alert("bad")</script>', 'escape-message')
        listing = chat_pages.listing(self.chat, self.reviewer)
        self.assertIn('&lt;script&gt;', listing)
        self.assertNotIn('<script>', listing)
        page = chat_pages.room(self.chat, self.reviewer, room)
        self.assertNotIn('<script>', page)
        self.assertNotIn('<svg ', page)
        self.assertIn('&lt;svg ', page)
        self.assertIn('&lt;script&gt;alert', page)
        self.assertIn(f'action="/chat/{room}/send"', page)
        self.assertIn('name="text" required maxlength="1000"', page)
        request = re.search(r'name="request_id" value="([a-f0-9]{32})"', page).group(1)
        next_page = chat_pages.room(self.chat, self.reviewer, room)
        self.assertNotIn('value="' + request + '"', next_page)
        with self.assertRaises(AuthError):
            chat_pages.room(self.chat, self.other_reviewer, room)


if __name__ == '__main__':
    unittest.main()

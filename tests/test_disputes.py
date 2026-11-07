"""Synthetic, local-only checks for the third-party dispute workflow."""
import base64
import io
import json
from pathlib import Path
import tempfile
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

from PIL import Image

from check_collect.auth import AuthError, Store
from check_collect.collection import Collection
from check_collect.disputes import Disputes
from check_collect.review import Review
from check_collect.roles import grant_reviewer, grant_role
from check_collect.events import queue_event


class DisputeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = Store(str(Path(self.temp.name) / 'disputes.sqlite'))
        self.collection = Collection(self.store)
        self.review = Review(self.collection)
        self.disputes = Disputes(self.collection)
        self.owner, self.owner_token = self.account('collector')
        self.reviewer, self.reviewer_token = self.account('reviewer')
        self.other, self.other_token = self.account('other_collector')
        self.staff, self.staff_token = self.account('third_party')
        self.other_staff, self.other_staff_token = self.account('other_third_party')
        self.collection.enroll(self.owner)
        self.collection.enroll(self.other)
        grant_reviewer(self.store, self.reviewer)
        grant_role(self.store, self.staff, 'third_party')
        self.capture = self.capture_value('initial-synthetic-capture-0001')
        self.poi = self.collection.create(self.owner, self.capture)
        with self.store.connect() as db:
            db.execute('DELETE FROM event_outbox')

    def account(self, name):
        return self.store.local_account(name, 'test-password-123', True)

    @staticmethod
    def capture_value(capture_id, color=(20, 120, 40)):
        image = io.BytesIO()
        Image.new('RGB', (24, 32), color).save(image, 'JPEG')
        now = time.time() * 1000
        # Synthetic image and coordinates; these do not represent a field visit.
        return dict(name='合成测试商铺', category='shop', description='自动测试，不是真实POI',
                    capture_id=capture_id, latitude=30., longitude=114., accuracy=12.,
                    captured_ms=now, located_ms=now,
                    photo_base64=base64.b64encode(image.getvalue()).decode())

    def report(self):
        return self.review.report(self.reviewer, self.poi, 1, '合成照片中的招牌需核实')

    def reject(self, issue):
        return self.review.respond(self.owner, self.poi, dict(
            issue_id=issue, revision=1, action='reject', reason='采集者认为原记录正确'))

    def file_case(self, opener=None):
        issue = self.report()
        self.reject(issue)
        case = self.disputes.file(opener or self.owner, issue, '请求独立现场核验')
        return issue, case

    def final_value(self, decision='error_confirmed', **extra):
        return {**self.capture_value('third-party-synthetic-capture-0002', (180, 20, 60)),
                'revision': 1, 'decision': decision, 'reason': '独立重采后的裁决说明', **extra}

    def events(self, kind=None):
        with self.store.connect() as db:
            rows = db.execute('SELECT * FROM event_outbox ORDER BY rowid').fetchall()
        return [dict(row) for row in rows if kind is None or row['event_type'] == kind]

    def state(self):
        """Compare all durable state and photo bytes across refused/failed work."""
        with self.store.connect() as db:
            data = {table: [dict(row) for row in db.execute('SELECT * FROM ' + table + ' ORDER BY rowid')]
                    for table in ('pois', 'issues', 'disputes', 'poi_versions', 'event_outbox')}
        data['photos'] = {p.name: p.read_bytes() for p in self.collection.photos.iterdir()}
        return data

    def revoke(self, uid, role):
        with self.store.connect() as db:
            db.execute('DELETE FROM user_roles WHERE user_id=? AND role=?', (uid, role))

    def test_dispute_requires_rejected_issue_and_no_premature_state_change(self):
        before = self.state()
        with self.assertRaises(AuthError):
            self.disputes.file(self.owner, 'a' * 32, '请求独立现场核验')
        self.assertEqual(self.state(), before)
        issue = self.report()
        before = self.state()
        for uid in (self.owner, self.reviewer):
            with self.subTest(uid=uid), self.assertRaises(AuthError):
                self.disputes.file(uid, issue, '尚未驳回，不得发起')
        self.assertEqual(self.state(), before)
        self.reject(issue)
        case = self.disputes.file(self.owner, issue, '请求独立现场核验')
        self.assertEqual(self.disputes.detail(self.owner, case)['case']['status'], 'assigned')

    def test_only_original_participants_with_current_roles_can_file(self):
        issue = self.report()
        self.reject(issue)
        grant_reviewer(self.store, self.other)
        before = self.state()
        for uid in (None, self.other, self.staff, self.other_staff):
            with self.subTest(uid=uid), self.assertRaises(AuthError):
                self.disputes.file(uid, issue, '无权发起')
        self.revoke(self.owner, 'collector')
        with self.assertRaises(AuthError):
            self.disputes.file(self.owner, issue, '已失去采集者权限')
        self.revoke(self.reviewer, 'reviewer')
        with self.assertRaises(AuthError):
            self.disputes.file(self.reviewer, issue, '已失去核验者权限')
        self.assertEqual(self.state(), before)
        grant_reviewer(self.store, self.reviewer)
        case = self.disputes.file(self.reviewer, issue, '核验者请求独立复核')
        self.assertEqual(self.disputes.detail(self.reviewer, case)['case']['status'], 'assigned')

    def test_no_independent_staff_rejects_filing_without_partial_case(self):
        issue = self.report()
        self.reject(issue)
        self.revoke(self.staff, 'third_party')
        grant_role(self.store, self.owner, 'third_party')
        grant_role(self.store, self.reviewer, 'third_party')
        before = self.state()
        with self.assertRaises(AuthError):
            self.disputes.file(self.owner, issue, '请求独立现场核验')
        self.assertEqual(self.state(), before)
        grant_role(self.store, self.staff, 'third_party')
        case = self.disputes.file(self.owner, issue, '请求独立现场核验')
        self.assertEqual(self.disputes.capture_context(self.staff, case)['case']['id'], case)

    def test_filing_retry_is_idempotent_but_conflicting_requests_are_refused(self):
        issue, case = self.file_case()
        before = self.state()
        self.assertEqual(self.disputes.file(self.owner, issue, '请求独立现场核验'), case)
        for uid, reason in ((self.owner, '改成另一个理由'), (self.reviewer, '请求独立现场核验')):
            with self.subTest(uid=uid), self.assertRaises(AuthError):
                self.disputes.file(uid, issue, reason)
        self.assertEqual(self.state(), before)
        assignments = self.events('dispute.assigned')
        self.assertEqual(len(assignments), 1)
        self.assertEqual(assignments[0]['recipient'], self.staff)
        self.assertIsNone(assignments[0]['delivered_ms'])

    def test_first_independent_staff_is_assigned_and_case_is_private(self):
        grant_role(self.store, self.owner, 'third_party')
        grant_role(self.store, self.reviewer, 'third_party')
        grant_role(self.store, self.other_staff, 'third_party')
        _, case = self.file_case()
        assigned, unassigned = sorted((self.staff, self.other_staff))
        for uid in (self.owner, self.reviewer, assigned):
            self.assertEqual(self.disputes.detail(uid, case)['poi']['id'], self.poi)
            self.assertIn(case, [row['id'] for row in self.disputes.list_owned(uid)])
        for uid in (None, self.other, unassigned):
            with self.subTest(uid=uid), self.assertRaises(AuthError):
                self.disputes.detail(uid, case)
        self.assertNotIn(case, [row['id'] for row in self.disputes.list_owned(unassigned)])
        self.assertEqual(self.events('dispute.assigned')[0]['recipient'], assigned)
        for uid in (self.owner, self.reviewer, unassigned):
            with self.subTest(uid=uid), self.assertRaises(AuthError):
                self.disputes.capture_context(uid, case)
        self.assertEqual(self.disputes.capture_context(assigned, case)['case']['id'], case)

    def test_access_is_revoked_when_corresponding_role_is_removed(self):
        _, case = self.file_case()
        for uid, role in ((self.owner, 'collector'), (self.reviewer, 'reviewer'),
                          (self.staff, 'third_party')):
            self.revoke(uid, role)
            with self.subTest(uid=uid), self.assertRaises(AuthError):
                self.disputes.detail(uid, case)
        with self.assertRaises(AuthError):
            self.disputes.capture_context(self.staff, case)
        before = self.state()
        with self.assertRaises(AuthError):
            self.disputes.finalize(self.staff, case, self.final_value())
        self.assertEqual(self.state(), before)

    def test_assigned_staff_only_has_photo_access_to_assigned_case(self):
        grant_role(self.store, self.other_staff, 'third_party')
        before_photo = self.collection.photo(self.owner, self.poi)
        with self.assertRaises(AuthError):
            self.collection.photo(self.staff, self.poi)
        _, case = self.file_case()
        assigned, unassigned = sorted((self.staff, self.other_staff))
        self.assertEqual(self.collection.photo(assigned, self.poi, 1), before_photo)
        for uid in (self.other, unassigned):
            with self.subTest(uid=uid), self.assertRaises(AuthError):
                self.collection.photo(uid, self.poi)
        unrelated = self.collection.create(self.other, self.capture_value('unrelated-synthetic-capture-0003'))
        with self.assertRaises(AuthError):
            self.collection.photo(assigned, unrelated)
        self.revoke(assigned, 'third_party')
        with self.assertRaises(AuthError):
            self.collection.photo(assigned, self.poi)
        self.assertEqual(self.disputes.detail(self.owner, case)['case']['status'], 'assigned')

    def test_both_decisions_publish_new_authoritative_capture_and_keep_history(self):
        for index, decision in enumerate(('error_confirmed', 'error_rejected')):
            with self.subTest(decision=decision):
                if index:
                    self.capture = self.capture_value('second-synthetic-capture-0004')
                    self.poi = self.collection.create(self.owner, self.capture)
                old_photo = self.collection.photo(self.owner, self.poi)
                _, case = self.file_case()
                value = self.final_value(decision, capture_id=f'final-synthetic-capture-{index:04d}')
                self.assertEqual(self.disputes.finalize(self.staff, case, value), 2)
                detail = Disputes(Collection(Store(self.store.path))).detail(self.owner, case)
                self.assertEqual(detail['case']['status'], 'finalized')
                self.assertEqual(detail['case']['decision'], decision)
                self.assertEqual(detail['case']['reason'], '请求独立现场核验')
                self.assertEqual(detail['case']['resolution'], value['reason'])
                self.assertEqual((detail['poi']['status'], detail['poi']['revision']), ('finalized', 2))
                self.assertEqual(detail['poi']['owner'], self.owner)
                self.assertEqual(detail['poi']['capture_id'], value['capture_id'])
                self.assertEqual([version['revision'] for version in detail['versions']], [1, 2])
                old, new = detail['versions']
                self.assertEqual(old['capture_id'], self.capture['capture_id'])
                self.assertEqual(new['captured_by'], self.staff)
                self.assertEqual(new['dispute_id'], case)
                self.assertEqual(new['description'], value['description'])
                self.assertEqual(new['latitude'], value['latitude'])
                self.assertEqual(self.collection.photo(self.owner, self.poi, 1), old_photo)
                self.assertNotEqual(self.collection.photo(self.owner, self.poi, 2), old_photo)
                self.assertEqual(self.collection.photo(self.staff, self.poi, 2), self.collection.photo(self.owner, self.poi))
                with self.store.connect() as db:
                    issue_status = db.execute('SELECT status FROM issues WHERE poi_id=?', (self.poi,)).fetchone()[0]
                self.assertEqual(issue_status, 'finalized')
                final_events = [event for event in self.events('dispute.finalized')
                                if json.loads(event['payload']).get('poi_id') == self.poi]
                self.assertEqual(len(final_events), 2)
                self.assertEqual({event['recipient'] for event in final_events}, {self.owner, self.reviewer})
                self.assertTrue(all(event['delivered_ms'] is None for event in final_events))

    def test_ordinary_or_unassigned_accounts_cannot_finalize(self):
        _, case = self.file_case()
        grant_role(self.store, self.other_staff, 'third_party')
        before = self.state()
        for uid in (None, self.owner, self.reviewer, self.other, self.other_staff):
            with self.subTest(uid=uid), self.assertRaises(AuthError):
                self.disputes.finalize(uid, case, self.final_value())
        self.assertEqual(self.state(), before)

    def test_fresh_valid_capture_and_original_revision_are_required(self):
        _, case = self.file_case()
        before = self.state()
        invalid = [
            {'revision': 0}, {'revision': 2}, {'revision': True}, {'revision': 2 ** 100},
            {'decision': 'pending'}, {'decision': []}, {'reason': ' '}, {'reason': 'x' * 1001},
            {'latitude': None}, {'latitude': float('nan')}, {'longitude': 181},
            {'accuracy': False}, {'captured_ms': 0}, {'located_ms': 0},
            {'located_ms': time.time() * 1000 - 21001}, {'photo_base64': 'not-image'},
            {'capture_id': '../capture'}, {'category': 'admin'}, {'name': ''},
        ]
        for extra in invalid:
            with self.subTest(extra=extra), self.assertRaises(AuthError):
                self.disputes.finalize(self.staff, case, self.final_value(**extra))
        for value in (None, [], 'payload'):
            with self.subTest(value=value), self.assertRaises(AuthError):
                self.disputes.finalize(self.staff, case, value)
        self.assertEqual(self.state(), before)

    def test_reused_original_or_staff_capture_is_refused(self):
        _, case = self.file_case()
        self.collection.enroll(self.staff)
        prior = self.capture_value('staff-prior-synthetic-capture-0005')
        self.collection.create(self.staff, prior)
        before = self.state()
        for capture_id in (self.capture['capture_id'], prior['capture_id']):
            with self.subTest(capture_id=capture_id), self.assertRaises(AuthError):
                self.disputes.finalize(self.staff, case, self.final_value(capture_id=capture_id))
        self.assertEqual(self.state(), before)

    def test_staff_cannot_reuse_capture_from_another_finalized_case(self):
        _, first = self.file_case()
        value = self.final_value()
        self.disputes.finalize(self.staff, first, value)
        self.poi = self.collection.create(self.other, self.capture_value('another-owner-synthetic-0008'))
        issue = self.review.report(self.reviewer, self.poi, 1, '另一个采集者的合成错误')
        self.review.respond(self.other, self.poi, dict(
            issue_id=issue, revision=1, action='reject', reason='请求独立判断'))
        second = self.disputes.file(self.other, issue, '另一个独立案件')
        before = self.state()
        with self.assertRaises(AuthError):
            self.disputes.finalize(self.staff, second, value)
        self.assertEqual(self.state(), before)
        self.assertEqual(self.disputes.detail(self.other, second)['case']['status'], 'assigned')
        self.assertEqual(self.disputes.finalize(self.staff, second, self.final_value(
            capture_id='another-final-synthetic-capture-0009')), 2)

    def test_finalized_third_party_capture_cannot_be_reused_after_collector_enrollment(self):
        _, case = self.file_case()
        evidence = self.final_value()
        self.disputes.finalize(self.staff, case, evidence)
        self.collection.enroll(self.staff)
        own_poi = self.collection.create(self.staff, self.capture_value('staff-own-synthetic-capture-0010'))
        issue = self.review.report(self.reviewer, own_poi, 1, '另一条合成记录需要重采')
        before = self.state()
        with self.assertRaises(AuthError):
            self.collection.create(self.staff, evidence)
        self.assertEqual(self.state(), before)
        with self.assertRaises(AuthError):
            self.review.respond(self.staff, own_poi, {
                **evidence, 'issue_id': issue, 'revision': 1, 'action': 'recollect',
                'reason': '不得把先前第三方裁决的证据用于本人的重采'})
        self.assertEqual(self.state(), before)

    def test_final_resolution_is_terminal_without_extra_versions_or_events(self):
        issue, case = self.file_case()
        value = self.final_value()
        self.disputes.finalize(self.staff, case, value)
        before = self.state()
        for payload in (value, self.final_value(capture_id='duplicate-final-synthetic-0006')):
            with self.assertRaises(AuthError):
                self.disputes.finalize(self.staff, case, payload)
        with self.assertRaises(AuthError):
            self.disputes.capture_context(self.staff, case)
        with self.assertRaises(AuthError):
            self.review.report(self.reviewer, self.poi, 2, '不得重开已终结记录')
        for action in ('update', 'recollect', 'reject'):
            payload = {**self.capture_value('post-final-synthetic-capture-0007'),
                       'issue_id': issue, 'revision': 2, 'action': action, 'reason': '不得重开'}
            if action == 'update':
                payload = {key: payload[key] for key in ('issue_id', 'revision', 'action', 'reason', 'name', 'category', 'description')}
            with self.subTest(action=action), self.assertRaises(AuthError):
                self.review.respond(self.owner, self.poi, payload)
        self.assertEqual(self.state(), before)
        self.assertEqual(self.disputes.detail(self.owner, case)['case']['status'], 'finalized')

    def test_assignment_event_failure_rolls_back_filing(self):
        issue = self.report()
        self.reject(issue)
        before = self.state()
        with patch('check_collect.disputes.queue_event', side_effect=RuntimeError('injected assignment failure')):
            with self.assertRaises(RuntimeError):
                self.disputes.file(self.owner, issue, '请求独立现场核验')
        self.assertEqual(self.state(), before)
        case = self.disputes.file(self.owner, issue, '请求独立现场核验')
        self.assertEqual(self.disputes.detail(self.owner, case)['case']['status'], 'assigned')
        self.assertEqual(len(self.events('dispute.assigned')), 1)

    def test_second_final_event_failure_rolls_back_everything_and_removes_new_photo(self):
        _, case = self.file_case()
        before = self.state()
        calls = []

        def fail_second(db, kind, recipient, payload):
            calls.append(kind)
            if len(calls) == 2:
                raise RuntimeError('injected second recipient failure')
            return queue_event(db, kind, recipient, payload)

        with patch('check_collect.disputes.queue_event', side_effect=fail_second):
            with self.assertRaises(RuntimeError):
                self.disputes.finalize(self.staff, case, self.final_value())
        self.assertEqual(calls, ['dispute.finalized', 'dispute.finalized'])
        self.assertEqual(self.state(), before)
        self.assertEqual(self.disputes.detail(self.owner, case)['case']['status'], 'assigned')
        self.assertEqual(self.disputes.finalize(self.staff, case, self.final_value()), 2)

    def test_snapshot_failure_rolls_back_and_cleans_new_photo_only(self):
        _, case = self.file_case()
        unrelated = self.collection.photos / 'operator-retained.jpg'
        unrelated.write_bytes(b'pre-existing unrelated evidence')
        before = self.state()
        with patch.object(self.collection, 'snapshot', side_effect=RuntimeError('injected snapshot failure')):
            with self.assertRaises(RuntimeError):
                self.disputes.finalize(self.staff, case, self.final_value())
        self.assertEqual(self.state(), before)
        self.assertEqual(self.disputes.detail(self.owner, case)['case']['status'], 'assigned')

    def test_existing_target_photo_is_preserved_on_file_conflict(self):
        _, case = self.file_case()
        target = self.collection.photos / f'{self.poi}-v2.jpg'
        target.write_bytes(b'unknown-existing-photo')
        before = self.state()
        with self.assertRaises(FileExistsError):
            self.disputes.finalize(self.staff, case, self.final_value())
        self.assertEqual(self.state(), before)
        self.assertEqual(target.read_bytes(), b'unknown-existing-photo')
        self.assertEqual(self.disputes.detail(self.owner, case)['case']['status'], 'assigned')

    def test_partial_photo_write_failure_cleans_only_new_file(self):
        _, case = self.file_case()
        retained = self.collection.photos / 'operator-retained.jpg'
        retained.write_bytes(b'pre-existing unrelated evidence')
        before = self.state()
        target = self.collection.photos / f'{self.poi}-v2.jpg'
        original_open = Path.open

        class BrokenWriter:
            def __init__(self, file):
                self.file = file

            def __enter__(self):
                return self

            def write(self, data):
                self.file.write(data[:20])
                raise OSError('injected disk write failure')

            def __exit__(self, *args):
                self.file.close()

        def open_photo(path, *args, **kwargs):
            file = original_open(path, *args, **kwargs)
            return BrokenWriter(file) if path == target else file

        with patch.object(Path, 'open', open_photo):
            with self.assertRaises(OSError):
                self.disputes.finalize(self.staff, case, self.final_value())
        self.assertEqual(self.state(), before)
        self.assertEqual(self.disputes.detail(self.owner, case)['case']['status'], 'assigned')

    def test_concurrent_finalization_has_one_winner_and_two_notifications(self):
        _, case = self.file_case()
        value = self.final_value()

        def attempt(_):
            try:
                return self.disputes.finalize(self.staff, case, value)
            except AuthError:
                return None

        with ThreadPoolExecutor(max_workers=2) as pool:
            result = list(pool.map(attempt, range(2)))
        self.assertEqual(result.count(2), 1)
        self.assertEqual(result.count(None), 1)
        self.assertEqual(len(self.disputes.detail(self.owner, case)['versions']), 2)
        self.assertEqual(len(self.events('dispute.finalized')), 2)

    def test_concurrent_filing_creates_one_case_and_assignment_event(self):
        issue = self.report()
        self.reject(issue)

        def attempt(_):
            return self.disputes.file(self.owner, issue, '请求独立现场核验')

        with ThreadPoolExecutor(max_workers=2) as pool:
            result = list(pool.map(attempt, range(2)))
        self.assertEqual(result[0], result[1])
        self.assertEqual(len(self.events('dispute.assigned')), 1)
        self.assertEqual(len(self.disputes.list_owned(self.owner)), 1)

    def test_invalid_case_ids_and_filing_values_are_refused_without_mutation(self):
        issue = self.report()
        self.reject(issue)
        before = self.state()
        for reason in (' ', None, [], 'x' * 1001):
            with self.subTest(reason=reason), self.assertRaises(AuthError):
                self.disputes.file(self.owner, issue, reason)
        for bad in (None, [], '../case', 'g' * 32, 'a' * 33):
            with self.subTest(bad=bad):
                with self.assertRaises(AuthError):
                    self.disputes.file(self.owner, bad, '请求独立现场核验')
                with self.assertRaises(AuthError):
                    self.disputes.detail(self.owner, bad)
                with self.assertRaises(AuthError):
                    self.disputes.capture_context(self.staff, bad)
                with self.assertRaises(AuthError):
                    self.disputes.finalize(self.staff, bad, self.final_value())
        self.assertEqual(self.state(), before)

    def test_trusted_role_grant_rejects_unknown_user_and_role(self):
        with self.assertRaises(AuthError):
            grant_role(self.store, 'not-a-user', 'third_party')
        with self.assertRaises(AuthError):
            grant_role(self.store, self.other, 'admin')
        self.collection.enroll(self.other)
        self.assertFalse(self.collection.has_role(self.other, 'third_party'))
        self.assertFalse(self.collection.has_role(self.other, 'reviewer'))
        grant_role(self.store, self.other, 'third_party')
        grant_role(self.store, self.other, 'third_party')
        self.assertTrue(self.collection.has_role(self.other, 'third_party'))

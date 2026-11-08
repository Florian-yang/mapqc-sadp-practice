import base64
from concurrent.futures import ThreadPoolExecutor
from email.message import Message
import hashlib
import io
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock, patch
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs
from urllib.request import Request

from PIL import Image

from check_collect.auth import AuthError, Store
from check_collect.collection import Collection
from check_collect.disputes import Disputes
from check_collect.ocr import (MAX_PHOTO_BYTES, MAX_RESPONSE_BYTES, MAX_TEXT_CHARS,
                               OCR_ENDPOINT, Ocr, OcrSpace, _NoRedirect, fetch_json)
from check_collect.review import Review
from check_collect.roles import grant_role


def jpeg(color=(35, 80, 120)):
    image = io.BytesIO()
    Image.new('RGB', (24, 32), color).save(image, 'JPEG')
    return image.getvalue()


def response(text='MAPQC SHOP 42', code=1, page_code=1, errored=False):
    return {'OCRExitCode': code, 'IsErroredOnProcessing': errored,
            'ParsedResults': [{'FileParseExitCode': page_code, 'ParsedText': text}]}


class OcrSpaceTests(unittest.TestCase):
    def test_missing_or_invalid_key_does_not_contact_provider(self):
        transport = Mock()
        for key in ('', ' ', None, 'key\r\nInjected: yes'):
            with self.subTest(key=key):
                service = OcrSpace(key, transport)
                self.assertIs(service.configured, False)
                with self.assertRaises(AuthError):
                    service.recognize(jpeg())
        transport.assert_not_called()

    def test_fixed_https_header_and_exact_jpeg_base64_request(self):
        original = jpeg()
        for language in ('auto', 'eng', 'chs'):
            with self.subTest(language=language):
                transport = Mock(return_value=response('合成商店' if language == 'chs' else 'MAPQC SHOP 42'))
                provider = OcrSpace('TEST_ONLY_KEY', transport)
                self.assertIs(provider.configured, True)
                result = provider.recognize(original, language)
                request = transport.call_args.args[0]
                self.assertIsInstance(request, Request)
                self.assertEqual((request.full_url, request.get_method()), (OCR_ENDPOINT, 'POST'))
                self.assertEqual(request.get_header('Apikey'), 'TEST_ONLY_KEY')
                self.assertNotIn(b'TEST_ONLY_KEY', request.data)
                fields = parse_qs(request.data.decode('ascii'), strict_parsing=True)
                self.assertEqual(fields['language'], [language])
                self.assertEqual(fields['OCREngine'], ['2'])
                self.assertEqual(fields['isOverlayRequired'], ['false'])
                encoded = fields['base64Image'][0]
                self.assertTrue(encoded.startswith('data:image/jpeg;base64,'))
                self.assertEqual(base64.b64decode(encoded.split(',', 1)[1], validate=True), original)
                self.assertEqual(result['suggested_category'], 'shop')
                self.assertEqual(result['language'], language)
                self.assertIs(result['partial'], False)

    def test_language_photo_type_and_free_file_size_limit(self):
        transport = Mock(return_value=response())
        provider = OcrSpace('TEST_ONLY_KEY', transport)
        for language in ('en', 'zh', '', None, [], 'auto&OCREngine=3'):
            with self.subTest(language=language), self.assertRaises(AuthError):
                provider.recognize(jpeg(), language)
        for image in (b'', b'not-jpeg', 'text', bytearray(jpeg()),
                      b'\xff\xd8\xff' + b'x' * MAX_PHOTO_BYTES + b'\xff\xd9'):
            with self.subTest(image_type=type(image).__name__), self.assertRaises(AuthError):
                provider.recognize(image)
        transport.assert_not_called()

    def test_success_accepts_documented_integer_and_string_codes(self):
        for code in (1, '1'):
            with self.subTest(code=code):
                result = OcrSpace('TEST_ONLY_KEY', lambda request: response(code=code, page_code=code)).recognize(jpeg())
                self.assertEqual(result['text'], 'MAPQC SHOP 42')

    def test_partial_result_retains_only_successful_text_and_labels_partial(self):
        value = response('已识别商店', code='2')
        value['ParsedResults'].append({'FileParseExitCode': -20, 'ParsedText': 'must not appear'})
        result = OcrSpace('TEST_ONLY_KEY', lambda request: value).recognize(jpeg(), 'chs')
        self.assertEqual(result['text'], '已识别商店')
        self.assertIs(result['partial'], True)
        self.assertEqual(result['suggested_category'], 'shop')

    def test_failed_missing_and_contradictory_statuses_are_rejected(self):
        mixed = response()
        mixed['ParsedResults'].append({'FileParseExitCode': -10, 'ParsedText': ''})
        values = [None, [], {}, response(errored=True), response(errored=0),
                  response(code=3), response(code=4), response(code=True),
                  response(code=1.0), response(code='01'), response(page_code=True),
                  response(page_code=None), response(page_code=-20),
                  response(code=2), response(code=2, page_code=-10), mixed,
                  {**response(), 'ParsedResults': []},
                  {**response(), 'ParsedResults': [None]}, response(text=None)]
        for value in values:
            with self.subTest(value=value), self.assertRaises(AuthError):
                OcrSpace('TEST_ONLY_KEY', lambda request, value=value: value).recognize(jpeg())

    def test_empty_and_ambiguous_text_do_not_invent_category(self):
        for text in ('', ' \r\n ', 'SHOPPER 42', '42', 'SHOP next to BUS STOP'):
            with self.subTest(text=text):
                result = OcrSpace('TEST_ONLY_KEY', lambda request: response(text)).recognize(jpeg())
                self.assertIsNone(result['suggested_category'])

    def test_keyword_categories_are_explainable(self):
        for text, category in (('公交车站', 'bus_stop'), ('花坛', 'greenery'),
                               ('合成住宅', 'house'), ('合成博物馆', 'scenic')):
            with self.subTest(text=text):
                result = OcrSpace('TEST_ONLY_KEY', lambda request: response(text)).recognize(jpeg())
                self.assertEqual(result['suggested_category'], category)

    def test_text_length_is_bounded_per_page_and_for_joined_pages(self):
        joined = response('x' * (MAX_TEXT_CHARS // 2), code=2)
        joined['ParsedResults'] += [{'FileParseExitCode': 1, 'ParsedText': 'y' * (MAX_TEXT_CHARS // 2)},
                                   {'FileParseExitCode': -10}]
        for value in (response('x' * (MAX_TEXT_CHARS + 1)), joined):
            with self.subTest(value_type='oversize'), self.assertRaises(AuthError):
                OcrSpace('TEST_ONLY_KEY', lambda request: value).recognize(jpeg())

    def test_network_and_provider_errors_do_not_echo_secrets(self):
        for error in (TimeoutError('TEST_ONLY_KEY'), URLError('TEST_ONLY_KEY'), ValueError('TEST_ONLY_KEY')):
            with self.subTest(error=type(error).__name__):
                with self.assertRaises(AuthError) as caught:
                    OcrSpace('TEST_ONLY_KEY', Mock(side_effect=error)).recognize(jpeg())
                self.assertNotIn('TEST_ONLY_KEY', str(caught.exception))
        value = {**response(errored=True), 'ErrorMessage': ['TEST_ONLY_KEY']}
        with self.assertRaises(AuthError) as caught:
            OcrSpace('TEST_ONLY_KEY', lambda request: value).recognize(jpeg())
        self.assertNotIn('TEST_ONLY_KEY', str(caught.exception))

    def test_default_transport_enforces_timeout_bound_and_no_redirect(self):
        headers = Message()
        headers['Content-Type'] = 'application/json; charset=utf-8'
        reply = Mock()
        reply.getcode.return_value = 200
        reply.headers = headers
        reply.read.return_value = json.dumps(response()).encode()
        reply.__enter__ = Mock(return_value=reply)
        reply.__exit__ = Mock(return_value=False)
        opener = Mock()
        opener.open.return_value = reply
        with patch('check_collect.ocr.urllib.request.build_opener', return_value=opener) as build:
            result = OcrSpace('TEST_ONLY_KEY').recognize(jpeg())
        self.assertEqual(result['text'], 'MAPQC SHOP 42')
        self.assertEqual(opener.open.call_args.kwargs['timeout'], 20)
        reply.read.assert_called_once_with(MAX_RESPONSE_BYTES + 1)
        self.assertIsInstance(build.call_args.args[0], _NoRedirect)
        self.assertIsNone(build.call_args.args[0].redirect_request(None, None, 302, '', {}, 'https://other.example'))
        for raw in (b'x' * (MAX_RESPONSE_BYTES + 1), b'not-json', b'[]'):
            reply.read.return_value = raw
            with self.subTest(raw_size=len(raw)), patch('check_collect.ocr.urllib.request.build_opener', return_value=opener):
                with self.assertRaises(AuthError):
                    OcrSpace('TEST_ONLY_KEY').recognize(jpeg())
        with self.assertRaises(AuthError):
            fetch_json(Request('http://api.ocr.space/parse/image', data=b'anything'))
        opener.open.side_effect = HTTPError(OCR_ENDPOINT, 302, 'redirect', {}, None)
        with patch('check_collect.ocr.urllib.request.build_opener', return_value=opener), self.assertRaises(AuthError):
            OcrSpace('TEST_ONLY_KEY').recognize(jpeg())


class OcrTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = Store(str(Path(self.temp.name) / 'ocr.sqlite'))
        self.collection = Collection(self.store)
        self.review = Review(self.collection)
        self.owner, _ = self.store.local_account('collector', 'test-password-123', True)
        self.reviewer, _ = self.store.local_account('reviewer', 'test-password-123', True)
        self.other, _ = self.store.local_account('stranger', 'test-password-123', True)
        self.collection.enroll(self.owner)
        grant_role(self.store, self.reviewer, 'reviewer')
        now = time.time() * 1000
        self.capture = dict(name='OCR合成POI', category='other', description='仅供自动测试',
                            capture_id='ocr-synthetic-capture-0001', latitude=30., longitude=114., accuracy=10.,
                            captured_ms=now, located_ms=now, photo_base64=base64.b64encode(jpeg()).decode())
        self.poi = self.collection.create(self.owner, self.capture)
        self.transport = Mock(return_value=response())
        self.provider = OcrSpace('TEST_ONLY_KEY', self.transport)
        self.ocr = Ocr(self.collection, self.provider)

    def count(self):
        with self.store.connect() as db:
            return db.execute('SELECT count(*) FROM ocr_results').fetchone()[0]

    def row(self):
        with self.store.connect() as db:
            return dict(db.execute('SELECT * FROM pois WHERE id=?', (self.poi,)).fetchone())

    def revoke(self, uid, role):
        with self.store.connect() as db:
            db.execute('DELETE FROM user_roles WHERE user_id=? AND role=?', (uid, role))

    def update(self, action='update'):
        issue = self.review.report(self.reviewer, self.poi, 1, '合成核验说明')
        payload = dict(issue_id=issue, revision=1, action=action, reason='合成更新说明',
                       name='更新后的POI', category='house', description='更新后的描述')
        if action == 'recollect':
            payload.update(self.capture, capture_id='ocr-synthetic-capture-0002',
                           photo_base64=base64.b64encode(jpeg((190, 25, 45))).decode())
        return self.review.respond(self.owner, self.poi, payload)

    def test_default_provider_reads_only_explicit_environment_key(self):
        with patch.dict('os.environ', {}, clear=True):
            provider = Ocr(self.collection).provider
            self.assertIs(provider.configured, False)
            with self.assertRaises(AuthError):
                Ocr(self.collection).recognize(self.owner, self.poi)
        with patch.dict('os.environ', {'OCR_SPACE_API_KEY': 'TEST_ONLY_KEY'}, clear=True):
            self.assertIs(Ocr(self.collection).provider.configured, True)
        self.assertEqual(self.count(), 0)

    def test_permissions_are_checked_before_any_external_call(self):
        for uid, poi in ((None, self.poi), (self.other, self.poi), (self.owner, 'f' * 32),
                         (self.owner, '../../photo'), (self.owner, None), ({}, self.poi)):
            with self.subTest(uid=uid, poi=poi), self.assertRaises(AuthError):
                self.ocr.recognize(uid, poi)
        self.transport.assert_not_called()
        self.assertEqual(self.count(), 0)

    def test_cache_keeps_version_hash_and_language_and_survives_restart(self):
        original = self.collection.photo(self.owner, self.poi)
        first = self.ocr.recognize(self.owner, self.poi)
        self.assertEqual(first['poi_id'], self.poi)
        self.assertEqual(first['revision'], 1)
        self.assertEqual(first['photo_sha256'], hashlib.sha256(original).hexdigest())
        restarted = Ocr(Collection(Store(self.store.path)), self.provider)
        self.assertEqual(restarted.recognize(self.reviewer, self.poi), first)
        self.assertEqual(self.transport.call_count, 1)
        self.ocr.recognize(self.owner, self.poi, 'chs')
        self.assertEqual(self.transport.call_count, 2)
        second_poi = self.collection.create(self.owner, {**self.capture, 'capture_id': 'ocr-synthetic-another-0001'})
        self.ocr.recognize(self.owner, second_poi)
        self.assertEqual((self.count(), self.transport.call_count), (3, 3))
        self.update()
        changed = self.ocr.recognize(self.owner, self.poi)
        self.assertEqual(changed['revision'], 2)
        self.assertEqual(changed['photo_sha256'], first['photo_sha256'])
        self.assertEqual((self.count(), self.transport.call_count), (4, 4))

    def test_cached_text_never_bypasses_current_permissions(self):
        self.ocr.recognize(self.reviewer, self.poi)
        self.revoke(self.reviewer, 'reviewer')
        for uid in (self.reviewer, self.other):
            with self.subTest(uid=uid), self.assertRaises(AuthError):
                self.ocr.recognize(uid, self.poi)
        self.assertEqual((self.transport.call_count, self.count()), (1, 1))

    def test_stale_page_revision_is_rejected_before_external_call_or_save(self):
        self.update('recollect')
        before = self.row()
        original = self.collection.photo(self.owner, self.poi)
        with self.assertRaisesRegex(AuthError, '版本已变化'):
            self.ocr.recognize(self.owner, self.poi, expected_revision=1)
        self.transport.assert_not_called()
        self.assertEqual(self.count(), 0)
        self.assertEqual(self.row(), before)
        self.assertEqual(self.collection.photo(self.owner, self.poi), original)

    def test_stale_page_revision_is_rejected_before_returning_current_cache(self):
        self.update('recollect')
        self.ocr.recognize(self.owner, self.poi, expected_revision=2)
        self.transport.reset_mock()
        with self.assertRaisesRegex(AuthError, '版本已变化'):
            self.ocr.recognize(self.owner, self.poi, expected_revision=1)
        self.transport.assert_not_called()
        self.assertEqual(self.count(), 1)

    def test_expected_revision_requires_a_positive_integer_excluding_bool(self):
        for revision in (True, False, 0, -1, 1.0, '1', '', [], {}):
            with self.subTest(revision=revision), self.assertRaises(AuthError):
                self.ocr.recognize(self.owner, self.poi, expected_revision=revision)
        self.transport.assert_not_called()
        self.assertEqual(self.count(), 0)

    def test_matching_expected_revision_uses_provider_then_cache(self):
        first = self.ocr.recognize(self.owner, self.poi, expected_revision=1)
        self.assertEqual(first['revision'], 1)
        self.assertEqual(self.ocr.recognize(self.owner, self.poi, expected_revision=1), first)
        self.assertEqual((self.transport.call_count, self.count()), (1, 1))

    def test_third_party_needs_current_role_and_poi_assignment(self):
        disputes = Disputes(self.collection)
        grant_role(self.store, self.other, 'third_party')
        with self.assertRaises(AuthError):
            self.ocr.recognize(self.other, self.poi)
        self.transport.assert_not_called()
        issue = self.review.report(self.reviewer, self.poi, 1, '合成核验说明')
        self.review.respond(self.owner, self.poi, dict(issue_id=issue, revision=1, action='reject', reason='申请独立核验'))
        disputes.file(self.owner, issue, '合成异议说明')
        self.assertEqual(self.ocr.recognize(self.other, self.poi)['text'], 'MAPQC SHOP 42')
        self.revoke(self.other, 'third_party')
        with self.assertRaises(AuthError):
            self.ocr.recognize(self.other, self.poi)
        self.assertEqual(self.transport.call_count, 1)

    def test_original_photo_and_all_business_rows_are_unchanged(self):
        before = self.row()
        original = self.collection.photo(self.owner, self.poi)
        with self.store.connect() as db:
            versions = [tuple(row) for row in db.execute('SELECT * FROM poi_versions')]
            events = [tuple(row) for row in db.execute('SELECT * FROM event_outbox')]
        result = self.ocr.recognize(self.owner, self.poi)
        self.assertEqual(result['suggested_category'], 'shop')
        self.assertEqual(self.row(), before)
        self.assertEqual(self.row()['category'], 'other')
        self.assertEqual(self.collection.photo(self.owner, self.poi), original)
        with self.store.connect() as db:
            self.assertEqual([tuple(row) for row in db.execute('SELECT * FROM poi_versions')], versions)
            self.assertEqual([tuple(row) for row in db.execute('SELECT * FROM event_outbox')], events)
        sent = parse_qs(self.transport.call_args.args[0].data.decode())['base64Image'][0]
        self.assertEqual(base64.b64decode(sent.split(',', 1)[1]), original)

    def test_empty_text_can_be_cached_without_suggestion(self):
        self.transport.return_value = response('  \n ')
        result = self.ocr.recognize(self.owner, self.poi)
        self.assertEqual(result['text'], '')
        self.assertIsNone(result['suggested_category'])
        self.assertEqual(self.ocr.recognize(self.owner, self.poi), result)
        self.assertEqual((self.count(), self.transport.call_count), (1, 1))

    def test_supplier_timeout_failure_and_oversize_photo_do_not_cache(self):
        self.transport.side_effect = TimeoutError('TEST_ONLY_KEY')
        with self.assertRaises(AuthError):
            self.ocr.recognize(self.owner, self.poi)
        self.assertEqual(self.count(), 0)
        self.transport.side_effect = None
        self.transport.return_value = response(errored=True)
        with self.assertRaises(AuthError):
            self.ocr.recognize(self.owner, self.poi)
        self.assertEqual(self.count(), 0)
        oversized = b'\xff\xd8\xff' + b'x' * MAX_PHOTO_BYTES + b'\xff\xd9'
        path = self.collection.photos / (self.poi + '.jpg')
        path.write_bytes(oversized)
        with self.store.connect() as db:
            db.execute('UPDATE pois SET photo_sha256=? WHERE id=?', (hashlib.sha256(oversized).hexdigest(), self.poi))
        with self.assertRaises(AuthError):
            self.ocr.recognize(self.owner, self.poi)
        self.assertEqual(path.read_bytes(), oversized)
        self.assertEqual((self.count(), self.transport.call_count), (0, 2))

    def test_revision_change_during_request_discards_stale_result(self):
        def in_flight(request):
            self.update('recollect')
            return response()
        self.transport.side_effect = in_flight
        with self.assertRaisesRegex(AuthError, '已变化'):
            self.ocr.recognize(self.owner, self.poi, expected_revision=1)
        self.assertEqual((self.row()['revision'], self.count()), (2, 0))
        self.transport.side_effect = None
        result = self.ocr.recognize(self.owner, self.poi)
        self.assertEqual(result['revision'], 2)
        self.assertEqual(self.count(), 1)

    def test_permission_revocation_during_request_discards_result(self):
        def in_flight(request):
            self.revoke(self.reviewer, 'reviewer')
            return response()
        self.transport.side_effect = in_flight
        with self.assertRaises(AuthError):
            self.ocr.recognize(self.reviewer, self.poi)
        self.assertEqual(self.count(), 0)
        self.assertEqual(self.row()['revision'], 1)

    def test_disk_photo_tamper_blocks_cached_and_in_flight_results(self):
        self.ocr.recognize(self.owner, self.poi)
        path = self.collection.photos / (self.poi + '.jpg')
        original = path.read_bytes()
        path.write_bytes(jpeg((220, 110, 20)))
        with self.assertRaisesRegex(AuthError, '校验失败'):
            self.ocr.recognize(self.owner, self.poi)
        self.assertEqual(self.transport.call_count, 1)
        path.write_bytes(original)
        with self.store.connect() as db:
            db.execute('DELETE FROM ocr_results')
        def in_flight(request):
            path.write_bytes(jpeg((220, 110, 20)))
            return response()
        self.transport.side_effect = in_flight
        with self.assertRaisesRegex(AuthError, '校验失败'):
            self.ocr.recognize(self.owner, self.poi)
        self.assertEqual(self.count(), 0)

    def test_hash_change_even_without_revision_change_cannot_use_old_cache(self):
        self.ocr.recognize(self.owner, self.poi)
        updated = jpeg((220, 110, 20))
        (self.collection.photos / (self.poi + '.jpg')).write_bytes(updated)
        new_hash = hashlib.sha256(updated).hexdigest()
        with self.store.connect() as db:
            db.execute('UPDATE pois SET photo_sha256=? WHERE id=?', (new_hash, self.poi))
        result = self.ocr.recognize(self.owner, self.poi)
        self.assertEqual(result['photo_sha256'], new_hash)
        self.assertEqual((self.count(), self.transport.call_count), (2, 2))

    def test_schema_can_initialize_concurrently(self):
        barrier = threading.Barrier(3)
        def initialize(_):
            barrier.wait(timeout=5)
            Ocr(self.collection, self.provider)
            with self.store.connect() as db:
                return [row['name'] for row in db.execute('PRAGMA table_info(ocr_results)')]
        with ThreadPoolExecutor(max_workers=3) as pool:
            columns = list(pool.map(initialize, range(3)))
        self.assertTrue(all('photo_sha256' in values for values in columns))


if __name__ == '__main__':
    unittest.main()

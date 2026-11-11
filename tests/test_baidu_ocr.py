import base64
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
from urllib.parse import parse_qs, urlsplit

from check_collect.auth import AuthError, Store
from check_collect.collection import Collection
from check_collect.ocr import (BaiduOcr, Ocr, BAIDU_TOKEN_ENDPOINT, BAIDU_OCR_ENDPOINT,
                               MAX_PHOTO_BYTES, fetch_json)
from test_ocr import jpeg


def recognized(text='兴旺商店'):
    return {'words_result_num': 1, 'words_result': [{'words': text}]}


class BaiduOcrTests(unittest.TestCase):
    def provider(self, replies):
        transport = Mock(side_effect=replies)
        return BaiduOcr('TEST_KEY', 'TEST_SECRET', transport), transport

    def test_requires_both_keys_and_valid_photo_before_network(self):
        transport = Mock()
        for key, secret in (('', ''), ('key', ''), ('', 'secret'), ('bad\nkey', 'secret')):
            provider = BaiduOcr(key, secret, transport)
            self.assertFalse(provider.configured)
            with self.assertRaises(AuthError): provider.recognize(jpeg())
        provider = BaiduOcr('key', 'secret', transport)
        for photo in (b'', b'not jpeg', b'\xff\xd8\xff' + b'x' * MAX_PHOTO_BYTES + b'\xff\xd9'):
            with self.assertRaises(AuthError): provider.recognize(photo)
        with self.assertRaises(AuthError): provider.recognize(jpeg(), 'invalid')
        transport.assert_not_called()

    def test_authorization_photo_and_cached_token(self):
        provider, transport = self.provider([
            {'access_token': 'TEST_TOKEN', 'expires_in': 3600}, recognized(), recognized('公交站')])
        photo = jpeg()
        self.assertEqual(provider.recognize(photo)['suggested_category'], 'shop')
        provider.recognize(photo, 'eng')
        self.assertEqual(transport.call_count, 3)
        auth, first, second = [call.args[0] for call in transport.call_args_list]
        self.assertEqual(auth.full_url, BAIDU_TOKEN_ENDPOINT)
        self.assertNotIn('TEST_SECRET', auth.full_url)
        self.assertEqual(parse_qs(auth.data.decode())['client_secret'], ['TEST_SECRET'])
        self.assertEqual(first.full_url.split('?')[0], BAIDU_OCR_ENDPOINT)
        self.assertEqual(parse_qs(urlsplit(first.full_url).query), {'access_token': ['TEST_TOKEN']})
        fields = parse_qs(first.data.decode())
        self.assertEqual(base64.b64decode(fields['image'][0]), photo)
        self.assertEqual(fields['language_type'], ['CHN_ENG'])
        self.assertEqual(parse_qs(second.data.decode())['language_type'], ['ENG'])

    def test_expired_token_refreshes_once(self):
        provider, transport = self.provider([
            {'access_token': 'FIRST', 'expires_in': 3600}, {'error_code': 110},
            {'access_token': 'SECOND', 'expires_in': 3600}, recognized()])
        self.assertEqual(provider.recognize(jpeg())['text'], '兴旺商店')
        self.assertEqual(transport.call_count, 4)
        provider, transport = self.provider([
            {'access_token': 'FIRST', 'expires_in': 3600}, {'error_code': 111},
            {'access_token': 'SECOND', 'expires_in': 3600}, {'error_code': 111}])
        with self.assertRaises(AuthError): provider.recognize(jpeg())
        self.assertEqual(transport.call_count, 4)

    def test_token_lifetime_expires(self):
        provider, transport = self.provider([
            {'access_token': 'FIRST', 'expires_in': 120}, recognized(),
            {'access_token': 'SECOND', 'expires_in': 120}, recognized()])
        with patch('check_collect.ocr.time.monotonic', return_value=100): provider.recognize(jpeg())
        with patch('check_collect.ocr.time.monotonic', return_value=170): provider.recognize(jpeg())
        self.assertEqual(transport.call_count, 4)

    def test_errors_do_not_expose_provider_credentials(self):
        for reply in ({'error': 'invalid_client', 'error_description': 'TEST_SECRET'},
                      {'access_token': 'bad\ntoken', 'expires_in': 3600},
                      {'access_token': 'TOKEN', 'expires_in': True}):
            provider, _ = self.provider([reply])
            with self.assertRaises(AuthError) as caught: provider.recognize(jpeg())
            self.assertNotIn('TEST_SECRET', str(caught.exception))
        provider, _ = self.provider([OSError('TEST_SECRET')])
        with self.assertRaises(AuthError) as caught: provider.recognize(jpeg())
        self.assertNotIn('TEST_SECRET', str(caught.exception))

    def test_quota_malformed_and_empty_results(self):
        invalid = [{'error_code': 17}, {'words_result': [{}], 'words_result_num': 1},
                   {'words_result': [], 'words_result_num': True},
                   {'words_result': [], 'words_result_num': 2}, recognized('x' * 10001)]
        for value in invalid:
            provider, _ = self.provider([{'access_token': 'TOKEN', 'expires_in': 3600}, value])
            with self.subTest(value_type=type(value).__name__), self.assertRaises(AuthError):
                provider.recognize(jpeg())
        provider, _ = self.provider([{'access_token': 'TOKEN', 'expires_in': 3600},
                                    {'words_result_num': 0, 'words_result': []}])
        self.assertEqual(provider.recognize(jpeg())['text'], '')

    def test_default_is_baidu_and_old_cache_is_not_relabelled(self):
        with tempfile.TemporaryDirectory() as folder:
            store = Store(str(Path(folder) / 'demo.sqlite3'))
            owner, _ = store.local_account('collector', 'Testing-only-2026!', True)
            collection = Collection(store)
            collection.enroll(owner)
            import time
            now = time.time() * 1000
            poi = collection.create(owner, {'capture_id': 'baidu-test-capture-0001', 'name': '测试',
                'category': 'other', 'description': '合成测试', 'latitude': 30, 'longitude': 114,
                'accuracy': 10, 'captured_ms': now, 'located_ms': now,
                'photo_base64': base64.b64encode(jpeg()).decode()})
            old = Mock()
            old.recognize.return_value = {'provider': 'OCR.Space', 'language': 'auto',
                'text': 'OLD', 'partial': False}
            Ocr(collection, old).recognize(owner, poi)
            provider, transport = self.provider([{'access_token': 'TOKEN', 'expires_in': 3600}, recognized()])
            result = Ocr(collection, provider).recognize(owner, poi)
            self.assertEqual((result['provider'], result['text']), ('百度OCR', '兴旺商店'))
            Ocr(collection, provider).recognize(owner, poi)
            self.assertEqual(transport.call_count, 2)
            with patch.dict('os.environ', {}, clear=True):
                self.assertIsInstance(Ocr(collection).provider, BaiduOcr)
                self.assertFalse(Ocr(collection).provider.configured)


if __name__ == '__main__':
    unittest.main()

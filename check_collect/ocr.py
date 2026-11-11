"""Explicit OCR suggestions for the current, authorized POI photo."""
import base64
import hashlib
import http.client
import json
import os
import re
import threading
import time
import urllib.parse
import urllib.request

from .auth import AuthError


OCR_ENDPOINT = 'https://api.ocr.space/parse/image'
BAIDU_TOKEN_ENDPOINT = 'https://aip.baidubce.com/oauth/2.0/token'
BAIDU_OCR_ENDPOINT = 'https://aip.baidubce.com/rest/2.0/ocr/v1/general_basic'
MAX_PHOTO_BYTES = 1_000_000
MAX_RESPONSE_BYTES = 256_000
MAX_TEXT_CHARS = 10_000
OCR_TIMEOUT = 20
LANGUAGES = frozenset(('auto', 'eng', 'chs'))


def _language(value):
    if not isinstance(value, str) or value not in LANGUAGES:
        raise AuthError('OCR语言仅支持auto、eng或chs')
    return value


def suggest_category(text):
    """A unique keyword match is a suggestion, never an automatic POI edit."""
    lowered = text.casefold()
    keywords = {
        'shop': ('shop', 'store', 'supermarket', 'restaurant', 'cafe', '商店', '商铺', '超市', '便利店', '餐厅', '饭店', '面馆', '药店'),
        'bus_stop': ('bus stop', 'bus station', '公交站', '公交车站', '巴士站'),
        'house': ('house', 'residential', 'apartment', '住宅', '公寓', '小区'),
        'greenery': ('tree', 'flower bed', '树木', '花坛', '绿化'),
        'scenic': ('scenic', 'museum', '景点', '博物馆'),
    }
    matched = set()
    for category, words in keywords.items():
        for word in words:
            if word.isascii():
                found = re.search(r'(?<![a-z0-9])' + re.escape(word) + r'(?![a-z0-9])', lowered)
            else:
                found = word in lowered
            if found:
                matched.add(category)
                break
    return next(iter(matched)) if len(matched) == 1 else None


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def fetch_json(request):
    """Use the fixed HTTPS service without logging keys or following redirects."""
    url = urllib.parse.urlsplit(request.full_url)
    baidu_ocr = (url.scheme == 'https' and url.netloc == 'aip.baidubce.com'
                 and url.path == '/rest/2.0/ocr/v1/general_basic' and not url.fragment
                 and set(urllib.parse.parse_qs(url.query)) == {'access_token'})
    if (request.full_url not in (OCR_ENDPOINT, BAIDU_TOKEN_ENDPOINT) and not baidu_ocr) or request.get_method() != 'POST':
        raise AuthError('OCR服务地址无效')
    try:
        opener = urllib.request.build_opener(_NoRedirect())
        with opener.open(request, timeout=OCR_TIMEOUT) as response:
            if response.getcode() != 200 or response.headers.get_content_type() != 'application/json':
                raise AuthError('OCR服务响应格式无效')
            raw = response.read(MAX_RESPONSE_BYTES + 1)
        if len(raw) > MAX_RESPONSE_BYTES:
            raise AuthError('OCR服务响应过大')
        data = json.loads(raw.decode('utf-8'))
        if not isinstance(data, dict):
            raise AuthError('OCR服务响应格式无效')
        return data
    except AuthError:
        raise
    except (OSError, ValueError, http.client.HTTPException):
        raise AuthError('OCR服务连接或响应异常，请稍后重试') from None


def _code(value, allowed):
    if type(value) is int and value in allowed:
        return value
    if isinstance(value, str) and value in {str(code) for code in allowed}:
        return int(value)
    raise AuthError('OCR服务返回了无效状态')


class OcrSpace:
    name = 'OCR.Space'

    def __init__(self, api_key, transport=None):
        self.api_key = api_key.strip() if isinstance(api_key, str) else ''
        self.transport = transport if transport is not None else fetch_json

    @property
    def configured(self):
        return bool(self.api_key and len(self.api_key) <= 1024 and
                    all(32 < ord(char) < 127 for char in self.api_key))

    def recognize(self, photo_bytes, language='auto'):
        if not self.configured:
            raise AuthError('尚未配置OCR_SPACE_API_KEY，无法调用文字识别')
        language = _language(language)
        if (not isinstance(photo_bytes, bytes) or not 4 <= len(photo_bytes) <= MAX_PHOTO_BYTES
                or not photo_bytes.startswith(b'\xff\xd8\xff') or not photo_bytes.endswith(b'\xff\xd9')):
            raise AuthError('OCR仅支持1MB以内的已保存JPEG照片；原照片未修改')
        body = urllib.parse.urlencode({
            'base64Image': 'data:image/jpeg;base64,' + base64.b64encode(photo_bytes).decode('ascii'),
            'language': language, 'OCREngine': '2', 'isOverlayRequired': 'false',
        }).encode('ascii')
        request = urllib.request.Request(OCR_ENDPOINT, data=body, method='POST', headers={
            'apikey': self.api_key, 'Content-Type': 'application/x-www-form-urlencoded',
            'Accept': 'application/json', 'User-Agent': 'CheckCollect/0.9 (POI course practice)',
        })
        try:
            data = self.transport(request)
        except (OSError, ValueError, http.client.HTTPException):
            # Exceptions may contain a request or credentials: do not expose them.
            raise AuthError('OCR服务连接或响应异常，请稍后重试') from None
        if not isinstance(data, dict) or data.get('IsErroredOnProcessing') is not False:
            raise AuthError('OCR服务未完成识别，请稍后重试')
        exit_code = _code(data.get('OCRExitCode'), (1, 2, 3, 4))
        if exit_code not in (1, 2):
            raise AuthError('OCR服务识别失败，请稍后重试')
        pages = data.get('ParsedResults')
        if not isinstance(pages, list) or not 1 <= len(pages) <= 100:
            raise AuthError('OCR服务缺少有效识别结果')
        texts, failures = [], 0
        for page in pages:
            if not isinstance(page, dict):
                raise AuthError('OCR服务响应格式无效')
            page_code = _code(page.get('FileParseExitCode'), (0, 1, -10, -20, -30, -99))
            if page_code != 1:
                failures += 1
                continue
            text = page.get('ParsedText')
            if not isinstance(text, str) or len(text) > MAX_TEXT_CHARS:
                raise AuthError('OCR识别文字无效或超过10000字')
            texts.append(text)
        if not texts or (exit_code == 1 and failures) or (exit_code == 2 and not failures):
            raise AuthError('OCR服务返回了不一致的识别状态')
        text = '\n'.join(texts).strip()
        if len(text) > MAX_TEXT_CHARS:
            raise AuthError('OCR识别文字超过10000字')
        return {'provider': 'OCR.Space', 'text': text, 'language': language,
                'partial': exit_code == 2, 'suggested_category': suggest_category(text)}


class BaiduOcr:
    name = '百度OCR'

    def __init__(self, api_key, secret_key, transport=None):
        self.api_key = api_key.strip() if isinstance(api_key, str) else ''
        self.secret_key = secret_key.strip() if isinstance(secret_key, str) else ''
        self.transport = transport if transport is not None else fetch_json
        self._token = ''
        self._expires = 0
        self._lock = threading.Lock()

    @property
    def configured(self):
        return all(value and len(value) <= 1024 and all(32 < ord(c) < 127 for c in value)
                   for value in (self.api_key, self.secret_key))

    def _post(self, endpoint, fields):
        request = urllib.request.Request(endpoint, data=urllib.parse.urlencode(fields).encode('ascii'),
            headers={'Content-Type': 'application/x-www-form-urlencoded', 'Accept': 'application/json'})
        try:
            result = self.transport(request)
        except (OSError, ValueError, http.client.HTTPException):
            raise AuthError('百度OCR连接或响应异常，请稍后重试') from None
        if not isinstance(result, dict):
            raise AuthError('百度OCR响应格式无效')
        return result

    def _access_token(self, rejected=None):
        with self._lock:
            if self._token and self._token != rejected and time.monotonic() < self._expires:
                return self._token
            data = self._post(BAIDU_TOKEN_ENDPOINT, {'grant_type': 'client_credentials',
                'client_id': self.api_key, 'client_secret': self.secret_key})
            token, lifetime = data.get('access_token'), data.get('expires_in')
            if (not isinstance(token, str) or not token or len(token) > 4096
                    or not all(32 < ord(c) < 127 for c in token)
                    or type(lifetime) is not int or lifetime <= 0):
                raise AuthError('百度OCR授权失败，请检查应用密钥和服务权限')
            self._token = token
            self._expires = time.monotonic() + max(0, lifetime - 60)
            return token

    def recognize(self, photo_bytes, language='auto'):
        if not self.configured:
            raise AuthError('请配置BAIDU_OCR_API_KEY和BAIDU_OCR_SECRET_KEY')
        language = _language(language)
        if (not isinstance(photo_bytes, bytes) or not 4 <= len(photo_bytes) <= MAX_PHOTO_BYTES
                or not photo_bytes.startswith(b'\xff\xd8\xff') or not photo_bytes.endswith(b'\xff\xd9')):
            raise AuthError('OCR仅支持1MB以内的已保存JPEG照片')
        fields = {'image': base64.b64encode(photo_bytes).decode('ascii'),
                  'language_type': 'ENG' if language == 'eng' else 'CHN_ENG', 'detect_direction': 'true'}
        token = self._access_token()
        for attempt in range(2):
            data = self._post(BAIDU_OCR_ENDPOINT + '?' + urllib.parse.urlencode({'access_token': token}), fields)
            if data.get('error_code') not in (110, 111) or attempt:
                break
            token = self._access_token(rejected=token)
        if 'error_code' in data:
            if data['error_code'] in (17, 18, 19):
                raise AuthError('百度OCR调用额度或频率受限，请检查控制台')
            raise AuthError('百度OCR识别失败，请检查服务权限或稍后重试')
        words = data.get('words_result')
        if (not isinstance(words, list) or len(words) > MAX_TEXT_CHARS
                or type(data.get('words_result_num')) is not int or data['words_result_num'] != len(words)
                or any(not isinstance(row, dict) or not isinstance(row.get('words'), str)
                       or len(row['words']) > MAX_TEXT_CHARS for row in words)):
            raise AuthError('百度OCR识别结果格式无效')
        text = '\n'.join(row['words'] for row in words).strip()
        if len(text) > MAX_TEXT_CHARS:
            raise AuthError('OCR识别文字超过10000字')
        return {'provider': self.name, 'text': text, 'language': language,
                'partial': False, 'suggested_category': suggest_category(text)}


class Ocr:
    def __init__(self, collection, provider=None):
        self.collection = collection
        self.store = collection.store
        self.provider = provider if provider is not None else BaiduOcr(
            os.getenv('BAIDU_OCR_API_KEY', ''), os.getenv('BAIDU_OCR_SECRET_KEY', ''))
        with self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            db.execute('''CREATE TABLE IF NOT EXISTS ocr_results(
              poi_id TEXT NOT NULL REFERENCES pois(id), revision INTEGER NOT NULL,
              photo_sha256 TEXT NOT NULL, language TEXT NOT NULL,
              provider TEXT NOT NULL, text TEXT NOT NULL,
              partial INTEGER NOT NULL CHECK(partial IN (0,1)), suggested_category TEXT,
              created_ms REAL NOT NULL,
              PRIMARY KEY(poi_id,revision,photo_sha256,language))''')

    def _snapshot(self, db, uid, poi_id):
        row = db.execute('SELECT owner,revision,photo_sha256 FROM pois WHERE id=?', (poi_id,)).fetchone()
        if not row or not self.collection.can_read(db, uid, poi_id, row['owner']):
            raise AuthError('POI不存在或无权识别照片')
        try:
            photo = self.collection.photo(uid, poi_id, revision=row['revision'])
        except OSError:
            raise AuthError('POI照片不可读取') from None
        if hashlib.sha256(photo).hexdigest() != row['photo_sha256']:
            raise AuthError('POI照片校验失败，请刷新后重试')
        return row['revision'], row['photo_sha256'], photo

    @staticmethod
    def _result(row):
        return {key: bool(row[key]) if key == 'partial' else row[key] for key in (
            'poi_id', 'revision', 'photo_sha256', 'provider', 'text', 'language', 'partial', 'suggested_category')}

    def recognize(self, uid, poi_id, language='auto', expected_revision=None):
        language = _language(language)
        if expected_revision is not None and (type(expected_revision) is not int or expected_revision < 1):
            raise AuthError('预期POI版本须为正整数')
        if not isinstance(uid, str) or not uid or not isinstance(poi_id, str) or not re.fullmatch(r'[a-f0-9]{32}', poi_id):
            raise AuthError('POI不存在或无权识别照片')
        with self.store.connect() as db:
            # This short transaction captures a coherent permission/photo/cache snapshot.
            db.execute('BEGIN IMMEDIATE')
            revision, photo_hash, photo = self._snapshot(db, uid, poi_id)
            if expected_revision is not None and revision != expected_revision:
                raise AuthError('POI版本已变化，请刷新照片并重新确认后识别')
            key = (poi_id, revision, photo_hash, language)
            cached = db.execute('SELECT * FROM ocr_results WHERE poi_id=? AND revision=? AND photo_sha256=? AND language=?', key).fetchone()
            if cached and cached['provider'] == getattr(self.provider, 'name', cached['provider']):
                return self._result(cached)

        # No database transaction is held during the external request.
        result = self.provider.recognize(photo, language=language)
        if (not isinstance(result, dict) or not isinstance(result.get('provider'), str)
                or not 1 <= len(result['provider']) <= 80 or result.get('language') != language
                or not isinstance(result.get('text'), str) or len(result['text']) > MAX_TEXT_CHARS
                or type(result.get('partial')) is not bool):
            raise AuthError('OCR服务结果格式无效')
        text = result['text'].strip()
        suggestion = suggest_category(text)
        with self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            current_revision, current_hash, _ = self._snapshot(db, uid, poi_id)
            if (current_revision, current_hash) != (revision, photo_hash):
                raise AuthError('POI版本或照片已变化，识别结果未保存，请刷新后重试')
            db.execute('''INSERT INTO ocr_results(
                poi_id,revision,photo_sha256,language,provider,text,partial,suggested_category,created_ms)
                VALUES(?,?,?,?,?,?,?,?,?) ON CONFLICT(poi_id,revision,photo_sha256,language)
                DO UPDATE SET provider=excluded.provider,text=excluded.text,partial=excluded.partial,
                suggested_category=excluded.suggested_category,created_ms=excluded.created_ms
                WHERE ocr_results.provider != excluded.provider''',
                key + (result['provider'], text, int(result['partial']), suggestion, time.time()*1000))
            saved = db.execute('SELECT * FROM ocr_results WHERE poi_id=? AND revision=? AND photo_sha256=? AND language=?', key).fetchone()
            return self._result(saved)

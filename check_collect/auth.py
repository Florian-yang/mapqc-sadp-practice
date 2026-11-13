"""WeChat website OAuth adapter and persistent local account/session storage."""
from __future__ import annotations
from dataclasses import dataclass
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import secrets
import sqlite3
import time
import urllib.parse
import urllib.request


class AuthError(ValueError):
    pass


def digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


@dataclass(frozen=True)
class Settings:
    origin: str
    app_id: str = ''
    app_secret: str = ''
    database: str = 'data/check-collect.sqlite3'
    wechat_mode: str = 'website'

    def __post_init__(self):
        if self.wechat_mode not in ('website', 'official_account'):
            raise ValueError('WECHAT_LOGIN_MODE须为website或official_account')
        p = urllib.parse.urlsplit(self.origin)
        if p.scheme not in ('http', 'https') or not p.hostname or p.username or p.password or p.path or p.query or p.fragment:
            raise ValueError('PUBLIC_ORIGIN须为不带路径的http(s)地址')
        if p.scheme != 'https' and p.hostname not in ('localhost', '127.0.0.1', '::1'):
            raise ValueError('公开部署必须使用HTTPS')

    @classmethod
    def from_env(cls):
        return cls(os.getenv('PUBLIC_ORIGIN', 'http://127.0.0.1:8765').rstrip('/'),
                   os.getenv('WECHAT_APP_ID', ''), os.getenv('WECHAT_APP_SECRET', ''),
                   os.getenv('CHECK_COLLECT_DB', 'data/check-collect.sqlite3'),
                   os.getenv('WECHAT_LOGIN_MODE', 'website'))

    @property
    def wechat_login_label(self):
        return '微信授权登录' if self.wechat_mode == 'official_account' else '微信扫码登录'

    @property
    def callback(self):
        return self.origin + '/auth/wechat/callback'

    @property
    def configured(self):
        return bool(self.app_id and self.app_secret)


def fetch_json(url: str) -> dict:
    # URLs may contain an application secret or access token: never log them.
    try:
        with urllib.request.urlopen(url, timeout=10) as response:
            raw = response.read(128_001)
        if len(raw) > 128_000:
            raise AuthError('微信响应过大')
        data = json.loads(raw)
        if not isinstance(data, dict):
            raise ValueError()
        return data
    except (OSError, ValueError) as exc:
        raise AuthError('微信服务连接或响应异常，请重新登录') from None


class WeChat:
    def __init__(self, settings: Settings, transport=fetch_json):
        self.settings, self.transport = settings, transport

    def authorization_url(self, state: str) -> str:
        if not self.settings.configured:
            raise AuthError('尚未配置微信应用，无法开始真实授权')
        official = self.settings.wechat_mode == 'official_account'
        endpoint = 'https://open.weixin.qq.com/connect/oauth2/authorize' if official else 'https://open.weixin.qq.com/connect/qrconnect'
        return endpoint + '?' + urllib.parse.urlencode({
            'appid': self.settings.app_id, 'redirect_uri': self.settings.callback,
            'response_type': 'code', 'scope': 'snsapi_base' if official else 'snsapi_login', 'state': state,
        }) + '#wechat_redirect'

    def identify(self, code: str) -> dict:
        if not self.settings.configured or not code or len(code) > 512:
            raise AuthError('授权码或应用配置无效')
        data = self.transport('https://api.weixin.qq.com/sns/oauth2/access_token?' + urllib.parse.urlencode({
            'appid': self.settings.app_id, 'secret': self.settings.app_secret,
            'code': code, 'grant_type': 'authorization_code'}))
        if data.get('errcode') or not all(isinstance(data.get(k), str) and data[k] for k in ('openid', 'access_token')):
            raise AuthError('微信授权失败或授权码已失效，请重新登录')
        # openid is authoritative; no user-supplied nickname is used as identity.
        # Keep tokens in this call only. Profile collection is unnecessary for login.
        if len(data['openid']) > 256:
            raise AuthError('微信身份格式无效')
        return {'appid': self.settings.app_id, 'openid': data['openid']}


class Store:
    def __init__(self, path: str):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        with self.connect() as db:
            db.executescript('''
            CREATE TABLE IF NOT EXISTS users(id TEXT PRIMARY KEY, created INTEGER NOT NULL);
            CREATE TABLE IF NOT EXISTS identities(
              appid TEXT NOT NULL, openid TEXT NOT NULL, user_id TEXT NOT NULL REFERENCES users(id),
              PRIMARY KEY(appid,openid), UNIQUE(appid,user_id));
            CREATE TABLE IF NOT EXISTS credentials(
              username TEXT PRIMARY KEY, user_id TEXT UNIQUE NOT NULL REFERENCES users(id),
              salt TEXT NOT NULL, password_hash TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS sessions(
              token_hash TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES users(id), expires INTEGER NOT NULL);
            CREATE TABLE IF NOT EXISTS states(
              token_hash TEXT PRIMARY KEY, browser_hash TEXT NOT NULL, purpose TEXT NOT NULL,
              user_id TEXT, session_hash TEXT, expires INTEGER NOT NULL);
            ''')

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA foreign_keys=ON')
        try:
            with db:
                yield db
        finally:
            db.close()

    def user(self, session: str) -> str | None:
        if not session:
            return None
        with self.connect() as db:
            r = db.execute('SELECT user_id FROM sessions WHERE token_hash=? AND expires>?',
                           (digest(session), int(time.time()))).fetchone()
            return r['user_id'] if r else None

    def local_account(self, username: str, password: str, register=False) -> tuple[str, str]:
        username = username.strip().casefold()
        if not (3 <= len(username) <= 40 and username.isascii() and all(c.isalnum() or c in '_-' for c in username)):
            raise AuthError('账号须为3—40位字母、数字、下划线或连字符')
        if not 8 <= len(password) <= 128:
            raise AuthError('密码须为8—128个字符')
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT * FROM credentials WHERE username=?', (username,)).fetchone()
            if register and row:
                raise AuthError('账号已存在')
            salt = secrets.token_hex(16) if register or row is None else row['salt']
            hashed = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt), n=16384, r=8, p=1).hex()
            if register:
                uid = secrets.token_hex(16)
                db.execute('INSERT INTO users VALUES(?,?)', (uid, int(time.time())))
                db.execute('INSERT INTO credentials VALUES(?,?,?,?)', (username, uid, salt, hashed))
            else:
                if row is None or not secrets.compare_digest(row['password_hash'], hashed):
                    raise AuthError('账号或密码错误')
                uid = row['user_id']
            token = secrets.token_urlsafe(32)
            db.execute('INSERT INTO sessions VALUES(?,?,?)', (digest(token), uid, int(time.time())+7200))
            return uid, token

    def start(self, purpose: str, session: str = '') -> tuple[str, str]:
        if purpose not in ('login', 'bind'):
            raise AuthError('无效授权操作')
        uid = self.user(session)
        if purpose == 'bind' and not uid:
            raise AuthError('绑定微信前请先登录本系统账号')
        state, browser = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
        now = int(time.time())
        with self.connect() as db:
            db.execute('DELETE FROM states WHERE expires<=?', (now,))
            db.execute('DELETE FROM sessions WHERE expires<=?', (now,))
            db.execute('INSERT INTO states VALUES(?,?,?,?,?,?)',
                       (digest(state), digest(browser), purpose, uid if purpose == 'bind' else None,
                        digest(session) if purpose == 'bind' else None, now + 600))
        return state, browser

    def consume(self, state: str, browser: str, session: str = '') -> dict:
        if not state or not browser:
            raise AuthError('授权状态缺失，请从本系统重新发起登录')
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT * FROM states WHERE token_hash=?', (digest(state),)).fetchone()
            if not row or row['expires'] <= int(time.time()) or not secrets.compare_digest(row['browser_hash'], digest(browser)):
                raise AuthError('授权状态无效、过期或浏览器不匹配')
            if row['purpose'] == 'bind' and (not session or row['session_hash'] != digest(session) or self.user(session) != row['user_id']):
                raise AuthError('绑定会话已变化，请重新发起绑定')
            db.execute('DELETE FROM states WHERE token_hash=?', (digest(state),))
            return dict(row)

    def complete(self, attempt: dict, identity: dict, previous: str = '') -> tuple[str, str]:
        appid, openid = identity['appid'], identity['openid']
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            existing = db.execute('SELECT user_id FROM identities WHERE appid=? AND openid=?', (appid, openid)).fetchone()
            if attempt['purpose'] == 'bind':
                uid = attempt['user_id']
                active = db.execute('SELECT user_id FROM sessions WHERE token_hash=? AND expires>?',
                                    (attempt['session_hash'], int(time.time()))).fetchone()
                if not active or active['user_id'] != uid:
                    raise AuthError('绑定会话已失效')
                owned = db.execute('SELECT openid FROM identities WHERE appid=? AND user_id=?', (appid, uid)).fetchone()
                if existing and existing['user_id'] != uid:
                    raise AuthError('该微信身份已绑定其他账号')
                if owned and owned['openid'] != openid:
                    raise AuthError('当前账号已有其他微信绑定，本版本不支持直接替换')
            else:
                uid = existing['user_id'] if existing else secrets.token_hex(16)
                if not existing:
                    db.execute('INSERT INTO users VALUES(?,?)', (uid, int(time.time())))
            db.execute('INSERT OR IGNORE INTO identities VALUES(?,?,?)', (appid, openid, uid))
            token = secrets.token_urlsafe(32)
            db.execute('INSERT INTO sessions VALUES(?,?,?)', (digest(token), uid, int(time.time()) + 7200))
            if previous:
                db.execute('DELETE FROM sessions WHERE token_hash=?', (digest(previous),))
            return uid, token

    def logout(self, token: str):
        with self.connect() as db:
            db.execute('DELETE FROM sessions WHERE token_hash=?', (digest(token),))

    def bound(self, uid: str) -> bool:
        with self.connect() as db:
            return bool(db.execute('SELECT 1 FROM identities WHERE user_id=?', (uid,)).fetchone())

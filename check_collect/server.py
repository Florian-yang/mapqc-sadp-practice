"""Local HTTP adapter; public deployments require an HTTPS reverse proxy."""
from __future__ import annotations
import argparse
import html
import threading
import time
from http.cookies import SimpleCookie, CookieError
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit, parse_qs
from .auth import Settings, Store, WeChat, AuthError


def application(settings: Settings, provider=None):
    store = Store(settings.database)
    provider = provider or WeChat(settings)
    attempts = {}
    attempts_lock = threading.Lock()

    class Handler(BaseHTTPRequestHandler):
        server_version = 'CheckCollect'

        def setup(self):
            super().setup()
            self.connection.settimeout(10)

        def log_message(self, *args):
            pass  # Do not expose OAuth code, state or tokens through access logs.

        def cookies(self):
            try:
                c = SimpleCookie(self.headers.get('Cookie', ''))
                return {k: v.value for k, v in c.items()}
            except CookieError:
                return {}

        def cookie(self, name, value, age):
            secure = '; Secure' if settings.origin.startswith('https:') else ''
            return f'{name}={value}; Path=/; HttpOnly; SameSite=Lax; Max-Age={age}{secure}'

        def reply(self, status, body='', location=None, cookies=()):
            self.send_response(status)
            self.send_header('Content-Type', 'text/html; charset=utf-8')
            self.send_header('Cache-Control', 'no-store')
            self.send_header('Referrer-Policy', 'no-referrer')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.send_header('Content-Security-Policy', "default-src 'none'; style-src 'unsafe-inline'; form-action 'self'; frame-ancestors 'none'; base-uri 'none'")
            if location:
                self.send_header('Location', location)
            for value in cookies:
                self.send_header('Set-Cookie', value)
            data = body.encode('utf-8')
            self.send_header('Content-Length', str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def page(self, content):
            return '<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>check@collect</title><style>body{font:18px/1.7 system-ui;max-width:640px;margin:8vh auto;padding:24px;color:#20352c;background:#f5f8f5}main{padding:28px;background:white;border:1px solid #dbe4dd;border-radius:14px}h1{font-size:28px}button{padding:12px 20px;background:#19734d;color:white;border:0;border-radius:7px;font-size:17px;cursor:pointer}a{color:#19734d}code{overflow-wrap:anywhere}</style><main><h1>check@collect</h1>' + content + '</main></html>'

        def do_GET(self):
            path = urlsplit(self.path).path
            cookies = self.cookies()
            session = cookies.get('cc_session', '')
            uid = store.user(session)
            if path == '/':
                if uid:
                    body = f'<p>已登录</p><p>账号：<code>{html.escape(uid)}</code></p><p>微信身份：{"已绑定" if store.bound(uid) else "未绑定"}</p>'
                    if settings.configured:
                        body += '<form action="/auth/wechat/bind" method="post"><button>绑定或核验微信</button></form>'
                    else:
                        body += '<p>微信绑定待平台配置。</p>'
                    body += '<p></p><form action="/auth/logout" method="post"><button>退出登录</button></form>'
                    body += '<p>POI采集与核验功能按后续计划接入。</p>'
                elif settings.configured:
                    body = '<p>POI数据采集与核验</p><p>使用微信身份登录，首次授权后建立本系统账号。</p><form action="/auth/wechat/login" method="post"><button>微信扫码登录</button></form>'
                else:
                    body = '<p>POI数据采集与核验</p><p>微信登录待配置</p><p>尚未配置微信应用与授权回调地址，当前无法进行真实扫码登录。</p>'
                if not uid:
                    body += '<hr><p>也可先建立本系统账号，随后绑定微信。</p><form action="/auth/local/login" method="post"><p><label>账号 <input name="username" required minlength="3" maxlength="40" autocomplete="username"></label></p><p><label>密码 <input type="password" name="password" required minlength="12" maxlength="128" autocomplete="current-password"></label></p><button>账号登录</button> <button formaction="/auth/local/register">注册账号</button></form>'
                self.reply(200, self.page(body))
            elif path == '/auth/wechat/callback':
                args = parse_qs(urlsplit(self.path).query)
                def one(key):
                    values = args.get(key, [])
                    return values[0] if len(values) == 1 else ''
                try:
                    attempt = store.consume(one('state'), cookies.get('cc_oauth', ''), session)
                    if one('error') or not one('code'):
                        raise AuthError('微信授权未完成，请重新发起')
                    identity = provider.identify(one('code'))
                    _, token = store.complete(attempt, identity, session)
                    self.reply(303, location='/', cookies=[self.cookie('cc_session', token, 7200), self.cookie('cc_oauth', '', 0)])
                except AuthError as exc:
                    self.reply(400, self.page(f'<p>{html.escape(str(exc))}</p><a href="/">返回登录页</a>'), cookies=[self.cookie('cc_oauth', '', 0)])
            else:
                self.reply(404, self.page('<p>页面不存在</p>'))

        def do_POST(self):
            # Browser forms have an Origin. No mutation from foreign origins.
            if self.headers.get('Origin') != settings.origin:
                self.reply(403, self.page('<p>请求来源无效</p>'))
                return
            path = urlsplit(self.path).path
            try:
                length = int(self.headers.get('Content-Length', '0'))
            except ValueError:
                length = -1
            local = path in ('/auth/local/login', '/auth/local/register')
            if self.headers.get('Transfer-Encoding') or not 0 <= length <= 4096 or (not local and length):
                self.close_connection = True
                self.reply(400, self.page('<p>该操作不接收表单内容</p>'))
                return
            session = self.cookies().get('cc_session', '')
            if local:
                if self.headers.get('Content-Type', '').split(';')[0] != 'application/x-www-form-urlencoded':
                    self.reply(400, self.page('<p>表单格式无效</p>'))
                    self.close_connection = True
                    return
                with attempts_lock:
                    now = time.monotonic()
                    for key in list(attempts):
                        attempts[key] = [t for t in attempts[key] if now-t < 60]
                        if not attempts[key]: del attempts[key]
                    key = self.client_address[0]
                    recent = attempts.setdefault(key, [])
                    limited = len(recent) >= 10
                    recent.append(now)
                if limited:
                    self.close_connection = True
                    self.reply(429, self.page('<p>尝试过多，请一分钟后重试</p>'))
                    return
                try:
                    fields = parse_qs(self.rfile.read(length).decode('utf-8'), max_num_fields=4)
                    if any(len(fields.get(k, [])) != 1 for k in ('username', 'password')):
                        raise AuthError('请填写账号与密码')
                    _, token = store.local_account(fields['username'][0], fields['password'][0], path.endswith('register'))
                    if session: store.logout(session)
                    self.reply(303, location='/', cookies=[self.cookie('cc_session', token, 7200)])
                except (AuthError, ValueError) as exc:
                    self.reply(400, self.page('<p>'+html.escape(str(exc) if isinstance(exc,AuthError) else '表单无效')+'</p><a href="/">返回</a>'))
            elif path == '/auth/logout':
                store.logout(session)
                self.reply(303, location='/', cookies=[self.cookie('cc_session', '', 0), self.cookie('cc_oauth', '', 0)])
            elif path in ('/auth/wechat/login', '/auth/wechat/bind'):
                try:
                    if not settings.configured:
                        raise AuthError('请先配置微信应用和回调地址')
                    state, browser = store.start('bind' if path.endswith('bind') else 'login', session)
                    self.reply(303, location=provider.authorization_url(state), cookies=[self.cookie('cc_oauth', browser, 600)])
                except AuthError as exc:
                    self.reply(400, self.page(f'<p>{html.escape(str(exc))}</p><a href="/">返回</a>'))
            else:
                self.reply(404)

    return Handler


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--host', default='127.0.0.1')
    parser.add_argument('--port', type=int, default=8765)
    args = parser.parse_args()
    settings = Settings.from_env()
    server = ThreadingHTTPServer((args.host, args.port), application(settings))
    print(f'check@collect: {settings.origin} (WeChat configured: {settings.configured})')
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == '__main__':
    main()

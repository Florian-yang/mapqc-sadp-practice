"""Local HTTP adapter; public deployments require an HTTPS reverse proxy."""
from __future__ import annotations
import argparse
import html
import json
import threading
import time
from http.cookies import SimpleCookie, CookieError
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit, parse_qs
from .auth import Settings, Store, WeChat, AuthError
from .collection import Collection, CATEGORIES, MAX_BODY
from .review import Review
from .notifications import Notifications
from .chat import Chat
from .disputes import Disputes, DECISIONS
from . import review_pages, chat_pages, dispute_pages


def application(settings: Settings, provider=None):
    store = Store(settings.database)
    collection = Collection(store)
    review = Review(collection)
    notifications = Notifications(store)
    chat = Chat(collection)
    disputes = Disputes(collection)
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

        def reply(self, status, body='', location=None, cookies=(), content_type='text/html; charset=utf-8'):
            self.send_response(status)
            self.send_header('Content-Type', content_type)
            self.send_header('Cache-Control', 'no-store')
            self.send_header('Referrer-Policy', 'no-referrer')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.send_header('Content-Security-Policy', "default-src 'none'; script-src 'self'; connect-src 'self'; img-src 'self' data:; media-src 'self' blob:; style-src 'unsafe-inline'; form-action 'self'; frame-ancestors 'none'; base-uri 'none'")
            self.send_header('Permissions-Policy', 'camera=(self), geolocation=(self), microphone=()')
            if location:
                self.send_header('Location', location)
            for value in cookies:
                self.send_header('Set-Cookie', value)
            data = body if isinstance(body,bytes) else body.encode('utf-8')
            self.send_header('Content-Length', str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def page(self, content):
            return '<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>check@collect</title><style>body{font:18px/1.7 system-ui;max-width:640px;margin:8vh auto;padding:24px;color:#20352c;background:#f5f8f5}main{padding:28px;background:white;border:1px solid #dbe4dd;border-radius:14px}h1{font-size:28px}button{padding:12px 20px;background:#19734d;color:white;border:0;border-radius:7px;font-size:17px;cursor:pointer}a{color:#19734d}code{overflow-wrap:anywhere}</style><main><h1>check@collect</h1>' + content + '</main></html>'

        def reply_error(self,status,message):
            if urlsplit(self.path).path.startswith('/api/'):
                self.reply(status,json.dumps({'error':message},ensure_ascii=False),content_type='application/json; charset=utf-8')
            else:
                self.reply(status,self.page('<p>'+html.escape(message)+'</p><a href="/">返回</a>'))

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
                    if collection.is_collector(uid):
                        body += '<p><a href="/collect">采集POI</a>　<a href="/my-pois">我的采集</a></p>'
                    else:
                        body += '<form action="/profile/collector" method="post"><button>加入采集者</button></form>'
                    if collection.has_role(uid,'reviewer'):
                        body += '<p><a href="/review">核验工作台</a></p>'
                    body += '<p><a href="/notifications">我的通知</a></p>'
                    if any(collection.has_role(uid,r) for r in ('collector','reviewer')):
                        body += '<p><a href="/chats">工作讨论</a></p>'
                    if any(collection.has_role(uid,r) for r in ('collector','reviewer','third_party')):
                        body += '<p><a href="/disputes">异议处理</a></p>'
                elif settings.configured:
                    body = '<p>POI数据采集与核验</p><p>使用微信身份登录，首次授权后建立本系统账号。</p><form action="/auth/wechat/login" method="post"><button>微信扫码登录</button></form>'
                else:
                    body = '<p>POI数据采集与核验</p><p>微信登录待配置</p><p>尚未配置微信应用与授权回调地址，当前无法进行真实扫码登录。</p>'
                if not uid:
                    body += '<hr><p>也可先建立本系统账号，随后绑定微信。</p><form action="/auth/local/login" method="post"><p><label>账号 <input name="username" required minlength="3" maxlength="40" autocomplete="username"></label></p><p><label>密码 <input type="password" name="password" required minlength="12" maxlength="128" autocomplete="current-password"></label></p><button>账号登录</button> <button formaction="/auth/local/register">注册账号</button></form>'
                self.reply(200, self.page(body))
            elif path=='/chats' or path.startswith('/chat/') or path in ('/disputes','/api/disputes') or path.startswith(('/dispute/','/api/disputes/')):
                try:
                    if not uid:self.reply_error(401,'请先登录');return
                    if path=='/chats':self.reply(200,self.page(chat_pages.listing(chat,uid)))
                    elif path.startswith('/chat/') and len(path.split('/'))==3:self.reply(200,self.page(chat_pages.room(chat,uid,path.split('/')[2])))
                    elif path=='/disputes':self.reply(200,self.page(dispute_pages.listing(disputes,uid)))
                    elif path=='/api/disputes':self.reply(200,json.dumps(disputes.list_owned(uid),ensure_ascii=False),content_type='application/json; charset=utf-8')
                    elif path.startswith('/dispute/') and len(path.split('/'))==3:self.reply(200,self.page(dispute_pages.detail(disputes,uid,path.split('/')[2])))
                    elif path.startswith('/api/disputes/') and len(path.split('/'))==4:self.reply(200,json.dumps(disputes.detail(uid,path.split('/')[3]),ensure_ascii=False),content_type='application/json; charset=utf-8')
                    else:self.reply(404)
                except AuthError as exc:self.reply_error(403,str(exc))
            elif path in ('/notifications','/api/notifications'):
                if not uid:
                    self.reply_error(401,'请先登录');return
                if path=='/notifications': self.reply(200,self.page(notifications.page(uid)))
                else:self.reply(200,json.dumps(notifications.list_owned(uid),ensure_ascii=False),content_type='application/json; charset=utf-8')
            elif path == '/static/capture.js':
                self.reply(200,(Path(__file__).parent/'static/capture.js').read_bytes(),content_type='text/javascript; charset=utf-8')
            elif path=='/review' or path=='/api/review' or path.startswith('/poi/') or path.startswith('/api/review/'):
                try:
                    if not uid:
                        self.reply_error(401,'请先登录');return
                    if path=='/review': self.reply(200,self.page(review_pages.listing(review,uid,True)))
                    elif path=='/api/review': self.reply(200,json.dumps(review.queue(uid),ensure_ascii=False),content_type='application/json; charset=utf-8')
                    elif path.startswith('/poi/') and len(path.split('/'))==3:
                        self.reply(200,self.page(review_pages.history(review.detail(uid,path.split('/')[2]),uid)))
                    elif path.startswith('/api/review/') and len(path.split('/'))==4:
                        self.reply(200,json.dumps(review.detail(uid,path.split('/')[3]),ensure_ascii=False),content_type='application/json; charset=utf-8')
                    else:self.reply(404)
                except AuthError as exc:
                    self.reply_error(403,str(exc))
            elif path in ('/collect','/my-pois') or path.startswith('/api/pois'):
                try:
                    if not uid:
                        self.reply(401,self.page('<p>请先登录</p><a href="/">登录</a>'));return
                    query=parse_qs(urlsplit(self.path).query)
                    arbitration_capture=path=='/collect' and set(query)=={'dispute_id'}
                    if not path.endswith('/photo') and not arbitration_capture: collection._require(uid)
                    if path == '/collect':
                        options=''.join(f'<option value="{k}">{v}</option>' for k,v in CATEGORIES.items())
                        content='<p><a href="/">账号</a> · <a href="/my-pois">我的采集</a></p><h2>采集POI</h2><p>打开相机后拍照，系统自动取得设备位置；定位成功后才能上传。</p><video autoplay playsinline muted style="width:100%;max-height:300px"></video><canvas hidden></canvas><p><button type="button" id="open-camera">打开相机</button> <button type="button" id="take-photo" disabled>拍照并自动定位</button></p><p id="notice" role="status"></p><img id="preview" alt="拍照预览" style="max-width:100%"><form id="capture-form"><p><label>POI名称 <input name="name" required maxlength="120"></label></p><p><label>类别 <select name="category">'+options+'</select></label></p><p><label>基本描述 <textarea name="description" required maxlength="1000" rows="3" style="width:100%;box-sizing:border-box"></textarea></label></p><button id="upload" disabled>上传照片与位置</button></form><script src="/static/capture.js" defer></script>'
                        query=parse_qs(urlsplit(self.path).query)
                        if arbitration_capture:
                            if len(query['dispute_id'])!=1:raise AuthError('重采任务无效')
                            data=disputes.capture_context(uid,query['dispute_id'][0]);case=data['case']
                            attrs=f'id="capture-form" data-dispute-id="{case["id"]}" data-revision="{case["based_revision"]}"'
                            content=content.replace('id="capture-form"',attrs).replace('<h2>采集POI</h2>','<h2>第三方重采与核验</h2>')
                            choices=''.join(f'<option value="{k}">{v}</option>' for k,v in DECISIONS.items())
                            content=content.replace('<button id="upload"','<p><label>核验结论 <select name="decision">'+choices+'</select></label></p><p><label>核验说明 <textarea name="reason" required maxlength="1000"></textarea></label></p><button id="upload"')
                        elif query:
                            if set(query)!={'poi_id','issue_id','revision'} or any(len(v)!=1 for v in query.values()): raise AuthError('重采请求无效')
                            detail=review.detail(uid,query['poi_id'][0]);row=detail['poi']
                            issue=next((x for x in detail['issues'] if x['id']==query['issue_id'][0] and x['status']=='open'),None)
                            if row['owner']!=uid or row['status']!='needs_action' or str(row['revision'])!=query['revision'][0] or not issue: raise AuthError('重采请求已失效或无权操作')
                            attrs=f'id="capture-form" data-poi-id="{row["id"]}" data-issue-id="{issue["id"]}" data-revision="{row["revision"]}"'
                            content=content.replace('id="capture-form"',attrs).replace('<h2>采集POI</h2>','<h2>重新采集POI</h2><p>错误说明：'+html.escape(issue['description'])+'</p>')
                            content=content.replace('<button id="upload"','<p><label>处理说明 <textarea name="reason" required maxlength="1000"></textarea></label></p><button id="upload"')
                        self.reply(200,self.page(content))
                    elif path == '/my-pois':
                        content=review_pages.listing(review,uid)
                        self.reply(200,self.page(content or '<p>暂无采集</p>'))
                    elif path == '/api/pois':
                        self.reply(200,json.dumps(collection.list_owned(uid),ensure_ascii=False),content_type='application/json; charset=utf-8')
                    elif path.endswith('/photo') and len(path.split('/'))==5:
                        query=parse_qs(urlsplit(self.path).query)
                        revision=None
                        if query:
                            if set(query)!={'revision'} or len(query['revision'])!=1 or len(query['revision'][0])>10 or not query['revision'][0].isascii() or not query['revision'][0].isdigit(): raise AuthError('照片版本无效')
                            revision=int(query['revision'][0])
                        self.reply(200,collection.photo(uid,path.split('/')[3],revision),content_type='image/jpeg')
                    else:self.reply(404)
                except AuthError as exc:
                    self.reply(403,json.dumps({'error':str(exc)},ensure_ascii=False),content_type='application/json; charset=utf-8')
                except OSError:
                    self.reply(500,self.page('<p>照片暂时无法读取</p>'))
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
                self.reply_error(403,'请求来源无效')
                return
            path = urlsplit(self.path).path
            try:
                length = int(self.headers.get('Content-Length', '0'))
            except ValueError:
                length = -1
            local = path in ('/auth/local/login', '/auth/local/register')
            response_json=path.startswith('/api/review/') and path.endswith('/respond') and len(path.split('/'))==5
            finalize_json=path.startswith('/api/disputes/') and path.endswith('/finalize') and len(path.split('/'))==5
            collaboration_form=path in ('/chats/open','/disputes/file') or (path.startswith('/chat/') and path.endswith('/send') and len(path.split('/'))==4)
            workflow=path in ('/review/report','/review/respond')
            upload = path == '/api/pois' or response_json or finalize_json
            if self.headers.get('Transfer-Encoding') or not 0 <= length <= (MAX_BODY if upload else 16384 if workflow or collaboration_form else 4096) or (not local and not upload and not workflow and not collaboration_form and length):
                self.close_connection = True
                self.reply_error(400,'请求内容长度或格式无效')
                return
            session = self.cookies().get('cc_session', '')
            if collaboration_form or finalize_json:
                uid=store.user(session)
                if not uid:self.reply_error(401,'请先登录');self.close_connection=True;return
                try:
                    raw=self.rfile.read(length).decode('utf-8')
                    if finalize_json:
                        if self.headers.get('Content-Type','').split(';')[0]!='application/json':raise AuthError('请求格式须为JSON')
                        case_id=path.split('/')[3]
                        revision=disputes.finalize(uid,case_id,json.loads(raw))
                        self.reply(200,json.dumps({'id':case_id,'revision':revision}),content_type='application/json; charset=utf-8')
                    else:
                        if self.headers.get('Content-Type','').split(';')[0]!='application/x-www-form-urlencoded':raise AuthError('表单格式无效')
                        parsed=parse_qs(raw,max_num_fields=5)
                        if any(len(v)!=1 for v in parsed.values()):raise AuthError('表单字段重复')
                        value={k:v[0] for k,v in parsed.items()}
                        if path=='/chats/open':
                            room_id=chat.open_private(uid,value.get('issue_id'));target='/chat/'+room_id
                        elif path=='/disputes/file':
                            case_id=disputes.file(uid,value.get('issue_id'),value.get('reason'));target='/dispute/'+case_id
                        else:
                            room_id=path.split('/')[2]
                            chat.send(uid,room_id,value.get('text'),value.get('request_id'));target='/chat/'+room_id
                        self.reply(303,location=target)
                except (AuthError,ValueError) as exc:self.reply_error(400,str(exc) if isinstance(exc,AuthError) else '处理内容无效')
                except OSError:self.reply_error(500,'保存失败，请稍后重试')
            elif path.startswith('/notifications/') and path.endswith('/read') and len(path.split('/'))==4:
                uid=store.user(session)
                if not uid:
                    self.reply_error(401,'请先登录');return
                try:
                    notifications.mark_read(uid,path.split('/')[2])
                    self.reply(303,location='/notifications')
                except AuthError as exc:self.reply_error(403,str(exc))
            elif workflow or response_json:
                uid=store.user(session)
                if not uid:
                    self.reply_error(401,'请先登录');self.close_connection=True;return
                try:
                    raw=self.rfile.read(length).decode('utf-8')
                    if response_json:
                        if self.headers.get('Content-Type','').split(';')[0]!='application/json': raise AuthError('请求格式须为JSON')
                        value=json.loads(raw)
                        poi_id=path.split('/')[3]
                        version=review.respond(uid,poi_id,value)
                        self.reply(200,json.dumps({'id':poi_id,'revision':version}),content_type='application/json; charset=utf-8')
                    else:
                        if self.headers.get('Content-Type','').split(';')[0]!='application/x-www-form-urlencoded': raise AuthError('表单格式无效')
                        parsed=parse_qs(raw,max_num_fields=10)
                        if any(len(v)!=1 for v in parsed.values()): raise AuthError('表单字段重复')
                        value={k:v[0] for k,v in parsed.items()}
                        value['revision']=int(value.get('revision',''))
                        if path=='/review/report': review.report(uid,value.get('poi_id'),value['revision'],value.get('description'))
                        else: review.respond(uid,value.get('poi_id'),value)
                        self.reply(303,location='/review' if path=='/review/report' else '/my-pois')
                except (AuthError,ValueError) as exc:
                    self.reply_error(400,str(exc) if isinstance(exc,AuthError) else '处理内容无效')
                except OSError:
                    self.reply_error(500,'保存失败，请稍后重试')
            elif path == '/profile/collector' or upload:
                uid=store.user(session)
                if not uid:
                    self.reply(401,json.dumps({'error':'请先登录'},ensure_ascii=False),content_type='application/json; charset=utf-8');self.close_connection=True;return
                try:
                    if upload:
                        if self.headers.get('Content-Type','').split(';')[0]!='application/json': raise AuthError('上传格式须为JSON')
                        value=json.loads(self.rfile.read(length).decode('utf-8'))
                        poi_id=collection.create(uid,value)
                        with store.connect() as db:
                            status=db.execute('SELECT status FROM pois WHERE id=? AND owner=?',(poi_id,uid)).fetchone()['status']
                        self.reply(201,json.dumps({'id':poi_id,'status':status}),content_type='application/json; charset=utf-8')
                    else:
                        collection.enroll(uid);self.reply(303,location='/collect')
                except (AuthError,ValueError) as exc:
                    self.reply(400,json.dumps({'error':str(exc) if isinstance(exc,AuthError) else '上传内容无效'},ensure_ascii=False),content_type='application/json; charset=utf-8')
                except OSError:
                    self.reply(500,json.dumps({'error':'保存失败，请稍后重试'},ensure_ascii=False),content_type='application/json; charset=utf-8')
            elif local:
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

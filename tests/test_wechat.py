import http.client
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
from urllib.parse import urlsplit, parse_qs
from http.cookies import SimpleCookie
from http.server import ThreadingHTTPServer
from check_collect.auth import Settings, WeChat, Store, AuthError, digest
from check_collect.server import application


class WeChatTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.config = Settings('http://127.0.0.1:8765', 'test-app', 'TEST_ONLY_SECRET', str(Path(self.temp.name)/'auth.sqlite'))
        self.store = Store(self.config.database)

    def login(self, openid='test-openid'):
        state, browser = self.store.start('login')
        return self.store.complete(self.store.consume(state, browser), {'appid':'test-app','openid':openid})

    def test_authorization_url_and_configuration(self):
        u = urlsplit(WeChat(self.config).authorization_url('random-state'))
        q = parse_qs(u.query)
        self.assertEqual(u.hostname, 'open.weixin.qq.com')
        self.assertEqual(q['scope'], ['snsapi_login'])
        self.assertEqual(q['redirect_uri'], [self.config.callback])
        self.assertNotIn('TEST_ONLY_SECRET', u.geturl())
        with self.assertRaises(AuthError): WeChat(Settings(self.config.origin)).authorization_url('x')
        with self.assertRaises(ValueError): Settings('http://example.com')

    def test_adapter_exchanges_code_and_rejects_provider_errors(self):
        urls=[]
        provider=WeChat(self.config, lambda u: urls.append(u) or {'openid':'wx-id','access_token':'test-token'})
        self.assertEqual(provider.identify('temporary-code'), {'appid':'test-app','openid':'wx-id'})
        self.assertEqual(parse_qs(urlsplit(urls[0]).query)['grant_type'], ['authorization_code'])
        for response in [{'errcode':40029}, {}, {'openid':'x'}, {'openid':12,'access_token':'x'}]:
            with self.subTest(response=response):
                with self.assertRaises(AuthError): WeChat(self.config,lambda u:response).identify('x')

    def test_official_account_authorization_and_environment(self):
        config = Settings('https://192.168.137.1:8443', 'test-app', 'TEST_ONLY_SECRET',
                          self.config.database, 'official_account')
        url = urlsplit(WeChat(config).authorization_url('browser-state'))
        query = parse_qs(url.query)
        self.assertEqual(url.path, '/connect/oauth2/authorize')
        self.assertEqual(query['scope'], ['snsapi_base'])
        self.assertEqual(query['redirect_uri'], [config.callback])
        self.assertEqual(query['state'], ['browser-state'])
        self.assertEqual(url.fragment, 'wechat_redirect')
        self.assertNotIn('TEST_ONLY_SECRET', url.geturl())
        self.assertEqual(config.wechat_login_label, '微信授权登录')
        with patch.dict('os.environ', {'WECHAT_LOGIN_MODE':'official_account'}, clear=True):
            self.assertEqual(Settings.from_env().wechat_mode, 'official_account')
        with self.assertRaises(ValueError):
            Settings(self.config.origin, wechat_mode='unknown')

    def test_state_bound_to_browser_and_single_use(self):
        s,b=self.store.start('login')
        with self.assertRaises(AuthError): self.store.consume(s,'different-browser')
        self.store.consume(s,b)
        with self.assertRaises(AuthError): self.store.consume(s,b)

    def test_expired_state_and_session(self):
        s,b=self.store.start('login')
        with self.store.connect() as db: db.execute('UPDATE states SET expires=0')
        with self.assertRaises(AuthError):self.store.consume(s,b)
        uid,session=self.login()
        with self.store.connect() as db:db.execute('UPDATE sessions SET expires=0')
        self.assertIsNone(self.store.user(session))

    def test_repeat_login_persistent_account_and_logout(self):
        uid,token=self.login()
        uid2,token2=self.login()
        self.assertEqual(uid,uid2)
        self.assertNotEqual(token,token2)
        self.assertEqual(Store(self.config.database).user(token),uid)
        with self.store.connect() as db:
            self.assertNotEqual(db.execute('SELECT token_hash FROM sessions LIMIT 1').fetchone()[0],token)
        self.store.logout(token)
        self.assertIsNone(self.store.user(token))
        self.assertEqual(self.store.user(token2),uid)

    def test_binding_requires_current_session_and_prevents_account_takeover(self):
        with self.assertRaises(AuthError):self.store.start('bind')
        uid,token=self.login('first')
        s,b=self.store.start('bind',token)
        with self.assertRaises(AuthError):self.store.consume(s,b,'other-session')
        a=self.store.consume(s,b,token)
        _,new=self.store.complete(a,{'appid':'test-app','openid':'first'},token)
        self.assertIsNone(self.store.user(token))
        self.assertEqual(self.store.user(new),uid)
        uid2,token2=self.login('second')
        s,b=self.store.start('bind',token2)
        a=self.store.consume(s,b,token2)
        with self.assertRaises(AuthError):self.store.complete(a,{'appid':'test-app','openid':'first'},token2)
        s,b=self.store.start('bind',new)
        a=self.store.consume(s,b,new)
        with self.assertRaises(AuthError):self.store.complete(a,{'appid':'test-app','openid':'new-account'},new)

    def test_binding_revoked_during_provider_call(self):
        uid,token=self.login()
        s,b=self.store.start('bind',token)
        a=self.store.consume(s,b,token)
        self.store.logout(token)
        with self.assertRaises(AuthError):self.store.complete(a,{'appid':'test-app','openid':'test-openid'},token)

    def test_register_bind_and_later_wechat_login_share_account(self):
        uid,token=self.store.local_account('collector_test','test-password-123',True)
        self.assertFalse(self.store.bound(uid))
        with self.assertRaises(AuthError):self.store.local_account('collector_test','test-password-123',True)
        with self.assertRaises(AuthError):self.store.local_account('collector_test','wrong-password-123')
        self.assertEqual(self.store.local_account('collector_test','test-password-123')[0],uid)
        s,b=self.store.start('bind',token)
        a=self.store.consume(s,b,token)
        self.store.complete(a,{'appid':'test-app','openid':'bound-wechat'},token)
        self.assertTrue(self.store.bound(uid))
        self.assertEqual(self.login('bound-wechat')[0],uid)
        with self.store.connect() as db:
            stored=db.execute('SELECT password_hash FROM credentials').fetchone()[0]
        self.assertNotIn('test-password-123',stored)

    def test_http_round_trip_with_injected_wechat_transport(self):
        # This is a protocol simulation, not evidence of real WeChat authorization.
        provider=WeChat(self.config,lambda u:{'openid':'http-user','access_token':'test-only'})
        server=ThreadingHTTPServer(('127.0.0.1',0),application(self.config,provider))
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        self.addCleanup(server.server_close);self.addCleanup(server.shutdown)
        jar={}
        def request(method,path,origin=True):
            c=http.client.HTTPConnection('127.0.0.1',server.server_port,timeout=3)
            headers={'Cookie':'; '.join(k+'='+v for k,v in jar.items())}
            if origin:headers['Origin']=self.config.origin
            c.request(method,path,headers=headers)
            r=c.getresponse();data=r.read().decode();headers=r.getheaders();c.close()
            for k,v in headers:
                if k.lower()=='set-cookie':
                    cookie=SimpleCookie(v)
                    jar.update({key:m.value for key,m in cookie.items()})
            return r.status,dict(headers),data
        status,headers,page=request('GET','/')
        self.assertIn('微信扫码登录',page)
        policy=headers['Content-Security-Policy']
        self.assertIn("form-action 'self' https://open.weixin.qq.com;",policy)
        self.assertIn("script-src 'self';",policy)
        self.assertEqual(request('POST','/auth/wechat/login',False)[0],403)
        status,h,_=request('POST','/auth/wechat/login')
        self.assertEqual(status,303)
        state=parse_qs(urlsplit(h['Location']).query)['state'][0]
        callback='/auth/wechat/callback?state='+state+'&code=test-code'
        status,h,_=request('GET',callback)
        self.assertEqual(status,303)
        self.assertIn('HttpOnly',h['Set-Cookie'])
        self.assertIn('已登录',request('GET','/')[2])
        self.assertEqual(request('GET',callback)[0],400)
        self.assertEqual(request('POST','/auth/wechat/bind')[0],303)
        self.assertEqual(request('POST','/auth/logout')[0],303)
        self.assertNotIn('已登录',request('GET','/')[2])

    def test_unconfigured_http_does_not_offer_fake_login(self):
        s=ThreadingHTTPServer(('127.0.0.1',0),application(Settings(self.config.origin,database=self.config.database)))
        threading.Thread(target=s.serve_forever,daemon=True).start()
        try:
            c=http.client.HTTPConnection('127.0.0.1',s.server_port,timeout=3);c.request('GET','/')
            r=c.getresponse();self.assertIn('微信登录待配置',r.read().decode());c.close()
        finally:s.shutdown();s.server_close()


if __name__=='__main__':unittest.main()

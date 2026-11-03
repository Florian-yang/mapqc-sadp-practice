import http.client
from http.cookies import SimpleCookie
from http.server import ThreadingHTTPServer
from pathlib import Path
import tempfile
import threading
import unittest
from urllib.parse import urlencode, urlsplit, parse_qs
from check_collect.auth import Settings, WeChat
from check_collect.server import application


class LocalAccountHttpTest(unittest.TestCase):
    def test_register_then_bind_then_wechat_sign_in(self):
        with tempfile.TemporaryDirectory() as temp:
            config=Settings('https://course.example','test-app','TEST_ONLY_SECRET',str(Path(temp)/'auth.db'))
            provider=WeChat(config,lambda url:{'openid':'test-wechat','access_token':'test-only'})
            server=ThreadingHTTPServer(('127.0.0.1',0),application(config,provider))
            threading.Thread(target=server.serve_forever,daemon=True).start()
            jar={}
            def request(path,fields=None,get=False,origin=None):
                body=urlencode(fields) if fields else None
                headers={'Origin':origin or config.origin,'Cookie':'; '.join(k+'='+v for k,v in jar.items())}
                if body is not None:headers['Content-Type']='application/x-www-form-urlencoded'
                c=http.client.HTTPConnection('127.0.0.1',server.server_port,timeout=3)
                c.request('GET' if get else 'POST',path,body,headers)
                r=c.getresponse();text=r.read().decode();h=r.getheaders();c.close()
                for k,v in h:
                    if k.lower()=='set-cookie':
                        jar.update({key:m.value for key,m in SimpleCookie(v).items()})
                return r.status,dict(h),text
            try:
                fields={'username':'demo_collector','password':'test-password-123'}
                self.assertEqual(request('/auth/local/register',fields,origin='https://foreign.example')[0],403)
                status,h,_=request('/auth/local/register',fields)
                self.assertEqual(status,303)
                self.assertIn('; Secure',h['Set-Cookie'])
                before=request('/',get=True)[2]
                self.assertIn('未绑定',before)
                status,h,_=request('/auth/wechat/bind')
                state=parse_qs(urlsplit(h['Location']).query)['state'][0]
                self.assertEqual(request('/auth/wechat/callback?state='+state+'&code=test',get=True)[0],303)
                after=request('/',get=True)[2]
                self.assertIn('已绑定',after)
                self.assertNotIn('test-wechat',after)
                self.assertEqual(request('/auth/logout')[0],303)
                _,h,_=request('/auth/wechat/login')
                state=parse_qs(urlsplit(h['Location']).query)['state'][0]
                self.assertEqual(request('/auth/wechat/callback?state='+state+'&code=test-again',get=True)[0],303)
                self.assertEqual(request('/',get=True)[2],after)
                self.assertEqual(request('/auth/logout')[0],303)
                fields['password']='incorrect-password'
                self.assertEqual(request('/auth/local/login',fields)[0],400)
                fields['password']='test-password-123'
                self.assertEqual(request('/auth/local/login',fields)[0],303)
            finally:
                server.shutdown();server.server_close()

import hashlib
import http.client
from http.server import ThreadingHTTPServer
import os
import plistlib
from pathlib import Path
import ssl
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from check_collect.hotspot import create_certificate,certificate_handler
from check_collect.auth import Settings
from check_collect.server import application


class HotspotTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);self.tls=self.root/'tls'
        self.fingerprint=create_certificate(self.tls,'127.0.0.1')

    def test_certificate_and_key_stay_private_and_no_overwrite(self):
        self.assertFalse((self.tls/'ca-key.pem').exists())
        self.assertEqual(self.fingerprint,hashlib.sha256((self.tls/'ca.cer').read_bytes()).hexdigest())
        before=(self.tls/'server-key.pem').read_bytes()
        with self.assertRaises(ValueError):create_certificate(self.tls,'127.0.0.1')
        self.assertEqual(before,(self.tls/'server-key.pem').read_bytes())
        with self.assertRaises(ValueError):create_certificate(self.root/'bad','8.8.8.8')

    def test_bootstrap_serves_only_public_certificate(self):
        server=ThreadingHTTPServer(('127.0.0.1',0),certificate_handler(self.tls/'ca.cer','https://127.0.0.1:8443'))
        threading.Thread(target=server.serve_forever,daemon=True).start()
        self.addCleanup(server.server_close);self.addCleanup(server.shutdown)
        for path,status in (('/',200),('/ca.cer',200),('/demo.mobileconfig',200),('/server-key.pem',404),('/../server-key.pem',404)):
            c=http.client.HTTPConnection('127.0.0.1',server.server_port)
            c.request('GET',path);r=c.getresponse();body=r.read();c.close()
            self.assertEqual(r.status,status)
            self.assertNotIn(b'PRIVATE KEY',body)
            if path=='/demo.mobileconfig':
                profile=plistlib.loads(body)
                self.assertEqual(profile['PayloadContent'][0]['PayloadContent'],(self.tls/'ca.cer').read_bytes())

    def test_real_https_login_requires_trusted_ca_and_secure_session(self):
        server=ThreadingHTTPServer(('127.0.0.1',0),application(Settings('https://127.0.0.1:8443',database=str(self.root/'demo.sqlite3'))))
        ctx=ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER);ctx.load_cert_chain(self.tls/'server.pem',self.tls/'server-key.pem')
        server.socket=ctx.wrap_socket(server.socket,server_side=True)
        threading.Thread(target=server.serve_forever,daemon=True).start()
        self.addCleanup(server.server_close);self.addCleanup(server.shutdown)
        untrusted=http.client.HTTPSConnection('127.0.0.1',server.server_port,timeout=3)
        with self.assertRaises(ssl.SSLCertVerificationError):untrusted.request('GET','/')
        untrusted.close()
        trusted=ssl.create_default_context(cafile=str(self.tls/'ca.pem'))
        c=http.client.HTTPSConnection('127.0.0.1',server.server_port,context=trusted)
        body='username=phone_demo&password=synthetic-password-123'
        c.request('POST','/auth/local/register',body,{'Content-Type':'application/x-www-form-urlencoded','Origin':'https://127.0.0.1:8443'})
        r=c.getresponse();r.read()
        self.assertEqual(r.status,303);self.assertIn('Secure',r.getheader('Set-Cookie'));c.close()

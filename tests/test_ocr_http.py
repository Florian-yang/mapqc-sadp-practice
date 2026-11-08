import base64
import http.client
import io
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
from http.server import ThreadingHTTPServer
from PIL import Image
from check_collect.auth import Store, Settings
from check_collect.collection import Collection
from check_collect.ocr import OcrSpace
from check_collect.server import application


class OcrHttpTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.store=Store(str(Path(self.temp.name)/'ocr.sqlite'));self.collection=Collection(self.store)
        self.uid,self.token=self.store.local_account('owner','synthetic-password-123',True)
        self.other,self.other_token=self.store.local_account('other','synthetic-password-123',True)
        self.collection.enroll(self.uid);self.collection.enroll(self.other)
        image=io.BytesIO();Image.new('RGB',(30,30),'white').save(image,'JPEG');now=time.time()*1000
        self.poi=self.collection.create(self.uid,dict(name='合成照片',description='OCR HTTP测试',category='other',capture_id='synthetic-ocr-http-001',latitude=30.,longitude=114.,accuracy=10.,captured_ms=now,located_ms=now,photo_base64=base64.b64encode(image.getvalue()).decode()))
        self.requests=[]
        def transport(request):
            self.requests.append(request)
            return {'IsErroredOnProcessing':False,'OCRExitCode':1,'ParsedResults':[{'FileParseExitCode':1,'ParsedText':'MAPQC SHOP <42>'}]}
        self.settings=Settings('http://127.0.0.1:8765',database=self.store.path)
        self.server=ThreadingHTTPServer(('127.0.0.1',0),application(self.settings,ocr_provider=OcrSpace('synthetic-test-key',transport)))
        threading.Thread(target=self.server.serve_forever,daemon=True).start()
        self.addCleanup(self.server.server_close);self.addCleanup(self.server.shutdown)

    def request(self,value=None,token=None,origin=None):
        headers={'Origin':origin or self.settings.origin,'Content-Type':'application/json'}
        if token:headers['Cookie']='cc_session='+token
        c=http.client.HTTPConnection('127.0.0.1',self.server.server_port,timeout=3)
        c.request('POST',f'/api/pois/{self.poi}/ocr',json.dumps(value),headers)
        response=c.getresponse();data=response.read();status=response.status;c.close()
        return status,json.loads(data)

    def test_real_http_result_cache_and_no_automatic_category_change(self):
        value={'language':'eng','expected_revision':1,'confirmed_external':True}
        status,result=self.request(value,self.token)
        self.assertEqual(status,200);self.assertEqual(result['text'],'MAPQC SHOP <42>')
        self.assertEqual(result['suggested_category'],'shop')
        self.assertEqual(self.request(value,self.token)[1],result);self.assertEqual(len(self.requests),1)
        self.assertEqual(self.collection.list_owned(self.uid)[0]['category'],'other')

    def test_confirmation_and_revision_are_required_before_external_call(self):
        for value in ({},{'confirmed_external':False,'expected_revision':1},{'confirmed_external':True},
                      {'confirmed_external':True,'expected_revision':True},{'confirmed_external':True,'expected_revision':2},
                      {'confirmed_external':True,'expected_revision':1,'image_url':'https://invalid.example'}):
            with self.subTest(value=value):self.assertEqual(self.request(value,self.token)[0],400)
        self.assertEqual(self.requests,[])

    def test_session_origin_and_other_account_cannot_recognize(self):
        value={'expected_revision':1,'confirmed_external':True}
        self.assertEqual(self.request(value)[0],401)
        self.assertEqual(self.request(value,self.token,'https://foreign.example')[0],403)
        self.assertEqual(self.request(value,self.other_token)[0],400)
        self.assertEqual(self.requests,[])

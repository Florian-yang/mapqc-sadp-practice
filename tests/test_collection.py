import base64
import copy
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
from check_collect.auth import Store,Settings,AuthError
from check_collect.collection import Collection
from check_collect.server import application


class CollectionTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.store=Store(str(Path(self.temp.name)/'capture.sqlite'))
        self.service=Collection(self.store)
        self.uid,self.token=self.store.local_account('collector','test-password-123',True)
        self.other,self.other_token=self.store.local_account('other_user','test-password-123',True)
        photo=io.BytesIO();Image.new('RGB',(32,24),(30,120,90)).save(photo,'JPEG')
        now=time.time()*1000
        # Synthetic fixture coordinates and image; no real device/location claim.
        self.value=dict(name='测试POI',description='仅用于自动测试',category='shop',capture_id='synthetic-capture-0001',latitude=30.0,longitude=114.0,accuracy=10.0,captured_ms=now,located_ms=now,photo_base64=base64.b64encode(photo.getvalue()).decode())

    def test_collector_opt_in_and_no_privileged_role(self):
        with self.assertRaises(AuthError):self.service.create(self.uid,self.value)
        self.service.enroll(self.uid);self.service.enroll(self.uid)
        with self.store.connect() as db:
            self.assertEqual([r[0] for r in db.execute('SELECT role FROM user_roles WHERE user_id=?',(self.uid,))],['collector'])

    def test_round_trip_image_metadata_and_restart(self):
        self.service.enroll(self.uid)
        poi=self.service.create(self.uid,self.value)
        service=Collection(Store(self.store.path))
        self.assertEqual(service.get_owned(self.uid,poi),{'id':poi,'status':'pending'})
        row=service.list_owned(self.uid)[0]
        self.assertEqual((row['id'],row['latitude'],row['longitude'],row['status']),(poi,30.0,114.0,'pending'))
        self.assertEqual((row['captured_ms'],row['located_ms']),(self.value['captured_ms'],self.value['located_ms']))
        with Image.open(io.BytesIO(service.photo(self.uid,poi))) as im:self.assertEqual(im.size,(32,24))

    def test_idempotent_retry_and_capture_replacement_rejected(self):
        self.service.enroll(self.uid)
        poi=self.service.create(self.uid,self.value)
        self.assertEqual(self.service.create(self.uid,self.value),poi)
        changed={**self.value,'latitude':31.0}
        with self.assertRaises(AuthError):self.service.create(self.uid,changed)
        self.assertEqual(len(self.service.list_owned(self.uid)),1)

    def test_other_user_cannot_read_photo_or_list_record(self):
        self.service.enroll(self.uid);self.service.enroll(self.other)
        poi=self.service.create(self.uid,self.value)
        self.assertEqual(self.service.list_owned(self.other),[])
        with self.assertRaises(AuthError):self.service.photo(self.other,poi)
        with self.assertRaises(AuthError):self.service.get_owned(self.other,poi)
        with self.assertRaises(AuthError):self.service.get_owned(self.uid,'f'*32)
        with self.assertRaises(AuthError):self.service.photo(self.uid,'../../other')

    def test_position_and_timestamps_required_and_finite(self):
        self.service.enroll(self.uid)
        for key,value in [('latitude',None),('longitude',181),('latitude',float('nan')),('accuracy',False),('accuracy',0),('captured_ms',0),('located_ms',self.value['captured_ms']-21000)]:
            with self.subTest(key=key,value=value):
                with self.assertRaises(AuthError):self.service.create(self.uid,{**self.value,key:value})
        self.assertEqual(list(self.service.photos.iterdir()),[])

    def test_invalid_photo_text_and_category_rejected(self):
        self.service.enroll(self.uid)
        for key,value in [('photo_base64','!not-base64'),('photo_base64',base64.b64encode(b'not-image').decode()),('category','admin'),('description',' '),('name',''),('capture_id','../../image')]:
            with self.subTest(key=key):
                with self.assertRaises(AuthError):self.service.create(self.uid,{**self.value,key:value})
        self.assertEqual(list(self.service.photos.iterdir()),[])

    def test_http_authenticated_upload_and_access_controls(self):
        config=Settings('http://127.0.0.1:8765',database=self.store.path)
        server=ThreadingHTTPServer(('127.0.0.1',0),application(config))
        threading.Thread(target=server.serve_forever,daemon=True).start()
        self.addCleanup(server.server_close);self.addCleanup(server.shutdown)
        def req(method,path,value=None,token=None,origin=None):
            headers={'Origin':origin or config.origin}
            if token:headers['Cookie']='cc_session='+token
            body=json.dumps(value) if value is not None else None
            if body:headers['Content-Type']='application/json'
            c=http.client.HTTPConnection('127.0.0.1',server.server_port,timeout=3);c.request(method,path,body,headers)
            r=c.getresponse();data=r.read();c.close();return r.status,data
        self.assertEqual(req('POST','/api/pois',self.value)[0],401)
        self.assertEqual(req('POST','/api/pois',self.value,self.token,'https://foreign.example')[0],403)
        self.assertEqual(req('POST','/profile/collector',token=self.token)[0],303)
        status,data=req('POST','/api/pois',self.value,self.token)
        self.assertEqual(status,201);poi=json.loads(data)['id']
        self.assertEqual(req('GET','/api/pois/'+poi+'/photo',token=self.token)[0],200)
        self.assertEqual(req('GET','/api/pois/'+poi+'/photo',token=self.other_token)[0],403)
        rows=json.loads(req('GET','/api/pois',token=self.token)[1]);self.assertEqual(rows[0]['id'],poi)
        status,data=req('GET','/collect',token=self.token)
        self.assertEqual(status,200);self.assertIn(b'/static/capture.js',data)
        self.assertNotIn(b'name="latitude"',data)
        self.assertEqual(req('GET','/static/capture.js')[0],200)
        self.store.logout(self.token)
        self.assertEqual(req('POST','/api/pois',self.value,self.token)[0],401)

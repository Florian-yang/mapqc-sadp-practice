import base64
import http.client
import io
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from http.server import ThreadingHTTPServer
from unittest.mock import patch
from urllib.parse import urlencode
from PIL import Image
from check_collect.auth import Store, Settings, AuthError
from check_collect.collection import Collection
from check_collect.review import Review
from check_collect.roles import grant_reviewer
from check_collect.server import application


class ReviewTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.store=Store(str(Path(self.temp.name)/'review.sqlite'))
        self.collection=Collection(self.store);self.review=Review(self.collection)
        self.owner,self.token=self.store.local_account('collector','test-password-123',True)
        self.reviewer,self.review_token=self.store.local_account('reviewer','test-password-123',True)
        self.other,self.other_token=self.store.local_account('stranger','test-password-123',True)
        self.collection.enroll(self.owner);self.collection.enroll(self.other)
        grant_reviewer(self.store,self.reviewer)
        self.capture=self.capture_value('initial-synthetic-0001')
        self.poi=self.collection.create(self.owner,self.capture)
        # These tests isolate review events; initial task broadcasts are tested separately.
        with self.store.connect() as db: db.execute('DELETE FROM event_outbox')

    def capture_value(self,capture_id,color=(20,120,40)):
        image=io.BytesIO();Image.new('RGB',(24,32),color).save(image,'JPEG')
        now=time.time()*1000
        return dict(name='合成测试商铺',category='shop',description='自动测试，不是真实POI',
                    capture_id=capture_id,latitude=30.,longitude=114.,accuracy=12.,
                    captured_ms=now,located_ms=now,photo_base64=base64.b64encode(image.getvalue()).decode())

    def report(self):
        return self.review.report(self.reviewer,self.poi,1,'招牌照片不清楚')

    def response(self,issue,action='update',**extra):
        return dict(issue_id=issue,revision=1,action=action,reason='已复核并处理',
                    name='合成测试商铺',category='shop',description='改正后的描述',**extra)

    def counts(self):
        with self.store.connect() as db:
            return tuple(db.execute('SELECT count(*) FROM '+t).fetchone()[0] for t in ('issues','poi_versions','event_outbox'))

    def test_privileged_role_is_local_only_and_self_review_is_forbidden(self):
        self.collection.enroll(self.reviewer)
        own=self.collection.create(self.reviewer,self.capture_value('reviewer-capture-0001'))
        self.assertNotIn(own,[x['id'] for x in self.review.queue(self.reviewer)])
        for uid in (self.owner,self.other,None):
            with self.assertRaises(AuthError):self.review.report(uid,self.poi,1,'测试错误')
        with self.assertRaises(AuthError):self.review.report(self.reviewer,own,1,'本人数据')
        with self.assertRaises(AuthError):grant_reviewer(self.store,'not-a-user')

    def test_error_and_outbox_are_atomic_and_duplicate_report_is_refused(self):
        issue=self.report()
        detail=self.review.detail(self.owner,self.poi)
        self.assertEqual(detail['poi']['status'],'needs_action')
        self.assertEqual(detail['issues'][0]['description'],'招牌照片不清楚')
        with self.assertRaises(AuthError):self.report()
        self.assertEqual(self.counts(),(1,1,1))
        with self.store.connect() as db:
            event=dict(db.execute('SELECT * FROM event_outbox').fetchone())
        self.assertEqual(event['recipient'],self.owner)
        self.assertIsNone(event['delivered_ms'])
        self.assertEqual(json.loads(event['payload'])['issue_id'],issue)

    def test_outbox_failure_rolls_back_error_and_status(self):
        with patch.object(self.review,'event',side_effect=RuntimeError('injected write failure')):
            with self.assertRaises(RuntimeError):self.report()
        self.assertEqual(self.counts(),(0,1,0))
        self.assertEqual(self.review.detail(self.owner,self.poi)['poi']['status'],'pending')

    def test_update_keeps_photo_and_location_with_immutable_version(self):
        photo=self.collection.photo(self.owner,self.poi)
        issue=self.report()
        self.assertEqual(self.review.respond(self.owner,self.poi,self.response(issue)),2)
        details=Review(Collection(Store(self.store.path))).detail(self.reviewer,self.poi)
        self.assertEqual(details['poi']['status'],'pending')
        self.assertEqual(details['versions'][0]['description'],self.capture['description'])
        self.assertEqual(details['versions'][1]['description'],'改正后的描述')
        self.assertEqual(details['versions'][1]['latitude'],30.)
        self.assertEqual(self.collection.photo(self.reviewer,self.poi,1),photo)
        self.assertEqual(self.collection.photo(self.reviewer,self.poi,2),photo)
        with self.assertRaises(AuthError):self.review.respond(self.owner,self.poi,self.response(issue))
        self.assertEqual(self.counts(),(1,2,2))

    def test_recollection_preserves_old_photo_and_rejects_reused_capture(self):
        issue=self.report();old=self.collection.photo(self.owner,self.poi)
        payload={**self.response(issue,'recollect'),**self.capture_value('new-synthetic-capture-0002',(180,20,60))}
        self.assertEqual(self.review.respond(self.owner,self.poi,payload),2)
        self.assertNotEqual(self.collection.photo(self.owner,self.poi),old)
        self.assertEqual(self.collection.photo(self.owner,self.poi,1),old)
        second=self.review.report(self.reviewer,self.poi,2,'需要再确认')
        reused={**payload,'issue_id':second,'revision':2}
        with self.assertRaises(AuthError):self.review.respond(self.owner,self.poi,reused)
        with self.assertRaises(AuthError):self.collection.create(self.owner,self.capture)
        self.assertEqual(self.counts(),(2,2,3))

    def test_reject_requires_reason_and_does_not_claim_final_resolution(self):
        issue=self.report()
        with self.assertRaises(AuthError):self.review.respond(self.owner,self.poi,{**self.response(issue,'reject'),'reason':' '})
        self.review.respond(self.owner,self.poi,self.response(issue,'reject'))
        detail=self.review.detail(self.owner,self.poi)
        self.assertEqual(detail['poi']['status'],'disputed')
        self.assertEqual(detail['issues'][0]['status'],'reject')
        self.assertEqual(len(detail['versions']),1)
        with self.assertRaises(AuthError):self.report()

    def test_ownership_stale_version_and_invalid_inputs_do_not_mutate(self):
        issue=self.report()
        for uid,payload in [(self.other,self.response(issue)),(self.reviewer,self.response(issue)),
                            (self.owner,{**self.response(issue),'revision':2}),
                            (self.owner,{**self.response(issue),'latitude':33}),
                            (self.owner,{**self.response(issue),'issue_id':[]}),
                            (self.owner,{**self.response(issue),'revision':True}),
                            (self.owner,{**self.response(issue),'revision':2**100}),
                            (self.owner,{**self.response(issue),'category':'invalid'})]:
            with self.subTest(uid=uid,payload=payload):
                with self.assertRaises(AuthError):self.review.respond(uid,self.poi,payload)
        with self.assertRaises(AuthError):self.review.detail(self.other,self.poi)
        with self.assertRaises(AuthError):self.collection.photo(self.other,self.poi,1)
        self.assertEqual(self.counts(),(1,1,1))

    def test_failed_recollection_validation_or_commit_keeps_old_version(self):
        issue=self.report();old=self.collection.photo(self.owner,self.poi)
        payload={**self.response(issue,'recollect'),**self.capture_value('new-synthetic-capture-0002')}
        for extra in ({'latitude':None},{'located_ms':0},{'photo_base64':'not-image'}):
            with self.assertRaises(AuthError):self.review.respond(self.owner,self.poi,{**payload,**extra})
        with patch.object(self.review,'event',side_effect=RuntimeError('injected outbox failure')):
            with self.assertRaises(RuntimeError):self.review.respond(self.owner,self.poi,payload)
        self.assertEqual(self.counts(),(1,1,1))
        self.assertEqual(len(list(self.collection.photos.iterdir())),1)
        self.assertEqual(self.collection.photo(self.owner,self.poi),old)

    def test_conflicting_file_is_preserved(self):
        issue=self.report()
        path=self.collection.photos/f'{self.poi}-v2.jpg';path.write_bytes(b'unknown-existing-file')
        payload={**self.response(issue,'recollect'),**self.capture_value('new-synthetic-capture-0002')}
        with self.assertRaises(FileExistsError):self.review.respond(self.owner,self.poi,payload)
        self.assertEqual(path.read_bytes(),b'unknown-existing-file')
        self.assertEqual(self.counts(),(1,1,1))

    def test_concurrent_review_creates_one_issue_and_one_event(self):
        def attempt(_):
            try:self.report();return True
            except AuthError:return False
        with ThreadPoolExecutor(max_workers=2) as pool:
            self.assertEqual(sorted(pool.map(attempt,range(2))),[False,True])
        self.assertEqual(self.counts(),(1,1,1))

    def test_v05_database_is_migrated_without_losing_capture(self):
        with self.store.connect() as db:
            db.execute('DROP TABLE poi_versions')
            db.execute('ALTER TABLE pois DROP COLUMN revision')
        collection=Collection(self.store)
        detail=Review(collection).detail(self.owner,self.poi)
        self.assertEqual(detail['versions'][0]['revision'],1)
        self.assertEqual(detail['versions'][0]['photo_sha256'],detail['poi']['photo_sha256'])
        self.assertTrue(collection.photo(self.owner,self.poi))

    def test_http_review_update_recollect_reject_and_access(self):
        config=Settings('http://127.0.0.1:8765',database=self.store.path)
        server=ThreadingHTTPServer(('127.0.0.1',0),application(config))
        threading.Thread(target=server.serve_forever,daemon=True).start()
        self.addCleanup(server.server_close);self.addCleanup(server.shutdown)
        def request(method,path,value=None,token=None,json_body=False,origin=None):
            headers={'Origin':origin or config.origin}
            if token:headers['Cookie']='cc_session='+token
            body=None
            if value is not None:
                body=json.dumps(value) if json_body else urlencode(value)
                headers['Content-Type']='application/json' if json_body else 'application/x-www-form-urlencoded'
            connection=http.client.HTTPConnection('127.0.0.1',server.server_port,timeout=3)
            connection.request(method,path,body,headers);r=connection.getresponse();data=r.read();connection.close()
            return r.status,data.decode('utf-8') if not path.split('?')[0].endswith('/photo') else data
        report={'poi_id':self.poi,'revision':1,'description':'<script>synthetic error</script>'}
        self.assertEqual(request('GET','/review')[0],401)
        status,data=request('POST',f'/api/review/{self.poi}/respond',{},json_body=True)
        self.assertEqual(status,401);self.assertEqual(json.loads(data)['error'],'请先登录')
        self.assertEqual(request('GET','/review',token=self.token)[0],403)
        self.assertEqual(request('GET','/review',token=self.review_token)[0],200)
        self.assertEqual(request('POST','/review/report',report,self.review_token,origin='https://foreign.example')[0],403)
        self.assertEqual(request('POST','/review/report',report,self.review_token)[0],303)
        issue=self.review.detail(self.owner,self.poi)['issues'][0]['id']
        page=request('GET','/my-pois',token=self.token)[1]
        self.assertIn('&lt;script&gt;',page);self.assertNotIn('<script>synthetic',page)
        self.assertIn('重新拍照采集',page)
        update={**self.response(issue),'poi_id':self.poi}
        self.assertEqual(request('POST','/review/respond',update,self.token)[0],303)
        self.assertEqual(request('POST','/review/report',{**report,'revision':2},self.review_token)[0],303)
        issue=self.review.detail(self.owner,self.poi)['issues'][-1]['id']
        query=urlencode({'poi_id':self.poi,'issue_id':issue,'revision':2})
        status,page=request('GET','/collect?'+query,token=self.token)
        self.assertEqual(status,200);self.assertIn('data-revision="2"',page)
        self.assertEqual(request('GET','/collect?'+query,token=self.other_token)[0],403)
        capture={**self.response(issue,'recollect'),**self.capture_value('http-recapture-synthetic-002'),'revision':2}
        self.assertEqual(request('POST',f'/api/review/{self.poi}/respond',capture,self.token,True)[0],200)
        status,data=request('POST',f'/api/review/{self.poi}/respond',capture,self.token,True)
        self.assertEqual(status,400);self.assertIn('记录已变化',json.loads(data)['error'])
        self.assertEqual(request('GET',f'/api/pois/{self.poi}/photo?revision=1',token=self.review_token)[0],200)
        for invalid in ('9223372036854775808','2147483648','0','9'*5000,'%C2%B2'):
            status,data=request('GET',f'/api/pois/{self.poi}/photo?revision={invalid}',token=self.review_token)
            self.assertEqual(status,403);self.assertEqual(json.loads(data)['error'],'照片版本无效')
        self.assertEqual(request('GET',f'/api/pois/{self.poi}/photo?revision=3',token=self.other_token)[0],403)
        self.assertEqual(request('POST','/review/report',{**report,'revision':3},self.review_token)[0],303)
        issue=self.review.detail(self.owner,self.poi)['issues'][-1]['id']
        reject={**self.response(issue,'reject'),'revision':3,'poi_id':self.poi}
        self.assertEqual(request('POST','/review/respond',reject,self.token)[0],303)
        self.assertIn('采集者已驳回',request('GET','/poi/'+self.poi,token=self.review_token)[1])
        self.assertEqual(self.counts(),(3,3,6))

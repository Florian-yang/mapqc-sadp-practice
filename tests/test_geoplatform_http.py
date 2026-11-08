"""Real loopback HTTP with synthetic POIs and an injected OSRM protocol transport."""
import base64
import http.client
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import io
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
from urllib.parse import parse_qs, urlsplit

from PIL import Image

from check_collect.auth import Settings, Store
from check_collect.collection import Collection
from check_collect.disputes import Disputes
from check_collect.maps import OsrmRouter
from check_collect.review import Review
from check_collect.roles import grant_role
from check_collect.server import application


class Response:
    def __init__(self, status, headers, body):
        self.status, self.headers, self.body = status, headers, body

    @property
    def text(self):
        return self.body.decode('utf-8')

    def json(self):
        return json.loads(self.body)


class ProtocolTransport:
    """Exercise the actual OSRM adapter without contacting any outside service."""
    def __init__(self):
        self.urls = []
        self.no_route = False

    def __call__(self, url):
        self.urls.append(url)
        if self.no_route:
            return {'code': 'NoRoute'}
        start, end = [[float(v) for v in point.split(',')] for point in urlsplit(url).path.rsplit('/', 1)[1].split(';')]
        return {'code': 'Ok', 'waypoints': [{'location': start}, {'location': end}],
                'routes': [{'distance': 250.4, 'duration': 45.2,
                            'geometry': {'type': 'LineString', 'coordinates': [start, [(start[0]+end[0])/2, (start[1]+end[1])/2], end]},
                            'legs': [{'steps': [
                                {'name': '合成协议道路', 'distance': 250.4,
                                 'maneuver': {'type': 'depart', 'modifier': 'straight', 'location': start}},
                                {'name': '合成协议道路', 'distance': 0,
                                 'maneuver': {'type': 'arrive', 'location': end}}
                            ]}]}]}


class GeoplatformHTTPTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = Store(str(Path(self.temp.name) / 'map-http.sqlite'))
        self.collection = Collection(self.store)
        self.review = Review(self.collection)
        self.disputes = Disputes(self.collection)
        self.tokens = {}
        for name in ('owner', 'other', 'reviewer', 'third_one', 'third_two', 'unroled'):
            uid, token = self.store.local_account('maps_' + name, 'synthetic-password-123', True)
            setattr(self, name, uid)
            self.tokens[uid] = token
        for uid in (self.owner, self.other):
            self.collection.enroll(uid)
        grant_role(self.store, self.reviewer, 'reviewer')
        for uid in (self.third_one, self.third_two):
            grant_role(self.store, uid, 'third_party')
        self.assignee, self.unassigned = sorted((self.third_one, self.third_two))
        self.serial = 0
        self.poi = self.collection.create(self.owner, self.capture('shop'))
        self.error_poi = self.collection.create(self.owner, self.capture('bus_stop'))
        self.private_poi = self.collection.create(self.other, self.capture('scenic'))
        self.issue = self.review.report(self.reviewer, self.error_poi, 1, '合成测试：分类需复核')
        self.transport = ProtocolTransport()
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), BaseHTTPRequestHandler)
        self.settings = Settings(f'http://127.0.0.1:{self.server.server_port}', database=self.store.path)
        self.server.RequestHandlerClass = application(self.settings, router=OsrmRouter(self.transport))
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.stop_server)

    def stop_server(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=3)

    def capture(self, category):
        self.serial += 1
        image = io.BytesIO()
        Image.new('RGB', (24, 24), (20, 120, 80)).save(image, 'JPEG')
        now = time.time() * 1000
        return {'name': '<b>合成POI</b>', 'category': category, 'description': '合成坐标和图片，不是真机证据',
                'capture_id': f'map-http-synthetic-{self.serial:04d}', 'latitude': 30.001, 'longitude': 114.002,
                'accuracy': 10, 'captured_ms': now, 'located_ms': now,
                'photo_base64': base64.b64encode(image.getvalue()).decode()}

    def request(self, method, path, value=None, uid=None, origin=True, raw=None, content_type='application/json'):
        headers = {}
        if uid:
            headers['Cookie'] = 'cc_session=' + self.tokens[uid]
        if origin is True:
            headers['Origin'] = self.settings.origin
        elif origin is not None:
            headers['Origin'] = origin
        body = raw
        if value is not None:
            body = json.dumps(value, ensure_ascii=False).encode('utf-8')
        if body is not None:
            headers['Content-Type'] = content_type
        connection = http.client.HTTPConnection('127.0.0.1', self.server.server_port, timeout=5)
        try:
            connection.request(method, path, body, headers)
            response = connection.getresponse()
            return Response(response.status, dict(response.getheaders()), response.read())
        finally:
            connection.close()

    def route(self, uid=None, **values):
        return self.request('POST', '/api/maps/route', {'poi_id': self.poi, 'latitude': 30.0, 'longitude': 114.0, **values}, uid or self.owner)

    def assert_api_error(self, response, statuses=(400,)):
        self.assertIn(response.status, statuses, response.text)
        self.assertTrue(response.headers['Content-Type'].startswith('application/json'))
        data = response.json()
        self.assertIsInstance(data.get('error'), str)
        self.assertTrue(data['error'])
        for key in ('geometry', 'steps', 'routes'):
            self.assertNotIn(key, data)

    def test_map_page_local_assets_attribution_and_tile_security_headers(self):
        login = self.request('GET', '/')
        self.assertEqual(login.headers['Referrer-Policy'], 'same-origin')
        page = self.request('GET', '/map', uid=self.owner)
        self.assertEqual(page.status, 200, page.text)
        self.assertIn('/static/vendor/leaflet.js', page.text)
        self.assertIn('/static/vendor/leaflet.css', page.text)
        self.assertIn('/static/map.js', page.text)
        self.assertIn('OpenStreetMap', page.text)
        self.assertEqual(page.headers['Referrer-Policy'], 'strict-origin-when-cross-origin')
        directives = {parts[0]: parts[1:] for item in page.headers['Content-Security-Policy'].split(';') if (parts := item.split())}
        self.assertEqual(directives['script-src'], ["'self'"])
        self.assertIn('https://tile.openstreetmap.org', directives['img-src'])
        self.assertNotIn(self.private_poi, page.text)
        for path, expected_type in (('/static/vendor/leaflet.js', 'javascript'), ('/static/vendor/leaflet.css', 'text/css'),
                                    ('/static/map.js', 'javascript')):
            response = self.request('GET', path)
            self.assertEqual(response.status, 200, path)
            self.assertIn(expected_type, response.headers['Content-Type'])
            self.assertTrue(response.body)

    def test_geojson_filter_error_progress_and_existing_pois_api_remain_distinct(self):
        response = self.request('GET', '/api/map', uid=self.owner)
        self.assertEqual(response.status, 200, response.text)
        data = response.json()
        self.assertEqual(data['type'], 'FeatureCollection')
        self.assertEqual({feature['id'] for feature in data['features']}, {self.poi, self.error_poi})
        marked = next(feature for feature in data['features'] if feature['id'] == self.error_poi)
        self.assertEqual(marked['geometry'], {'type': 'Point', 'coordinates': [114.002, 30.001]})
        self.assertEqual((marked['properties']['status'], marked['properties']['has_error']), ('needs_action', True))
        self.assertEqual(set(marked['properties']), {'id', 'name', 'category', 'description', 'status', 'revision', 'has_error'})
        self.assertNotIn('<b>', json.dumps(data))
        filtered = self.request('GET', '/api/map?category=bus_stop&status=needs_action', uid=self.owner)
        self.assertEqual(filtered.status, 200)
        self.assertEqual([feature['id'] for feature in filtered.json()['features']], [self.error_poi])
        empty = self.request('GET', '/api/map?category=shop&status=needs_action', uid=self.owner)
        self.assertEqual(empty.status, 200)
        self.assertEqual(empty.json()['features'], [])
        owned = self.request('GET', '/api/pois', uid=self.owner)
        self.assertEqual(owned.status, 200)
        self.assertIsInstance(owned.json(), list)
        self.assertEqual({row['id'] for row in owned.json()}, {self.poi, self.error_poi})
        self.assertEqual(self.transport.urls, [])

    def test_duplicate_empty_invalid_and_unknown_filters_are_explicit_errors(self):
        for query in ('category=shop&category=house', 'category=shop&category=shop', 'status=pending&status=pending',
                      'category=', 'status=', 'category=SHOP', 'category=unknown', 'status=done', 'status=%20pending',
                      'category=shop&private=all'):
            with self.subTest(query=query):
                self.assert_api_error(self.request('GET', '/api/map?' + query, uid=self.owner), (400, 403))
        self.assertEqual(self.transport.urls, [])

    def test_login_and_current_work_role_are_required(self):
        for path in ('/map', '/api/map'):
            response = self.request('GET', path)
            self.assertEqual(response.status, 401, response.text)
            if path.startswith('/api/'):
                self.assert_api_error(response, (401,))
            self.assertEqual(self.request('GET', path, uid=self.unroled).status, 403)
        anonymous = self.request('POST', '/api/maps/route', {'poi_id': self.poi, 'latitude': 30, 'longitude': 114})
        self.assert_api_error(anonymous, (401,))
        self.assert_api_error(self.route(self.unroled), (400, 403))
        with self.store.connect() as db:
            db.execute('DELETE FROM user_roles WHERE user_id=?', (self.owner,))
        self.assertEqual(self.request('GET', '/api/map', uid=self.owner).status, 403)
        self.assert_api_error(self.route(self.owner), (400, 403))
        self.assertEqual(self.transport.urls, [])

    def test_owner_reviewer_and_assigned_third_party_have_current_record_visibility(self):
        reviewer = self.request('GET', '/api/map', uid=self.reviewer)
        self.assertEqual({feature['id'] for feature in reviewer.json()['features']}, {self.poi, self.error_poi, self.private_poi})
        self.assertEqual(self.request('GET', '/api/map', uid=self.third_one).json()['features'], [])
        self.review.respond(self.owner, self.error_poi, {'revision': 1, 'issue_id': self.issue, 'action': 'reject', 'reason': '合成测试申请第三方'})
        self.disputes.file(self.owner, self.issue, '合成测试独立核验')
        assigned = self.request('GET', '/api/map', uid=self.assignee)
        self.assertEqual(assigned.status, 200)
        self.assertEqual([feature['id'] for feature in assigned.json()['features']], [self.error_poi])
        self.assertEqual(self.request('GET', '/api/map', uid=self.unassigned).json()['features'], [])
        self.assertEqual(self.route(self.assignee, poi_id=self.error_poi).status, 200)
        self.assert_api_error(self.route(self.unassigned, poi_id=self.error_poi), (400, 403))
        with self.store.connect() as db:
            db.execute('DELETE FROM user_roles WHERE user_id=?', (self.assignee,))
        self.assertEqual(self.request('GET', '/api/map', uid=self.assignee).status, 403)
        self.assert_api_error(self.route(self.assignee, poi_id=self.error_poi), (400, 403))
        self.assertEqual(len(self.transport.urls), 1)

    def test_route_has_authorized_destination_real_adapter_geometry_and_steps(self):
        with self.store.connect() as db:
            before = dict(db.execute('SELECT * FROM pois WHERE id=?', (self.poi,)).fetchone())
            versions = db.execute('SELECT COUNT(*) FROM poi_versions').fetchone()[0]
        response = self.route()
        self.assertEqual(response.status, 200, response.text)
        data = response.json()
        self.assertEqual(data['destination']['id'], self.poi)
        self.assertEqual(data['destination']['coordinates'], [114.002, 30.001])
        self.assertEqual(data['coordinate_system'], 'WGS84')
        self.assertEqual(data['geometry']['type'], 'LineString')
        self.assertEqual(len(data['geometry']['coordinates']), 3)
        self.assertEqual(data['geometry']['coordinates'][0], [114.0, 30.0])
        self.assertEqual(data['geometry']['coordinates'][-1], [114.002, 30.001])
        self.assertEqual((data['distance'], data['duration']), (250.4, 45.2))
        self.assertEqual([step['type'] for step in data['steps']], ['depart', 'arrive'])
        navigation = urlsplit(data['navigation_url'])
        self.assertEqual((navigation.scheme, navigation.netloc, navigation.path), ('https', 'www.openstreetmap.org', '/directions'))
        self.assertEqual(parse_qs(navigation.query)['engine'], ['fossgis_osrm_car'])
        self.assertEqual(urlsplit(self.transport.urls[0]).netloc, 'router.project-osrm.org')
        with self.store.connect() as db:
            self.assertEqual(dict(db.execute('SELECT * FROM pois WHERE id=?', (self.poi,)).fetchone()), before)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM poi_versions').fetchone()[0], versions)

    def test_destination_permissions_and_invalid_coordinates_prevent_provider_calls(self):
        for uid, poi in ((self.other, self.poi), (self.third_one, self.poi), (self.owner, self.private_poi),
                         (self.owner, 'a' * 32), (self.owner, '../../private')):
            with self.subTest(uid=uid, poi=poi):
                self.assert_api_error(self.route(uid, poi_id=poi), (400, 403))
        for latitude, longitude in ((True, 114), (30, False), (float('nan'), 114), (30, float('inf')),
                                    ('30', 114), (None, 114), (-91, 114), (30, 181)):
            with self.subTest(latitude=latitude, longitude=longitude):
                self.assert_api_error(self.route(latitude=latitude, longitude=longitude), (400,))
        self.assertEqual(self.transport.urls, [])

    def test_route_origin_content_type_json_and_provider_failure_are_explicit_errors(self):
        value = {'poi_id': self.poi, 'latitude': 30, 'longitude': 114}
        for origin in (None, 'https://foreign.example', self.settings.origin + '/', self.settings.origin + '.example'):
            with self.subTest(origin=origin):
                self.assert_api_error(self.request('POST', '/api/maps/route', value, self.owner, origin=origin), (403,))
        for raw, content_type in ((b'{', 'application/json'), (b'[]', 'application/json'), (b'{}', 'text/plain')):
            with self.subTest(raw=raw, content_type=content_type):
                self.assert_api_error(self.request('POST', '/api/maps/route', uid=self.owner, raw=raw, content_type=content_type), (400,))
        self.assertEqual(self.transport.urls, [])
        self.transport.no_route = True
        response = self.route()
        self.assert_api_error(response, (400, 502, 503))
        self.assertEqual(len(self.transport.urls), 1)


if __name__ == '__main__':
    unittest.main()

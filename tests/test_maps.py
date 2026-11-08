import base64
import copy
from email.message import Message
import http.client
import io
import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import Mock, patch
from urllib.parse import parse_qs, urlsplit

from PIL import Image

from check_collect.auth import AuthError, Store
from check_collect.collection import Collection
from check_collect.disputes import Disputes
from check_collect.maps import Maps, OsrmRouter, MAX_RESPONSE_BYTES, ROUTE_TIMEOUT, fetch_json
from check_collect.review import Review, STATUS


def route_fixture():
    # Synthetic protocol fixture, not evidence of a real provider/device request.
    return {'code': 'Ok', 'waypoints': [{'location': [114.0, 30.0]}, {'location': [114.002, 30.001]}],
            'routes': [{'distance': 250.4, 'duration': 45.2,
                        'geometry': {'type': 'LineString', 'coordinates': [[114.0, 30.0], [114.001, 30.0], [114.002, 30.001]]},
                        'legs': [{'steps': [
                            {'name': '<b>测试道路</b>', 'distance': 250.4, 'duration': 45.2,
                             'maneuver': {'type': 'depart', 'modifier': 'straight', 'location': [114.0, 30.0]},
                             'voiceInstructions': [{'announcement': '<script>not accepted</script>'}]},
                            {'name': '测试道路', 'distance': 0, 'duration': 0,
                             'maneuver': {'type': 'arrive', 'location': [114.002, 30.001]}}
                        ]}]}]}


class RecordingRouter:
    def __init__(self):
        self.calls = []

    def route(self, *args):
        self.calls.append(args)
        return {'provider': 'injected-test', 'geometry': {'type': 'LineString', 'coordinates': [[args[1], args[0]], [args[3], args[2]]]},
                'distance': 250, 'duration': 45, 'steps': []}


class MapTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = Store(str(Path(self.temp.name) / 'map.sqlite'))
        self.collection = Collection(self.store)
        self.review = Review(self.collection)
        self.disputes = Disputes(self.collection)
        self.owner = self.account('map_owner', 'collector')
        self.other = self.account('map_other', 'collector')
        self.reviewer = self.account('map_reviewer', 'reviewer')
        self.third = self.account('map_third', 'third_party')
        self.unroled = self.account('map_unroled')
        self.router = RecordingRouter()
        self.maps = Maps(self.collection, self.router)
        image = io.BytesIO()
        Image.new('RGB', (16, 16), (30, 120, 90)).save(image, 'JPEG')
        now = time.time() * 1000
        self.value = {'name': '合成测试商铺', 'description': '地图权限与路线测试', 'category': 'shop',
                      'capture_id': 'synthetic-map-capture-0001', 'latitude': 30.001, 'longitude': 114.002,
                      'accuracy': 10, 'captured_ms': now, 'located_ms': now,
                      'photo_base64': base64.b64encode(image.getvalue()).decode()}
        self.poi = self.collection.create(self.owner, self.value)

    def account(self, username, role=None):
        uid, _ = self.store.local_account(username, 'test-password-123', True)
        if role:
            with self.store.connect() as db:
                db.execute('INSERT INTO user_roles VALUES(?,?)', (uid, role))
        return uid

    def feature(self, uid=None):
        return self.maps.features(uid or self.owner)['features'][0]

    def reject_and_assign(self):
        issue = self.review.report(self.reviewer, self.poi, 1, '测试错误说明')
        self.review.respond(self.owner, self.poi, {'revision': 1, 'issue_id': issue, 'action': 'reject', 'reason': '提交第三方核验'})
        return self.disputes.file(self.owner, issue, '申请独立处理')

    def test_geojson_has_wgs84_current_coordinates_and_only_public_fields(self):
        feature = self.feature()
        self.assertEqual(feature['geometry'], {'type': 'Point', 'coordinates': [114.002, 30.001]})
        self.assertEqual(feature['id'], self.poi)
        self.assertEqual(set(feature['properties']), {'id', 'name', 'category', 'description', 'status', 'revision', 'has_error'})
        self.assertEqual(feature['properties']['status'], 'pending')
        self.assertFalse(feature['properties']['has_error'])
        self.assertNotIn('owner', json.dumps(feature))
        self.assertNotIn('photo_sha256', json.dumps(feature))

    def test_layer_is_owner_reviewer_or_assigned_third_party_only(self):
        other_poi = self.collection.create(self.other, {**self.value, 'capture_id': 'synthetic-map-capture-0002'})
        self.assertEqual([x['id'] for x in self.maps.features(self.owner)['features']], [self.poi])
        self.assertEqual({x['id'] for x in self.maps.features(self.reviewer)['features']}, {self.poi, other_poi})
        self.assertEqual(self.maps.features(self.third)['features'], [])
        self.reject_and_assign()
        self.assertEqual([x['id'] for x in self.maps.features(self.third)['features']], [self.poi])
        with self.store.connect() as db:
            db.execute('DELETE FROM user_roles WHERE user_id=?', (self.third,))
        with self.assertRaises(AuthError):
            self.maps.features(self.third)
        with self.assertRaises(AuthError):
            self.maps.route(self.third, self.poi, 30, 114)

    def test_unroled_missing_and_revoked_roles_are_denied(self):
        for uid in (self.unroled, None, '', {}, 'not-a-user'):
            with self.subTest(uid=uid), self.assertRaises(AuthError):
                self.maps.features(uid)
        with self.store.connect() as db:
            db.execute('DELETE FROM user_roles WHERE user_id=?', (self.owner,))
        with self.assertRaises(AuthError):
            self.maps.features(self.owner)
        with self.assertRaises(AuthError):
            self.maps.route(self.owner, self.poi, 30, 114)
        self.assertEqual(self.router.calls, [])

    def test_category_and_status_filters_use_exact_enums(self):
        self.assertEqual(len(self.maps.features(self.owner, 'shop', 'pending')['features']), 1)
        self.assertEqual(self.maps.features(self.owner, 'house')['features'], [])
        for status in STATUS:
            with self.store.connect() as db:
                db.execute('UPDATE pois SET status=? WHERE id=?', (status, self.poi))
            self.assertEqual(self.maps.features(self.owner, status=status)['features'][0]['properties']['status'], status)
        for key, invalid in [('category', ''), ('category', '商铺'), ('category', 'SHOP'), ('category', []),
                             ('status', ''), ('status', 'complete'), ('status', ' pending'), ('status', {}), ('status', True)]:
            with self.subTest(key=key, invalid=invalid), self.assertRaises(AuthError):
                self.maps.features(self.owner, **{key: invalid})

    def test_error_indicator_follows_latest_issue_and_resolution(self):
        issue = self.review.report(self.reviewer, self.poi, 1, '招牌分类错误')
        self.assertTrue(self.feature()['properties']['has_error'])
        self.review.respond(self.owner, self.poi, {'revision': 1, 'issue_id': issue, 'action': 'update', 'reason': '修正分类',
                                                  'name': '更新后名称', 'category': 'house', 'description': '更新后的描述'})
        current = self.feature()['properties']
        self.assertEqual((current['revision'], current['category'], current['status'], current['has_error']), (2, 'house', 'pending', False))
        issue2 = self.review.report(self.reviewer, self.poi, 2, '仍需复核')
        self.assertTrue(self.feature()['properties']['has_error'])
        self.review.respond(self.owner, self.poi, {'revision': 2, 'issue_id': issue2, 'action': 'reject', 'reason': '申请核验'})
        self.assertTrue(self.feature()['properties']['has_error'])

    def test_third_party_final_recapture_uses_current_destination_and_clears_error(self):
        case = self.reject_and_assign()
        self.assertTrue(self.feature()['properties']['has_error'])
        self.disputes.finalize(self.third, case, {**self.value, 'revision': 1, 'capture_id': 'synthetic-final-capture-0001',
                                               'latitude': 30.002, 'longitude': 114.003, 'decision': 'error_confirmed', 'reason': '已重新采集'})
        feature = self.feature(self.third)
        self.assertEqual(feature['geometry']['coordinates'], [114.003, 30.002])
        self.assertEqual((feature['properties']['revision'], feature['properties']['status'], feature['properties']['has_error']), (2, 'finalized', False))
        result = self.maps.route(self.third, self.poi, 30, 114)
        self.assertEqual(self.router.calls, [(30, 114, 30.002, 114.003)])
        self.assertEqual(result['destination']['coordinates'], [114.003, 30.002])

    def test_destination_authorization_precedes_provider_calls(self):
        for uid, poi in ((self.other, self.poi), (self.third, self.poi), (self.owner, 'a' * 32), (self.owner, '../../private')):
            with self.subTest(uid=uid, poi=poi), self.assertRaises(AuthError):
                self.maps.route(uid, poi, 30, 114)
        self.assertEqual(self.router.calls, [])
        result = self.maps.route(self.owner, self.poi, 30, 114)
        self.assertEqual(result['destination']['id'], self.poi)
        self.assertEqual(self.router.calls, [(30, 114, 30.001, 114.002)])
        navigation = urlsplit(result['navigation_url'])
        self.assertEqual((navigation.scheme, navigation.netloc, navigation.path), ('https', 'www.openstreetmap.org', '/directions'))
        self.assertEqual(parse_qs(navigation.query), {'engine': ['fossgis_osrm_car'],
                                                     'route': ['30.00000000,114.00000000;30.00100000,114.00200000']})

    def test_delayed_provider_result_is_rejected_after_role_version_or_coordinate_change(self):
        for sql, params in (
            ('UPDATE pois SET revision=revision+1 WHERE id=?', (self.poi,)),
            ('UPDATE pois SET latitude=30.002 WHERE id=?', (self.poi,)),
            ('DELETE FROM user_roles WHERE user_id=?', (self.owner,)),
        ):
            def changed_during_request(*args):
                with self.store.connect() as db:
                    db.execute(sql, params)
                return self.router.route(*args)
            self.maps.router = Mock()
            self.maps.router.route.side_effect = changed_during_request
            with self.subTest(sql=sql), self.assertRaises(AuthError):
                self.maps.route(self.owner, self.poi, 30, 114)
            with self.store.connect() as db:
                db.execute('UPDATE pois SET revision=1,latitude=30.001 WHERE id=?', (self.poi,))
                db.execute('INSERT OR IGNORE INTO user_roles VALUES(?,?)', (self.owner, 'collector'))

    def test_delayed_provider_result_rechecks_third_party_assignment(self):
        case = self.reject_and_assign()
        def reassigned_during_request(*args):
            with self.store.connect() as db:
                db.execute('UPDATE disputes SET assignee=? WHERE id=?', (self.other, case))
            return self.router.route(*args)
        self.maps.router = Mock()
        self.maps.router.route.side_effect = reassigned_during_request
        with self.assertRaises(AuthError):
            self.maps.route(self.third, self.poi, 30, 114)

    def test_delayed_provider_result_returns_current_progress_metadata(self):
        def reported_during_request(*args):
            self.review.report(self.reviewer, self.poi, 1, '路线请求期间新增的错误')
            return self.router.route(*args)
        self.maps.router = Mock()
        self.maps.router.route.side_effect = reported_during_request
        result = self.maps.route(self.owner, self.poi, 30, 114)
        self.assertEqual(result['destination']['status'], 'needs_action')
        self.assertTrue(result['destination']['has_error'])

    def test_route_rejects_non_numeric_and_nonfinite_origins_before_transport(self):
        for latitude, longitude in ((True, 114), (30, False), ('30', 114), (None, 114), (float('nan'), 114),
                                    (30, float('inf')), (-91, 114), (30, 181), (10 ** 400, 114)):
            with self.subTest(latitude=latitude, longitude=longitude), self.assertRaises(AuthError):
                self.maps.route(self.owner, self.poi, latitude, longitude)
        self.assertEqual(self.router.calls, [])

    def test_plain_text_layer_and_persistent_read_only_projection(self):
        with self.store.connect() as db:
            db.execute('UPDATE pois SET name=?,description=? WHERE id=?',
                       ('<b>测试商铺</b>', '&lt;script&gt;secret()&lt;/script&gt;普通描述', self.poi))
            before = dict(db.execute('SELECT * FROM pois WHERE id=?', (self.poi,)).fetchone())
        restarted = Maps(Collection(Store(self.store.path)), self.router)
        props = restarted.features(self.owner)['features'][0]['properties']
        self.assertEqual(props['name'], '测试商铺')
        self.assertEqual(props['description'], '普通描述')
        restarted.route(self.owner, self.poi, 30, 114)
        with self.store.connect() as db:
            self.assertEqual(dict(db.execute('SELECT * FROM pois WHERE id=?', (self.poi,)).fetchone()), before)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM poi_versions').fetchone()[0], 1)

    def test_layer_works_before_review_schema_exists(self):
        with self.store.connect() as db:
            db.execute('DROP TABLE disputes')
            db.execute('DROP TABLE issues')
        self.assertFalse(self.feature()['properties']['has_error'])


class OsrmTests(unittest.TestCase):
    def test_fixed_https_url_lon_lat_and_required_options(self):
        transport = Mock(return_value=route_fixture())
        result = OsrmRouter(transport).route(30, 114, 30.001, 114.002)
        url = urlsplit(transport.call_args.args[0])
        self.assertEqual((url.scheme, url.netloc), ('https', 'router.project-osrm.org'))
        self.assertEqual(url.path, '/route/v1/driving/114.00000000,30.00000000;114.00200000,30.00100000')
        query = parse_qs(url.query)
        self.assertEqual({key: query[key] for key in ('steps', 'overview', 'geometries')},
                         {'steps': ['true'], 'overview': ['full'], 'geometries': ['geojson']})
        self.assertEqual(result['geometry']['coordinates'][-1], [114.002, 30.001])
        self.assertEqual((result['distance'], result['duration'], result['coordinate_system']), (250.4, 45.2, 'WGS84'))
        self.assertEqual(result['steps'][0]['name'], '测试道路')
        self.assertEqual(set(result['steps'][0]), {'type', 'modifier', 'name', 'distance', 'location'})
        self.assertEqual([step['type'] for step in result['steps']], ['depart', 'arrive'])

    def test_invalid_input_does_not_contact_provider(self):
        transport = Mock()
        for values in ((True, 114, 30, 114), (30, 114, float('nan'), 114), (30, '114', 30, 114), (30, 114, 30, 181)):
            with self.subTest(values=values), self.assertRaises(AuthError):
                OsrmRouter(transport).route(*values)
        transport.assert_not_called()

    def test_provider_error_and_network_failure_never_make_fallback_routes(self):
        for data in ({'code': 'NoRoute'}, {'code': 'NoSegment'}, {'code': 'InvalidQuery'}, [], None, {'code': 'Ok', 'routes': []}):
            with self.subTest(data=data), self.assertRaises(AuthError):
                OsrmRouter(lambda url: data).route(30, 114, 30.001, 114.002)
        with self.assertRaises(AuthError):
            OsrmRouter(Mock(side_effect=TimeoutError())).route(30, 114, 30.001, 114.002)
        with self.assertRaises(AuthError):
            OsrmRouter(Mock(side_effect=http.client.IncompleteRead(b'partial JSON', 100))).route(30, 114, 30.001, 114.002)

    def test_malformed_geometry_numeric_fields_and_steps_are_rejected(self):
        mutations = [
            lambda d: d['routes'][0].update(geometry={'type': 'Point', 'coordinates': [114, 30]}),
            lambda d: d['routes'][0]['geometry'].update(coordinates=[]),
            lambda d: d['routes'][0]['geometry']['coordinates'][1].__setitem__(0, float('nan')),
            lambda d: d['routes'][0]['geometry']['coordinates'][1].__setitem__(0, True),
            lambda d: d['routes'][0]['geometry']['coordinates'][1].append(0),
            lambda d: d['routes'][0]['geometry']['coordinates'].reverse(),
            lambda d: d['routes'][0].update(distance=-1),
            lambda d: d['routes'][0].update(distance=True),
            lambda d: d['routes'][0].update(distance=10 ** 400),
            lambda d: d['routes'][0].update(duration=float('inf')),
            lambda d: d['routes'][0].update(duration='45'),
            lambda d: d['routes'][0].update(legs=[]),
            lambda d: d['routes'][0]['legs'][0].update(steps=[]),
            lambda d: d['routes'][0]['legs'][0]['steps'][0]['maneuver'].update(type='<script>'),
            lambda d: d['routes'][0]['legs'][0]['steps'][0]['maneuver'].update(type='turn'),
            lambda d: d['routes'][0]['legs'][0]['steps'][0].update(distance=float('nan')),
            lambda d: d['routes'][0]['legs'][0]['steps'][0].update(name=[]),
            lambda d: d['routes'][0]['legs'][0]['steps'][-1]['maneuver'].update(location=[115, 31]),
            lambda d: d.update(waypoints=[]),
            lambda d: d['waypoints'][1].update(location=[115, 31]),
        ]
        for index, mutate in enumerate(mutations):
            data = copy.deepcopy(route_fixture())
            mutate(data)
            with self.subTest(case=index), self.assertRaises(AuthError):
                OsrmRouter(lambda url: data).route(30, 114, 30.001, 114.002)


class Response:
    def __init__(self, raw, status=200, content_type='application/json'):
        self.raw, self.status = raw, status
        self.headers = Message()
        self.headers['Content-Type'] = content_type
        self.limit = None

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def getcode(self):
        return self.status

    def read(self, limit):
        self.limit = limit
        return self.raw[:limit]


class OsrmTransportTests(unittest.TestCase):
    url = 'https://router.project-osrm.org/route/v1/driving/114,30;114.002,30.001?steps=true'

    def test_user_agent_timeout_response_bound_and_redirect_handler(self):
        response = Response(json.dumps(route_fixture()).encode())
        opener = Mock()
        opener.open.return_value = response
        with patch('check_collect.maps.urllib.request.build_opener', return_value=opener) as build:
            self.assertEqual(fetch_json(self.url)['code'], 'Ok')
        request = opener.open.call_args.args[0]
        self.assertIn('CheckCollect/', request.get_header('User-agent'))
        self.assertEqual(opener.open.call_args.kwargs, {'timeout': ROUTE_TIMEOUT})
        self.assertEqual(response.limit, MAX_RESPONSE_BYTES + 1)
        handler = build.call_args.args[0]
        self.assertIsNone(handler.redirect_request(request, None, 302, '', {}, 'https://other.example/'))

    def test_wrong_hosts_plain_http_and_provider_protocol_failures_are_rejected(self):
        with patch('check_collect.maps.urllib.request.build_opener') as build:
            for url in ('http://router.project-osrm.org/route/v1/driving/1,2;3,4', 'https://evil.example/route/v1/driving/1,2;3,4',
                        'https://router.project-osrm.org@evil.example/route/v1/driving/1,2;3,4'):
                with self.subTest(url=url), self.assertRaises(AuthError):
                    fetch_json(url)
            build.assert_not_called()
        for response in (Response(b'{}', status=503), Response(b'{}', content_type='text/html'), Response(b'not-json'),
                         Response(b'[]'), Response(b'{"distance":NaN}'), Response(b'x' * (MAX_RESPONSE_BYTES + 1))):
            opener = Mock()
            opener.open.return_value = response
            with self.subTest(status=response.status, size=len(response.raw)), patch('check_collect.maps.urllib.request.build_opener', return_value=opener), self.assertRaises(AuthError):
                fetch_json(self.url)

    def test_truncated_chunked_response_and_malformed_status_are_domain_errors(self):
        response = Response(b'')
        response.read = Mock(side_effect=http.client.IncompleteRead(b'{"code":"Ok",', 200))
        opener = Mock()
        opener.open.return_value = response
        with patch('check_collect.maps.urllib.request.build_opener', return_value=opener), self.assertRaises(AuthError):
            fetch_json(self.url)
        response.read.assert_called_once_with(MAX_RESPONSE_BYTES + 1)
        opener.open.side_effect = http.client.BadStatusLine('not HTTP')
        with patch('check_collect.maps.urllib.request.build_opener', return_value=opener), self.assertRaises(AuthError):
            fetch_json(self.url)


if __name__ == '__main__':
    unittest.main()

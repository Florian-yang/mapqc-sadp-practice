"""Authorized WGS84 POI layers and an OSRM driving-route adapter."""
import html
from html.parser import HTMLParser
import http.client
import json
import math
import re
import urllib.parse
import urllib.request

from .auth import AuthError
from .collection import CATEGORIES
from .review import STATUS


OSRM_ORIGIN = 'https://router.project-osrm.org'
MAX_RESPONSE_BYTES = 2 * 1024 * 1024
ROUTE_TIMEOUT = 12
MAX_SNAP_METERS = 1000


class _Text(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []
        self.hidden = 0

    def handle_starttag(self, tag, attrs):
        if tag in ('script', 'style'):
            self.hidden += 1

    def handle_endtag(self, tag):
        if tag in ('script', 'style') and self.hidden:
            self.hidden -= 1

    def handle_data(self, data):
        if not self.hidden:
            self.parts.append(data)


def _text(value, limit):
    if not isinstance(value, str) or len(value) > limit:
        raise AuthError('地图文字数据无效')
    parser = _Text()
    parser.feed(html.unescape(value))
    # The result is plain data, including malformed input containing angle brackets.
    return ''.join(parser.parts).replace('<', '‹').replace('>', '›')


def _number(value, low, high):
    try:
        valid = type(value) in (int, float) and low <= value <= high and math.isfinite(value)
    except (OverflowError, TypeError):
        valid = False
    if not valid:
        raise AuthError('地图坐标或路线数值无效')
    return value


def _point(value):
    if not isinstance(value, list) or len(value) != 2:
        raise AuthError('路线坐标格式无效')
    return [_number(value[0], -180, 180), _number(value[1], -90, 90)]


def _meters(a, b):
    lon1, lat1, lon2, lat2 = map(math.radians, (*a, *b))
    value = math.sin((lat2-lat1)/2)**2 + math.cos(lat1)*math.cos(lat2)*math.sin((lon2-lon1)/2)**2
    return 6371008.8 * 2 * math.asin(math.sqrt(min(1, max(0, value))))


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def fetch_json(url):
    """Only the fixed OSRM HTTPS endpoint is contacted; never follow redirects."""
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme != 'https' or parsed.netloc != 'router.project-osrm.org' or not parsed.path.startswith('/route/v1/driving/'):
        raise AuthError('路线服务地址无效')
    request = urllib.request.Request(url, headers={
        'User-Agent': 'CheckCollect/0.9 (POI course practice)',
        'Accept': 'application/json',
    })
    try:
        opener = urllib.request.build_opener(_NoRedirect())
        with opener.open(request, timeout=ROUTE_TIMEOUT) as response:
            if response.getcode() != 200 or response.headers.get_content_type() != 'application/json':
                raise AuthError('路线服务响应格式无效')
            raw = response.read(MAX_RESPONSE_BYTES + 1)
        if len(raw) > MAX_RESPONSE_BYTES:
            raise AuthError('路线服务响应过大')
        data = json.loads(raw.decode('utf-8'), parse_constant=lambda value: (_ for _ in ()).throw(ValueError(value)))
        if not isinstance(data, dict):
            raise ValueError()
        return data
    except (OSError, ValueError, http.client.HTTPException):
        raise AuthError('路线服务连接或响应异常，请稍后重试') from None


class OsrmRouter:
    """Transport injection supplies protocol fixtures without external requests."""
    def __init__(self, transport=fetch_json):
        self.transport = transport

    def route(self, latitude, longitude, destination_latitude, destination_longitude):
        start = _point([longitude, latitude])
        end = _point([destination_longitude, destination_latitude])
        # Device and stored coordinates are WGS84. OSRM expects longitude first.
        coordinates = ';'.join(','.join(format(v, '.8f') for v in point) for point in (start, end))
        query = urllib.parse.urlencode({'steps': 'true', 'overview': 'full', 'geometries': 'geojson',
                                       'alternatives': 'false', 'radiuses': f'{MAX_SNAP_METERS};{MAX_SNAP_METERS}'})
        try:
            data = self.transport(OSRM_ORIGIN + '/route/v1/driving/' + coordinates + '?' + query)
            if not isinstance(data, dict) or data.get('code') != 'Ok':
                raise AuthError('路线服务未找到可用道路或路线')
            routes, waypoints = data.get('routes'), data.get('waypoints')
            if not isinstance(routes, list) or not routes or not isinstance(routes[0], dict):
                raise AuthError('路线服务响应缺少路线')
            if not isinstance(waypoints, list) or len(waypoints) != 2 or not all(isinstance(x, dict) for x in waypoints):
                raise AuthError('路线服务响应缺少起终点')
            snapped = [_point(x.get('location')) for x in waypoints]
            if any(_meters(given, actual) > MAX_SNAP_METERS for given, actual in zip((start, end), snapped)):
                raise AuthError('附近没有可导航道路，请核对定位')
            route = routes[0]
            geometry = route.get('geometry')
            if not isinstance(geometry, dict) or geometry.get('type') != 'LineString':
                raise AuthError('路线几何格式无效')
            points = geometry.get('coordinates')
            if not isinstance(points, list) or not 2 <= len(points) <= 100000:
                raise AuthError('路线几何坐标无效')
            points = [_point(point) for point in points]
            if _meters(points[0], snapped[0]) > 5 or _meters(points[-1], snapped[1]) > 5:
                raise AuthError('路线起终点与目的地不一致')
            legs = route.get('legs')
            if not isinstance(legs, list) or len(legs) != 1 or not isinstance(legs[0], dict):
                raise AuthError('路线分段无效')
            raw_steps = legs[0].get('steps')
            if not isinstance(raw_steps, list) or not 2 <= len(raw_steps) <= 10000:
                raise AuthError('路线缺少导航步骤')
            steps = []
            for step in raw_steps:
                if not isinstance(step, dict) or not isinstance(step.get('maneuver'), dict):
                    raise AuthError('导航步骤无效')
                maneuver = step['maneuver']
                kind, modifier = maneuver.get('type'), maneuver.get('modifier', '')
                if not isinstance(kind, str) or not re.fullmatch(r'[a-z ]{1,40}', kind):
                    raise AuthError('导航动作无效')
                if not isinstance(modifier, str) or (modifier and not re.fullmatch(r'[a-z ]{1,40}', modifier)):
                    raise AuthError('导航方向无效')
                steps.append({'type': kind, 'modifier': modifier, 'name': _text(step.get('name', ''), 1000),
                              'distance': _number(step.get('distance'), 0, math.inf),
                              'location': _point(maneuver.get('location'))})
            if steps[0]['type'] != 'depart' or steps[-1]['type'] != 'arrive':
                raise AuthError('路线缺少出发或到达步骤')
            if _meters(steps[0]['location'], snapped[0]) > 5 or _meters(steps[-1]['location'], snapped[1]) > 5:
                raise AuthError('导航步骤起终点无效')
            return {'provider': 'OSRM', 'profile': 'driving', 'coordinate_system': 'WGS84',
                    'geometry': {'type': 'LineString', 'coordinates': points},
                    'distance': _number(route.get('distance'), 0, math.inf),
                    'duration': _number(route.get('duration'), 0, math.inf), 'steps': steps}
        except AuthError:
            raise
        except (OSError, ValueError, TypeError, KeyError, OverflowError, http.client.HTTPException):
            raise AuthError('路线服务连接或响应异常，请稍后重试') from None


class Maps:
    def __init__(self, collection, router=None):
        self.collection = collection
        self.store = collection.store
        self.router = router if router is not None else OsrmRouter()

    def _require(self, uid):
        if not isinstance(uid, str) or not uid or not any(self.collection.has_role(uid, role) for role in ('collector', 'reviewer', 'third_party')):
            raise AuthError('请先取得工作角色再查看地图')

    @staticmethod
    def _properties(row):
        return {'id': row['id'], 'name': _text(row['name'], 120), 'category': row['category'],
                'description': _text(row['description'], 1000), 'status': row['status'],
                'revision': row['revision'], 'has_error': row['latest_issue_status'] in ('open', 'reject')}

    @staticmethod
    def _select(db):
        issues = db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='issues'").fetchone()
        latest = "(SELECT status FROM issues WHERE poi_id=p.id ORDER BY created_ms DESC,rowid DESC LIMIT 1)" if issues else 'NULL'
        return 'SELECT p.id,p.owner,p.name,p.category,p.description,p.latitude,p.longitude,p.status,p.revision,' + latest + ' AS latest_issue_status FROM pois p'

    def features(self, uid, category=None, status=None):
        self._require(uid)
        for value, allowed, label in ((category, CATEGORIES, '类别'), (status, STATUS, '进展')):
            if value is not None and (not isinstance(value, str) or value not in allowed):
                raise AuthError('地图' + label + '筛选无效')
        with self.store.connect() as db:
            clauses, params = [], []
            for column, value in (('category', category), ('status', status)):
                if value is not None:
                    clauses.append('p.' + column + '=?')
                    params.append(value)
            sql = self._select(db) + (' WHERE ' + ' AND '.join(clauses) if clauses else '') + ' ORDER BY p.created_ms DESC,p.id'
            features = []
            for row in db.execute(sql, params):
                if self.collection.can_read(db, uid, row['id'], row['owner']):
                    features.append({'type': 'Feature', 'id': row['id'],
                                     'geometry': {'type': 'Point', 'coordinates': _point([row['longitude'], row['latitude']])},
                                     'properties': self._properties(row)})
        return {'type': 'FeatureCollection', 'features': features}

    def route(self, uid, poi_id, latitude, longitude):
        self._require(uid)
        if not isinstance(poi_id, str) or not re.fullmatch(r'[a-f0-9]{32}', poi_id):
            raise AuthError('POI不存在或无权访问')
        with self.store.connect() as db:
            row = db.execute(self._select(db) + ' WHERE p.id=?', (poi_id,)).fetchone()
            if not row or not self.collection.can_read(db, uid, poi_id, row['owner']):
                raise AuthError('POI不存在或无权访问')
            end = _point([row['longitude'], row['latitude']])
        start = _point([longitude, latitude])
        result = self.router.route(start[1], start[0], end[1], end[0])
        # A provider can take several seconds; never return an old target after
        # recollection, reassignment or role revocation during that request.
        self._require(uid)
        with self.store.connect() as db:
            current = db.execute(self._select(db) + ' WHERE p.id=?', (poi_id,)).fetchone()
            if (not current or not self.collection.can_read(db, uid, poi_id, current['owner'])
                    or current['revision'] != row['revision']
                    or [current['longitude'], current['latitude']] != end):
                raise AuthError('POI或访问权限已变化，请刷新后重新导航')
            destination = {**self._properties(current), 'latitude': end[1], 'longitude': end[0], 'coordinates': end}
        navigation_url = 'https://www.openstreetmap.org/directions?' + urllib.parse.urlencode({
            'engine': 'fossgis_osrm_car',
            'route': f'{start[1]:.8f},{start[0]:.8f};{end[1]:.8f},{end[0]:.8f}',
        })
        return {**result, 'destination': destination, 'navigation_url': navigation_url}

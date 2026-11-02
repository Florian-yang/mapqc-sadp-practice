"""Format strategies returning a common document, independent of Qt."""
from __future__ import annotations
import json
import math
from pathlib import Path
from typing import Protocol
from .models import MapDocument, document_key


class LoadError(ValueError):
    """Readable input failure for the future controller."""


class MapLoader(Protocol):
    def can_load(self, path: Path) -> bool: ...
    def load(self, path: Path) -> MapDocument: ...


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding='utf-8-sig')
    except (OSError, UnicodeError) as exc:
        raise LoadError(f'无法读取 {path.name}：{exc}') from exc


def _position(value):
    if not isinstance(value, list) or len(value) < 2 or not all(type(x) in (int, float) and math.isfinite(x) for x in value):
        raise LoadError('坐标须包含至少两个有限数值')


def _line(value, minimum=2):
    if not isinstance(value, list) or len(value) < minimum:
        raise LoadError('坐标数量不足')
    for p in value:
        _position(p)


def _polygon(value):
    if not isinstance(value, list) or not value:
        raise LoadError('多边形缺少环')
    for ring in value:
        _line(ring, 4)
        if ring[0] != ring[-1]:
            raise LoadError('多边形环未闭合')


def _geometry(g):
    if g is None:  # GeoJSON permits unlocated features.
        return
    if not isinstance(g, dict):
        raise LoadError('geometry须为对象或null')
    kind, c = g.get('type'), g.get('coordinates')
    if kind == 'GeometryCollection':
        members = g.get('geometries')
        if not isinstance(members, list):
            raise LoadError('geometries须为数组')
        for member in members:
            if member is None:
                raise LoadError('几何集合成员不能为null')
            _geometry(member)
    elif kind == 'Point':
        _position(c)
    elif kind == 'LineString':
        _line(c)
    elif kind == 'Polygon':
        _polygon(c)
    elif kind in ('MultiPoint', 'MultiLineString', 'MultiPolygon'):
        if not isinstance(c, list):
            raise LoadError('多部件坐标须为数组')
        check = {'MultiPoint': _position, 'MultiLineString': _line, 'MultiPolygon': _polygon}[kind]
        for member in c:
            check(member)
    else:
        raise LoadError(f'不支持的几何类型：{kind}')


class GeoJsonLoader:
    def can_load(self, path: Path) -> bool:
        return path.suffix.lower() in ('.geojson', '.json')

    def load(self, path: Path) -> MapDocument:
        path = Path(path)
        try:
            data = json.loads(_read(path))
            if not isinstance(data, dict) or data.get('type') != 'FeatureCollection' or not isinstance(data.get('features'), list):
                raise LoadError('需要GeoJSON FeatureCollection及features数组')
            for feature in data['features']:
                if not isinstance(feature, dict) or feature.get('type') != 'Feature' or 'geometry' not in feature or 'properties' not in feature:
                    raise LoadError('无效Feature：缺少类型、geometry或properties')
                if feature['properties'] is not None and not isinstance(feature['properties'], dict):
                    raise LoadError('properties须为对象或null')
                _geometry(feature['geometry'])
        except (json.JSONDecodeError, RecursionError) as exc:
            raise LoadError(f'GeoJSON内容无法解析：{path.name}') from exc
        return MapDocument(document_key(path), path.resolve(), 'vector', features=data['features'])


class TextLoader:
    def can_load(self, path: Path) -> bool:
        return path.suffix.lower() == '.txt'

    def load(self, path: Path) -> MapDocument:
        path = Path(path)
        return MapDocument(document_key(path), path.resolve(), 'text', text=_read(path))


class LoaderRegistry:
    def __init__(self, loaders: list[MapLoader] | None = None):
        self.loaders = list(loaders) if loaders is not None else [GeoJsonLoader(), TextLoader()]

    def load(self, path: Path) -> MapDocument:
        path = Path(path)
        for loader in self.loaders:
            if loader.can_load(path):
                return loader.load(path)
        raise LoadError(f'不支持的文件格式：{path.suffix or "无扩展名"}')

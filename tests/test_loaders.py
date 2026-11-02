import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from mapqc_practice.loaders import LoaderRegistry, LoadError
from mapqc_practice.models import Location, ErrorRecord, document_key

ROOT = Path(__file__).resolve().parents[1]


class LoadingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.registry = LoaderRegistry()

    def geo(self, geometry):
        p = self.root / 'map.geojson'
        p.write_text(json.dumps({'type': 'FeatureCollection', 'features': [
            {'type': 'Feature', 'geometry': geometry, 'properties': {'name': '教学楼'}}]}), encoding='utf-8')
        return p

    def test_course_vector(self):
        d = self.registry.load(ROOT / 'samples/vector/campus.geojson')
        self.assertEqual([f['geometry']['type'] for f in d.features], ['Polygon', 'LineString', 'Point'])
        self.assertEqual(d.features[2]['geometry']['coordinates'], [15, 10])
        self.assertEqual(d.features[0]['properties']['name'], '示例建筑')
        self.assertEqual(d.document_key, document_key(d.path))

    def test_course_text(self):
        d = self.registry.load(ROOT / 'samples/text/notes.txt')
        self.assertEqual(len(d.lines), 3)
        self.assertEqual(d.lines[1], '地块002：名称待核对。')

    def test_text_bom_newlines_empty(self):
        p = self.root / '中文.TXT'
        p.write_bytes(b'\xef\xbb\xbf' + '甲\r\n\r\n乙\n'.encode())
        self.assertEqual(self.registry.load(p).lines, ['甲', '', '乙'])
        p.write_text('', encoding='utf-8')
        self.assertEqual(self.registry.load(p).lines, [])

    def test_missing_encoding_and_suffix(self):
        for name, data in [('bad.txt', b'\xff'), ('bad.png', b'123')]:
            p = self.root / name
            p.write_bytes(data)
            with self.assertRaises(LoadError): self.registry.load(p)
        with self.assertRaises(LoadError): self.registry.load(self.root / 'missing.txt')

    def test_invalid_json_shapes(self):
        p = self.root / 'bad.geojson'
        for data in ['{', '[]', '{}', '{"type":"FeatureCollection","features":[{}]}']:
            with self.subTest(data=data):
                p.write_text(data, encoding='utf-8')
                with self.assertRaises(LoadError): self.registry.load(p)

    def test_invalid_geometries(self):
        for g in [{'type': 'Point', 'coordinates': [True, 1]},
                  {'type': 'Point', 'coordinates': [float('nan'), 1]},
                  {'type': 'Point', 'coordinates': [1]},
                  {'type': 'LineString', 'coordinates': [[0, 1]]},
                  {'type': 'Polygon', 'coordinates': [[[0, 0], [1, 0], [1, 1], [0, 1]]]},
                  {'type': 'Bad', 'coordinates': []}]:
            with self.subTest(g=g):
                with self.assertRaises(LoadError): self.registry.load(self.geo(g))

    def test_multigeometries_null_and_empty(self):
        for g in [None, {'type':'MultiPoint','coordinates':[[1, 2], [3, 4]]},
                  {'type':'MultiLineString','coordinates':[[[1, 2], [3, 4]]]},
                  {'type':'MultiPolygon','coordinates':[[[[0, 0], [1, 0], [1, 1], [0, 0]]]]},
                  {'type':'GeometryCollection','geometries':[{'type':'Point','coordinates':[1, 2]}]}]:
            with self.subTest(g=g): self.assertEqual(self.registry.load(self.geo(g)).features[0]['geometry'], g)
        p = self.root / 'empty.geojson'
        p.write_text('{"type":"FeatureCollection","features":[]}', encoding='utf-8')
        self.assertEqual(self.registry.load(p).features, [])

    def test_locations_and_record(self):
        for location in [Location('vector', x=1, y=2), Location('text', line=1), Location('raster', row=0, column=0)]:
            self.assertEqual(ErrorRecord('map', 1, location, '名称', '待核对').location, location)
        for kwargs in [dict(kind='text', line=0), dict(kind='vector', x=1, y=float('inf')),
                       dict(kind='text', line=True), dict(kind='raster', row=-1, column=0),
                       dict(kind='vector', x=1, y=2, line=1), dict(kind='other')]:
            with self.subTest(kwargs=kwargs):
                with self.assertRaises(ValueError): Location(**kwargs)

    def test_strategy_injection_and_no_qt_dependency(self):
        class Custom:
            def can_load(self, path): return path.suffix == '.custom'
            def load(self, path): return 'custom result'
        self.assertEqual(LoaderRegistry([Custom()]).load(Path('x.custom')), 'custom result')
        result = subprocess.run([sys.executable, '-c', 'import sys; import mapqc_practice.loaders; assert not any(k.startswith("PySide6") for k in sys.modules)'], cwd=ROOT, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == '__main__': unittest.main()

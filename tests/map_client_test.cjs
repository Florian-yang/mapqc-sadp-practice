// Node VM/DOM/Leaflet/device doubles only: no browser, tiles, GPS or provider calls.
const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const code = fs.readFileSync('check_collect/static/map.js', 'utf8');
const settle = () => new Promise(resolve => setImmediate(resolve));
const deferred = () => { let resolve; const promise = new Promise(ok => { resolve = ok; }); return {promise, resolve}; };
const success = value => ({ok: true, value});
const failure = error => ({ok: false, value: {error}});

class Element {
  constructor(tag, id = '') { this.tagName = tag; this.id = id; this.children = []; this.handlers = {}; this.dataset = {}; this.style = {}; this.disabled = false; this._text = ''; }
  set textContent(value) { this._text = String(value); this.children = []; }
  get textContent() { return this._text + this.children.map(child => child.textContent).join(''); }
  set innerHTML(value) { throw new Error('Untrusted text entered innerHTML: ' + value); }
  append(...children) { this.children.push(...children); }
  replaceChildren(...children) { this._text = ''; this.children = [...children]; }
  addEventListener(type, handler) { this.handlers[type] = handler; }
  click() { if (!this.disabled && this.handlers.click) return this.handlers.click(); }
}

function feature(id = 'synthetic-map-one', category = 'greenery') {
  return {type: 'Feature', id, geometry: {type: 'Point', coordinates: [114.002, 30.001]},
    properties: {id, name: '<img src=x onerror="bad()">合成POI', category,
      description: '<svg onload="bad()">合成描述', status: 'needs_action', revision: 1, has_error: true}};
}
const collection = features => ({type: 'FeatureCollection', features});
function routeResult(item, latitude = 30, longitude = 114) {
  return {provider: 'synthetic-browser-fixture', coordinate_system: 'WGS84', destination: {id: item.properties.id, name: item.properties.name},
    geometry: {type: 'LineString', coordinates: [[longitude, latitude], [114.001, 30.0005], item.geometry.coordinates]},
    distance: 250.4, duration: 90, navigation_url: 'https://www.openstreetmap.org/directions?engine=fossgis_osrm_car',
    steps: [{type: 'depart', modifier: 'straight', name: '<b>合成路</b>', distance: 250.4, location: [longitude, latitude]},
      {type: 'arrive', modifier: '', name: '', distance: 0, location: item.geometry.coordinates}]};
}

function setup(options = {}) {
  const elements = {};
  for (const id of ['poi-map', 'map-notice', 'map-filter', 'route-summary', 'route-steps', 'ocr-result', 'stop-navigation']) elements[id] = new Element('div', id);
  elements['poi-map'].dataset.ocrReady = options.ocrReady === false ? 'false' : 'true';
  elements['stop-navigation'].disabled = true;
  elements['map-filter'].values = {category: '', status: ''};
  const env = {elements, features: options.features || [feature()], requests: [], layers: [], activeLayers: new Set(),
    windows: {}, cleared: [], watches: new Map(), confirmed: true, confirmCalls: 0, geoFailure: false, geoCalls: 0, nextWatch: 1};
  env.mapHandler = () => success(collection(env.features));
  env.routeHandler = request => success(routeResult(env.features.find(item => item.properties.id === request.body.poi_id), request.body.latitude, request.body.longitude));
  env.ocrHandler = () => success({text: '<svg onload="bad()">合成招牌', partial: true, suggested_category: 'shop'});
  const map = {setView() { return this; }, fitBounds() {}, removeLayer(layer) { env.activeLayers.delete(layer); }};
  function layer(kind, data, settings) {
    const item = {kind, data, settings, popups: [], addTo() { env.activeLayers.add(this); return this; }, getBounds() { return {}; },
      setLatLng(point) { this.data = point; return this; }, bindPopup(popup) { this.popup = popup; return this; }};
    env.layers.push(item);
    return item;
  }
  const L = {
    map: () => map,
    circleMarker: (point, settings) => layer('position', point, settings),
    geoJSON(data, settings = {}) {
      const item = layer(data.type === 'FeatureCollection' ? 'features' : 'route', data, settings);
      for (const point of data.features || []) {
        const marker = settings.pointToLayer ? settings.pointToLayer(point, [...point.geometry.coordinates].reverse()) : layer('point', point);
        if (settings.onEachFeature) settings.onEachFeature(point, marker);
        item.popups.push(marker.popup);
      }
      return item;
    },
    tileLayer(url, settings) { env.tile = {url, settings}; return {on() { return this; }, addTo() { return this; }}; },
  };
  const context = vm.createContext({console, URLSearchParams, L,
    document: {getElementById: id => elements[id], createElement: tag => new Element(tag)},
    window: {isSecureContext: true, addEventListener: (name, handler) => { env.windows[name] = handler; }, confirm() { env.confirmCalls++; return env.confirmed; }},
    navigator: {geolocation: {
      getCurrentPosition(ok, fail, settings) { env.geoCalls++; assert.equal(settings.maximumAge, 0); if (env.geoFailure) fail({message: '定位权限被拒绝'}); else ok({coords: {latitude: 30, longitude: 114}}); },
      watchPosition(ok, fail, settings) { const id = env.nextWatch++; env.watches.set(id, {ok, fail, settings}); return id; },
      clearWatch(id) { env.cleared.push(id); env.watches.delete(id); },
    }},
    FormData: class { constructor(form) { this.form = form; } get(key) { return this.form.values[key]; } },
    async fetch(url, settings = {}) {
      const request = {url, method: settings.method || 'GET', body: settings.body ? JSON.parse(settings.body) : undefined};
      env.requests.push(request);
      const result = await (url.startsWith('/api/map?') ? env.mapHandler(request) : url === '/api/maps/route' ? env.routeHandler(request) : url.endsWith('/ocr') ? env.ocrHandler(request) : Promise.reject(new Error('Unexpected request: ' + url)));
      return {ok: result.ok, json: async () => result.value};
    },
  });
  vm.runInContext(code, context);
  env.featureLayers = () => [...env.activeLayers].filter(item => item.kind === 'features');
  env.routeLayers = () => [...env.activeLayers].filter(item => item.kind === 'route');
  env.popup = (index = 0) => env.featureLayers().at(-1).popups[index];
  env.nav = (index = 0) => env.popup(index).children.find(item => item.tagName === 'button' && item.textContent === '导航至此POI');
  env.ocr = (index = 0) => env.popup(index).children.find(item => item.tagName === 'button' && item.textContent === '识别照片文字');
  env.submit = values => { elements['map-filter'].values = values; elements['map-filter'].handlers.submit({preventDefault() {}}); };
  env.routeRequests = () => env.requests.filter(request => request.url === '/api/maps/route');
  env.ocrRequests = () => env.requests.filter(request => request.url.endsWith('/ocr'));
  return env;
}

const scenarios = [
  ['popup text stays out of HTML and uses attributed OSM tiles', async () => {
    const env = setup(); await settle();
    assert.ok(env.popup().textContent.includes('<img src=x onerror="bad()">'));
    assert.ok(env.popup().textContent.includes('<svg onload="bad()">'));
    const photo = env.popup().children.find(item => item.tagName === 'img');
    assert.equal(photo.src, '/api/pois/synthetic-map-one/photo?revision=1');
    assert.equal(env.tile.url, 'https://tile.openstreetmap.org/{z}/{x}/{y}.png');
    assert.ok(env.tile.settings.attribution.includes('OpenStreetMap'));
    assert.equal(env.requests.length, 1);
  }],
  ['arbitration status keeps orange priority over the unresolved error marker', async () => {
    const arbitration = feature('synthetic-arbitration'); arbitration.properties.status = 'under_arbitration';
    const env = setup({features: [arbitration]}); await settle();
    const marker = env.layers.find(item => item.kind === 'position');
    assert.equal(marker.settings.color, '#b45309');
    assert.ok(env.popup().textContent.includes('第三方处理中'));
  }],
  ['category and progress filters form the correct query', async () => {
    const env = setup(); await settle();
    env.submit({category: 'bus_stop', status: 'needs_action'}); await settle();
    const url = new URL(env.requests.at(-1).url, 'http://loopback.test');
    assert.equal(url.pathname, '/api/map');
    assert.deepEqual([...url.searchParams], [['category', 'bus_stop'], ['status', 'needs_action']]);
    assert.equal(env.featureLayers().length, 1);
  }],
  ['device location draws returned route, instructions and live position', async () => {
    const env = setup(); await settle(); await env.nav().click();
    assert.deepEqual(env.routeRequests()[0].body, {poi_id: 'synthetic-map-one', latitude: 30, longitude: 114});
    assert.equal(env.routeLayers().length, 1);
    assert.equal(env.routeLayers()[0].data.coordinates.length, 3);
    assert.equal(env.elements['route-steps'].children.length, 2);
    assert.ok(env.elements['route-steps'].textContent.includes('<b>合成路</b>'));
    assert.equal(env.watches.size, 1);
    assert.equal(env.elements['stop-navigation'].disabled, false);
    const watcher = [...env.watches.values()][0];
    watcher.ok({coords: {latitude: 30.0002, longitude: 114.0003}});
    const position = [...env.activeLayers].find(item => item.kind === 'position');
    assert.deepEqual(Array.from(position.data), [30.0002, 114.0003]);
    assert.ok(env.elements['route-summary'].children.some(item => item.href && item.href.startsWith('https://www.openstreetmap.org/directions?')));
  }],
  ['denied device location never submits a route', async () => {
    const env = setup(); await settle(); env.geoFailure = true; await env.nav().click();
    assert.equal(env.routeRequests().length, 0);
    assert.equal(env.routeLayers().length, 0);
    assert.equal(env.watches.size, 0);
    assert.equal(env.elements['map-notice'].textContent, '定位权限被拒绝');
  }],
  ['route errors remove old routes and never draw fallback geometry', async () => {
    const env = setup(); await settle(); await env.nav().click();
    assert.equal(env.routeLayers().length, 1);
    env.routeHandler = () => failure('合成提供方：没有道路'); await env.nav().click();
    assert.equal(env.routeLayers().length, 0);
    assert.equal(env.layers.filter(item => item.kind === 'route').length, 1);
    assert.equal(env.elements['route-steps'].children.length, 0);
    assert.equal(env.watches.size, 0);
    assert.equal(env.elements['map-notice'].textContent, '合成提供方：没有道路');
  }],
  ['stop and pagehide clear device watches', async () => {
    const env = setup(); await settle(); await env.nav().click();
    env.elements['stop-navigation'].click();
    assert.deepEqual(env.cleared, [1]); assert.equal(env.watches.size, 0);
    assert.equal(env.elements['stop-navigation'].disabled, true);
    await env.nav().click(); env.windows.pagehide();
    assert.deepEqual(env.cleared, [1, 2]); assert.equal(env.watches.size, 0);
    assert.equal(env.elements['stop-navigation'].disabled, true);
  }],
  ['unconfigured OCR is disabled and cancelled consent sends nothing', async () => {
    const disabled = setup({ocrReady: false}); await settle(); await disabled.ocr().click();
    assert.equal(disabled.ocr().disabled, true); assert.equal(disabled.confirmCalls, 0); assert.equal(disabled.ocrRequests().length, 0);
    const cancelled = setup(); await settle(); cancelled.confirmed = false; await cancelled.ocr().click();
    assert.equal(cancelled.confirmCalls, 1); assert.equal(cancelled.ocrRequests().length, 0);
  }],
  ['partial OCR presents text and a suggestion without changing the POI', async () => {
    const env = setup(); await settle(); const before = JSON.stringify(env.features); await env.ocr().click();
    assert.deepEqual(env.ocrRequests()[0].body, {language: 'auto', expected_revision: 1, confirmed_external: true});
    assert.equal(env.ocrRequests()[0].url, '/api/pois/synthetic-map-one/ocr');
    assert.ok(env.elements['ocr-result'].textContent.includes('<svg onload="bad()">合成招牌'));
    assert.ok(env.elements['ocr-result'].textContent.includes('部分内容识别成功'));
    assert.ok(env.elements['ocr-result'].textContent.includes('建议类别：商铺'));
    assert.equal(JSON.stringify(env.features), before);
    assert.deepEqual(env.requests.filter(request => request.method === 'POST').map(request => request.url), ['/api/pois/synthetic-map-one/ocr']);
  }],
  ['late filter responses cannot replace a newer layer', async () => {
    const env = setup(); await settle(); const old = deferred(), latest = deferred(); let count = 0;
    env.mapHandler = () => (++count === 1 ? old.promise : latest.promise);
    env.submit({category: 'house', status: ''}); env.submit({category: 'shop', status: ''});
    latest.resolve(success(collection([feature('newest-poi', 'shop')]))); await settle();
    old.resolve(success(collection([feature('stale-poi', 'house')]))); await settle();
    assert.equal(env.featureLayers().length, 1);
    assert.equal(env.featureLayers()[0].data.features[0].id, 'newest-poi');
  }],
  ['late navigation and pagehide responses cannot restore an obsolete route', async () => {
    const firstFeature = feature('first-poi'), secondFeature = feature('second-poi');
    secondFeature.properties.name = '第二个合成POI'; secondFeature.geometry.coordinates = [114.004, 30.003];
    const env = setup({features: [firstFeature, secondFeature]}); await settle();
    const old = deferred(), latest = deferred(); let count = 0;
    env.routeHandler = () => (++count === 1 ? old.promise : latest.promise);
    const oldNavigation = env.nav(0).click(); await settle();
    const newNavigation = env.nav(1).click(); await settle();
    latest.resolve(success(routeResult(secondFeature))); await newNavigation;
    old.resolve(success(routeResult(firstFeature))); await oldNavigation;
    assert.equal(env.routeLayers().length, 1);
    assert.equal(env.watches.size, 1);
    assert.equal(env.elements['route-summary'].children.find(item => item.tagName === 'p').textContent.startsWith(secondFeature.properties.name), true);
    const late = deferred(); env.routeHandler = () => late.promise;
    const hiddenNavigation = env.nav(0).click(); await settle(); env.windows.pagehide();
    late.resolve(success(routeResult(firstFeature))); await hiddenNavigation;
    assert.equal(env.routeLayers().length, 0); assert.equal(env.watches.size, 0);
  }],
];

(async () => {
  for (const [name, run] of scenarios) { await run(); console.log('PASS ' + name); }
  console.log(`${scenarios.length}/${scenarios.length} map client scenarios passed. Synthetic DOM, Leaflet, location and API only.`);
})().catch(error => { console.error(error); process.exitCode = 1; });

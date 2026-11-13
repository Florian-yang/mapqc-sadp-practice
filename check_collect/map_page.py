import html
from .collection import CATEGORIES
from .review import STATUS


def page(ocr_ready):
    categories=''.join(f'<option value="{k}">{v}</option>' for k,v in CATEGORIES.items())
    states=''.join(f'<option value="{k}">{v}</option>' for k,v in STATUS.items())
    return f'''<link rel="stylesheet" href="/static/vendor/leaflet.css">
    <link rel="stylesheet" href="/static/desktop.css">
    <div class="workspace"><aside class="sidebar">
    <div class="section-heading"><div><p class="eyebrow">地图工作台</p><h2>采集点位</h2></div><a class="new-poi" href="/collect">＋ 采集</a></div>
    <p class="intro">查看点位，跟进数据核验。</p>
    <div class="metrics"><div><strong id="poi-count">—</strong><span>当前点位</span></div><div><strong id="error-count">—</strong><span>有待处理错误</span></div></div>
    <form id="map-filter"><label>类别 <select name="category"><option value="">全部类别</option>{categories}</select></label>
    <label>进展 <select name="status"><option value="">全部进展</option>{states}</select></label><button>筛选点位</button></form>
    <p id="map-notice" role="status">正在加载点位…</p>
    <div id="poi-list" class="poi-list" aria-label="POI列表"></div>
    <div class="sidebar-footer"><a href="/review">核验工作台 ↗</a><a href="/disputes">异议处理 ↗</a></div>
    </aside><section class="map-stage" aria-label="POI分布地图">
    <div id="poi-map" data-ocr-ready="{str(bool(ocr_ready)).lower()}" aria-label="POI分布地图"></div>
    <div class="map-legend"><span><i class="blue"></i>待核验</span><span><i class="red"></i>待处理错误</span><span><i class="orange"></i>第三方处理中</span><span><i class="green"></i>已裁决</span></div>
    </section><aside class="detail-panel"><p class="eyebrow">点位详情</p>
    <div id="poi-detail"><h2>从地图开始</h2><p class="intro">点击地图标点或左侧记录，查看照片、识别文字或规划路线。</p></div>
    <section id="ocr-result" aria-live="polite"></section><section id="route-summary" aria-live="polite"></section><ol id="route-steps"></ol>
    <button id="stop-navigation" type="button" disabled>停止位置跟随</button>
    {'' if ocr_ready else '<p>文字识别待配置服务密钥。</p>'}
    <details class="service-note"><summary>地图与识别服务</summary><p>底图：© <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>贡献者；路线：OSRM（驾车）。路线查询发送起终点坐标，OCR仅在勾选同意后发送照片至百度。</p></details>
    </aside></div>
    <script src="/static/vendor/leaflet.js" defer></script><script src="/static/map.js" defer></script>'''

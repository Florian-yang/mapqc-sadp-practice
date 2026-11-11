import html
from .collection import CATEGORIES
from .review import STATUS


def page(ocr_ready):
    categories=''.join(f'<option value="{k}">{v}</option>' for k,v in CATEGORIES.items())
    states=''.join(f'<option value="{k}">{v}</option>' for k,v in STATUS.items())
    return f'''<link rel="stylesheet" href="/static/vendor/leaflet.css">
    <p><a href="/">账号</a> · <a href="/my-pois">我的采集</a></p><h2>POI地图</h2>
    <form id="map-filter"><label>类别 <select name="category"><option value="">全部</option>{categories}</select></label>
    <label>进展 <select name="status"><option value="">全部</option>{states}</select></label> <button>查询</button></form>
    <p>红色：待处理错误；橙色：第三方处理中；绿色：已裁决；蓝色：待核验。</p>
    <div id="poi-map" data-ocr-ready="{str(bool(ocr_ready)).lower()}" style="height:420px;width:100%;margin-top:18px" aria-label="POI分布地图"></div>
    <p>底图：© <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>贡献者；路线：OSRM（驾车）。</p>
    <p id="map-notice" role="status"></p><p>路线查询会向OSRM发送起终点坐标；识别按钮会将选定照片发送到百度OCR。</p>
    <p><button id="stop-navigation" type="button" disabled>停止位置跟随</button></p>
    <section id="route-summary"></section><ol id="route-steps"></ol><section id="ocr-result"></section>
    {'' if ocr_ready else '<p>文字识别待配置服务密钥。</p>'}
    <script src="/static/vendor/leaflet.js" defer></script><script src="/static/map.js" defer></script>'''

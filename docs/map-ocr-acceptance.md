# 地图与文字识别

## 地图与导航

进入“POI地图”，按类别、处理进展查询。标记显示照片、当前版本和错误状态：待核验为蓝色，错误为红色，第三方处理为橙色，已裁决为绿色。

地图使用Leaflet与OpenStreetMap底图，坐标为WGS84。点击“导航至此POI”后，设备获取当前位置，服务器调用OSRM驾车路线，页面显示路线、距离、步骤并跟随位置。停止按钮结束跟随；偏离路线可重新点击导航。当前没有语音播报或自动偏航重算。

## 百度OCR

在地图中打开照片，勾选同意将照片发送至百度OCR，再点击识别。页面显示文字与类别建议，由采集者核对后更新分类。

在百度智能云文字识别控制台创建应用，开通通用文字识别（标准版），确认调用额度。启动服务前设置：

```powershell
$env:BAIDU_OCR_API_KEY='本机填写API Key'
$env:BAIDU_OCR_SECRET_KEY='本机填写Secret Key'
```

后端获取并缓存访问令牌，失效时刷新一次。照片采用1MB以内的JPEG，结果按照片版本、摘要、语言及供应商核对缓存。网络或额度异常时保留原POI内容并显示提示。

接口说明：[百度OCR](https://ai.baidu.com/ai-doc/OCR/zk3h7xz52)、[鉴权](https://ai.baidu.com/ai-doc/OCR/Ck3h7y2ia)、[OSRM](https://project-osrm.org/docs/v5.24.0/api/)、[OSM瓦片使用](https://operations.osmfoundation.org/policies/tiles/)。

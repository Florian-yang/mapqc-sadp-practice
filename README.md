# check@collect · POI采集与核验

《软件体系结构与设计模式》综合实践。11月3日收到完整题干后，从离线地图质检准备调整为移动Web/Internet POI采集核验系统。沿用当前私有仓库以保留历史，不改写已有提交。

- [完整原始题干](docs/assignment-original.md)
- [逐项验收](docs/requirements.md)、[修订日程](docs/plan.md)、[架构与一图二表](docs/architecture.md)
- [微信申请及配置](docs/wechat-setup.md)

当前v0.5新增采集者入口、相机拍照与设备自动定位前端、POI照片上传与本人记录查看；认证已有本地验证。真实微信和HTTPS手机相机/GPS仍待验收，见[采集验收说明](docs/collection-acceptance.md)。核验、聊天、中间件、地图与OCR按后续日程实现。

```powershell
& 'C:/Users/flori/AppData/Local/Programs/Python/Python313/python.exe' -m check_collect.server
& 'C:/Users/flori/AppData/Local/Programs/Python/Python313/python.exe' -m unittest discover -s tests -v
```

打开http://127.0.0.1:8765。未配置微信时页面会显示待配置。Web服务需安装requirements-web.txt（Pillow用于图片解码校验）；requirements.txt中的Qt依赖仅供旧桌面准备代码使用。公网HTTPS与平台审核需另行配置核验。

旧`mapqc_practice`、旧运行包与v0.1—v0.3标签保留为真实历史准备材料，不能计作check@collect功能完成。旧文档快照在`docs/history/mapqc-before-20261103`。

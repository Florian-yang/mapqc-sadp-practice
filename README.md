# check@collect

POI照片采集与核验应用。《软件体系结构与设计模式》综合实践，杨健淳，2024302131017，个人完成。

## 运行

使用Python 3.13，在项目目录安装依赖：

```powershell
python -m pip install -r requirements-demo.txt
python -m check_collect.server
```

本机页面为 http://127.0.0.1:8765 。消息通知需要同时运行NATS和消息进程，见[本机演示](docs/demo.md)。手机使用[热点HTTPS入口](docs/hotspot-demo.md)。

Windows电脑端可用[一键启动入口](docs/desktop.md)，自动启动地图网页、NATS和通知处理服务。登录后展示已有POI、照片及核验进展；右上角铃铛显示当前账号的未读通知。

## 文档

- [功能说明](docs/requirements.md)、[架构设计与质量分析](docs/architecture.md)
- [微信配置](docs/wechat-setup.md)、[地图与OCR](docs/map-ocr-acceptance.md)
- [消息通知](docs/messaging-acceptance.md)、[讨论与异议](docs/collaboration-acceptance.md)、[部署说明](docs/deployment.md)

微信和百度OCR的凭据由运行者配置，变量见`.env.example`；程序直接读取环境变量。面馆计价程序及类图位于`extras/noodle`。

微信支持网站扫码登录，以及微信公众号测试号的微信内网页授权；后者将`WECHAT_LOGIN_MODE`设为`official_account`。运行配置、照片、数据库和证书不随源码提供。

## 测试

```powershell
python -m unittest discover -s tests -v
node tests/capture_client_test.cjs
node tests/map_client_test.cjs
```

运行消息集成测试前，将`NATS_SERVER_EXE`设为本机NATS程序路径。

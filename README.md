# check@collect POI采集与核验

《软件体系结构与设计模式》综合实践，杨健淳，2024302131017，个人完成。当前为1.0.0-rc.1候选版，软件及外部平台验收见[需求表](docs/requirements.md)。旧MapQC桌面代码作为Git历史准备保留，不随本题源码包交付。

- [题干](docs/assignment-original.md)、[日程](docs/plan.md)、[UML一图二表](docs/architecture.md)
- [本机演示](docs/demo.md)、[部署](docs/deployment.md)、[消息](docs/messaging-acceptance.md)
- [微信](docs/wechat-setup.md)、[地图与OCR](docs/map-ocr-acceptance.md)、[讨论及异议](docs/collaboration-acceptance.md)

在项目目录安装`requirements-web.txt`后运行`python -m check_collect.server`，本地入口http://127.0.0.1:8765。消息投递还需启动NATS和消息进程，三者共用同一个数据库，具体命令见演示文档。`.env.example`只列变量，程序不自动加载该文件。

验证命令：`python -m unittest discover -s tests -v`；真实代理场景先设置`NATS_SERVER_EXE`为本机NATS程序路径，跳过项不计通过。前端检查为`node tests/capture_client_test.cjs`和`node tests/map_client_test.cjs`。源码包只包含当前POI测试，仓库另保留9项旧加载器回归。

面馆装饰模式C++与类图在`extras/noodle`。报告和第9周Excel在`reports`。个人OCR Key、微信应用及域名尚未配置；手机现场和公网演示待验收。

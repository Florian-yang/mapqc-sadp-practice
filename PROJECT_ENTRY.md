# check@collect项目入口

## 权威要求与当前状态

11/3用户补充完整题干：[assignment-original.md](docs/assignment-original.md)。本项目为移动Web/Internet的POI采集核验，必须覆盖自动位置与照片、核验重采、争议处理、地图导航、微信绑定登录、OCR及消息中间件。旧离线范围已失效，旧提交保留为历史准备。

今天新增check_collect Web认证模块，源码v0.4。已能本地注册、登录、退出；微信登录和绑定流程有本地测试，真实应用、域名与公网回调均未配置，不能标为真实微信验收通过。当前结果以evidence/2026-11-03和Git为准。

## 下一步

按docs/plan.json执行。11/3晚间先核对今天已有提交和测试，只补今日欠项，不重复开发或空提交。平台申请由用户处理；不因缺微信凭据停止后续日期的独立模块，也不提前实现后续日期功能。11/4推进移动照片和自动定位、POI字段与角色。

## 入口与截止

- 运行：`python -m check_collect.server`，本地http://127.0.0.1:8765；明确使用AGENTS约定解释器。
- 原路径、私有仓库保持：`C:/Users/flori/Desktop/RuanJian/作业/综合实践/MapQC-Practice`，https://github.com/Florian-yang/mapqc-sadp-practice 。
- 微信申请步骤与真实验收条件见docs/wechat-setup.md。今天本地协议模拟不能替代外部联调。
- 正常开发到11/9；11/10—11/11只补完整题目既定范围。最新题干截止北京时间11/11 22:30，覆盖旧22:00记录。
- 自动任务mapqc-11-9沿用，每天北京时间21:30；先读AGENTS、原题、计划、验收表、日记和远端状态。

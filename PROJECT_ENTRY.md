# check@collect项目入口

## 当前状态

权威题干为docs/assignment-original.md，范围和验收状态见docs/requirements.md。11/3认证、11/4采集和11/5核验处理均有本地验证。v0.6新增错误标识、更新/重采/驳回、历史版本及通知事件待发送箱。

证据见evidence/2026-11-04与evidence/2026-11-05。照片与坐标均为合成测试数据，HTTPS手机相机/GPS、真实重采及微信仍待联调。待发送箱没有生产消费或用户收信证据，不能计作消息中间件通过；细节见docs/review-acceptance.md。

## 下一步

先核对Git与证据，补齐截至当天的问题，再按docs/plan.json推进。11/6接入真实消息中间件，消费既有event_outbox，验证群发、点发、重试和幂等；不把SQLite表当中间件。11/5未提前实现私聊、异议裁决、地图或OCR。HTTPS与微信条件可用后补真实设备和扫码，不重复催密钥。

## 入口与规则

- 目录与私有仓库不变：C:/Users/flori/Desktop/RuanJian/作业/综合实践/MapQC-Practice，https://github.com/Florian-yang/mapqc-sadp-practice 。
- 使用AGENTS约定解释器安装requirements-web.txt后运行`python -m check_collect.server`，本地http://127.0.0.1:8765；照片与数据库在data，不入Git。
- 课件、报告模板、旧代码历史保留。每日任务mapqc-11-9为北京时间21:30，原计划正常开发至11/9，11/10—11/11只补既定范围。截止11/11北京时间22:30。

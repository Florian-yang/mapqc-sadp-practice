# check@collect项目入口

## 当前状态

权威题干为docs/assignment-original.md，范围和验收状态见docs/requirements.md。v0.8新增问题私聊、两个角色群及第三方异议处理，复用NATS通知链路。被指派的第三方重采后形成最终版本，记录采集账号并通知双方。

证据按实际日期保存在evidence，11/7的服务、HTTP与真实本机NATS测试覆盖讨论和裁决链路；见docs/collaboration-acceptance.md。照片、位置和账号为合成样例，HTTPS手机采集/重采、第三方现场、真实微信及公网演示待执行。

## 下一步

先核对Git与证据，补齐截至当天的问题，再按docs/plan.json推进。11/8接入地图显示、类别/错误/进度、路线导航及OCR，以真实外部调用留证；平台资质或凭据缺失时标记待联调。11/7只完成当日讨论和异议范围。HTTPS与微信条件具备后补手机和现场演示，不重复催密钥。

## 入口与规则

- 目录与私有仓库不变：C:/Users/flori/Desktop/RuanJian/作业/综合实践/MapQC-Practice，https://github.com/Florian-yang/mapqc-sadp-practice 。
- 使用AGENTS约定解释器安装requirements-web.txt后运行`python -m check_collect.server`，本地http://127.0.0.1:8765；照片与数据库在data，不入Git。
- 课件、报告模板、旧代码历史保留。每日任务mapqc-11-9为北京时间21:30，原计划正常开发至11/9，11/10—11/11只补既定范围。截止11/11北京时间22:30。

# check@collect项目入口

## 当前状态

权威题干为docs/assignment-original.md，范围和验收状态见docs/requirements.md。v0.7在认证、采集、核验基础上接入NATS JetStream，实现角色群发、待检与错误点发、本人通知收件箱。

证据按实际日期保存在evidence，11/6通过真实本机NATS子进程、TCP、发布/消费确认验证通知链路；运行方法见docs/messaging-acceptance.md。照片、位置和账号为合成样例，HTTPS手机采集/重采、真实微信及公网部署仍待联调。

## 下一步

先核对Git与证据，补齐截至当天的问题，再按docs/plan.json推进。11/7实现私聊、两个角色群聊及异议转第三方现场核验和裁决；复用本轮已验证的通知链路。11/6未提前实现聊天、第三方裁决、地图或OCR。HTTPS与微信条件具备后补真实设备和扫码，不重复催密钥。

## 入口与规则

- 目录与私有仓库不变：C:/Users/flori/Desktop/RuanJian/作业/综合实践/MapQC-Practice，https://github.com/Florian-yang/mapqc-sadp-practice 。
- 使用AGENTS约定解释器安装requirements-web.txt后运行`python -m check_collect.server`，本地http://127.0.0.1:8765；照片与数据库在data，不入Git。
- 课件、报告模板、旧代码历史保留。每日任务mapqc-11-9为北京时间21:30，原计划正常开发至11/9，11/10—11/11只补既定范围。截止11/11北京时间22:30。

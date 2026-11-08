# check@collect项目入口

## 当前状态

权威题干为docs/assignment-original.md，范围及验收状态见docs/requirements.md。v0.9新增地图类别/错误/进展、OSRM驾车路线和OCR.Space照片识别，沿用认证、采集核验、讨论裁决及NATS通知链路。

11/8真实外部接口已返回路线及中英文识别，浏览器确认地图与照片弹窗；见docs/map-ocr-acceptance.md和当日evidence。合成样例验证软件及平台接口。个人OCR Key尚未配置；HTTPS手机与现场、真实微信及公网演示待验收。浏览器OCR确认框的自动操作中断，结果展示待复测。

## 下一步

先核对Git与证据，补齐截至当天的问题，再按docs/plan.json推进。11/9联调，整理适当UML一图二表、报告及演示，汇总第9周Excel。11/8不提前做该交付任务。缺平台配置的项目保留待验收，继续独立模块。

## 入口与规则

- 目录与私有仓库不变：C:/Users/flori/Desktop/RuanJian/作业/综合实践/MapQC-Practice，https://github.com/Florian-yang/mapqc-sadp-practice 。
- 使用AGENTS约定解释器安装requirements-web.txt后运行`python -m check_collect.server`，本地http://127.0.0.1:8765；照片与数据库在data，不入Git。
- OCR申请与本机配置见docs/map-ocr-acceptance.md。微信步骤见docs/wechat-setup.md，密钥不发聊天、不进Git。
- 每日任务mapqc-11-9为北京时间21:30。常规开发至11/9，11/10—11/11只补既定范围，截止11/11北京时间22:30。

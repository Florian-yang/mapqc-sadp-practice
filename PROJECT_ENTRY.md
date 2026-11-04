# check@collect项目入口

## 当前状态

权威题干为docs/assignment-original.md，范围和验收状态见docs/requirements.md。11/3已完成Web认证本地验证，真实微信仍待应用及回调域名。11/4源码v0.5新增采集者加入、相机取帧与自动定位前端、POI上传、照片存储和本人列表。

采集后端与前端处理器测试通过，证据见evidence/2026-11-04。所有测试照片与坐标均为合成数据，HTTPS手机相机、GPS和权限流程未实测，R01仍待完整验收；细节见docs/collection-acceptance.md。不能将文件存在或本地测试算作真机或微信接入通过。

## 下一步

先核对Git与证据，补齐截至当天的问题，再按docs/plan.json推进。11/5开发核验错误、采集者重采/更新/驳回及角色边界，不提前写聊天或地图模块。HTTPS环境可用后补真机采集，微信平台条件可用后补真实扫码；此前不重复催密钥，继续当日独立模块。

## 入口与规则

- 目录与私有仓库不变：C:/Users/flori/Desktop/RuanJian/作业/综合实践/MapQC-Practice，https://github.com/Florian-yang/mapqc-sadp-practice 。
- 使用AGENTS约定解释器安装requirements-web.txt后运行`python -m check_collect.server`，本地http://127.0.0.1:8765；照片与数据库在data，不入Git。
- 课件、报告模板、旧代码历史保留。每日任务mapqc-11-9为北京时间21:30，原计划正常开发至11/9，11/10—11/11只补既定范围。截止11/11北京时间22:30。

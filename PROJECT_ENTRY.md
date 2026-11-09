# check@collect项目入口

## 当前状态

权威题干为docs/assignment-original.md，逐项状态见docs/requirements.md。当前1.0.0-rc.1候选版涵盖采集核验、私聊群聊、第三方异议、NATS通知、地图及OCR。11/9串联真实HTTP和NATS完成综合场景，地图/OCR协议在该场景注入；平台真实响应见11/8证据。

11/9补做11/8中断的浏览器OCR显示检查：改成照片旁默认不勾选的外发同意项，已在真实浏览器看到中英文缓存结果和分类建议。该检查不重复外发照片，未替代个人Key或真机验收。

一图二表、教师模板报告、源码包及本机演示材料已整理，第9周Excel填写C5/C9。用户11/9确认个人完成：杨健淳，2024302131017，工作量100%。文件及验证证据见reports、dist和evidence/2026-11-09。

## 下一步

11/10—11/11只补既定范围：个人OCR Key、微信应用与回调域名、公开HTTPS手机入口及现场采集/导航验收。配置已在docs/wechat-setup.md、map-ocr-acceptance.md和deployment.md说明；不重复索要密钥。已有软件流程、文档和推送状态先对账，不重复造版本。

## 入口与截止

- 目录C:/Users/flori/Desktop/RuanJian/作业/综合实践/MapQC-Practice，私有仓库https://github.com/Florian-yang/mapqc-sadp-practice。
- 使用AGENTS约定Python 3.13，依赖requirements-web.txt。本机合成演示按docs/demo.md创建独立数据目录，拒绝覆盖已有数据库。
- 每天北京时间21:30核对遗漏。教师截止11/11北京时间22:30，到时停止开发提交并如实保留未验收项。

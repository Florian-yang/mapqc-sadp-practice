# check@collect项目入口

## 当前状态

权威题干为docs/assignment-original.md，逐项状态见docs/requirements.md。11/9的1.0.0-rc.1历史候选版涵盖采集核验、私聊群聊、第三方异议、NATS通知、地图及OCR。11/9串联真实HTTP和NATS完成综合场景，地图/OCR协议在该场景注入；平台真实响应见11/8证据。

11/9补做11/8中断的浏览器OCR显示检查：改成照片旁默认不勾选的外发同意项，已在真实浏览器看到中英文缓存结果和分类建议。该检查不重复外发照片，未替代个人Key或真机验收。

一图二表、教师模板报告、源码包及本机演示材料已整理，第9周Excel填写C5/C9。用户11/9确认个人完成：杨健淳，2024302131017，工作量100%。文件及验证证据见reports、dist和evidence/2026-11-09。

## 11月11日更新

手机通过电脑热点HTTPS提交照片与定位成功，NATS已将待检通知投递给核验者。证据在evidence/local/hotspot-live/phone-submission.json，不记录为公网部署。用户决定暂不继续真机逐流程验收，转为提交源码。默认OCR改为百度，微信保留网站扫码登录与绑定代码；两者均等待真实凭据配置，不继续申请或外部联调。

## 下一步

当前为1.0.0-rc.3，已整理源码与完整提交包。定时任务已取消，本轮只完成接入代码和提交说明，平台申请、真实微信与百度OCR联调、公网和现场导航暂不推进。报告及第10周Excel已同步当前图文；源码包单独提供，完整包含报告与周表。

## 入口与截止

- 目录C:/Users/flori/Desktop/RuanJian/作业/综合实践/MapQC-Practice，私有仓库https://github.com/Florian-yang/mapqc-sadp-practice。
- 使用AGENTS约定Python 3.13，依赖requirements-web.txt。本机合成演示按docs/demo.md创建独立数据目录，拒绝覆盖已有数据库。
- 定时推进已取消。教师截止11/11北京时间22:30，到时停止开发提交并如实保留未验收项。

## 职责整理与交付复核

控制器通过Collection.get_owned读取上传结果，通知服务仅处理数据，notification_pages负责页面渲染。图及说明同步，独立代码与文字复核均通过；报告保留教师封面及评价区。具体验证与包摘要见evidence/2026-11-11/refactor-delivery.json。

一图二表按11/11用户明确的名称修订：总体组织架构图、解决方案映射表、质量属性表。映射表为6组关键需求、方案与对应实现，报告保持5页；桌面填写版与源码包已同步。记录见evidence/2026-11-11/mapping-delivery.json。

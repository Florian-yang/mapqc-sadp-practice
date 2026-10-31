# MapQC 综合实践

软件体系结构与设计模式课程的地图数据质检工具。从需求、架构和项目骨架开始，按日实现并记录验证结果。

当前版本 **v0.1.0**：已有需求与验收清单、用例图、最小窗口和打包脚本。地图读取、错误登记与查询尚未实现。

## 运行与验证

使用Python 3.13，安装`requirements.txt`后运行：

```powershell
python -m mapqc_practice
python -m mapqc_practice --smoke-test --result evidence/local/source-smoke.json
powershell -File scripts/build.ps1 -PythonPath C:/path/to/python.exe
```

构建后输出至`dist/MapQCPractice/MapQCPractice.exe`。`--smoke-test`检查离屏窗口创建与事件处理，不代替桌面交互和业务验收。

## 材料

- [项目入口](PROJECT_ENTRY.md)：当前状态、下一步与执行规则。
- [需求与验收](docs/requirements.md)：功能范围及检查方法。
- [课程要求对应表](docs/course-checklist.md)：课件和报告需要的证据。
- [逐日计划](docs/plan.md)：10月31日至11月9日的任务。
- [用例图](docs/diagrams/use-cases.svg)：计划支持的用户操作。
- [环境与验证](docs/environment.md)、[每日执行配置](docs/automation.md)。
- [日记](docs/diary/index.md)、[第8周材料](docs/weekly/week08.md)。
- [报告工作副本](reports/README.md)：保留老师模板，图文和周材料按计划合并。

此仓库是新一轮综合实践。原第二次作业的MapQC包保留作参考，没有作为本轮已完成功能导入。

# 运行环境与今日验证

2026年10月31日实际核对：Windows 11（10.0.26200）、Python 3.13.7、PySide6及Qt 6.11.2、PyInstaller 6.22.3。运行依赖版本见`requirements.txt`。

本轮v0.1.0已完成两项启动检查：源码和打包程序均能创建离屏窗口、保存截图并以退出码0结束。小样例也已检查基本结构。详见`evidence/2026-10-31/verification.json`。

构建脚本临时使用Python与Windows目录作为依赖搜索路径，结束后恢复原进程PATH，避免其他工具的同名DLL混入。诊断结果采用UTF-8文件保存，控制台JSON使用ASCII转义，适应Windows不同输出编码。

当前包位于`dist/MapQCPractice`，由程序及内部依赖共同组成，需整目录使用。它是启动骨架，地图加载、标注和查询将在后续版本实现；普通桌面交互与其他平台还没有完成验证。

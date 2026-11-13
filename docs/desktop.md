# 电脑端运行

Windows安装Python 3.13及`requirements-demo.txt`中的依赖，执行`scripts/get_nats.py`准备NATS。

首次运行可创建独立演示数据库：

```powershell
python scripts/create_demo.py --database data/learning-demo/demo.sqlite3
```

该命令会创建示例账号和一条合成POI，已有数据库时不会覆盖。入口固定读取`data/learning-demo/demo.sqlite3`；该位置已有数据时不必再次执行，其他路径的数据库不会自动载入。

在`data/local-config`中创建两个本机配置文件：

- `微信.txt`：每行一个配置项，分别为`APP_ID=自己的AppID`、`APP_SECRET=自己的AppSecret`。
- `百度OCR.txt`：分别为`API_KEY=自己的APIKey`、`SECRET_KEY=自己的SecretKey`。

双击`scripts/start_desktop.cmd`，或运行：

```powershell
python scripts/start_desktop.py
```

入口会打开 http://127.0.0.1:8765/?desktop=1 ，用数据库中的账号密码登录。电脑端不要求开启热点，使用账号登录；手机微信授权在热点HTTPS入口完成。现有账号、照片、POI和通知由两端共享。

先关闭原启动窗口，开启电脑热点，再次启动后手机可访问 https://192.168.137.1:8443/ 。微信测试号的网页授权回调地址填写`192.168.137.1:8443`。新手机首次使用时，用Safari访问 http://192.168.137.1:8444/ 安装演示证书，并在系统设置中信任。证书保存在本机，重复启动复用；入口优先沿用尚未到期的原演示证书。

启动窗口须保持打开。关闭窗口或按Ctrl+C停止本入口管理的服务。重复启动会打开已有电脑端页面；其他程序占用所需端口时会提示，不会强行结束它们。日志位于`data/learning-demo/desktop-runtime`。

铃铛显示当前账号未读通知，每15秒刷新。进入通知页不会自动标为已读，点击“标为已读”后更新状态。

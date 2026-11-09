# 本机演示

在项目目录执行；生成器只接受data独立空子目录，已有数据库会拒绝覆盖。

```powershell
$P = 'C:/Users/flori/AppData/Local/Programs/Python/Python313/python.exe'
& $P scripts/create_demo.py
# 每个服务终端先设置P及以下环境变量；生成命令只运行一次
$env:CHECK_COLLECT_DB = (Join-Path $PWD 'data/demo/demo.sqlite3')
$env:PUBLIC_ORIGIN = 'http://127.0.0.1:8765'
$env:CHECK_COLLECT_NATS_URL = 'nats://127.0.0.1:14222'
# 终端一
& './dist/tools/nats-server-v2.15.2-windows-amd64/nats-server.exe' -js -a 127.0.0.1 -p 14222 -sd data/demo/nats
# 终端二
& $P -m check_collect.messaging
# 终端三
& $P -m check_collect.server
```

NATS未安装时按[消息说明](messaging-acceptance.md)准备。三个账号为demo_collector、demo_reviewer、demo_staff，共用演示口令`Demo-Only-2026!`，仅用于本机合成演示。

1. 采集者登录，查看合成招牌及版本。
2. 在地图筛选类别、查看照片；配置OCR后勾选同意识别，核对建议。
3. 核验者标错，采集者在通知中查看。
4. 双方私聊、查看角色群，采集者驳回并提交异议。
5. 第三方接收任务；手机可访问的HTTPS入口配置后，进行实拍重采并提交结论。
6. 完成重采与裁决后，双方查看结果、历史照片及地图进展。

初始照片和位置是合成样例。微信应用及域名、个人OCR Key尚未配置；真实微信、HTTPS手机采集和现场导航待验收。第三方步骤须补实拍，不把预置照片当作现场重采。配置见[微信](wechat-setup.md)、[地图与OCR](map-ocr-acceptance.md)。

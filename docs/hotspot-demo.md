# 手机热点演示

电脑开启移动热点，手机连接后，在热点网卡地址运行HTTPS后台。启动脚本只绑定指定网卡；NATS只监听本机。此模式用于局域网真机演示，Internet访问单独验收。

安装requirements-demo.txt后，在项目目录执行：

```powershell
& 'C:/Users/flori/AppData/Local/Programs/Python/Python313/python.exe' -X utf8 scripts/start_hotspot.py --address 192.168.137.1
```

地址以实际热点网卡为准。11/11本机热点为192.168.137.1，启动后的手机设置页为`http://192.168.137.1:8444/`，应用为`https://192.168.137.1:8443/`。

用iPhone Safari打开设置页，点击“iPhone安装演示证书”。在“设置→通用→VPN与设备管理”安装CheckCollect Demo CA，再到“通用→关于本机→证书信任设置”开启该证书的完全信任。演示CA有效三天，服务端证书有效两天；演示结束后移除描述文件。CA签名私钥未保存，服务端私钥留在data内，不进入源码包。重启启动器会生成新证书，手机需重新安装。

账号demo_collector、demo_reviewer、demo_staff共用演示口令`Demo-Only-2026!`。进入采集者账号后拍照，按Safari提示允许相机与定位，填写类别、描述并上传；核验和第三方流程见demo.md。

若手机无法打开设置页，在电脑以管理员PowerShell运行`./scripts/allow_hotspot.ps1 -Address 192.168.137.1`。它只允许所选网卡同一/24网段访问8443和8444，不关闭防火墙。此操作由用户手动执行。结束后运行`Remove-NetFirewallRule -Name CheckCollect-Hotspot-Demo`移除规则。

创建空文件`data/hotspot-demo/stop`可停止启动器及其三个子进程。数据保留，重启不覆盖已有演示数据库。预置照片、位置与账号均为合成数据，实际手机拍摄、定位和提交须另留验证记录。

依据：[Safari相机的安全上下文要求](https://developer.mozilla.org/en-US/docs/Web/API/MediaDevices/getUserMedia)、[Apple手动证书信任说明](https://support.apple.com/en-us/102390)。

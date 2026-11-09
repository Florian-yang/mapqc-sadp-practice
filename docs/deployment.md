# 运行与部署

当前验证环境为Windows、Python 3.13、Pillow 12.1.0、nats-py 2.16.0、NATS Server 2.15.2，浏览器加载本地Leaflet 1.9.4。先安装`requirements-web.txt`；旧Qt依赖只属于历史桌面代码。

本机合成演示按[demo.md](demo.md)启动，三个进程共用`CHECK_COLLECT_DB`。Web和NATS均默认监听127.0.0.1，关闭后由原命令重新启动；事件保留在待发送箱，投递状态见本人通知。演示数据和实际采集数据使用不同目录。

Internet部署需要可从手机访问的HTTPS入口，将请求反向代理到Web进程，并把`PUBLIC_ORIGIN`设为该公开源。服务端校验浏览器Origin，代理不能改成另一个源。NATS保留在本机，数据目录持久保存。当前尚未配置公开HTTPS入口。

微信应用、回调域名和个人OCR Key在平台申请后放到本机环境变量，见[微信配置](wechat-setup.md)与[地图OCR配置](map-ocr-acceptance.md)。`.env.example`仅为变量样例，程序不自动读取该文件。

本机消息与软件流程已有运行证据；手机拍照定位、现场重采和到达导航、真实微信及公网访问分别验收。核验者与第三方角色通过可信本机命令授予已有账号，不能在网页上自行升级角色。

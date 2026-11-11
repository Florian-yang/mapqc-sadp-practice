# 运行与部署

后端使用Python 3.13、SQLite和Pillow，前端使用JavaScript与Leaflet。消息进程通过nats-py连接NATS JetStream。依赖位于`requirements-web.txt`；热点演示另用`requirements-demo.txt`。

本机演示见[demo.md](demo.md)。Web、消息进程和维护命令共用`CHECK_COLLECT_DB`，照片放在数据库同级的photos目录，NATS数据单独保存。

手机演示使用[电脑热点HTTPS入口](hotspot-demo.md)。公网部署时，在公开域名配置HTTPS反向代理，并将`PUBLIC_ORIGIN`设为该公开源；NATS保持本机监听。数据目录需持久保存，代理设置请求大小、连接数和速率限制。

微信与百度OCR凭据配置见[微信](wechat-setup.md)、[地图OCR](map-ocr-acceptance.md)。凭据只提供给服务端，`.env.example`是变量样例。

核验者和第三方角色由维护者命令授予已有账号，采集者可在登录后主动加入。

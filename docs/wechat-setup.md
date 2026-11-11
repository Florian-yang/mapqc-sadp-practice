# 微信配置

采用微信网站应用扫码登录，支持将微信授权身份绑定到已有账号。

## 平台配置

在[微信开放平台](https://open.weixin.qq.com/)申请网站应用及微信登录权限，配置本站域名为授权回调域。回调路径为`https://你的域名/auth/wechat/callback`。

启动服务前设置：

```powershell
$env:PUBLIC_ORIGIN='https://你的域名'
$env:WECHAT_APP_ID='本机填写AppID'
$env:WECHAT_APP_SECRET='本机填写AppSecret'
```

凭据保存在服务端环境变量。应用读取`.env.example`列出的变量，文件本身不自动加载。

## 使用

首页选择“微信扫码登录”。首次授权创建账号，以后授权进入同一账号。绑定已有账号时，先用本地账号登录，再选择“绑定或核验微信”。绑定通过appid和openid建立关联，同一微信身份只对应一个本系统账号。

未配置微信应用时使用本地账号。当前提供网站扫码方式，手机单机微信内授权需另选公众号或小程序接入方式。

授权状态与浏览器会话绑定，回调状态只使用一次；绑定过程中核对原会话，跨账号覆盖被拒绝。当前不提供直接替换已绑定微信身份的入口。

接口说明：[网站应用微信登录](https://developers.weixin.qq.com/doc/oplatform/Website_App/WeChat_Login/Wechat_Login.html)。

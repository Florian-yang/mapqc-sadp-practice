# 11月6日消息中间件

## 结构

采用独立NATS Server 2.15.2和JetStream文件存储，Python客户端固定nats-py 2.16.0。业务事务先写待发送箱；生产者取得JetStream发布确认后记published_ms，消费者把通知存入本人收件箱后再确认消费。delivered_ms表示已存入本系统收件箱，不表示用户已阅读，也不是微信推送。

系统维护者可向采集者或核验者群发通知。新POI自动通知当时的核验者，标错点发原采集者；更新、重采通知核验者，驳回通知原核验者。群发按发起时的角色成员生成接收人快照，之后加入的成员不追收旧广播。网页账号只能查看自己的通知和标记已读，不能群发系统通知。

事件ID用于发布去重，收件箱主键用于重复消费去重。消费失败不提前确认，发布失败保留待发送事件。这里没有把SQLite表当作中间件，也不声称端到端严格恰好一次。

## 本机运行

先安装requirements-web.txt。以下命令均在项目目录执行，三个服务窗口分别保留运行；关闭服务会暂停实际投递，重新启动后继续处理。NATS只监听127.0.0.1，存储位于data/nats，不开放到公网。当前未配置集群、TLS或多机访问认证。

```powershell
$P = 'C:/Users/flori/AppData/Local/Programs/Python/Python313/python.exe'
& $P scripts/get_nats.py
& './dist/tools/nats-server-v2.15.2-windows-amd64/nats-server.exe' -js -a 127.0.0.1 -p 14222 -sd data/nats
# 另一个终端
& $P -m check_collect.messaging
# 再一个终端
& $P -m check_collect.server
```

维护者可用`python -m check_collect.notifications collector "交通管制，暂停采集"`生成系统通知；也可把collector换成reviewer。该命令使用当前CHECK_COLLECT_DB，用户在首页进入“我的通知”查看。Web、维护命令和消息进程须指向同一数据库，不能把待发送提示当作已送达。

## 实际验证

本机启动真实NATS子进程并通过TCP投递，验证采集者、核验者群发，自动待检通知和错误点发；实际HTTP验证本人收件箱、跨账号拒绝与已读操作。还验证发布确认未记入业务库后重发、消费入库但未确认时客户端断开重连、数据库处理失败、代理进程停机重试和文件存储重启，以及旧待发送箱迁移。集成测试必须设置NATS_SERVER_EXE；跳过测试不算通过，结果见evidence/2026-11-06。

照片、坐标和账号均为合成样例，消息代理与传输是真实运行。进程重启测试不能证明断电恢复或集群高可用。微信、HTTPS手机采集/重采仍待原有外部条件；私聊、两个群聊、第三方裁决留11/7。

当日最终结果为52项Python测试通过、0跳过，其中10项真实代理场景；6个前端合成场景通过。满流时先消费腾出空间，已有不安全的流或消费者配置会被拒绝；下载中断可重试。

开发依据：[JetStream概念](https://docs.nats.io/concepts/jetstream)、[确认与去重机制](https://github.com/nats-io/nats.docs/blob/master/using-nats/jetstream/model_deep_dive.md)、[官方发布](https://github.com/nats-io/nats-server/releases/tag/v2.15.2)。二进制压缩包已按官方SHA256SUMS校验，不入Git。

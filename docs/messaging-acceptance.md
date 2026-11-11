# 消息通知

业务事务保存通知事件，独立消息进程通过NATS JetStream发布和消费，入箱后确认。NATS使用文件存储；事件ID用于发布与入箱去重。

新POI通知核验者，标错通知原采集者，更新和重采通知核验者。私聊、群聊和第三方任务也生成通知。群发接收者取发送时的角色成员，之后加入者不补收旧广播。

投递状态分为待发送、已发布、已入箱和已读。发布失败保留事件，消费失败重新投递。聊天正文保存在业务库，消息通知保存讨论标识。

## 运行

在项目目录执行，三个终端使用相同的`CHECK_COLLECT_DB`：

```powershell
python scripts/get_nats.py
# 终端一
& './dist/tools/nats-server-v2.15.2-windows-amd64/nats-server.exe' -js -a 127.0.0.1 -p 14222 -sd data/nats
# 终端二
python -m check_collect.messaging
# 终端三
python -m check_collect.server
```

NATS只监听本机回环地址。维护者发送角色通知：

```powershell
python -m check_collect.notifications collector '交通管制，暂停采集'
```

将`collector`换为`reviewer`可通知核验者。登录后在“我的通知”查看，网页账号只能读取和标记本人的通知。

参考：[JetStream](https://docs.nats.io/concepts/jetstream)。

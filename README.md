# AMQP 通道与确认记账纠偏

## 题面

网关侧接了一批 AMQP 生产者，最近一个月出现过三次整条连接假死。日志里能对上：某个客户端把 frame-max 报成 1MB，网关没做协商上限，直接按客户端说的收帧，单个 body 帧把内存吃满；channel 号 0 被客户端拿来发业务方法，网关当成普通通道处理；channel 表达到上限后自动分配一直返回同一个号，两个业务流互相串了 delivery-tag；publisher confirm 里同一个 tag 被确认两次，未确认计数被减成负数；body 分片总长与声明值不一致，网关仍然把半截消息投给消费者；心跳协商 60 秒，实际到 120 秒才判定超时，中间那段时间连接既不算活也不算死；连接关闭后 channel 没有释放，重启恢复时把已关的连接又当成活的。实现在 gsb/batch47-safety-d-04 的 amqp_channel_frame_guard.py，类 AmqpChannelFrameGuard。

它负责连接协商、通道分配、帧校验、分片拼装与确认记账。构造 AmqpChannelFrameGuard(*, frame_max=131072, channel_max=32, heartbeat=60, max_body_bytes=1048576, max_connections=1000, clock=None)。接口：open_connection(conn_id, *, frame_max=None, heartbeat=None, now=None) -> dict；close_connection(conn_id, *, now=None) -> dict；purge(now=None) -> dict；open_channel(conn_id, *, channel_id=None, now=None) -> dict；close_channel(conn_id, channel_id, *, now=None) -> dict；frame(conn_id, *, channel_id, frame_type, payload_size, now=None) -> dict；assemble(conn_id, channel_id, *, parts, declared_size=None, now=None) -> dict；publish(conn_id, channel_id, *, exchange, routing_key, body, now=None) -> dict；confirm(conn_id, channel_id, *, delivery_tag, now=None) -> dict；heartbeat(conn_id, *, now=None) -> dict；check_timeout(conn_id, *, now=None) -> dict；stats()、reset_stats()、snapshot()、restore(state)。错误统一抛 AmqpError(code, detail)。

要覆盖的故障与边界：一、frame_max 只接受 4096 到 131072，越界或非整数抛 AMQP_BAD_FRAME_MAX，协商值取请求值与实例上限的较小者；二、heartbeat 只接受 0 到 65535，越界抛 AMQP_BAD_HEARTBEAT，重复连接标识抛 AMQP_DUPLICATE_CONNECTION，活跃连接超上限抛 AMQP_CONNECTION_LIMIT；三、通道 0 保留给心跳帧，业务帧用 0 抛 AMQP_RESERVED_CHANNEL，显式重复占用抛 AMQP_CHANNEL_IN_USE，自动分配取最小空闲号，没有空闲号抛 AMQP_CHANNEL_LIMIT；四、关闭通道返回未确认条数，关闭不存在的通道抛 AMQP_UNKNOWN_CHANNEL；五、帧类型只接受 method、header、body、heartbeat，心跳帧只能走 0 号通道且负载为 0，否则抛 AMQP_BAD_FRAME，业务帧负载超过协商上限抛 AMQP_FRAME_TOO_LARGE；六、拼装的每个分片都不能超过帧上限，总长小于声明值抛 AMQP_BODY_INCOMPLETE，大于声明值或超过 max_body_bytes 抛 AMQP_BODY_TOO_LARGE，空分片列表抛 AMQP_BAD_FRAME；七、publish 的 exchange 为空抛 AMQP_BAD_EXCHANGE，body 不是字符串抛 AMQP_BAD_BODY，字节数超限抛 AMQP_BODY_TOO_LARGE 且不占用序号；八、同一通道的序号从 1 单调递增，返回值带未确认条数；九、confirm 只接受 1 到当前最大序号且未确认过的 tag，重复、越界、0 或负数一律抛 AMQP_UNKNOWN_TAG，乱序确认合法；十、check_timeout 在空闲达到两倍心跳间隔时报 AMQP_HEARTBEAT_TIMEOUT，恰好等于边界也算超时，心跳为 0 的连接永不超时；十一、连接关闭后任何通道操作抛 AMQP_CONNECTION_CLOSED，未知连接抛 AMQP_UNKNOWN_CONNECTION，purge 清理已关闭连接并返回条数；十二、snapshot/restore 覆盖连接、通道序号与已确认集合，非法结构抛 AMQP_SNAPSHOT_INVALID 且原状态不变。

硬约束：连接表、通道表与确认集合必须持同一把锁，八个线程并发 publish 得到的序号必须恰好是 1 到 8 且不重复，八个线程并发开通道、上限为四时只能成功四个；snapshot 是深拷贝，恢复后未确认消息与已确认集合继续生效；未确认集合与通道表不允许无界增长；stats 精确统计连接、通道、帧、发布与确认，reset_stats 归零。

验收：python3 -m pytest -q tests 的 15 条必须全部通过；不得修改测试、跳过或用固定输入写死；并发与重启恢复场景自行验证；只做本地实现。

## 运行方式

```bash
python3 -m pytest -q tests
```

## 平台字段

- domain: 安全
- sub: 网络与通信安全
- type: Bugfix
- difficulty: 困难

# 题面

视频会议厂商的 TURN 中继集群这周报了两起资损事故。一起是某间会议室入会失败，抓包发现同一五元组同时存在两条 allocation，客户端刷新时把流量分到了已经过期的那条上；另一起更严重，攻击者用脚本反复申请 allocation 和 peer permission，中继节点内存被 peer 表撑爆，带宽也被某个租户的转发流量吃光，正常会议的音频包开始丢。值班同学还发现，ChannelBind 自动分配的通道号在重启后重复使用，两个不同的对端抢同一个 channel，数据被投递到了错误的地址；有人用别的账号猜到了 allocation id，直接给别人的会话创建 permission 并转发数据。中继服务还需要跟着会议的生命周期做续期与清理，但现在的实现既不会在 allocation 到期时回收 peer 表，也不会在滚动重启后接着用原来的额度记录。

范围是 gsb/batch47-safety-d-08 下的 turn_allocation_guard.py，类 TurnAllocationGuard。公开接口（Python 类型注解）：`TurnAllocationGuard(*, max_allocations: int = 64, max_permissions: int = 16, max_channels: int = 16, lifetime: int = 600, max_lifetime: int = 3600, permission_ttl: int = 300, channel_ttl: int = 600, bandwidth_bytes: int = 1048576, clock=None)`；`add_user(username: str, *, realm: str) -> dict`；`allocate(*, username: str, realm: str, five_tuple, requested_lifetime=None, now=None) -> dict`；`refresh(*, username: str, allocation_id: str, lifetime=None, now=None) -> dict`；`create_permission(*, username: str, allocation_id: str, peer_ip: str, now=None) -> dict`；`bind_channel(*, username: str, allocation_id: str, peer_ip: str, channel=None, now=None) -> dict`；`relay(*, username: str, allocation_id: str, peer_ip: str, size: int, now=None) -> dict`；`release(*, username: str, allocation_id: str) -> dict`；`sweep(*, now=None) -> int`；`describe(*, username: str, allocation_id: str, now=None) -> dict`；`snapshot() -> dict`；`restore(state: dict) -> None`；`stats() -> dict`；`reset_stats() -> None`。失败统一抛 TurnError(code, detail)。

故障与边界：一、同一五元组重复申请 allocation 抛 TURN_ALLOCATION_EXISTS，活动 allocation 总数超过 max_allocations 抛 TURN_ALLOCATION_QUOTA，释放后可以重新申请且编号继续递增；二、五元组必须是三个字符串，协议只接受 udp 或 tcp，两个端点必须是点分四段 IPv4 加一至六五五三五端口，否则抛 TURN_BAD_FIVE_TUPLE；三、用户名未登记抛 TURN_UNKNOWN_USER，realm 与登记值不符抛 TURN_REALM_MISMATCH，同名用户换 realm 登记抛 TURN_USER_CONFLICT；四、requested_lifetime 缺省用 lifetime，小于六十、非整数或布尔值抛 TURN_BAD_LIFETIME，超过 max_lifetime 压到上限；五、refresh 只允许 allocation 的属主调用，他人调用抛 TURN_ALLOCATION_MISMATCH，未知编号抛 TURN_NO_ALLOCATION，已到期抛 TURN_ALLOCATION_EXPIRED，lifetime 规则同上并返回新的到期时间；六、permission 每个 allocation 最多 max_permissions 条，超限抛 TURN_PERMISSION_QUOTA，重复创建同一 peer 幂等返回 created 为 False 并刷新到期时间，peer 不是合法 IPv4 抛 TURN_BAD_PEER；七、ChannelBind 不给通道号时从 0x4000 起分配最小的空闲号，显式通道号必须在 0x4000 到 0x7FFF，否则抛 TURN_BAD_CHANNEL，同一通道号已被别的 peer 占用抛 TURN_CHANNEL_IN_USE，通道数超过 max_channels 抛 TURN_CHANNEL_QUOTA，通道绑定同时建立或刷新该 peer 的 permission，permission 配额不够时抛 TURN_PERMISSION_QUOTA；八、转发数据前必须存在未过期的 permission，否则抛 TURN_NO_PERMISSION，size 必须是正整数，否则抛 TURN_BAD_SIZE；九、每个 allocation 的累计转发字节不得超过 bandwidth_bytes，超限整包拒绝且计数不变，抛 TURN_QUOTA_EXCEEDED，成功时返回 used 与 remaining；十、release 返回释放的 permission 数、通道数和已转发字节，重复释放抛 TURN_NO_ALLOCATION；sweep 回收到期 allocation 以及过期的 permission 与 channel，返回被回收的 allocation 数；十一、describe 返回五元组、到期时间、按字典序的 peer 列表、peer 到 channel 的映射和已转发字节；十二、snapshot 必须深拷贝用户、allocation、peer 表、通道、额度用量与统计，restore 后已用的五元组不能重复申请、已转发额度不能清零，结构非法或五元组重复抛 TURN_SNAPSHOT_INVALID 且原状态不变。

硬约束：allocate、bind_channel、relay 全程持锁，八个线程抢同一五元组只能成功一个，其余全部抛 TURN_ALLOCATION_EXISTS；并发转发时累计字节绝不能超过 bandwidth_bytes，也不能出现两个请求都读到同一 used 值再各自加；sweep 与 refresh 并发时不能把刚续期的 allocation 当成过期回收；快照必须能跨进程恢复并保持剩余额度。

验收：`python3 -m pytest -q tests` 的 15 条测试全部通过；不得修改测试、跳过或按固定输入写死；并发与重启恢复场景自行验证；只做本地实现。

## 运行方式

```bash
python3 -m pytest -q tests
```

## 平台字段

- domain: 安全
- sub: 网络与通信安全
- type: Bugfix
- difficulty: 困难

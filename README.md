# VRRP 通告守卫

## 题面

上周三晚高峰，核心旁边的两台三层交换机同时对外宣称自己是同一个 VRID 的 master，下挂服务器的网关 MAC 在两张表之间来回跳，业务断了四次。抓包复盘发现问题不止一处：有台机器从非直连网段发来的通告 TTL 只有 62，照样被当成合法通告收下；抢占领没有配延迟，一台重启回来的高优先级设备每秒把 master 抢走一次；还有个分组的通告间隔和本地配置不一致，备份侧算出来的倒换定时器只有零点几秒，主设备一抖动就跟着切换。现在要求把 VRRP 通告校验、主备选举、抢占和倒换计时做成一个独立守卫模块。范围是 gsb/batch47-safety-c-09 下的 vrrp_advert_guard.py，类 VrrpAdvertGuard。

公开接口：`VrrpAdvertGuard(*, version: int = 2, default_advert_interval: int = 1, master_down_factor: int = 3, default_preempt_delay: int = 0, clock=None)`；`create_group(vrid: int, *, interface: str, vip: str, priority: int, preempt: bool = True, preempt_delay=None, advert_interval=None, auth=None) -> dict`；`receive(advert: dict, *, now=None) -> dict`；`group_state(vrid: int, *, now=None) -> dict`；`groups() -> list`；`takeover(vrid: int, *, now=None) -> dict`；`step_down(vrid: int, *, now=None) -> dict`；`set_priority(vrid: int, priority: int) -> dict`；`block_router(src: str) -> None`；`unblock_router(src: str) -> bool`；`flush(now=None) -> int`；`snapshot() -> dict`；`restore(state: dict) -> None`；`stats() -> dict`；`reset_stats() -> None`。校验失败抛 VrrpError(code, detail)。

通告字典字段为 vrid、priority、src、interval、ttl、version、auth_type、auth_data、vip_count。故障与边界：一、VRID 不在 1 到 255、VIP 不是合法 IPv4、优先级不在 1 到 254 分别抛 VRRP_INVALID_VRID、VRRP_INVALID_VIP、VRRP_PRIORITY_INVALID；同 VRID 重复建组完全一致要幂等，任一项不同抛 VRRP_CONFLICT；二、ttl 不等于 255 抛 VRRP_TTL_INVALID，通告版本与本地不一致抛 VRRP_VERSION_MISMATCH，VRID 未建组抛 VRRP_UNKNOWN_VRID；三、src 不是合法 IPv4 抛 VRRP_INVALID_SOURCE，命中 block_router 名单抛 VRRP_SOURCE_BLOCKED，unblock 返回是否真的移除；四、通告间隔必须等于分组配置，否则抛 VRRP_INTERVAL_MISMATCH；分组使用 simple 认证时 auth_type 与 auth_data 必须完全匹配，none 分组收到带认证或非空 auth_data 的通告同样抛 VRRP_AUTH_MISMATCH；五、分组还没有 master 时，第一条合法通告把发送方记为 master，状态保持 backup，master_down_remaining 等于 master_down_factor 乘通告间隔；六、同一 master 重复通告返回 refreshed 并重置倒换计时，master_down_remaining 回到满值；七、更高优先级的来源替换 master 返回 accepted，相同优先级比较源地址、地址更大的胜出，输的一方返回 ignored 且 code 为 VRRP_TIE_LOST；八、优先级更低的来源返回 ignored、code 为 VRRP_LOWER_PRIORITY，master 不变；九、priority 为 0 表示主动退位，来自当前 master 时返回 stopped 并清空 master，来自其他来源返回 ignored、code 为 VRRP_PRIORITY_ZERO；十、分组开启抢占领且本地优先级更高时，只有经过 preempt_delay 秒才在收到通告后返回 takeover 并转成 master，关闭抢占或没到时间都保持 backup；十一、备份侧连续 master_down_factor 个通告间隔收不到 master 通告，flush 或状态查询要把它提升为 master，恰好到期算到期，master_down_remaining 归零；十二、takeover 立即本地升主，step_down 退回 backup 并清空 master，set_priority 只改本地优先级；十三、snapshot 深拷贝分组、master 时刻、黑名单和统计，restore 结构非法抛 VRRP_SNAPSHOT_INVALID 且原状态不动。

硬约束：receive、group_state、flush、takeover 全程持锁，八个线程同时收到同一 master 的合法通告，每条都记 refreshed，计数不能丢也不能重复提升；快照跨进程恢复后倒换计时仍然有效；每个 VRID 只有一份状态，黑名单与统计不允许无界增长；stats 精确区分 accepted、refreshed、ignored、rejected、promotions，reset_stats 归零。

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

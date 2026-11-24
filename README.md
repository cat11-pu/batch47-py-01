# IGMP 侦听成员守卫

## 题面

园区视频监控上线两周，接入交换机上的组播流量一直按广播处理，一个探头的码流被复制到全楼所有端口，接入带宽被打满；运维手工关掉几个端口之后，又有终端抱怨加入某个频道后收不到画面，因为组内另一个成员发出 leave 报文，表项被立刻删掉，共享链路上的其他接收者被一起断流。再往后是表项容量问题：一台被扫描器盯上的交换机在十分钟内被塞了两万多个组地址，侦听表直接把内存吃满，重启后表项全丢，组播又退化成泛洪。另外，配置里没有地方限制单个端口能加入多少个组，扫描器换个组地址就能继续灌。现在要求把 IGMP 侦听的成员管理、VLAN 隔离、路由端口和老化收拢成一个守卫模块。范围是 gsb/batch47-safety-c-10 下的 igmp_snoop_guard.py，类 IgmpSnoopGuard。

公开接口：`IgmpSnoopGuard(*, group_timeout: int = 260, leave_window: int = 2, max_groups_per_port: int = 32, max_groups_per_vlan: int = 256, max_vlans: int = 64, clock=None)`；`add_vlan(vlan: int, *, querier: str | None = None, fast_leave: bool = False) -> dict`；`set_querier(vlan: int, addr: str, *, now=None) -> dict`；`mark_router_port(vlan: int, port: str, *, router: bool = True) -> dict`；`join(vlan: int, port: str, group: str, *, version: int = 2, source=None, now=None) -> dict`；`leave(vlan: int, port: str, group: str, *, source=None, now=None) -> dict`；`members(vlan: int, group: str) -> list`；`flood_targets(vlan: int, group: str, *, now=None) -> list`；`deny_group(vlan: int, group: str) -> dict`；`allow_group(vlan: int, group: str) -> bool`；`expire(now=None) -> int`；`snapshot() -> dict`；`restore(state: dict) -> None`；`stats() -> dict`；`reset_stats() -> None`。校验失败抛 IgmpError(code, detail)。

故障与边界：一、VLAN 不在 1 到 4094 抛 IGMP_INVALID_VLAN，超过 max_vlans 抛 IGMP_VLAN_LIMIT，未登记 VLAN 抛 IGMP_UNKNOWN_VLAN，同 VLAN 重复登记配置一致要幂等、任一配置不同抛 IGMP_CONFLICT；二、组地址必须是 IPv4 组播，10.0.0.5 或 IPv6 组地址抛 IGMP_INVALID_GROUP；三、VLAN 没有 querier 时 join 抛 IGMP_NO_QUERIER，set_querier 的地址非法抛 IGMP_INVALID_QUERIER；四、version 只接受 1、2、3，否则抛 IGMP_INVALID_VERSION；v1、v2 带 source 抛 IGMP_SOURCE_NOT_ALLOWED，v3 的 source 非法抛 IGMP_INVALID_SOURCE，端口名为空抛 IGMP_INVALID_PORT；五、v2 首次 join 返回 joined 和到期时刻，重复 join 返回 refreshed 并顺延到期，members 里始终只有一条，字段包含端口、版本、sources、last_seen、leaving；六、同一端口组数达到 max_groups_per_port 抛 IGMP_GROUP_LIMIT，同一 VLAN 不同组数达到 max_groups_per_vlan 抛 IGMP_VLAN_GROUP_LIMIT，失败不占表项；七、deny_group 之后该组 join 抛 IGMP_GROUP_DENIED，allow_group 返回是否真的解除；八、VLAN 开启 fast_leave 时 leave 立即删除并返回 removed，未开启时返回 pending 和截止时刻，期间 flooding 目标不再包含该端口，截止时刻到达后由 expire 删除；九、leave 不存在的成员抛 IGMP_NOT_MEMBER，重复 leave 抛 IGMP_ALREADY_LEAVING；十、v3 源特定成员按 source 增删，新增源返回 joined_source，删掉部分源返回 source_left，最后一个源删掉才移除成员；对不带源的表项按源 leave 抛 IGMP_SOURCE_NOT_TRACKED，版本不符抛 IGMP_VERSION_CONFLICT；十一、成员最后活跃时间加 group_timeout 小于等于 now 时由 expire 删除，恰好到期算到期，pending leave 到期计入 leave 而不是老化；十二、路由端口无条件出现在 flood_targets 里，与其他成员端口合并后按名字排序，router 参数不是布尔抛 IGMP_INVALID_ROUTER；十三、snapshot 深拷贝 VLAN 配置、成员、sources、pending leave、路由端口、黑名单和统计，restore 结构非法抛 IGMP_SNAPSHOT_INVALID 且原状态不动。

硬约束：join、leave、expire、restore 全程持锁，八个线程同时对同一端口同一组发 join，只能有一次 joined，其余全是 refreshed，members 只有一条；快照与运行状态彻底分离；VLAN 数、每端口组数、每 VLAN 组数都有硬上限；stats 精确区分 joined、refreshed、left、expired、rejected，reset_stats 归零。

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

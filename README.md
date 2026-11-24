# LLDP 邻居库守卫

## 题面

接入机房的同事这周报了两起事故：一台接入交换机下面的语音终端突然从邻居表里消失，电话批量掉线；同一时间监控看到某个端口的邻居条目十分钟翻了六倍，面板里塞满伪造的 chassis id，真正的核心交换机反而挤不进去。还有一处更麻烦，同一台交换机的 chassis id 从两个不同端口同时冒出来，拓扑图把这条链路画成了环路，生成树跟着抖。值班同事还提到，被隔离的端口没有人手工放开，第二天这条链路仍然不通，排障只能靠人肉翻日志；端口策略也没留痕，事后说不清是谁改的。我们决定把 LLDP 邻居库拆成一个独立的守卫模块，端口的信任策略、邻居登记、老化、隔离和快照都收在这里。范围是 gsb/batch47-safety-c-06 下的 lldp_neighbor_guard.py，类 LldpNeighborGuard。

公开接口：`LldpNeighborGuard(*, ttl_scale: int = 1, max_neighbors: int = 64, max_age: int = 120, max_ports: int = 64, clock=None)`；`configure_port(port: str, *, trust: str = "untrusted", max_neighbors=None, allowed_system_names=None, require_capability=None, allowed_mgmt_networks=None) -> dict`；`receive(port: str, frame: dict, *, now=None) -> dict`；`neighbors(port: str) -> list`；`lookup(port: str, chassis_id: str) -> dict | None`；`quarantine(port: str, *, reason: str) -> dict`；`release(port: str) -> dict`；`is_quarantined(port: str) -> bool`；`expire(now=None) -> int`；`snapshot() -> dict`；`restore(state: dict) -> None`；`stats() -> dict`；`reset_stats() -> None`。校验失败统一抛 LldpError(code, detail)，code 用下面这些字符串。

故障与边界：一、未登记端口收到报文抛 LLDP_UNKNOWN_PORT，同一端口用完全相同的策略重复配置必须幂等返回同一份字典，任一策略项不同抛 LLDP_CONFLICT；二、untrusted 端口只接受 allowed_system_names 里的 system_name，其余抛 LLDP_UNTRUSTED_PEER，端口没配允许名单时视为全部拒绝；三、端口声明 require_capability 时，报文 capabilities 里不含该项抛 LLDP_CAPABILITY_MISMATCH；四、mgmt_addr 不是合法 IP 字面量抛 LLDP_MGMT_ADDR_INVALID，配了 allowed_mgmt_networks 却不在任何网段内抛 LLDP_MGMT_ADDR_DENIED；五、ttl 必须是 1 到 65535 的整数，0 或字符串抛 LLDP_TTL_INVALID，过期时间按 now 加 ttl 乘 ttl_scale 计算；六、过期时间还要被 max_age 截断，超长 ttl 不能把条目留过 max_age 秒；七、同一端口同一 chassis 重复上报返回 updated 并刷新条目，不新增第二条，首次登记返回 accepted；八、每端口邻居数以策略值优先、否则用全局 max_neighbors，新增 chassis 超限抛 LLDP_NEIGHBOR_LIMIT，已有 chassis 刷新不受限；九、同一 chassis 出现在第二个端口抛 LLDP_CHASSIS_CONFLICT，且不得写进第二个端口；十、隔离端口后续 receive 抛 LLDP_PORT_QUARANTINED，已登记邻居保留，release 后恢复接收，重复隔离或重复释放都要幂等；十一、expire 删除到期条目并返回条数，恰好等于到期时刻算到期；十二、snapshot 必须深拷贝，能恢复端口策略、邻居、隔离状态和统计，恢复后已登记邻居继续刷新，结构非法抛 LLDP_SNAPSHOT_INVALID 且原状态一条不动。

硬约束：receive、expire 全程持锁，八个线程同时上报同一 chassis 只能有一次 accepted，其余全是 updated，邻居表只留一条；快照与运行状态彻底分离，恢复前后写入互不影响；邻居数、端口数都必须有上限，不允许无界增长；stats 精确区分 received、accepted、updated、expired、rejected，reset_stats 全部归零。

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

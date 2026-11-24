# 用户命名空间 ID 映射与从属额度守卫

## 题面

容器平台开放用户命名空间自助申请后，隔离问题集中冒出来。研发把一个 uid_map 写成两段互相重叠的区间，容器里的 root 映射到了宿主机两个不同用户；有租户在同一个命名空间里第二次写 uid_map，进程看到的身份被整体改写；子命名空间把范围映射到父命名空间没覆盖的 ID 上，创建容器成功但文件属主全部变成 nobody；gid_map 在 setgroups 仍是 allow 时就写进去，容器内进程可以保留额外组；某个租户申请的从属 ID 额度是 100 个，实际却映射出 120 个；多层嵌套命名空间下，运维查不到某个容器 ID 最终对应宿主机的哪个 uid；宿主重启后映射表和已用额度全部丢失，重新建命名空间时把同一段宿主 ID 又分配了一次；两个调度器同时给同一个命名空间写映射，后写的把先写的覆盖；删除父命名空间后子命名空间还挂在树上；统计里看不出有多少次 ID 落到溢出值。相关代码在 gsb/batch47-safety-e-02 的 userns_id_map_guard.py，类 UsernsIdMapGuard，只用 Python 标准库，失败统一抛 UsernsError(code, detail)。

接口保持：`__init__(self, *, overflow_uid: int = 65534, overflow_gid: int = 65534, max_extents: int = 5, max_depth: int = 8)`；`create_namespace(self, ns_id: str, *, parent: str | None = None, owner: str | None = None) -> dict`；`write_uid_map(self, ns_id: str, lines) -> dict`；`write_gid_map(self, ns_id: str, lines) -> dict`；`setgroups(self, ns_id: str, value: str) -> str`；`map_uid(self, ns_id: str, uid: int, *, direction: str = "out") -> int`；`map_gid(self, ns_id: str, gid: int, *, direction: str = "out") -> int`；`mapping_of(self, ns_id: str) -> dict`；`compose(self, ns_id: str) -> dict`；`grant_subid(self, owner: str, *, start: int, count: int, kind: str = "uid") -> dict`；`subid_remaining(self, owner: str, *, kind: str = "uid") -> int`；`destroy_namespace(self, ns_id: str) -> bool`；`stats(self) -> dict`；`snapshot(self) -> dict`；`restore(self, state: dict) -> None`。

故障与边界：一、命名空间 ID 只接受小写字母数字开头、最长 32 位，非法抛 USERNS_BAD_ID，重复创建抛 USERNS_DUPLICATE，父不存在抛 USERNS_UNKNOWN_PARENT；二、映射行必须是“内部起始 外部起始 数量”三个十进制整数，数量至少为 1，格式错误一律抛 USERNS_MAP_SYNTAX；三、单次最多 max_extents 段，超出抛 USERNS_MAP_TOO_MANY；内部或外部区间越过 2^32 抛 USERNS_MAP_RANGE；四、内部区间互相重叠、外部区间互相重叠都抛 USERNS_MAP_OVERLAP；五、uid_map 与 gid_map 各自只能写一次，第二次抛 USERNS_MAP_LOCKED，失败的写入不占用次数；六、子命名空间的外部区间必须完整落在父命名空间的内部区间内，否则抛 USERNS_MAP_OUTSIDE_PARENT；七、写 gid_map 前 setgroups 必须为 deny，否则抛 USERNS_SETGROUPS_REQUIRED；deny 之后不允许改回 allow，抛 USERNS_SETGROUPS_LOCKED；非法取值抛 USERNS_SETGROUPS_VALUE；八、映射方向只接受 out 与 in，其他抛 USERNS_BAD_DIRECTION，ID 为负或超过 2^32-1 抛 USERNS_BAD_ID_VALUE，映射未写入就查询抛 USERNS_MAP_MISSING，map_uid 与 map_gid 只换算到父命名空间，沿父链算到初始命名空间由 compose 负责；九、未命中的 ID 返回 overflow_uid 或 overflow_gid，并计入溢出次数；十、有 owner 的命名空间，其外部区间必须落在该 owner 已授予的从属 ID 额度内，超出抛 USERNS_SUBID_EXCEEDED，额度区间互相重叠抛 USERNS_SUBID_CONFLICT，uid 与 gid 的额度分别授予、分别核算；十一、删除有子节点的命名空间抛 USERNS_HAS_CHILDREN，删除后查询抛 USERNS_UNKNOWN_NS；十二、compose 沿父链把内部 ID 逐层换算到初始命名空间；snapshot/restore 覆盖命名空间树、两份映射、setgroups 状态、额度与统计，结构非法抛 USERNS_SNAPSHOT_INVALID 且原状态不变。

硬约束：所有写入与查询持同一把锁，多个线程同时写同一个 uid_map 只能有一个成功，其余全部抛锁错误；快照深拷贝，恢复后能继续写新命名空间并正确计算剩余额度；额度与统计不得因失败写入而漂移；父子链深度受 max_depth 限制。

验收：`python3 -m pytest -q tests` 的 15 条测试全部通过；不得修改测试、跳过或按固定输入写死；并发与重启恢复场景自行验证；只做本地实现。

## 运行方式

```bash
python3 -m pytest -q tests
```

## 平台字段

- domain: 安全
- sub: 系统安全
- type: Bugfix
- difficulty: 地狱

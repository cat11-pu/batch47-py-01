# 需求说明

## 题面

上周三 A 集群的一批构建容器突然全部起不来，报错都指向 user namespace 的 UID 映射：两个租户的容器被分到了同一段宿主机 UID，谁先启动谁正常，后启动的直接失败；运维手工补了一段映射之后，重启节点又发现映射表里出现了三段互相重叠的记录，容器里的进程全变成了 nobody。更糟的是，有租户把容器内 UID 0 映射到宿主机 100000，另一租户的映射正好从 100000 开始，前者删掉了后者的文件，宿主机审计里只留下一个陌生 UID。值班同学先手工清空了映射表，正在运行的容器随即全部失联，业务方要求当天给出可重复分配、重启后还能核对归属的方案。这次要动的是 gsb/batch47-safety-e-06 下的 userns_idmap_guard.py，类 UsernsIdmapGuard，负责用户命名空间的 UID 区间分配、重叠检测、容器内 ID 到宿主机 ID 的换算以及映射表快照。公开接口：`UsernsIdmapGuard(*, host_id_min: int = 100000, host_id_max: int = 999999, max_mappings: int = 64, max_count: int = 65536)`；`reserve(namespace: str, *, container_start: int, container_count: int, host_start: int | None = None, owner: str = "") -> dict`；`release(namespace: str) -> dict`；`assign(namespace: str, *, uid: int) -> int`；`lookup(host_id: int) -> dict | None`；`mappings() -> list`；`occupancy() -> dict`；`is_reserved(namespace: str) -> bool`；`snapshot() -> dict`；`restore(state: dict) -> None`；`stats() -> dict`；`reset_stats() -> None`。失败统一抛 IdmapError(code, detail)，code 用下面列出的字符串。故障与边界：一、namespace 为空或不是字符串抛 IDMAP_INVALID_NAMESPACE；二、container_count 小于 1、container_start 为负或区间末端超过 4294967294 抛 IDMAP_RANGE_INVALID，单段数量超过 max_count 抛 IDMAP_COUNT_TOO_LARGE；三、显式 host_start 越出宿主区间或 host_end 超过 host_id_max 抛 IDMAP_HOST_RANGE，未给 host_start 时按首次适配从 host_id_min 找第一段足够大的空位，找不到抛 IDMAP_NO_SPACE；四、不同 namespace 的宿主区间有任何重叠抛 IDMAP_OVERLAP，这次分配不得写入；五、同一 namespace 重复提交完全相同的参数幂等返回且不占新槽位，参数不同抛 IDMAP_CONFLICT；六、映射数量达到 max_mappings 时新 namespace 抛 IDMAP_LIMIT，幂等重复不受此限；七、assign 对未登记 namespace 抛 IDMAP_UNKNOWN_NAMESPACE，uid 不在容器区间抛 IDMAP_UID_OUT_OF_RANGE，合法时返回宿主起点加偏移；八、lookup 对落空或超出宿主全局区间的 ID 返回 None，命中时返回 namespace 与 container_id；九、release 返回释放的宿主起点与数量，重复释放抛 IDMAP_UNKNOWN_NAMESPACE，释放出的空位能被下一次首次适配复用；十、mappings 按 host_start 升序，occupancy 给出 used、free、mappings 三个计数；十一、快照必须深拷贝，恢复后映射与统计原样回来，结构非法抛 IDMAP_SNAPSHOT_INVALID 且原状态不变；十二、stats 分开记 reserved、idempotent、released、assigned、rejected，reset_stats 全部归零。硬约束：reserve、release 与 assign 全程持同一把锁，多线程同时登记同一 namespace 只能产生一条映射，其余按幂等返回；两个线程抢同一段宿主区间只能有一个成功；宿主区间容量固定，任何路径都不能突破 max_mappings 与区间上限；每条映射都要能反查到租户与写入来源，审计抽查时不能出现无主区间。验收：`python3 -m pytest -q tests` 的 15 条测试全部通过；不得修改测试、跳过或针对固定输入写死；并发与重启恢复场景自行验证；只做本地实现，不涉及宿主机上的真实内核操作。

## 运行方式

```bash
python3 -m pytest -q tests
```

## 平台字段

- domain: 安全
- sub: 系统安全
- type: Bugfix
- difficulty: 地狱

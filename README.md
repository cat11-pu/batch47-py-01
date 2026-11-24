# 任务说明

## 题面

跨服大战场加入断线重连后，回流玩家的战场状态频繁错乱：明明已经掉线的旗子还在计分，重新连上后增益消失、冷却归零，偶尔还会出现整份旧快照覆盖新状态。运维把日志翻出来发现，服务器每次提交的增量没有连续序号，客户端拿着更旧的序号请求补齐时，服务端直接推一份全量，几十面旗和上百个单位一起下发，弱网客户端当场卡死；有时客户端序号比服务端还大，接口却照样返回成功；进程滚动重启后，日志从中间截断，重连玩家拿到的增量缺少前面几笔，落地的状态和服务器永久分叉。另有玩家改本地包重放旧的提交，重复的键值被算了两遍，战场积分的对账每天都对不上。

范围是 gsb/batch47-game-c-02 下的 delta_state_resync.py，类 DeltaStateResync，负责权威键值状态、提交日志、增量补齐与快照。公开接口：`__init__(*, max_journal: int = 256, max_keys: int = 10000, max_patch_ops: int = 64)`；`set_initial(state: dict) -> dict`；`commit(changes: dict, *, now_ms=None) -> dict`；`remove_keys(keys, *, now_ms=None) -> dict`；`resync(*, client_seq: int, max_ops=None) -> dict`；`get(key: str)`；`state() -> dict`；`checksum() -> str`；`journal_span() -> dict`；`compact(keep_last: int) -> int`；`snapshot() -> dict`；`restore(state: dict) -> None`；`stats() -> dict`；`reset_stats() -> None`。失败统一抛 ResyncError(code, detail)，code 为字符串。

故障与边界：一、初始状态只能安装一次，重复安装抛 RESYNC_ALREADY_INITIALIZED，键为空串、值为列表或 None、超过 max_keys 分别抛 RESYNC_BAD_STATE 与 RESYNC_KEY_LIMIT；二、commit 里值为 None 表示墓碑删除，只把真正改变或删除的键计入 applied，全为空操作的提交仍然占用一个 seq；三、单次提交键数超过 max_patch_ops 抛 RESYNC_PATCH_TOO_LARGE，提交后键数超过 max_keys 抛 RESYNC_KEY_LIMIT，两种失败都不能消耗 seq，也不能改动任何键；四、日志最多保留 max_journal 条，超出时丢弃最旧的条目并计入 journal_evicted；五、client_seq 落在日志覆盖范围内时返回 mode=delta，只带该序号之后发生变化键的最终值，按 key 升序，删除用 {"op":"del"}，新增和修改用 {"op":"set"}；六、client_seq 早于日志最旧条目减一，或本次增量键数超过 max_ops（缺省取 max_patch_ops）时返回 mode=full，base_seq 为 0，ops 覆盖当前所有键；七、client_seq 等于当前 seq 返回 mode=noop，大于当前 seq 抛 RESYNC_FUTURE_SEQ，负数抛 RESYNC_BAD_SEQ；八、resync 是只读操作，反复调用同一序号必须返回完全相同的 ops 与 checksum；九、compact(keep_last) 只留最后若干条日志并返回丢弃条数，keep_last 为 0 时日志清空、oldest_seq 为 None，负数抛 RESYNC_BAD_COMPACT；十、remove_keys 对不存在的键静默跳过，空列表或非字符串键抛 RESYNC_BAD_PATCH；十一、checksum 对当前状态做规范化编码后取 12 位小写十六进制，键的插入顺序不能影响结果，值变化必须改变结果；十二、snapshot 返回 version=1 的深拷贝，覆盖 initialized、seq、state、journal 与 stats；restore 对版本、类型、负数计数、缺失字段一律抛 RESYNC_SNAPSHOT_INVALID 且原状态、序号、日志完全不变，恢复后从旧 seq 补齐仍要按恢复时的日志计算。

硬约束：commit、compact、restore 必须整段持锁，八个线程同时提交不同键时最终 seq 必须正好是 8，不能有两条提交拿到同一个序号，也不能丢键；增量与全量补齐都要受 max_ops 约束，禁止一次性把无界状态推给客户端；快照必须与内部对象完全隔离，恢复后的日志与统计要能继续支撑补齐。验收：`python3 -m pytest -q tests` 的 15 条全部通过；不得修改测试、跳过或按固定输入写死；并发与重启恢复场景自行验证；只做本地实现。

## 运行方式

```bash
python3 -m pytest -q tests
```

## 平台字段

- domain: 游戏、图形与多媒体
- sub: 游戏开发
- type: Bugfix
- difficulty: 地狱

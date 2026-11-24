# 任务说明

## 题面

竞技场赛季末的举报量突然翻了四倍，客服整理后发现同一批账号的技能释放间隔整齐得像节拍器：每次都在 100 毫秒上下，连续几十次没有一次抖动，人类手速根本做不到。反外挂组想用现有埋点复现，却发现采集到的输入事件按会话分散在各台机器上，没有统一的会话概念；同一会话的乱序补包会把时间轴打乱后照样入库；一名玩家同时开着多个会话时，分数各算各的，封禁名单里查不到最高分；处罚到期后仍显示封禁中，人工解封又没有审计计数；服务重启后会话、证据和处罚全部丢失，申诉时拿不出当时的判定依据。更糟的是长局玩家每秒上百次输入，内存一路涨到进程被杀，而短样本只有三五次点击的会话也会被误判成宏。

范围是 gsb/batch47-game-c-03 下的 input_macro_forensics.py，类 InputMacroForensics，负责输入会话、事件采集、节奏评分、玩家处罚与快照。公开接口：`__init__(*, window_ms: int = 60000, min_samples: int = 12, jitter_threshold_ms: int = 6, repeat_threshold: float = 0.85, penalty_ttl_ms: int = 300000, max_sessions: int = 500, max_events: int = 1024, batch_limit: int = 64, clock=None)`；`open_session(session_id: str, *, player_id: str, device_class: str = "pc") -> dict`；`close_session(session_id: str, *, now_ms: int) -> dict`；`ingest(session_id: str, events, *, now_ms: int) -> dict`；`session_report(session_id: str) -> dict`；`flag_player(player_id: str, *, reason: str, now_ms: int, ttl_ms=None) -> dict`；`is_penalized(player_id: str, *, now_ms: int) -> bool`；`clear_penalty(player_id: str) -> bool`；`player_summary(player_id: str, *, now_ms: int) -> dict`；`top_suspects(limit: int = 5) -> list`；`purge(now_ms: int) -> dict`；`snapshot() -> dict`；`restore(state: dict) -> None`；`stats() -> dict`；`reset_stats() -> None`。失败统一抛 ForensicsError(code, detail)。

故障与边界：一、session_id 相同且 player_id、device_class 相同的重复打开幂等返回 created=False，玩家不同的重名会话抛 FORENSICS_SESSION_CONFLICT，超过 max_sessions 抛 FORENSICS_SESSION_LIMIT；二、同一会话内 at_ms 必须非递减，等于上一条允许，整批里出现回退抛 FORENSICS_OUT_OF_ORDER，这一批一条都不能入库；三、单批事件数超过 batch_limit 抛 FORENSICS_BATCH_TOO_LARGE，action 为空、at_ms 缺失、为负或非整数抛 FORENSICS_BAD_EVENT，同样整批回滚；四、单会话最多保留 max_events 条，超出时丢最旧的并在返回值和统计里报告 dropped；五、评分只看最后一条事件往前 window_ms 的窗口，窗口内同类事件不足 min_samples 条时该动作 points 记 0；六、每个动作按相邻事件间隔计算均值、总体标准差与出现次数最多的间隔占比，标准差不超过 jitter_threshold_ms 记 metronomic，占比不低于 repeat_threshold 记 repetitive；七、两项同时命中的动作记 70 分，只命中 metronomic 记 45 分，只命中 repetitive 记 30 分，总分封顶 100，70 分及以上判 macro、40 到 69 判 suspicious、其余判 clean；八、已关闭会话拒绝继续 ingest，抛 FORENSICS_SESSION_CLOSED，但报告仍可读取；九、flag_player 的到期时间取 now_ms 加 ttl 与已有到期时间的较大值，已有处罚仍有效时 extended=True，is_penalized 在 now_ms 等于到期时间时返回 False，空玩家或空原因抛 FORENSICS_BAD_PENALTY；十、top_suspects 按玩家取各会话最高分，分数降序、同分玩家 ID 升序，limit 小于 1 抛 FORENSICS_BAD_LIMIT；十一、purge 删除关闭时间早于 now_ms 减两倍 window_ms 的会话和已到期处罚，返回 {"sessions": 数量, "penalties": 数量}，未关闭会话永不清理；十二、snapshot 返回 version=1 的深拷贝，覆盖 sessions、penalties 与 stats，restore 遇到版本、类型或负数计数问题抛 FORENSICS_SNAPSHOT_INVALID 且原数据完全不变。

硬约束：open_session、ingest、close_session、flag_player、purge、restore 必须整段持锁，八个线程同时打开同一个会话只能创建一次，返回里 created=True 恰好一条；每会话事件数、会话数、单批大小全部有硬上限，禁止无界增长；快照必须与内部对象隔离，恢复后继续采集不能污染快照。验收：`python3 -m pytest -q tests` 的 15 条全部通过；不得修改测试、跳过或按固定输入写死；并发与重启恢复场景自行验证；只做本地实现。

## 运行方式

```bash
python3 -m pytest -q tests
```

## 平台字段

- domain: 游戏、图形与多媒体
- sub: 游戏开发
- type: Bugfix
- difficulty: 困难

# 任务题面

## 题面

公会联盟系统在跨服联赛前两周集中爆雷。三家谈好的互不侵犯协定，投票没结束就被标成生效，两家没投票的公会直接出现在盟友列表；另一个协定只有一家赞成，系统按一票通过，联赛被背刺后运营才发现。同一协定重复提交四次，面板出现四份同名条约，容量额度瞬间占满。有公会同时挂五份协定，界面写最多三份；解散后再结盟没冷却，上午撕约下午就能重签。投票权重也不对：十级公会和五十级公会算同一权重；有人把权重填负数或零，票数照算。协定到期仍显示进行中，投票接口不关。解散通知期形同虚设，点完立即生效，对方没有备战时间。重启后投票记录、协定状态和解散冷却全部丢失，已生效条约变回待投票。还有反馈说，两家之间已有生效协定，又能再提一份。

范围是 gsb/batch47-game-b-05 下的 alliance_pact_guard.py，类 AlliancePact，负责公会登记、协定提案、加权投票、计票生效、解散通知与冷却清扫。公开接口：`AlliancePact(*, vote_window=3600, break_notice=600, reform_cooldown=1800, max_pacts_per_guild=3, quorum_ratio=0.5)`；`register_guild(guild_id, *, level, members) -> dict`；`propose(proposer, *, pact_id, kind, partners, now=None) -> dict`；`vote(guild_id, pact_id, *, approve, weight=None, now=None) -> dict`；`tally(pact_id, *, now=None) -> dict`；`finalize(pact_id, *, now=None) -> dict`；`break_pact(pact_id, *, guild_id, now=None) -> dict`；`sweep(*, now=None) -> list`；`status(pact_id) -> dict`；`pacts_of(guild_id) -> list`；`snapshot() -> dict`；`restore(state) -> None`；`stats() -> dict`；`reset_stats() -> None`。失败统一抛 PactError(code, detail)。

故障与边界：一、公会等级与人数必须是正整数，同一公会同等级同人数重复登记幂等返回，任一项不同抛 PACT_GUILD_CONFLICT，非法参数抛 PACT_INVALID_GUILD；二、提案类型只接受 non_aggression、trade、mutual_defense，其他抛 PACT_UNKNOWN_KIND，成员列表重复抛 PACT_DUPLICATE_PARTNER，提案人不在名单抛 PACT_PROPOSER_MISSING，少于两家抛 PACT_TOO_FEW_PARTNERS；三、任一参与公会未登记抛 PACT_UNKNOWN_GUILD，pact_id 已存在抛 PACT_DUPLICATE，任意两家之间已有待投票、生效中或解散中的协定抛 PACT_ALREADY_EXISTS；四、每家公会同时占用的协定数达到 max_pacts_per_guild 抛 PACT_CAPACITY，已解散的协定不占额度；五、已解散协定的两家公会在 reform_cooldown 内再次提案抛 PACT_COOLDOWN，冷却终点时刻允许；六、投票只允许参与公会，第三方抛 PACT_NOT_PARTNER，未知公会抛 PACT_UNKNOWN_GUILD，未知协定抛 PACT_UNKNOWN_PACT；七、同一公会对同一协定只能投一次，重复抛 PACT_ALREADY_VOTED，投到 deadline 边界及以后抛 PACT_VOTE_CLOSED；八、approve 必须是布尔值，否则抛 PACT_INVALID_VOTE；weight 缺省取公会等级，显式传入时必须是 1 到该公会等级的整数，否则抛 PACT_INVALID_WEIGHT；九、finalize 在投票未齐且未到截止时间时抛 PACT_VOTE_OPEN，到截止时间后按参与票数计算法定人数，不足 quorum_ratio 判 rejected 且 reason 为 quorum，权重赞成不超过反对判 rejected 且 reason 为 majority，权重赞成严格更多才 active；十、已 finalize 的协定再次 finalize 或投票抛 PACT_ALREADY_FINALIZED；十一、break_pact 只对 active 协定生效，非参与方抛 PACT_NOT_PARTNER，非 active 抛 PACT_NOT_ACTIVE，成功后状态为 breaking 并给出 effective_at 等于 now 加 break_notice；十二、sweep 只把 effective_at 不晚于 now 的 breaking 协定置为 broken，按生效时间排序返回，重复调用不得二次结算，stats 恰好包含 proposed、finalized、activated、rejected、broken、votes 六个累计值，reset_stats 归零。

硬约束：提案、投票、计票、解散、清扫共用一把可重入锁，八个线程同时对同一份已集齐票的协定执行 finalize，只能有一个成功并把状态改为 active，其余全部按 PACT_ALREADY_FINALIZED 拒绝；snapshot 必须深拷贝公会、协定、投票明细、解散冷却与统计，跨进程恢复后待投票协定仍能继续投票、生效协定仍可解散、已解散的冷却继续生效，已关闭协定不得回到 pending；单公会协定数、投票窗口、解散通知期、重组冷却四个边界都必须拒绝越界而不是截断；restore 遇到版本不符、公会或协定字段类型错误、投票权重超出公会等级、协定状态与时间戳不匹配、冷却键引用未登记公会时抛 PACT_SNAPSHOT_INVALID，原状态保持不变。时间参数接受带时区的 ISO 8601 字符串，缺省取当前 UTC 时间。

验收：`python3 -m pytest -q tests` 的 15 条测试全部通过；不得修改测试、跳过或按固定输入写死；并发与重启恢复场景自行验证；只做本地实现。

## 运行方式

```bash
python3 -m pytest -q tests
```

## 平台字段

- domain: 游戏、图形与多媒体
- sub: 游戏开发
- type: Bugfix
- difficulty: 地狱

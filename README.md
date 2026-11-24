# 任务题面

## 题面

师徒系统开放跨服拜师后，客服投诉比平时多了四倍。有导师同时带六个徒弟，面板只显示三个，其余照常领日常奖励；同一个徒弟被两名导师同时收下，出师时两边都发了称号。有人向已出师的账号发拜师申请，申请一直挂在列表里，二十四小时后仍能接受；也有人过期后重发，旧申请没清掉，新申请提示重复。教学点按导师累计，海外导师凌晨带课，点数算进前一天，第二天中午就报当日上限。课程进度也乱：同一节课记两次，进度直接翻倍；一节课点数填 0 或一百五，面板照样收下。解除关系后双方还能立刻拜师，冷却形同虚设；有人被解除关系后，历史导师仍显示在线，重新拜师提示已绑定。维护重启后，进行中的关系、待处理申请和当日点数全部清零，玩家拿截图要求补发。

范围是 gsb/batch47-game-b-03 下的 mentorship_bond_guard.py，类 MentorshipBond，负责玩家档案、拜师申请、接受与拒绝、课程进度、出师、解除关系和状态快照。公开接口：`MentorshipBond(*, mentor_level_min=40, apprentice_level_max=30, graduate_level=41, max_apprentices=3, request_ttl=86400, cooldown_seconds=86400, daily_point_cap=200, required_lessons=3)`；`register_profile(player_id, *, level, server) -> dict`；`update_level(player_id, level) -> dict`；`profile(player_id) -> dict`；`apply(mentor_id, apprentice_id, *, now=None) -> dict`；`accept(bond_id, *, apprentice_id, now=None) -> dict`；`decline(bond_id, *, player_id, now=None) -> dict`；`pending_for(player_id, *, now=None) -> list`；`purge(*, now=None) -> int`；`bond_of(player_id) -> dict | None`；`progress(bond_id, *, lesson, points, now=None) -> dict`；`graduate(bond_id, *, now=None) -> dict`；`dissolve(bond_id, *, actor, now=None) -> dict`；`snapshot() -> dict`；`restore(state) -> None`；`stats() -> dict`；`reset_stats() -> None`。失败统一抛 MentorshipError(code, detail)。

故障与边界：一、档案等级必须是正整数、服务器不能为空，重复登记同一等级幂等返回，等级不同抛 MENTOR_PROFILE_CONFLICT，换服抛 MENTOR_SERVER_IMMUTABLE，改等级只走 update_level；二、apply 对未登记玩家抛 MENTOR_UNKNOWN_PLAYER，自己拜自己抛 MENTOR_SELF_PAIR，跨服抛 MENTOR_SERVER_MISMATCH，导师低于 mentor_level_min 或徒弟高于 apprentice_level_max 抛 MENTOR_LEVEL_INELIGIBLE；三、导师在冷却期内、徒弟在冷却期内都抛 MENTOR_COOLDOWN，导师在带徒弟数达到 max_apprentices 先抛 MENTOR_CAPACITY，再判断已绑定关系抛 MENTOR_ALREADY_BOUND，任一方已有待处理申请抛 MENTOR_PENDING_EXISTS；四、申请自创建起等待达到 request_ttl 秒即过期，accept 在边界时刻抛 MENTOR_REQUEST_EXPIRED 并计入 expired，过期申请不能再被接受；五、accept 只允许申请里的徒弟本人操作，其他人抛 MENTOR_NOT_APPRENTICE，未知或已消费的申请抛 MENTOR_UNKNOWN_REQUEST，接受成功后双方 bond_of 指向同一 bond_id，申请从列表移除；六、decline 只允许导师或徒弟本人，第三方抛 MENTOR_NOT_PARTICIPANT，拒绝后申请消失且不能再接受；七、pending_for 按申请创建顺序返回，调用前先清理过期申请，未知玩家抛 MENTOR_UNKNOWN_PLAYER；八、progress 只对进行中的关系生效，同一节课只能记一次，重复抛 MENTOR_LESSON_DONE，点数必须是 1 到 100 的整数，否则抛 MENTOR_INVALID_POINTS，空课程名抛 MENTOR_INVALID_LESSON；九、导师每日教学点按 UTC 自然日累计，超过 daily_point_cap 抛 MENTOR_DAILY_CAP 且不记课程、不加点，跨日从零开始；十、graduate 要求已记录课程数达到 required_lessons 且徒弟等级达到 graduate_level，否则抛 MENTOR_NOT_READY，出师后关系关闭、双方解绑、再次操作抛 MENTOR_BOND_CLOSED；十一、dissolve 只允许关系双方，第三方抛 MENTOR_NOT_PARTICIPANT，解除后双方进入 cooldown_seconds 冷却，冷却内再次拜师抛 MENTOR_COOLDOWN，冷却边界时刻允许；十二、stats 恰好包含 applied、accepted、declined、graduated、dissolved、expired 六个计数，reset_stats 全部归零。

硬约束：所有写操作共用一把可重入锁，八名导师同时向同一名徒弟发起申请时只能生成一条待处理申请，其余全部按 MENTOR_PENDING_EXISTS 拒绝；snapshot 必须深拷贝玩家档案、申请、关系、绑定索引、每日点数和冷却时间，跨进程恢复后进行中的关系、当日额度和冷却继续有效，已关闭关系不得复活；导师带徒上限、申请有效期、每日点数三个边界都必须拒绝越界而不是截断；restore 遇到版本不符、档案或关系字段类型错误、bond_of 索引与进行中关系不一致、申请引用未登记玩家时抛 MENTOR_SNAPSHOT_INVALID，且原状态保持不变。时间参数接受带时区的 ISO 8601 字符串，缺省取当前 UTC 时间。

验收：`python3 -m pytest -q tests` 的 15 条测试全部通过；不得修改测试、跳过或按固定输入写死；并发与重启恢复场景自行验证；只做本地实现。

## 运行方式

```bash
python3 -m pytest -q tests
```

## 平台字段

- domain: 游戏、图形与多媒体
- sub: 游戏开发
- type: Bugfix
- difficulty: 困难

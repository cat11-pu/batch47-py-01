# batch47-game-b-10

## 题面

gsb/batch47-game-b-10 的语音房在周末歌会期间被投诉到爆：主持人在台上讲话，麦位却被一个刚进房的公会管理直接顶掉，被顶的人从列表里消失；高优先级用户在候补区一直排到三十多位，前面低优先级的新人却先上了麦；开着麦离开十分钟的人一直占着位置，心跳过期后麦位空着没人递补；房主关门后还有人能看到麦位列表并继续加队；自己被静音后无法解除，主持人的解除操作有时又落到别人身上。问题集中在 voice_room_queue.py 的 VoiceRoomQueue 类，负责房间登记、麦位占用、候补队列优先级、抢占、心跳续期、静音与关房。

公开接口（Python 类型注解）：VoiceRoomQueue(*, seat_ttl: int = 60, max_queue: int = 100, max_seats: int = 16, clock=None)；create_room(room_id: str, *, name: str, owner: str, seats: int) -> dict；close_room(room_id: str, *, by_user: str, now=None) -> dict；join_queue(room_id: str, user_id: str, *, priority: int = 0, request_id: str | None = None, now=None) -> dict；leave_queue(room_id: str, user_id: str, *, now=None) -> dict；leave_seat(room_id: str, user_id: str, *, now=None) -> dict；set_seats(room_id: str, seats: int, *, by_user: str, now=None) -> dict；preempt(room_id: str, target_id: str, *, by_user: str, now=None) -> dict；renew(room_id: str, user_id: str, *, now=None) -> dict；mute(room_id: str, user_id: str, *, by_user: str, muted: bool = True, now=None) -> dict；seats(room_id: str) -> list；queue(room_id: str) -> list；describe_room(room_id: str) -> dict；expire(now=None) -> int；snapshot() -> dict；restore(state: dict) -> None；stats() -> dict；reset_stats() -> None。校验失败统一抛 VoiceError(code, detail)。入队结果固定为 room_id、user_id、status、seat、position、priority、expires_at、idempotent 八个字段，seats 返回从零开始编号的完整麦位表，queue 返回带 user_id、priority、position 的顺序列表，describe_room 返回登记字段加 occupied、queued 两个计数，set_seats 返回 room_id、seats、occupied、queued、promoted（用户编号列表），抢占返回新占位者、麦位、被顶下的人、是否回队与队首位置。

故障与边界：一、房间编号重复抛 VOICE_ROOM_EXISTS，名称为空或 seats 不在 1 到 max_seats 抛 VOICE_BAD_SPEC；二、未登记房间抛 VOICE_UNKNOWN_ROOM，已关闭房间除 seats、queue、describe_room 查询外全部抛 VOICE_ROOM_CLOSED；三、只有房主能关房，否则抛 VOICE_NOT_OWNER，关房把在麦与候补人数计入返回值并清空两者，重复关房抛 VOICE_ROOM_CLOSED；四、priority 必须是 0 到 9 的整数，否则抛 VOICE_BAD_PRIORITY；候补队列按优先级降序、同优先级先到先得；五、队列为空且有自由麦位时直接上麦并返回最小空闲麦位号，否则进候补；候补已满抛 VOICE_QUEUE_FULL；六、已在麦上再入队抛 VOICE_ALREADY_SEATED，已在候补再入队抛 VOICE_ALREADY_QUEUED，同一用户携带相同 request_id 重试返回原记录并带 idempotent 标记；七、扩容与缩容只能由房主执行，seats 必须在 1 到 max_seats，缩减后不能丢掉任何在麦用户，否则抛 VOICE_SEATS_BELOW_OCCUPIED，扩容后按队列顺序把候补提到新麦位；八、离开麦位后只递补队首一人，离开候补后其余位次前移，未在麦上离开麦位抛 VOICE_NOT_SEATED，未在候补离开队列抛 VOICE_NOT_QUEUED；九、抢占只能由房主发起，其他人抛 VOICE_NOT_MODERATOR，被顶的目标必须在麦上否则抛 VOICE_NOT_SEATED，目标为房主抛 VOICE_CANNOT_PREEMPT_OWNER，房主已在麦上抛 VOICE_ALREADY_SEATED；十、抢占后房主接管该麦位并强制静音，原占位者插到候补队首并保留原优先级，候补已满则直接移出房间且在返回值里标明未回队；十一、renew 把到期时间推到 now 加 seat_ttl，now 已到或超过原到期时间抛 VOICE_SEAT_EXPIRED，该麦位立即释放并递补队首；十二、静音由本人或房主发起，第三人操作抛 VOICE_NOT_MODERATOR，目标不在麦上抛 VOICE_NOT_SEATED；expire 扫描所有开启房间，把到点麦位清空后按队列递补，返回清空数量，同一时刻重复调用返回 0。

硬约束：join_queue、preempt、renew、leave_seat、expire 全程持锁，八线程抢一个麦位只能有一个 seated，其余进入候补且位次不重复；抢占与递补不得让同一用户同时出现在麦位和候补；snapshot 深拷贝房间、麦位、队列、请求索引、计数器与顺序号，键为 version/rooms/seats/queues/counters/next_seq，restore 后候补顺序、静音状态、到期时间与统计原样恢复，非法结构抛 VOICE_SNAPSHOT_INVALID 且原状态不变；麦位与候补都受 seats、max_queue 硬上限约束，查询方法只读，不得触发递补或改变统计。

验收：python3 -m pytest -q tests 的 15 条测试全部通过；不得修改测试、跳过或按固定输入写死；并发与重启恢复场景自行验证；只做本地实现。

## 运行方式

```bash
python3 -m pytest -q tests
```

## 平台字段

- domain: 游戏、图形与多媒体
- sub: 游戏开发
- type: Bugfix
- difficulty: 困难

# batch47-safety-a-04

## 题面

客服后台和代理商门户共用一套会话服务，最近两周的故障单集中在几件事：登录成功后 session id 没有更换，攻击者预先埋下的会话直接被接管；同一账号能开出几十个会话，运营只能挨个手动踢；后台页面每十分钟保存一次草稿，第二天回来仍然在线，可账号早该在八小时前整体失效；点下全员登出后，被踢掉的会话下一次请求仍然通过；会话过期后列表接口还把它算进在线人数；网关重启后所有会话凭空消失，运维手工恢复的那几条又带着过期时间复活；设备指纹变了不拦，固定 IP 策略也没生效；普通账号改一个前端参数就把自己提到管理员；会话表没有上限，压测时内存翻了三倍；两个标签页同时换 session id，同一个用户拿到两个新 id。范围是 gsb/batch47-safety-a-04 下的 session_lifecycle_guard.py，类 SessionLifecycleGuard，负责会话创建、校验、轮换、提权、撤销、列表与过期清理。

公开接口：`SessionLifecycleGuard(*, idle_timeout: int = 900, absolute_timeout: int = 28800, max_sessions_per_user: int = 5, clock=None)`；`create_session(*, user_id: str, device_id: str, ip: str, auth_level: int = 1, pin_ip: bool = False, now=None) -> dict`；`validate(session_id: str, *, device_id=None, ip=None, now=None) -> dict`；`rotate(session_id: str, *, now=None) -> dict`；`elevate(session_id: str, *, auth_level: int, now=None) -> dict`；`revoke(session_id: str, *, reason: str = "user") -> bool`；`revoke_all(user_id: str, *, except_session=None) -> int`；`list_sessions(user_id: str, *, now=None) -> list`；`purge(now=None) -> int`；`snapshot() -> dict`；`restore(state: dict) -> None`；`stats() -> dict`；`reset_stats() -> None`。所有拒绝统一抛 SessionError(code, detail)，code 取下面列出的字符串。

故障与边界：一、create_session 返回 session_id、user_id、auth_level、created_at、expires_at 与 idle_expires_at，session_id 每次互不相同且不得包含 user_id；同一用户活跃会话达到 max_sessions_per_user 抛 SESSION_LIMIT_EXCEEDED，已经超时的会话不计数。二、validate 找不到会话抛 SESSION_UNKNOWN；now 大于等于绝对到期抛 SESSION_ABSOLUTE_TIMEOUT；now 大于等于空闲到期抛 SESSION_IDLE_TIMEOUT；device_id 与创建时不同抛 SESSION_DEVICE_MISMATCH；pin_ip 为真且传入 ip 与创建时不同抛 SESSION_IP_MISMATCH，ip 缺省时不做这项检查；成功时刷新最近活动与空闲到期。三、validate 成功返回 status 为 ok、session_id、user_id、auth_level、expires_at 与刷新后的 idle_expires_at；绝对到期为创建时间加 absolute_timeout，任何校验或提权都不能延长；空闲到期为最近一次成功活动加 idle_timeout。四、rotate 在会话有效时签发新 id 并删除旧 id，保留创建时间、绝对到期、auth_level、设备、IP 与 pin_ip，空闲计时重置；旧 id 之后一律抛 SESSION_UNKNOWN。五、elevate 只接受 2 或 3 且必须严格高于当前级别，否则抛 SESSION_AUTH_LEVEL_INVALID；会话超时抛对应的超时码，未知会话抛 SESSION_UNKNOWN。六、revoke 删除成功返回 True、会话不存在返回 False 且不计数；revoke_all 删除该用户全部会话但保留 except_session 指定的一条，返回删除条数，未知用户返回 0。七、list_sessions 只返回 now 时刻未超时的会话，按 session_id 升序，每项包含 session_id、user_id、device_id、auth_level、created_at、expires_at 与 idle_expires_at。八、purge 删除空闲或绝对超时的会话并返回条数，边界为 now 大于等于到期时间。九、会话 id 一旦轮换不得复用，旧 id 不得再出现在列表中；重复 revoke 同一会话只应影响第一次的计数。十、snapshot 覆盖会话与统计，restore 后会话按原始时间戳继续计时，结构非法抛 SESSION_SNAPSHOT_INVALID 且原状态不变。十一、stats 固定返回 created、validated、rejected、revoked、rotated 五个整数，rejected 只统计 validate 的失败次数，reset_stats 归零。十二、并发轮换同一会话只能有一个成功，其余抛 SESSION_UNKNOWN，且最终只留下一个可用的新 id。

硬约束：创建、校验、轮换、提权、撤销、清理全程持锁，八路并发轮换同一会话只能产生一个成功结果；快照必须是深拷贝，跨进程恢复后不与恢复前的写入共享可变结构；同一用户活跃会话数严格受 max_sessions_per_user 约束，超时会话必须能被 purge 及时释放；stats 五个计数器在并发下不得丢计数。

验收：`python3 -m pytest -q tests` 的 15 条测试必须全部通过；不得修改测试、跳过或按固定输入写死；并发和重启恢复场景自行验证；只做本地实现，不涉及上游提交。

## 运行方式

```bash
python3 -m pytest -q tests
```

## 平台字段

- domain: 安全
- sub: 身份认证
- type: Bugfix
- difficulty: 困难
- module: session_lifecycle_guard
- typeName: SessionLifecycleGuard

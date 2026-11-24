# GSB 任务包

## 题面

上周安全团队做容器逃逸演练，用 seccomp user notification 把容器里的 mount 和 openat 通知给宿主机上的代理进程处理，演练到一半代理自己出了岔子：审计日志里同一份通知被回复了两次，第二次居然也返回成功；有通知 ID 超过 64 位后被截断，跟另一个容器的通知撞在一起，代理把文件描述符注入到了错误的进程；回复超时的通知一直挂在待决表里占着配额，跑了六小时代理累计泄漏两千多个描述符，最后 sendmsg 全部失败；容器重启后代理没有清理旧目标，新进程复用了同一个 pid，旧通知的回复直接落到新进程身上；监听端被删掉以后，代理还在往已经不存在的路径投递。并发压测时更糟，八个 worker 同时对同一通知回 continue，宿主机上出现了两次 continue 响应，内核直接返回 ENOENT。

范围是 gsb/batch47-safety-e-07 下的 seccomp_notify_broker.py，类型 SeccompNotifyBroker，负责目标进程与监听端登记、通知收发、超时回收和 ADDFD 描述符分配。公开接口：`SeccompNotifyBroker(*, target_limit: int = 8, max_pending: int = 64, addfd_limit: int = 4, timeout_ms: int = 500, first_fd: int = 1000, clock=None)`；`attach_target(pid: int) -> dict`；`detach_target(pid: int) -> dict`；`register_listener(path: str, *, listener_fd: int) -> dict`；`unregister_listener(path: str) -> dict`；`submit_notification(notify_id: int, *, listener_path: str, syscall: int, pid: int, args=(), flags: int = 0, now=None) -> dict`；`reply_continue(notify_id: int, *, val: int = 0) -> dict`；`reply_error(notify_id: int, *, errno: int) -> dict`；`reply_addfd(notify_id: int, *, source_fd: int, target_fd: int | None = None, flags: int = 0) -> dict`；`cancel(notify_id: int, *, reason: str = "cancelled") -> dict`；`expire(now=None) -> list`；`pending() -> list`；`listener_stats(path: str) -> dict`；`snapshot() -> dict`；`restore(state: dict) -> None`；`stats() -> dict`；`reset_stats() -> None`。失败统一抛 SeccompBrokerError(code, detail)。时间参数用 ISO-8601 字符串；pending() 按 notify_id 升序返回 notify_id、listener、syscall、pid、deadline、addfd_count，deadline 为 ISO-8601。

故障与边界：一、notify_id 必须是正整数，否则抛 SECCOMP_INVALID_NOTIFY_ID；同一 ID 在待决或已完成且尚未过期的状态下重复提交抛 SECCOMP_DUPLICATE_NOTIFY；二、监听端未登记抛 SECCOMP_UNKNOWN_LISTENER，目标从未 attach 抛 SECCOMP_UNKNOWN_TARGET，detach 之后抛 SECCOMP_TARGET_GONE，三者不可混用；三、attach 超过 target_limit 抛 SECCOMP_TARGET_LIMIT，重复 attach 幂等返回；四、同一路径重复登记但 listener_fd 不同抛 SECCOMP_LISTENER_CONFLICT，完全一致幂等返回；五、syscall 必须是非负整数，args 每一项必须是整数且最多 6 个，否则抛 SECCOMP_INVALID_ARGUMENT，失败提交不计入 submitted；六、continue 的 val 必须在 0 到 65535 之间，error 的 errno 必须在 1 到 4095 之间，越界分别抛 SECCOMP_INVALID_ARGUMENT 与 SECCOMP_INVALID_ERRNO；七、只有待决通知能被回复，已回复抛 SECCOMP_ALREADY_REPLIED，已过期、已取消或目标已 detach 抛 SECCOMP_NOTIFY_GONE，从未出现抛 SECCOMP_UNKNOWN_NOTIFY；八、ADDFD 的 source_fd 必须非负，flags 只允许 0x1 与 0x2，显式 target_fd 已被占用抛 SECCOMP_FD_IN_USE，未指定时从 first_fd 起分配最小空闲值，同一通知注入数超过 addfd_limit 抛 SECCOMP_ADDFD_LIMIT 且通知保持待决；九、ADDFD 只注入描述符不结束通知，continue 或 error 才结束，注入成功的描述符计入 installed_fds 并保留到目标 detach；十、待决通知的 deadline 等于 now 加 timeout_ms，expire 把 deadline 小于等于 now 的通知置为过期并按 notify_id 升序返回，同时释放其注入的描述符，之后再回复抛 SECCOMP_NOTIFY_GONE；十一、待决数达到 max_pending 时先做过期扫描，仍满抛 SECCOMP_PENDING_FULL 且不登记；十二、detach 与注销监听端要把名下所有待决通知置为 abandoned 并释放描述符，cancel 返回释放条数，stats 包含 submitted、replied、expired、cancelled、abandoned、rejected、installed_fds，rejected 只统计未被受理的提交，listener_stats 按监听端统计 path 与六项计数及 pending。

硬约束：提交、回复、取消、过期扫描全程持锁，多线程同时回复同一通知只能有一个成功，其余全部 SECCOMP_ALREADY_REPLIED，且任何失败路径都不能改变 installed_fds；快照必须深拷贝，覆盖目标、已 detach 名单、监听端、待决通知、已完成记录、描述符分配和统计，restore 遇到缺键或类型不符抛 SECCOMP_SNAPSHOT_INVALID 且原状态不变，恢复后未决通知的 deadline 与已分配描述符必须延续，新注入的 fd 不得与已有值冲突；待决表与已完成记录都受 max_pending 与 deadline 约束，不允许无界增长。

验收：`python3 -m pytest -q tests` 15 条测试全部通过；不得修改测试、跳过或按固定输入写死；并发与重启恢复场景自行验证；只做本地实现。

## 运行方式

```bash
python3 -m pytest -q tests
```

## 平台字段

- domain: 安全
- sub: 系统安全
- type: Bugfix
- difficulty: 困难
- name: batch47-safety-e-07
- module: seccomp_notify_broker
- typeName: SeccompNotifyBroker

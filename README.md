# 需求说明

## 题面

容器平台的值班表上连续三周都是同一类工单：业务容器跑满 48 小时后新进程 fork 失败，docker exec 进去一看全是 `<defunct>`；原因是镜像里的启动脚本不是合格的 PID 1，子进程退出后没人收尸，僵尸一路涨到 cgroup 的 pid 上限。还有一次更麻烦，父进程被 kill 之后它的三个子进程成了孤儿，信号全打在已经不存在的 pid 上，业务以为进程还在跑，健康检查却全部超时；编排侧滚动重启之后，之前记录在内存里的退出顺序和僵尸队列一起丢光，运维只能整容器重启。监控里还能看到，某些容器的 pid 计数在重启后从零开始，历史僵尸和退出码全没了，事后追责只能靠业务日志猜。这次要立的是 gsb/batch47-safety-e-10 下的 pidns_reaper_guard.py，类 PidnsReaperGuard，负责 PID 命名空间内进程树登记、退出转僵尸、孤儿收养、按序回收和容器级 pid 预算。公开接口：`PidnsReaperGuard(*, max_pids: int = 256, reap_budget: int = 32)`；`register_container(container_id: str, *, init_pid: int = 1) -> dict`；`unregister_container(container_id: str, *, force: bool = False) -> dict`；`spawn(container_id: str, *, pid: int, ppid: int, comm: str = "") -> dict`；`exit(container_id: str, *, pid: int, status: int = 0) -> dict`；`reap(container_id: str, *, limit: int | None = None) -> list`；`adopt_orphans(container_id: str) -> list`；`signal(container_id: str, *, pid: int, signum: int) -> dict`；`tree(container_id: str) -> list`；`is_alive(container_id: str, pid: int) -> bool`；`snapshot() -> dict`；`restore(state: dict) -> None`；`stats() -> dict`；`reset_stats() -> None`。失败统一抛 ReaperError(code, detail)。故障与边界：一、容器 ID 为空抛 REAPER_INVALID_CONTAINER，重复登记但 init_pid 不同抛 REAPER_CONFLICT，完全相同幂等；二、init_pid 小于 1 或超过 4194304 抛 REAPER_INVALID_PID；三、spawn 时容器不存在抛 REAPER_UNKNOWN_CONTAINER，pid 非正或重复抛 REAPER_INVALID_PID、REAPER_PID_DUPLICATE，父进程不存在或已退出抛 REAPER_NO_PARENT；四、活跃进程加僵尸达到 max_pids 抛 REAPER_LIMIT，被拒的 pid 不占号；五、exit 把进程置为 zombie 并记录退出序，直接子进程父指针清成 0 等待收养，重复 exit 返回 already_exited 且不重复计数；六、exit 未登记的 pid 抛 REAPER_UNKNOWN_PID，exit 掉 init 抛 REAPER_INIT_PROTECTED；七、adopt_orphans 把父指针为 0 的进程挂回 init 并按 pid 升序返回，第二次调用返回空表；八、reap 只回收 zombie，按退出序先进先出，返回每条的 pid 与退出码，limit 大于 reap_budget 或小于 1 抛 REAPER_REAP_BUDGET，limit 为空时用 reap_budget；九、signal 的 signum 必须在 1 到 64 之间，否则抛 REAPER_INVALID_SIGNAL，对僵尸或已回收进程抛 REAPER_PID_NOT_RUNNING，对 init 发 9 号信号抛 REAPER_INIT_PROTECTED；十、unregister 在还有 init 之外的运行中进程且未 force 时抛 REAPER_CONTAINER_BUSY，僵尸不阻止注销，force 时整容器移除并返回移除数量，tree 与 is_alive 按 pid 升序和运行状态如实汇报；十一、snapshot 深拷贝容器、退出序、僵尸队列与统计，恢复后 reap 仍按原退出序出队；十二、restore 结构非法抛 REAPER_SNAPSHOT_INVALID 且原状态不变，stats 分开记 registered、spawned、exited、reaped、signaled、rejected，reset_stats 归零。硬约束：spawn、exit、reap、signal 与 unregister 全程持锁，多线程对同一 pid 同时 exit 只能产生一次状态迁移，其余返回 already_exited；同一 pid 并发 spawn 只能成功一次；回收预算在恢复快照后依然生效，不允许一次 reap 把超过预算的僵尸全部放出去；进程树与退出序在快照往返后必须逐条一致，不允许出现 pid 复用或顺序错乱。验收：`python3 -m pytest -q tests` 的 15 条测试全部通过；不得修改测试、跳过或按固定输入写死；并发退出与重启恢复自行验证；只做本地实现，不真正调用信号或等待系统调用。

## 运行方式

```bash
python3 -m pytest -q tests
```

## 平台字段

- domain: 安全
- sub: 系统安全
- type: Bugfix
- difficulty: 困难

# 容器运行时生命周期状态机

## 题面

容器运行时换上新版本后，值班同学发现容器状态机和台账对不上。created 状态的容器被重复 start，两次都返回 running，第二个进程拿到另一个 pid；容器已经 stopped，delete 却报成功但记录还在；pause 之后还能 exec，宿主机上看到两个同名进程；stop 设置了 30 秒宽限期，宽限期到了进程没退出，状态一直停在 stopping；kill 传 SIGTERM 与传 15 得到不同退出码，审计里 137 和 143 混着记；runtime 重启后所有 running 容器都变成 created，调度器把它们又拉起一遍；exec 编号在重启后从 1 重来，和旧日志撞号；事件环形缓冲把最近一次 stop 挤掉，排障时看不到关键动作；容量上限按历史创建数算，删除容器也释放不出名额；并发 start 同一个容器，两个请求都成功，输出两套日志，事后复盘只能靠猜。相关代码在 gsb/batch47-safety-e-04 的 oci_runtime_guard.py，类 OciRuntimeGuard，只用 Python 标准库，失败统一抛 OciError(code, detail)。

接口保持：`__init__(self, *, max_containers: int = 64, stop_grace: int = 10, event_limit: int = 100)`；`create(self, container_id: str, *, bundle: str, argv=None, env=None, labels=None) -> dict`；`start(self, container_id: str) -> dict`；`exec_in(self, container_id: str, argv=None) -> dict`；`pause(self, container_id: str) -> dict`；`resume(self, container_id: str) -> dict`；`kill(self, container_id: str, signal: int | str = 15) -> dict`；`stop(self, container_id: str, *, timeout: int | None = None) -> dict`；`report_exit(self, container_id: str, code: int) -> dict`；`delete(self, container_id: str, *, force: bool = False) -> bool`；`state(self, container_id: str) -> dict`；`list_containers(self, *, label: str | None = None) -> list`；`tick(self, seconds: int = 1) -> int`；`events(self, limit: int | None = None) -> list`；`stats(self) -> dict`；`snapshot(self) -> dict`；`restore(self, state: dict) -> None`。

故障与边界：一、容器 ID 只允许字母数字开头、总长不超过 64，非法抛 OCI_BAD_ID；bundle 为空抛 OCI_BAD_BUNDLE；重复 ID 抛 OCI_EXISTS；活跃容器数达到 max_containers 抛 OCI_LIMIT，删除后名额释放；二、状态机只允许 created→running、running↔paused、running/paused/stopping→stopped，其余转移抛 OCI_BAD_STATE；三、start 从 1000 起单调分配 pid，已启动过的容器再次 start 一律拒绝；exec 编号按容器独立计数，格式 <id>-exec-<n>，空 argv 抛 OCI_BAD_ARGV；四、kill 接受 1 到 64 的整数或 SIGTERM、SIGKILL、SIGINT 名称，退出码为 128 加信号值，未知信号抛 OCI_BAD_SIGNAL 且状态不变；五、stop 把 running 或 paused 置为 stopping 并给出 deadline 等于当前逻辑时钟加超时（缺省用 stop_grace），对 stopping 容器重复 stop 必须原样返回同一 deadline；六、tick 推进逻辑时钟，返回本轮因超过 deadline 被强杀的容器数，强杀退出码固定 137 并计入 forced_kills；七、report_exit 只接受 0 到 255 的退出码，超范围抛 OCI_BAD_EXIT_CODE，created 或已 stopped 的容器不能上报；八、delete 只允许 created 或 stopped 的容器，running、paused、stopping 必须带 force 才删除并以 137 记退出，重复删除或未知 ID 抛 OCI_NOT_FOUND；九、list_containers 按 ID 升序返回，label 过滤只接受 key=value 形式，格式非法抛 OCI_BAD_LABELS；十、事件按 seq 递增记录，只保留最近 event_limit 条，events 从新到旧返回，limit 为负抛 OCI_BAD_LIMIT；十一、state 返回 id、status、bundle、pid、exit_code、execs，未启动时 pid 为 None，未结束时 exit_code 为 None；十二、snapshot/restore 覆盖全部容器、pid 与 exec 计数、逻辑时钟、事件和统计，恢复后 running 容器保持 running，结构非法抛 OCI_SNAPSHOT_INVALID 且原状态不变。

硬约束：状态转移、pid 分配、exec 计数和事件追加在同一把锁内完成，多个线程同时 start 同一个 created 容器只能有一个成功，其余全部抛 OCI_BAD_STATE；快照深拷贝，恢复后能继续 exec、stop 和删除；事件缓冲和容器数量都有上限，不得无界增长；状态与事件要能按 seq 逐条对上；统计精确给出 created、started、stopped、deleted、execs、forced_kills。

验收：`python3 -m pytest -q tests` 的 15 条测试全部通过；不得修改测试、跳过或按固定输入写死；并发与重启恢复场景自行验证；只做本地实现。

## 运行方式

```bash
python3 -m pytest -q tests
```

## 平台字段

- domain: 安全
- sub: 系统安全
- type: Bugfix
- difficulty: 困难

# GSB 任务

## 题面

制品签名网关今年开始为所有发布物校验透明日志条目：制品推入仓库时把叶子哈希写进日志，部署前再向网关出示日志索引、树大小和审计路径。上个月审计发现几类穿透：一份根本没进过日志的制品，用另一棵树的小尺寸根哈希也拿到了通过；某日志运营方把树大小从五十万回退到四十万，网关照样接受；同一树大小下换了根哈希，网关没有识别成视图分叉；老条目在新树里早已不存在，只要审计路径自洽仍被放行；同一个日志条目被重复提交两次，两条都记成了新条目；攻击者提交长度上百的审计路径，服务端逐层哈希把 CPU 打满。

范围是 gsb/batch47-safety-b-02 下的 log_inclusion_guard.py，类 InclusionGuard，负责日志登记、检查点验收、Merkle 包含证明校验与条目去重。模块还要导出三个函数：leaf_hash(data: bytes) -> str 返回 SHA-256(0x00 字节前缀接叶子原文) 的小写十六进制；node_hash(left: str, right: str) -> str 返回 SHA-256(0x01 字节前缀接左右子哈希原始字节) 的小写十六进制；checkpoint_message(log_id: str, tree_size: int, root_hash: str) -> str 返回由 checkpoint:v1、日志名、树大小、根哈希四行以换行拼接的规范串。公开接口：InclusionGuard(*, max_entries: int = 512, batch_limit: int = 50, max_audit_path: int = 32)；register_log(log_id: str, *, key_hex: str, max_tree_size: int = 1 << 32) -> dict；revoke_log(log_id: str) -> None；accept_checkpoint(log_id: str, *, tree_size: int, root_hash: str, signature: str, anchor=None) -> dict；verify_inclusion(log_id: str, *, leaf_hash: str, leaf_index: int, tree_size: int, audit_path, root_hash: str | None = None) -> dict；accept_entry(log_id: str, *, entry_id: str, leaf_hash: str, leaf_index: int, tree_size: int, audit_path) -> dict；accept_batch(log_id: str, entries) -> list；is_known(entry_id: str) -> bool；checkpoint(log_id: str) -> dict | None；entries(log_id: str | None = None) -> list；snapshot() -> dict；restore(state: dict) -> None；stats() -> dict；reset_stats() -> None。失败统一抛 InclusionError(code, detail)。

故障与边界：一、日志名或密钥格式不对分别抛 TRANSLOG_BAD_LOG_ID、TRANSLOG_BAD_KEY，同一日志名换成另一把密钥或另一个上限抛 TRANSLOG_CONFLICT，配置相同则幂等返回；二、检查点签名必须是对规范串做 HMAC-SHA256 的小写十六进制，校验失败抛 TRANSLOG_BAD_SIGNATURE；根哈希与叶子哈希只接受 64 位小写十六进制，否则 TRANSLOG_BAD_CHECKPOINT 或 TRANSLOG_BAD_PROOF；三、树大小回退抛 TRANSLOG_ROLLBACK，同尺寸不同根抛 TRANSLOG_FORK，同尺寸同根幂等返回 updated=False，超过登记上限抛 TRANSLOG_TREE_TOO_LARGE；四、树增长时若已有接受过的条目，必须附上最近条目的包含证明，缺失抛 TRANSLOG_ANCHOR_MISSING，证明不通过抛 TRANSLOG_ANCHOR_INVALID 且旧检查点保持不变；五、包含证明按 RFC 6962 的左右顺序验证，索引越界或路径对不上抛 TRANSLOG_INCLUSION_INVALID，树大小没有对应检查点抛 TRANSLOG_UNKNOWN_ROOT，路径超过 max_audit_path 抛 TRANSLOG_PROOF_TOO_LONG；六、条目按 entry_id 与叶子哈希双重去重，重复提交返回 status=replayed 与 TRANSLOG_REPLAY，不再写入；七、条目总数达到 max_entries 抛 TRANSLOG_CAPACITY_FULL，被拒条目不能占用名额；八、accept_batch 超过 batch_limit 整批拒绝抛 TRANSLOG_BATCH_TOO_LARGE，未超限时逐条返回 accepted、replayed 或 rejected，单条失败不影响后续；九、已撤销日志的所有读写都抛 TRANSLOG_LOG_REVOKED，撤销本身幂等，未登记日志抛 TRANSLOG_UNKNOWN_LOG；十、entries 按日志名与索引排序，只返回元数据不回传审计路径；十一、snapshot 深拷贝日志密钥、检查点、条目、去重索引与统计，恢复后已接受条目仍按重放处理，结构或类型不合法抛 TRANSLOG_SNAPSHOT_INVALID 且原状态不变；十二、stats 区分 accepted、replayed、rejected、checkpoints，reset_stats 归零。

硬约束：检查点验收与条目写入全程持锁，多个线程同时提交同一条目只能有一个 accepted，其余全部 replayed；快照必须能跨进程恢复，恢复前后的后续写入互不影响；检查点大小、条目数量和审计路径长度都受上限约束，不允许因重试或异常输入无界增长。

验收：python3 -m pytest -q tests 的 15 条测试全部通过；不得修改测试、跳过或按固定输入写死；并发接受与重启恢复自行验证；只做本地实现。

## 运行方式

```bash
python3 -m pytest -q tests
```

## 平台字段

- domain: 安全
- sub: 身份认证
- type: Bugfix
- difficulty: 地狱
- module: log_inclusion_guard
- typeName: InclusionGuard

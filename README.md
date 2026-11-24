# GSB 任务

## 题面

密钥托管平台这季度接管了代码签名根密钥、发布证书私钥和时间戳服务密钥，全部要求拆成多片交给不同保管人，至少凑够法定人数才能重建。上周演练暴露了一串问题：三位保管人提交的分片拼出来的密钥摘要对不上；轮换之后离职保管人手里的旧分片仍能参与重建；同一份分片被重复提交两次也被算成两个人；退役的密钥记录还留在托管列表里，值守人员误以为仍可恢复；两个团队同时给同一个密钥做轮换，最终版本号跳了两级；审计日志只记了成功次数，出现多少次失败申请完全查不到；演练报告只给了结论，没有留下可复现的步骤。

范围是 gsb/batch47-safety-b-01 下的 key_escrow_guard.py，类 KeyEscrowGuard，负责托管登记、分片重建、保管人轮换、退役与状态导出。公开接口按 Python 类型注解实现：KeyEscrowGuard(*, max_secrets: int = 64, max_share_bytes: int = 256)；create_escrow(secret_id: str, *, key_material, custodians, threshold: int = 0) -> dict；reconstruct(secret_id: str, *, shares_by_custodian) -> bytes；verify_share(secret_id: str, *, custodian: str, share) -> bool；rotate_escrow(secret_id: str, *, shares_by_custodian, custodians, threshold: int = 0) -> dict；retire_escrow(secret_id: str) -> dict；status(secret_id: str) -> dict；is_retired(secret_id: str) -> bool；list_escrows() -> list；audit(*, limit=None) -> list；snapshot() -> dict；restore(state: dict) -> None；stats() -> dict；reset_stats() -> None。失败统一抛 EscrowError(code, detail)，code 用下面的字符串。

故障与边界：一、threshold 为 0 时取保管人数量的多数，显式给出的阈值必须落在 2 到保管人数之间，否则 ESCROW_THRESHOLD_INVALID；二、保管人列表少于两个、名字为空或重复分别返回 ESCROW_CUSTODIAN_INVALID 与 ESCROW_CUSTODIAN_DUPLICATE；三、重复登记同一 secret_id 抛 ESCROW_DUPLICATE_SECRET，超过 max_secrets 抛 ESCROW_CAPACITY_FULL；四、空密钥或非字节入参抛 ESCROW_SECRET_INVALID，长度超过 max_share_bytes 抛 ESCROW_SECRET_TOO_LARGE；五、重建时提交不足阈值的分片抛 ESCROW_QUORUM_SHORT，提交名单外的保管人抛 ESCROW_CUSTODIAN_UNKNOWN；六、分片内容被改动、版本或持有者序号不符抛 ESCROW_SHARE_INVALID；七、轮换后旧版本分片一律抛 ESCROW_VERSION_MISMATCH，不能按新阈值重新组合；八、退役后的密钥重建与分片校验都抛 ESCROW_RETIRED，保管人清空但摘要与版本保留备查；九、重复退役必须幂等，第二次返回 already=True 且不再累加计数；十、reconstruct 只读且可重复调用，同一组分片连续重建结果一致；十一、审计环形缓冲最多保留 200 条，成功与拒绝事件都要记录，rejected 事件带 code；十二、snapshot 必须深拷贝并覆盖托管记录、版本、退役标记、审计与统计，restore 校验版本与字段类型，结构非法抛 ESCROW_SNAPSHOT_INVALID 且原状态完全不变。

硬约束：create、rotate、retire 全程持锁，多个线程同时对同一密钥轮换时只能有一个成功，其余全部收到版本不匹配；重建与轮换不能交错看到半新半旧的记录；每个密钥的托管记录数受 max_secrets 约束，不允许因失败重试无限增长；audit 与 stats 在并发下不能出现负数或丢失。

验收：python3 -m pytest -q tests 的 15 条测试全部通过；不得修改测试、跳过或按固定输入写死；并发轮换与快照恢复场景自行验证；只做本地实现。

## 运行方式

```bash
python3 -m pytest -q tests
```

## 平台字段

- domain: 安全
- sub: 身份认证
- type: Bugfix
- difficulty: 地狱
- module: key_escrow_guard
- typeName: KeyEscrowGuard

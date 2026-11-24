# 任务说明

季度发布窗口前一天，值班发现支付服务的制品在只有两名工程师点头的情况下就完成了签名，安全角色的会签记录缺失；被停职的签名人上个月留下的批准仍然计入了权重，过期的提案第二天还能补签，同一个人重复点击批准把权重加了两次。审计同事还发现签名历史只记了最后一步，谁在什么时候撤销过批准完全查不到；签名服务只校验了总权重，没有校验角色覆盖面，也没有把停职状态带进最终计算。请修 gsb/batch47-safety-b-09 下的 release_signing_quorum.py，公开类型 SigningQuorum，异常 QuorumError(code, detail)，只改这个文件，不要动 tests 目录。

保持这些公开接口：`SigningQuorum(*, max_open=20, max_approvals_per_signer=5, clock=None)`；`register_signer(signer, *, role, weight=1, keys=()) -> dict`；`suspend_signer(signer, *, reason) -> dict`；`reinstate_signer(signer) -> dict`；`open_proposal(proposal_id, *, artifact, digest, channel, required_roles=(), min_weight=1, expires_at=None, now=None) -> dict`；`approve(proposal_id, signer, *, now=None) -> dict`；`reject(proposal_id, signer, *, reason, now=None) -> dict`；`revoke_approval(proposal_id, signer, *, now=None) -> dict`；`finalize(proposal_id, *, now=None) -> dict`；`close_proposal(proposal_id, *, reason, now=None) -> dict`；`history(proposal_id, *, limit=50) -> list`；`snapshot() -> dict`；`restore(state) -> None`；`stats() -> dict`；`reset_stats() -> None`。

故障与边界：一、签名人角色限 release-manager、security、engineer，权重 1 到 5，同人同配置重复登记幂等，配置变化抛 QUORUM_CONFLICT，未知签名人抛 QUORUM_UNKNOWN_SIGNER；二、停职签名人不能批准，抛 QUORUM_SIGNER_SUSPENDED，停职时已有的批准在 finalize 时不计权重也不满足角色要求；三、提案 ID 重复抛 QUORUM_PROPOSAL_EXISTS，artifact 为空抛 QUORUM_BAD_ARTIFACT，digest 非 sha256 抛 QUORUM_BAD_DIGEST，channel 只接受 stable、beta、nightly，required_roles 含未知角色或重复抛 QUORUM_BAD_ROLES，min_weight 小于 1 抛 QUORUM_BAD_WEIGHT，expires_at 不晚于当前时间抛 QUORUM_BAD_WINDOW；四、同时打开的提案超过 max_open 抛 QUORUM_CAPACITY，关闭后可继续新建；五、同一签名人对同一提案重复批准返回 duplicate 且权重只算一次，批准返回当前累计权重与人数；六、非 open 状态的提案批准、驳回、撤销批准、finalize 一律抛 QUORUM_PROPOSAL_CLOSED；七、时间达到 expires_at 后批准与 finalize 抛 QUORUM_PROPOSAL_EXPIRED，恰好早一秒仍可用；八、任一签名人驳回即终止提案，之后不能翻转；九、finalize 要求累计权重大于等于 min_weight 且 required_roles 每个角色至少有一名未停职的批准人，不满足抛 QUORUM_NOT_MET；十、revoke_approval 只能撤销已存在的批准，否则抛 QUORUM_NOT_APPROVED，撤销后可以重新批准；十一、同一签名人同时批准的 open 提案数不得超过 max_approvals_per_signer，超出抛 QUORUM_APPROVAL_LIMIT，撤销后释放名额；十二、每次 open、approve、reject、revoke、finalize、close 都要写入带递增 sequence 的审计事件，事件包含 signer、at 与 detail，history 按从新到旧返回，limit 限 1 到 200，否则抛 QUORUM_BAD_LIMIT；snapshot 覆盖 version、signers、proposals、stats、sequence，必须深拷贝，restore 结构非法抛 QUORUM_SNAPSHOT_INVALID 且原状态不变。

硬约束：批准与 finalize 共用一把锁，八个人同时批准同一提案时每个人只计一次，并发 finalize 只能有一个 signed；同一签名人不能通过重复提交把权重刷高；已签名提案不能再被任何后续操作改写；快照恢复到新进程后，停职状态、批准、审计序列和统计都要能继续使用；打开提案数与每人批准数都有硬上限。

验收：`python3 -m pytest -q tests` 的 15 条测试全部通过；不得修改测试、跳过或按固定输入写死；并发与重启恢复要自行验证；只做本地实现。

## 运行方式

```bash
python3 -m pytest -q tests
```

## 平台字段

- domain: 安全
- sub: 身份认证
- type: Bugfix
- difficulty: 困难

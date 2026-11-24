# batch47-game-b-09

## 题面

gsb/batch47-game-b-09 的赛事争议仲裁台在八强赛结束后被工单淹没：败方在结果公布四十多分钟后仍能提交申诉；同一名选手对同一场连交三份申诉，三个裁判各看一份；裁判名单里混进当事人的队友；三票里两票主张改判，系统却把原胜者又判了一次冠军；SLA 过了四小时，工单还挂在待分配。问题集中在 match_dispute_arbitration.py 的 MatchDisputeArbitration 类，负责对局登记、申诉受理、裁判分配、投票与改判。公开接口：MatchDisputeArbitration(*, appeal_window=3600, evidence_limit=5, panel_size=3, sla_seconds=1800, max_open_per_match=2, clock=None)；open_match(match_id, *, winner, participants, now=None) -> dict；submit_dispute(match_id, *, player_id, reason, evidence=(), now=None) -> dict；assign_reviewers(dispute_id, reviewers, *, now=None) -> dict；cast_vote(dispute_id, *, reviewer, verdict, now=None) -> dict；resolve(dispute_id, *, now=None) -> dict；withdraw_dispute(dispute_id, *, player_id, now=None) -> dict；escalate(dispute_id, *, reason, now=None) -> dict；disputes(*, match_id=None, status=None) -> list；match_result(match_id) -> dict；expire(now=None) -> int；snapshot()/restore(state)/stats()/reset_stats()。校验失败统一抛 DisputeError(code, detail)。

故障与边界：一、对局编号重复抛 DISPUTE_MATCH_EXISTS，胜者不在参赛名单或名单含重复项抛 DISPUTE_BAD_RESULT；二、申诉未知对局抛 DISPUTE_UNKNOWN_MATCH，非参赛者提交抛 DISPUTE_NOT_PARTICIPANT，理由为空抛 DISPUTE_BAD_REASON；三、证据条数超过 evidence_limit 抛 DISPUTE_TOO_MUCH_EVIDENCE；四、超过 appeal_window 后提交抛 DISPUTE_WINDOW_CLOSED，正好落在时限上仍受理；五、同一玩家对同一场重复提交抛 DISPUTE_DUPLICATE，无论前一份是否已撤回；六、单场未结案申诉达到 max_open_per_match 抛 DISPUTE_LIMIT；七、裁判人数必须等于 panel_size 且不得重复，否则抛 DISPUTE_PANEL_SIZE，裁判是当事人抛 DISPUTE_REVIEWER_CONFLICT，重复分配抛 DISPUTE_ALREADY_ASSIGNED；八、非分配裁判投票抛 DISPUTE_NOT_REVIEWER，裁决值非法抛 DISPUTE_BAD_VERDICT，同一裁判重复投票抛 DISPUTE_DUPLICATE_VOTE；九、票数未齐或工单仍处待分配时结案抛 DISPUTE_VOTES_PENDING；十、多数改判时对局胜者翻转到另一位参赛者并标记 overturned，多数维持时保留原胜者，平票转为 escalated；十一、只有申诉人本人能在分配前撤回，其他人抛 DISPUTE_NOT_FILER，已分配或已结案抛 DISPUTE_LOCKED；十二、expire 把超时的待分配申诉置为 expired、把超时未结案的在审工单升级为 escalated 并返回条数，重复升级抛 DISPUTE_ALREADY_ESCALATED。

硬约束：提交、投票、结案全程持锁，八线程提交同一玩家同一场只能受理一份，八线程用同一裁判投票只能记一票，八线程同时结案只能落定一次；snapshot 深拷贝对局、申诉、裁判名单、票箱与统计，restore 后已投票的裁判不能再投、改判结果保持，非法结构抛 DISPUTE_SNAPSHOT_INVALID 且原状态不变；SLA 与申诉窗口只按传入的 now 判断，证据上限与单场未结案上限不得被绕过。

补充要求：申诉理由为空白字符串也按空处理；证据允许为空列表，只在超过上限时拒绝；裁判名单里的重复姓名按人数不足处理；结案前逐票核对在册裁判；改判只翻转胜负，不修改参赛名单与对局编号；撤回后的工单不再占用单场未结案名额；已结案的工单不会被过期清理二次处理；工单列表按提交顺序稳定返回。

验收：python3 -m pytest -q tests 的 15 条测试全部通过；不得修改测试、跳过或按固定输入写死；并发与重启恢复场景自行验证；只做本地实现。

## 运行方式

```bash
python3 -m pytest -q tests
```

## 平台字段

- domain: 游戏、图形与多媒体
- sub: 游戏开发
- type: Bugfix
- difficulty: 地狱

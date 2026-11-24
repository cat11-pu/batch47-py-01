# GSB 任务：声明式渲染帧图的依赖排序、裁剪与屏障编译

## 题面

渲染组改成声明式帧图之后，夜晚城市场景偶发某几帧阴影贴图全黑，抓帧发现几何 pass 被排到采样 pass 之后，还有分支资源根本没被声明却照样提交。相关实现位于 gsb/batch47-game-e-02/render_frame_graph.py，核心类型是 FrameGraph，异常 FrameGraphError 通过 code 暴露原因。需要把逻辑资源、pass 读写和最终输出整理成可编译、可裁剪、可重复执行的帧图。帧图每帧重建一次，资源名和 pass 名在整帧内唯一，声明顺序不等于执行顺序。

接口按现有调用方式保留：register_resource(name, *, kind="buffer", size=0) 返回资源描述；add_pass(name, *, reads=(), writes=(), execute=None) 返回 {name, reads, writes, index}；remove_pass(name)；mark_output(*resource_names) 返回输出列表；compile() 返回 {order, culled, barriers, versions, outputs}；execute(context=None) 返回按顺序排列的 {pass, result}；snapshot() 与 restore(state)；stats() 与 reset_stats()；is_compiled() 判断当前声明是否已编译。返回字典的键必须与约定一致，调用方按字段读取。

需要满足的行为：一，执行顺序由读写依赖决定，不能沿 add_pass 的声明顺序；读取尚未声明写入者的资源时，要依赖该资源在整张图里的生产者，即使生产者后声明。生产者和消费者之间不能靠遍历顺序碰巧正确。二，出现环时报 FG_CYCLE，detail 给出成环 pass 的排序名单，删除其中一个 pass 后同一实例必须还能正常编译。三，资源名或 pass 名重复报 FG_DUPLICATE_RESOURCE 或 FG_DUPLICATE_PASS，空名字报 FG_INVALID_NAME。四，reads 与 writes 出现同一资源报 FG_READ_WRITE_CONFLICT，detail 为冲突资源排序列表。五，未知资源报 FG_UNKNOWN_RESOURCE，未知 pass 报 FG_UNKNOWN_PASS，execute 不是可调用对象报 FG_INVALID_CALLBACK。六，空图报 FG_EMPTY_GRAPH，没有任何输出报 FG_NO_OUTPUT，输出资源没有任何 pass 生产时报 FG_OUTPUT_UNPRODUCED。七，pass 数量超过 max_passes 报 FG_TOO_MANY_PASSES，删除 pass 后名额立即释放。八，每次写入把资源版本加一，barriers 依次记录 {resource, pass, from, to, kind}，读也要给出从当前版本到自身的记录。九，compile 只保留标记输出及其传递依赖的 pass，未被依赖的分支进入 culled，顺序仍按拓扑序；culled 按声明顺序列出，barriers 按执行顺序排列，versions 给出所有已注册资源的终值；同一份声明重复编译结果必须完全一致。十，execute 必须在 compile 之后，否则报 FG_NOT_COMPILED；任何新增、删除或重新标记输出都会让已编译结果失效。十一，回调抛出异常要包装成 FG_PASS_FAILED，detail 含 pass 名和原始错误文本，后续 pass 不再执行，已经执行的 pass 计数保留。十二，snapshot 要保存资源、pass 读写和输出，restore 到新实例后重新编译得到相同顺序、版本和屏障；结构损坏报 FG_SNAPSHOT_INVALID 且当前实例状态不变。恢复后的图没有回调时，execute 仍要按顺序产出 pass 名和空结果。

硬约束：声明顺序与执行顺序必须解耦，重复编译必须幂等；快照恢复后拓扑一致；pass 上限是硬限制；execute 只能按编译顺序各跑一次。并发编译与声明修改要串行化，不能读到半张图。验收以 python3 -m pytest -q tests 为准，15 条必须全部通过；不得修改或跳过测试，不得按固定输入写死结果；重启恢复和失败中断场景自行验证；只做本地实现，不接上游提交。

## 运行方式

```bash
python3 -m pytest -q tests
```

## 平台字段

| 字段 | 值 |
| --- | --- |
| name | batch47-game-e-02 |
| domain | 游戏、图形与多媒体 |
| sub | 图形渲染 |
| type | Bugfix |
| difficulty | 地狱 |
| module | render_frame_graph |
| typeName | FrameGraph |
| file | render_frame_graph.py |

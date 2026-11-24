# GSB Bugfix Task

## 题面

材质系统统一之后，常量缓冲上传成了新瓶颈：材质库里有大量参数完全相同的块，却各自占一段偏移反复上传；手写的偏移没有按 16 字节对齐，vec3 后面直接跟 float，GPU 读到的值整体错位；帧间只改了一个材质，整块缓冲仍然全量重传；长时间运行后不再使用的材质越积越多，容量被撑满，新材质申请不到空间，只能靠重启解决。

范围是 gsb/batch47-game-d-04 下的 uniform_buffer_packer.py，类 UniformBufferPacker，负责材质参数布局计算、值更新、偏移分配、内容去重与回收。公开接口：`UniformBufferPacker(*, capacity: int = 1024, max_slots: int = 64, retention_frames: int = 3)`；`define_material(material: str, *, params) -> dict`；`layout(material: str) -> dict`；`set_values(material: str, values: dict, *, frame: int) -> dict`；`pack(*, frame: int) -> dict`；`slot_of(material: str)`；`release(material: str) -> bool`；`gc(*, frame: int) -> int`；`snapshot() -> dict`；`restore(state: dict) -> None`；`stats() -> dict`；`reset_stats() -> None`；`reset() -> None`。拒绝统一抛 PackError(code, detail)。

故障与边界：一、float 与 int 占 4 字节、vec2 按 8、vec3 按 12 但起始对齐 16、vec4 按 16、mat4 按 64 且起始对齐 16，数组元素一律按 16 字节步长展开，整块大小向上取到 16 的倍数；二、参数类型不认识、参数列表为空、参数重名、数量为零或条目形状不对，分别在定义时拒绝；三、同一材质用完全相同的布局重复定义是幂等返回，布局有变化要报物料冲突；四、set_values 的键集合必须完全一致，多键、缺键、标量位置传列表、向量长度不对、int 位置传小数或布尔，都要报值形状错误；五、内容完全相同的重复 set 返回 changed 为假、版本不变，内容变化才把版本加一并置脏；六、pack 只处理脏材质，按材质名升序分配偏移，布局与值都相同的块共用一个偏移并计入去重；七、没有新内容时 pack 不产生任何写入，块数和已用字节保持不变；八、偏移加块大小超过 capacity 时 pack 整体失败，不得留下半分配的块，错误要计入上限失败次数；九、活动块数超过 max_slots 同样整体失败，去重之后能放下就应成功；十、release 只解除绑定并丢掉该材质的值，腾出的空洞要等 gc 才回收；十一、gc 只淘汰 frame 减去最后写入帧大于 retention_frames 的材质，之后按材质名重新紧凑排列并重算偏移；十二、快照覆盖布局、值、块、绑定、游标和统计，恢复后偏移与版本必须一致，非法快照整体拒绝且不改动现有状态。

硬约束：布局一旦定义就不允许静默改变，热更材质必须明确报冲突；set_values、pack、gc 共用同一把锁，多线程对同一材质写入相同内容只允许一次版本变化，对八个不同材质并发写入后一次 pack 必须全部写入；pack 失败必须原子，失败后内部块、绑定、游标和脏标记与调用前完全一致；去重、分配和 gc 紧凑顺序不得依赖字典或哈希顺序。

验收：`python3 -m pytest -q tests` 的 15 条测试全部通过；不得修改测试、跳过或按固定输入写死；并发与恢复场景自行验证；只做本地实现。

## 运行方式

```bash
python3 -m pytest -q tests
```

## 平台字段

- domain: 游戏、图形与多媒体
- sub: 图形渲染
- type: Bugfix
- difficulty: 困难

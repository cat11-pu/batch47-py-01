# 需求说明

## 题面

财务共享盘的权限每周都要被投诉一次：主管在部门目录上设了默认 ACL，新建的子目录却只继承了 r-x，新入职的同事拿不到写权限；另一个目录里给审计员单独加了 user 条目但忘了 mask，审计员实际拿到了 rwx，等于能改凭证文件；运维用脚本批量导出再导入 ACL 时，`mask::` 行被丢掉，导入失败还留下半套规则。上周有实习生在自己的目录里建了一个同名子目录，把部门默认 ACL 整个覆盖掉，回收站里也找不回来。权限组试过用 setfacl 手工修，修完第二天又被新脚本覆盖，大家现在不敢在共享盘上批量建目录，运维每天人工比对权限仍然对不上。这次要补的是 gsb/batch47-safety-e-08 下的 acl_inherit_guard.py，类 AclInheritGuard，负责 POSIX ACL 的登记、默认 ACL 继承、mask 计算、有效权限判定和文本互转。公开接口：`AclInheritGuard(*, umask: int = 0o022)`；`ensure_path(path: str, *, kind: str = "dir", owner: str, group: str, mode: int = 0o755) -> dict`；`set_access(path: str, *, owner: str | None = None, group: str | None = None, entries) -> dict`；`set_default(path: str, entries) -> dict`；`get_access(path: str) -> dict`；`get_default(path: str) -> list | None`；`create_child(parent: str, name: str, *, kind: str = "file", mode: int = 0o666, owner: str = "", group: str = "") -> dict`；`effective(path: str, *, user: str, groups=()) -> str`；`to_text(path: str) -> str`；`from_text(path: str, text: str) -> dict`；`remove(path: str) -> int`；`list_paths() -> list`；`snapshot() -> dict`；`restore(state: dict) -> None`；`stats() -> dict`；`reset_stats() -> None`。失败统一抛 AclError(code, detail)。故障与边界：一、access ACL 必须同时含 user::、group::、other::，缺一条抛 ACL_INCOMPLETE；二、tag 只认 user、group、other、mask，权限串必须是三位且每位取自 rwx 或减号，同一 tag 加限定符重复出现，分别抛 ACL_INVALID_TAG、ACL_INVALID_PERMS、ACL_DUPLICATE；三、存在具名 user 或 group 条目而没给 mask 时，mask 取 group 类条目权限的并集，给了 mask 则具名条目和 group:: 的有效权限都要与 mask 相与；四、effective 判定顺序为所有者、具名 user、所属组与具名组并集、other，所有者不受 mask 限制；五、路径不存在抛 ACL_NOT_FOUND，对普通文件设默认 ACL 抛 ACL_NOT_DIR；六、父目录有默认 ACL 时，新建子项按默认条目继承并与请求 mode 的对应类位相与，不再套用 umask；七、父目录没有默认 ACL 时，文件权限为 mode 与 umask 取反后相与；八、新建目录同时继承父目录默认 ACL 作为自己的默认 ACL，文件不继承默认 ACL；九、create_child 的名字含斜杠或是点、点点抛 ACL_INVALID_NAME，目标已存在抛 ACL_EXISTS，kind 非 file 或 dir 抛 ACL_INVALID_KIND；十、to_text 按 user::、具名 user、group::、具名 group、mask::、other:: 的固定顺序输出，from_text 解析失败抛 ACL_INVALID_TEXT 且不改动原 ACL；十一、remove 递归删除并返回删除节点数，删根抛 ACL_ROOT，删不存在的路径抛 ACL_NOT_FOUND；十二、snapshot 深拷贝全部节点、默认 ACL、umask 与统计，restore 结构非法抛 ACL_SNAPSHOT_INVALID 且原状态不变，stats 分开记 created、updated、removed、rejected。硬约束：set_access、set_default、create_child 与 remove 串行执行，多线程同时在同一父目录下创建同名子项只能成功一次；继承与 mask 计算必须在同一临界区内完成，不允许出现继承了一半的 ACL；文本导入要么整体生效要么完全不生效；同一份 ACL 文本导入到任何节点都必须得到完全相同的条目与有效权限。验收：`python3 -m pytest -q tests` 的 15 条测试全部通过；不得修改测试、跳过或按固定输入写死；并发写入和快照恢复场景自行验证；只做本地实现。

## 运行方式

```bash
python3 -m pytest -q tests
```

## 平台字段

- domain: 安全
- sub: 系统安全
- type: Bugfix
- difficulty: 困难

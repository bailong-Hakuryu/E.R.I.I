# E.R.I.I. 数据生命周期 / Data Lifecycle

本文是 `0.4.0` 稳定源码里程碑的数据生命周期操作手册；其生命周期语义来自已接受
的 `0.4.0b1` 基线
`f6dca322379c4ea88320c69d752cab471d035e95`。最后一个历史发布仍是
`0.4.0a8`。项目不要求为后续 `0.x` 分发包；复现时应固定 full commit SHA。所有写
操作都遵循同一顺序：

```text
inspect（只读识别） → plan（零写入 dry-run） → execute（执行并终态验证）
```

示例只应在**没有活跃写入者**的可信本地目录中运行。宿主应先停止 worker、关闭
Engine/Storage，并确保来源、备份和目标的父目录已经存在。Lifecycle Plan 不包含
聊天正文，但包含绝对路径、数据指纹和选择器；它仍属于敏感运维元数据。

English quick reference is available [below](#english-quick-reference). The
Python calls are identical in both languages.

## 先检查，不要猜格式

```python
from erii import (
    DataLifecycleCoordinator,
    LifecycleTarget,
    LifecycleTargetKind,
)

lifecycle = DataLifecycleCoordinator()
sqlite_target = LifecycleTarget(
    LifecycleTargetKind.SQLITE,
    "./data/erii.db",
)
assessment = lifecycle.inspect(sqlite_target)

print(assessment.status.value)       # missing / empty / current / migration_required
print(assessment.detected_version)   # 例如 "9"；missing/empty 可为 None
print(assessment.current_version)    # "9"
print(assessment.file_count)
print(assessment.fingerprint)        # 无正文 SHA-256 身份
```

`inspect()` 不创建文件、目录、manifest 或 SQLite sidecar。未来未知格式、损坏 JSON、
活动 WAL/journal、链接和不稳定来源会显式失败，而不是“尽力读取”。

## 可验证备份与缺失目标恢复

三种 live target 都可以备份：`FILE_STORAGE`、`SQLITE`、`MEMORY_PACK`。

```python
from pathlib import Path

from erii import (
    BackupRequest,
    LifecyclePlan,
    LifecycleTarget,
    LifecycleTargetKind,
    RestoreRequest,
)

Path("./backups").mkdir(parents=True, exist_ok=True)
backup_target = LifecycleTarget(
    LifecycleTargetKind.BACKUP,
    "./backups/erii-before-change.eriibak",
)

source = lifecycle.inspect(sqlite_target)
backup_plan = lifecycle.plan(
    BackupRequest(source=source, destination=backup_target)
)

# 可保存后在另一个进程重载；execute 会再次检查来源。
serialized = backup_plan.to_json()
backup_report = lifecycle.execute(LifecyclePlan.from_json(serialized))
print(backup_report.to_dict())

Path("./restored").mkdir(parents=True, exist_ok=True)
restored_target = LifecycleTarget(
    LifecycleTargetKind.SQLITE,
    "./restored/erii.db",
)
backup = lifecycle.inspect(backup_target)
restore_plan = lifecycle.plan(
    RestoreRequest(backup=backup, destination=restored_target)
)
restore_report = lifecycle.execute(restore_plan)
print(restore_report.outcome.value)  # applied / already_complete
```

恢复保持备份中的原格式和字节/语义身份，只发布到缺失目标，不覆盖已有文件或目录。
若备份的是旧 schema，恢复出来的仍是旧 schema；升级是下一节的独立操作。

可直接运行：
[`examples/lifecycle_backup_restore.py`](../examples/lifecycle_backup_restore.py)。

## 并排升级旧格式

升级始终保留旧来源，并在改变格式前生成独立、可验证的备份。当前支持：

- FileStorage `legacy → 1`；
- SQLite schema `6 → 9`；
- 版本目录中所有旧的可读 MemoryPack → `0.4.0a8`。

SQLite schema `0`–`5`、`7`、`8` 可以被 inspector 识别，但 b1 没有为它们声明经过
fixture 验证的升级路线。不要因为 assessment 返回版本号就构造 `UpgradeRequest`，
也不要用 `SQLiteStorage` 打开它们来触发隐式迁移。

```python
from erii import UpgradeRequest

old_target = LifecycleTarget(
    LifecycleTargetKind.SQLITE,
    "./legacy/erii-schema6.db",
)
new_target = LifecycleTarget(
    LifecycleTargetKind.SQLITE,
    "./upgraded/erii-schema9.db",
)
pre_upgrade_backup = LifecycleTarget(
    LifecycleTargetKind.BACKUP,
    "./backups/schema6.eriibak",
)

old = lifecycle.inspect(old_target)
assert old.status.value == "migration_required"

plan = lifecycle.plan(
    UpgradeRequest(
        source=old,
        destination=new_target,
        backup_destination=pre_upgrade_backup,
    )
)  # 到这里来源、目标和备份都没有被写入
report = lifecycle.execute(plan)
print(report.outcome.value)
```

来源、升级目标和备份必须互不重叠，两个目标必须原先不存在。不要把升级目标直接写成
旧来源路径；b1 不提供任意原地升级或 downgrade。SQLiteStorage 打开旧 schema 会
要求迁移，不再把“构造 Storage 对象”当作受支持的迁移流程。

## MemoryPack 原子导入到全新 Storage

下面的入口与 `ERIIEngine.import_memory()` 的在线合并语义不同：它在隔离 staging 中
调用生产导入校验，并只在完整成功后发布一个全新的 FileStorage v1 或 SQLite v9。

```python
from erii import MemoryPackImportRequest

pack_target = LifecycleTarget(
    LifecycleTargetKind.MEMORY_PACK,
    "./exports/relationship.erii",
)
fresh_sqlite = LifecycleTarget(
    LifecycleTargetKind.SQLITE,
    "./imported/relationship.db",
)

pack = lifecycle.inspect(pack_target)
plan = lifecycle.plan(
    MemoryPackImportRequest(
        source=pack,
        destination=fresh_sqlite,
    )
)
report = lifecycle.execute(plan)
print(report.details.to_dict())  # 只有 ID、计数与摘要，没有记忆正文
```

目标必须不存在；此入口不向正在运行的 Storage 合并。可选的
`target_agent_id=` 与 `target_user_id=` 必须一起提供，并且不能绕过 Pack 自身的关系
绑定规则；含 Source Turn 或关系权威历史的 Pack 通常只能保持原身份。

## Backup-first 删除

删除只适用于当前 FileStorage v1 或 SQLite v9。下面删除整段关系：

```python
from erii import (
    EraseRequest,
    ErasureScope,
    ErasureSelector,
)

live_target = LifecycleTarget(
    LifecycleTargetKind.SQLITE,
    "./data/erii.db",
)
erase_backup = LifecycleTarget(
    LifecycleTargetKind.BACKUP,
    "./backups/before-relationship-erasure.eriibak",
)
selector = ErasureSelector(
    scope=ErasureScope.RELATIONSHIP,
    agent_id="agent_lumi",
    user_id="user_chen",
    relationship_id="the-stable-relationship-id",
)

source = lifecycle.inspect(live_target)
plan = lifecycle.plan(
    EraseRequest(
        source=source,
        selector=selector,
        backup_destination=erase_backup,
    )
)
report = lifecycle.execute(plan)
print(report.details.inventory.to_dict())
```

另外三种严格 selector：

```python
# 删除一个 Source Turn 及其依赖，并重建仍受影响的投影
turn_selector = ErasureSelector(
    scope=ErasureScope.SOURCE_TURN,
    agent_id="agent_lumi",
    user_id="user_chen",
    relationship_id="the-stable-relationship-id",
    source_turn_id="turn-001",
)

# 删除一个权威 Relationship Event，并由剩余历史重建
event_selector = ErasureSelector(
    scope=ErasureScope.RELATIONSHIP_EVENT,
    agent_id="agent_lumi",
    user_id="user_chen",
    relationship_id="the-stable-relationship-id",
    relationship_event_id="event-001",
)

# 删除该本地存储中与一个稳定用户身份匹配的全部关系
user_selector = ErasureSelector(
    scope=ErasureScope.COMPLETE_USER,
    user_id="user_chen",
    user_identity_id="the-stable-user-identity-id",
)
```

不要从显示名称猜 `relationship_id` 或 `user_identity_id`；使用初始化关系时持久返回的
稳定 ID。selector 缺失、歧义或越过 `Agent × User` 边界时，`plan()` 会失败。

删除成功报告不回显正文。`deleted` 是本 live store 中删除的数量，`rebuilt` 是重新
生成的派生投影，`delegated` 和 `unverified_external` 是宿主仍需处理或内核无法证明
已删除的副本。**预删除 Lifecycle Backup 仍含原数据**；外部向量库、已导出的 Pack、
复制的数据库、日志、云备份和远程服务也不会被自动清除。

删除早期 Source Turn 或 Relationship Event 还会撤销依赖它的后续权威：如果一个
Relationship Processing Run 冻结的事件/裁决前缀包含被删历史，该 Run、它产生的
Event、Reflection、Growth 和归档记忆，以及继续依赖它的后续 Run 都会被确定性移除。
未被 selector 命中的原始 Source Transcript 仍保留；但如果其现代
`TurnContextBaseline` 曾声明已经看过现已删除的账本前缀，该 Turn 会降级为
`turn-record/v1` 的 Legacy-unavailable 权威，而不是伪造一次从未发生过的重新审查。
宿主以后可以用显式 historical reprocessing 从保留的聊天重新提取；删除本身不会
自动让模型重新解释过去。

发布 staging 前，协调器会用生产 MemoryPack 导出路径导出受影响关系，再导入一个
全新的同类型临时 Storage。只有这个语义往返也成功，变更才可以替换 live target；
仅仅“文件能打开”或物理摘要匹配不算完成。

## 只重建关系投影

当权威事件账本仍正确、但派生的 Current Belief、Relationship State、Episode 或
Chapter 需要核对时，可以只重建一段关系：

```python
from erii import RebuildRequest

rebuild_backup = LifecycleTarget(
    LifecycleTargetKind.BACKUP,
    "./backups/before-rebuild.eriibak",
)
source = lifecycle.inspect(live_target)
plan = lifecycle.plan(
    RebuildRequest(
        source=source,
        selector=selector,  # 必须是 RELATIONSHIP selector
        backup_destination=rebuild_backup,
    )
)
report = lifecycle.execute(plan)
for proof in report.details.rebuild_proofs:
    print(proof.to_dict())
```

重建不删除权威事件，也不会从聊天文本推测新事件；它只从仍有效的权威关系历史执行
生产 projector、consolidator 与时间验证器。

## 故障恢复与重试

1. 保留原计划 JSON；不要在失败后用新的路径或 selector 假装是同一次操作。
2. 检查异常类型和 `recovery_status`。不要自动删除一个已经可见但最终校验失败的
   目标。
3. 升级、删除和重建若已经留下匹配的 verified backup，可用同一 plan 精确重试。
4. 若要回退，把该 backup `RestoreRequest` 到一个缺失路径，验证后由宿主显式切换；
   不要覆盖 live target。
5. 删除完成后按产品留存政策单独处理预删除备份、外部向量索引、Pack 和其他副本。

## 资源与安全边界

- 文件和目录复制/哈希采用至多 1 MiB 的流式块；SQLite 语义摘要也按游标流式读取。
- MemoryPack 生命周期输入上限为 256 MiB；需要物化的 transform 上限为 512 MiB；
  backup manifest 上限为 16 MiB。
- 这些限制不能代替产品配额、速率限制或不可信上传隔离。
- SHA-256 和 plan digest 用于损坏/漂移检测，不是签名、MAC 或来源认证。
- 数据、Pack 和备份默认明文。锁只协调可信、遵守协议的宿主，不抵抗拥有目录写权限
  的恶意同机进程。
- Windows 的目录项掉电持久性不宣称与 POSIX `fsync` 等价。

详细支持矩阵见 [`compatibility.md`](compatibility.md)，完整安全边界见
[`../SECURITY.md`](../SECURITY.md)。

## English quick reference

`0.4.0b1` exposes one lifecycle flow: read-only `inspect`, zero-write `plan`,
then terminally verified `execute`. Use it only after all writers are stopped
and the live paths are in trusted local directories.

- Backup/restore supports FileStorage, SQLite and MemoryPack. Restore is
  byte/format preserving and only publishes to a missing destination.
- Side-by-side upgrade supports FileStorage `legacy → 1`, SQLite `6 → 9`, and
  every declared older readable MemoryPack → `0.4.0a8`. A verified backup is
  published first; the source remains unchanged. Other identifiable historical
  SQLite schemas are not verified upgrade routes.
- `MemoryPackImportRequest` atomically publishes a validated pack into a fresh,
  missing FileStorage v1 or SQLite v9. It is not a merge into an online store.
- `EraseRequest` covers relationship, Source Turn, Relationship Event and
  complete-user scopes. `RebuildRequest` recomputes one relationship's derived
  projections. Both are backup-first.
- Removing an earlier turn or event revokes every processing run whose frozen
  journal prefix depended on it, plus transitively derived events and memories.
  Unselected source transcripts remain, but affected modern turns lose their
  unprovable context baseline and become explicit legacy-unavailable records;
  erasure never fabricates a historical re-review.
- Before publication, the affected relationship must survive a production
  MemoryPack export/import round trip into a fresh store of the same adapter.
- Reports contain IDs, digests and aggregate counts, not content. A successful
  erasure does not delete its pre-change backup, vector indexes, exported packs,
  logs, remote-provider copies or other external replicas.
- Plan v3 is the current writer; readers accept strict v1–v3. MemoryPack is
  capped at 256 MiB, materialized transforms at 512 MiB and backup manifests at
  16 MiB.

The executable snippets in the Chinese sections use the public API directly;
copy them unchanged. See [`b1-implementation-contract.md`](b1-implementation-contract.md)
for invariants and [`../SECURITY.md`](../SECURITY.md) before production use.

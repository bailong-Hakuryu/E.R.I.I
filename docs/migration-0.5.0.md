# Migration Guide: 0.4.0 → 0.5.0

本文档描述从 `0.4.0` 升级到 `0.5.0a1` 的迁移步骤和注意事项。

## 概览

`0.5.0` 引入了 **Relationship Consequence** 和 **Narrative Tension** 系统，用于追踪关系决策的长期影响和叙事张力状态。此升级包含：

- 新的领域模型和 API
- SQLite schema 升级（v9 → v10）
- MemoryPack wire 格式升级（`0.4.0a8` → `0.5.0a1`）
- 新的 REST API 端点

## 数据库迁移

### SQLite Storage

SQLite schema 从 v9 升级至 v10，新增两张表：

```sql
CREATE TABLE relationship_consequences (
    consequence_id TEXT PRIMARY KEY,
    relationship_id TEXT NOT NULL,
    tension_id TEXT NOT NULL,
    source_turn_id TEXT NOT NULL,
    source_revision TEXT NOT NULL,
    source_decision_id TEXT NOT NULL,
    source_event_id TEXT NOT NULL,
    source_message_id TEXT NOT NULL,
    effects TEXT NOT NULL,
    summary TEXT NOT NULL,
    recorded_at TEXT NOT NULL
);

CREATE TABLE narrative_tension_links (
    link_id TEXT PRIMARY KEY,
    relationship_id TEXT NOT NULL,
    tension_id TEXT NOT NULL,
    consequence_id TEXT NOT NULL,
    source_turn_id TEXT NOT NULL,
    source_revision TEXT NOT NULL,
    source_decision_id TEXT NOT NULL,
    source_event_id TEXT NOT NULL,
    outcome TEXT NOT NULL,
    summary TEXT NOT NULL,
    recorded_at TEXT NOT NULL
);
```

**迁移步骤**：Lifecycle API 使用 `inspect → plan → execute`，升级写入新文件，
并在发布新文件前生成经过验证的备份；不会原地覆盖 v9 数据库。

```python
from erii import (
    DataLifecycleCoordinator,
    ERIIEngine,
    LifecycleStatus,
    LifecycleTarget,
    LifecycleTargetKind,
    SQLiteStorage,
    UpgradeRequest,
)

lifecycle = DataLifecycleCoordinator()
source_target = LifecycleTarget(
    LifecycleTargetKind.SQLITE,
    "path/to/memory-v9.sqlite3",
)
destination_target = LifecycleTarget(
    LifecycleTargetKind.SQLITE,
    "path/to/memory-v10.sqlite3",
)
backup_target = LifecycleTarget(
    LifecycleTargetKind.BACKUP,
    "path/to/backups/memory-v9.eriibak",
)

source = lifecycle.inspect(source_target)
plan = lifecycle.plan(
    UpgradeRequest(
        source=source,
        destination=destination_target,
        backup_destination=backup_target,
    )
)
# plan.to_json() 可先保存供人工审阅；plan() 本身零写入。
report = lifecycle.execute(plan)
upgraded = lifecycle.inspect(destination_target)
assert upgraded.status is LifecycleStatus.CURRENT
assert upgraded.detected_version == "10"

# 使用显式 SQLiteStorage 打开升级副本并验证业务读取。
engine = ERIIEngine(storage_driver=SQLiteStorage(destination_target.path))
try:
    profile = engine.storage.get_relationship("agent", "user")
finally:
    engine.close()
```

### FileStorage

FileStorage 当前格式为 v2。v2 增加 consequence 与 tension journal；v1 数据应使用
同一套 `LifecycleTargetKind.FILE_STORAGE + UpgradeRequest` 流程迁移到新的目录，
并保留自动生成的已验证备份。新的数据写入：

- `_relationship_consequences/` - 关系后果 journal
- `_narrative_tension_links/` - 叙事张力链接 journal

## API 变更

### 新增方法

**Engine 方法**：

```python
# 记录关系后果
consequence = engine.record_relationship_consequence(
    agent_id="agent",
    user_id="user",
    source_turn_id="turn-123",
    source_decision_id="decision-456",
    source_event_id="event-789",
    effects=("harm", "trust_decrease"),
    summary="The choice damaged trust.",
    recorded_at=None,  # 可选，默认当前时间
)

# 记录叙事张力链接
link = engine.record_narrative_tension_link(
    agent_id="agent",
    user_id="user",
    consequence_id="consequence-123",
    source_turn_id="turn-456",
    source_decision_id="decision-789",
    source_event_id="event-012",
    outcome="addressed_unresolved",
    summary="The harm was addressed but remains unresolved.",
    recorded_at=None,
)
```

**Storage 方法**：

```python
# 查询关系后果
consequences = storage.list_relationship_consequences(relationship_id)

# 查询叙事张力链接
links = storage.list_narrative_tension_links(relationship_id)

# 追加后果（内部使用）
storage.append_relationship_consequence(consequence)

# 追加链接（内部使用）
storage.append_narrative_tension_link(link)
```

### REST API 新增端点

```bash
# 记录关系后果
POST /api/v1/relationship/consequences
{
  "agent_id": "agent",
  "user_id": "user",
  "source_turn_id": "turn-123",
  "source_decision_id": "decision-456",
  "source_event_id": "event-789",
  "effects": ["harm", "trust_decrease"],
  "summary": "The choice damaged trust."
}

# 查询关系后果
GET /api/v1/relationship/consequences?agent_id=agent&user_id=user

# 记录叙事张力链接
POST /api/v1/relationship/narrative-tension-links
{
  "agent_id": "agent",
  "user_id": "user",
  "consequence_id": "consequence-123",
  "source_turn_id": "turn-456",
  "source_decision_id": "decision-789",
  "source_event_id": "event-012",
  "outcome": "addressed_unresolved",
  "summary": "The harm was addressed."
}

# 查询叙事张力链接
GET /api/v1/relationship/narrative-tension-links?agent_id=agent&user_id=user
```

## Recall 变更

### Narrative Tension 投影

Structured Recall 结果现在包含 `narrative_tensions` 字段：

```python
from erii.models.recall import RecallRequest, RecallAudience, RecallOptions, RecallBudget

request = RecallRequest(
    agent_id="agent",
    user_id="user",
    query="trust",
    audience=RecallAudience.AGENT_PRIVATE,  # 必须是 AGENT_PRIVATE
    options=RecallOptions(
        persona_delivery="full",
        budget=RecallBudget(max_cost=30_000),
    ),
)

result = engine.recall_structured(request)

# 访问叙事张力投影
for tension in result.narrative_tensions:
    print(f"Tension: {tension.tension_id}")
    print(f"Outcome: {tension.outcome}")
    print(f"Effects: {tension.effects}")
    print(f"Summary: {tension.summary}")
```

### 隐私边界

**重要**：Narrative Tension 投影仅对 `RecallAudience.AGENT_PRIVATE` 可见。公共召回（`RecallAudience.PUBLIC`）不包含后果数据。

```python
# ✅ 可以访问 narrative_tensions
private_result = engine.recall_structured(
    request.model_copy(update={"audience": RecallAudience.AGENT_PRIVATE})
)
assert len(private_result.narrative_tensions) > 0

# ❌ narrative_tensions 为空
public_result = engine.recall_structured(
    request.model_copy(update={"audience": RecallAudience.PUBLIC})
)
assert public_result.narrative_tensions == ()
```

## MemoryPack 格式

### wire 版本与新增字段

`0.5.0a1` writer 会把 MemoryPack `metadata.version` 写为 `0.5.0a1`，并在
根对象中写入两个字段（没有记录时写为空数组）：

```python
pack = MemoryPack(
    agent_id="agent",
    user_id="user",
    # ... 其他字段 ...
    relationship_consequences=[...],  # 新增
    narrative_tension_links=[...],    # 新增
)
```

### 单向兼容边界

- `0.5.0a1` reader 可以读取既有 `0.4.0a8` MemoryPack；缺失的
  `relationship_consequences` 与 `narrative_tension_links` 会解释为空列表。
- `0.4.0a8` reader 不能读取 `0.5.0a1` MemoryPack。旧 reader 对根字段采用严格校验，
  因此会把上述两个新字段识别为未知字段，而不是静默忽略。
- 不提供把包含 consequence 数据的 `0.5.0a1` pack 降级为 `0.4.0a8` 的有损写出。
  若要把旧 pack 固化为新格式，请通过 Data Lifecycle upgrade 生成并校验
  `0.5.0a1` 副本；原文件与备份保持不变。

**导出示例**：

```python
pack = engine.export_memory("agent", "user")

# 检查是否包含 consequence 数据
print(f"Consequences: {len(pack.relationship_consequences)}")
print(f"Tension Links: {len(pack.narrative_tension_links)}")
```

## 生命周期删除

### 级联删除行为

删除操作现在会级联删除相关的 consequence 和 tension link：

- **删除 Relationship Event**：删除以该 event 为来源的 consequence 和 link
- **删除 Source Turn**：删除以该 turn 为来源的所有 consequence 和 link
- **删除 Relationship**：删除该关系的所有 consequence 和 link

**示例**：

```python
from erii.lifecycle_erasure import ErasureSelector, ErasureScope, erase_staged_storage

selector = ErasureSelector(
    scope=ErasureScope.RELATIONSHIP_EVENT,
    agent_id="agent",
    user_id="user",
    relationship_id="rel-123",
    relationship_event_id="event-789",
)

result = erase_staged_storage(
    "path/to/memory.sqlite3",
    "sqlite",
    selector,
)

# 查看删除统计
print(result.inventory.counts["deleted"])
# {'relationship_event': 1, 'relationship_consequence': 2, 'narrative_tension_link': 1}
```

### 重建证明

重建证明现在包含 consequence 和 tension 的完整性指标：

```python
proof = result.rebuild_proofs[0]
print(f"Consequence count: {proof.consequence_count}")
print(f"Tension link count: {proof.tension_link_count}")
print(f"Tension count: {proof.tension_count}")
print(f"Tension digest: {proof.tension_digest}")
```

## 来源校验规则

### 记录 Consequence 的前置条件

Consequence 只能从满足以下条件的 Turn 记录：

1. **Turn 已完成**：`turn.status == TurnStatus.COMPLETED`
2. **最终回复已精确展示**：`turn.delivery_disposition == SHOWN`；
   `SHOWN_UNREVIEWED` 不具备 consequence authority
3. **连续性受支持**：review 为 `REVIEWED`，且 verdict 是 `ALIGNED` 或
   `SUPPORTED_NEW_CHOICE`
4. **Review 与最终消息绑定**：relationship、turn、reply 长度与 SHA-256 均一致
5. **Event 已接受**：decision outcome 是 `ACCEPTED`，event 属于该 decision，
   且其证据精确引用最终 Agent message

**写入即校验**：公开 API 会在持久化前原子校验完整来源链；不要调用内部
Coordinator 做预检查。

```python
try:
    consequence = engine.record_relationship_consequence(
        agent_id="agent",
        user_id="user",
        source_turn_id="turn-123",
        source_decision_id="decision-456",
        source_event_id="event-789",
        effects=("harm", "trust_decrease"),
        summary="The shown choice damaged trust.",
    )
except ValueError as exc:
    print(f"来源链未通过校验: {exc}")
```

## 故障排除

### SQLite 迁移失败

如果 SQLite 迁移失败，恢复操作同样使用 `inspect → plan → execute`，且只发布到
一个尚不存在的新目标路径：

```python
from erii import (
    DataLifecycleCoordinator,
    LifecycleTarget,
    LifecycleTargetKind,
    RestoreRequest,
)

lifecycle = DataLifecycleCoordinator()
backup_target = LifecycleTarget(
    LifecycleTargetKind.BACKUP,
    "path/to/backups/memory-v9.eriibak",
)
restore_target = LifecycleTarget(
    LifecycleTargetKind.SQLITE,
    "path/to/restore/memory-v9-restored.sqlite3",
)
backup = lifecycle.inspect(backup_target)
restore_plan = lifecycle.plan(
    RestoreRequest(backup=backup, destination=restore_target)
)
restore_report = lifecycle.execute(restore_plan)
```

### Consequence 写入失败

常见错误：

1. **Turn 未完成**：确保在 `complete_turn()` 之后记录 consequence
2. **连续性未评估**：确保 Turn 包含 `continuity_result`
3. **Event 不存在**：确保 `source_event_id` 对应的 event 已通过 adjudication

### Recall 不返回 Tension

检查：

1. **Audience 设置**：必须是 `RecallAudience.AGENT_PRIVATE`
2. **Relationship 初始化**：确保 relationship 已初始化
3. **Consequence 存在**：确认该关系确实有 consequence 记录

## 最佳实践

### 何时记录 Consequence

在以下场景记录 consequence：

- 用户做出显著影响关系的选择
- Agent 的回复产生明确的情感影响（伤害、信任变化）
- 边界被测试或违反
- 承诺被做出或违反

### 何时记录 Tension Link

在后续对话中：

- 用户或 Agent 明确提及之前的后果
- 采取行动试图修复之前的伤害
- 声明后果已解决或关系终止

### 效应标签建议

使用清晰、一致的效应标签：

- `harm` / `comfort` - 伤害或安慰
- `refusal` / `anger` / `conflict` - 拒绝、愤怒或冲突
- `boundary_expression` - 明确表达边界
- `trust_decrease` - 信任下降
- `temporary_distance` / `relationship_end` - 暂时疏远或关系终止
- `repair_attempt` / `repair_refused` - 修复尝试或修复被拒绝

## 兼容性承诺

- `0.5.x` 系列不会破坏 `0.5.0a1` 的 consequence 数据格式
- SQLite schema v10 在 `0.5.x` 期间保持稳定
- MemoryPack wire `0.5.0a1` 在 `0.5.x` 期间保持稳定；兼容承诺是新 reader
  读取 `0.4.0a8`，不是旧 reader 读取新 pack

## 相关文档

- [Data Lifecycle](data-lifecycle.md) - 生命周期管理详细说明
- [Domain Model](domain-model.md) - 领域模型概览
- [API Stability](api-stability.md) - API 稳定性策略
- [CHANGELOG](../CHANGELOG.md) - 完整变更日志

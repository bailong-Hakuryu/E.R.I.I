# Migration Guide: 0.4.0 → 0.5.0

本文档描述从 `0.4.0` 升级到 `0.5.0a1` 的迁移步骤和注意事项。

## 概览

`0.5.0` 引入了 **Relationship Consequence** 和 **Narrative Tension** 系统，用于追踪关系决策的长期影响和叙事张力状态。此升级包含：

- 新的领域模型和 API
- SQLite schema 升级（v9 → v10）
- MemoryPack 格式扩展（向后兼容）
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

**迁移步骤**：

1. 使用 Lifecycle Coordinator 备份现有数据库：

```python
from erii.data_lifecycle import DataLifecycleCoordinator

coordinator = DataLifecycleCoordinator()
backup_result = coordinator.backup(
    source_path="path/to/memory.sqlite3",
    source_kind="sqlite",
    backup_parent_dir="path/to/backups",
)
```

2. 运行 SQLite schema 升级：

```python
from erii.lifecycle_sqlite_upgrade import upgrade_sqlite_schema

upgrade_sqlite_schema(
    "path/to/memory.sqlite3",
    target_version=10,
    backup_parent_dir="path/to/backups",
)
```

3. 验证升级结果：

```python
from erii.engine import ERIIEngine

engine = ERIIEngine(storage_dir="path/to/memory.sqlite3")
# 确认可以正常访问
profile = engine.storage.get_relationship("agent", "user")
```

### FileStorage

FileStorage 格式保持 v1 不变，无需迁移。新的 consequence 数据将写入新的 journal 文件：

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

### 新增字段

MemoryPack 新增两个可选字段：

```python
pack = MemoryPack(
    agent_id="agent",
    user_id="user",
    # ... 其他字段 ...
    relationship_consequences=[...],  # 新增
    narrative_tension_links=[...],    # 新增
)
```

### 向后兼容性

- 旧版本的 MemoryPack（不含 consequence 字段）仍可被 `0.5.0` 正常导入
- 新版本的 MemoryPack 可以被旧版本导入，consequence 字段会被忽略

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
2. **回复已展示**：`turn.delivery_disposition in (SHOWN, SHOWN_UNREVIEWED)`
3. **连续性受支持**：Turn 包含有效的 `continuity_result`
4. **Message 来源明确**：`agent_message.message_id` 存在且非空

**验证示例**：

```python
from erii.core.consequence import RelationshipConsequenceSourceCoordinator

coordinator = RelationshipConsequenceSourceCoordinator(storage)

# 检查 turn 是否满足来源条件
try:
    coordinator.require_turn_consequence_authority(
        "turn-123",
        relationship_id="rel-456",
    )
    print("✓ Turn 满足来源条件")
except ValueError as e:
    print(f"✗ Turn 不满足条件: {e}")
```

## 故障排除

### SQLite 迁移失败

如果 SQLite 迁移失败，可以从备份恢复：

```python
from erii.data_lifecycle import DataLifecycleCoordinator

coordinator = DataLifecycleCoordinator()
coordinator.restore(
    backup_path="path/to/backups/backup-xxx.zip",
    target_parent_dir="path/to/restore",
)
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

- `harm` - 造成伤害
- `trust_increase` / `trust_decrease` - 信任变化
- `boundary_violation` / `boundary_respected` - 边界相关
- `commitment_made` / `commitment_broken` - 承诺相关
- `vulnerability_shared` - 脆弱性分享
- `support_offered` / `support_refused` - 支持相关

## 兼容性承诺

- `0.5.x` 系列不会破坏 `0.5.0a1` 的 consequence 数据格式
- SQLite schema v10 在 `0.5.x` 期间保持稳定
- MemoryPack 的 consequence 字段在 `0.5.x` 期间保持向后兼容

## 相关文档

- [Data Lifecycle](data-lifecycle.md) - 生命周期管理详细说明
- [Domain Model](domain-model.md) - 领域模型概览
- [API Stability](api-stability.md) - API 稳定性策略
- [CHANGELOG](../CHANGELOG.md) - 完整变更日志

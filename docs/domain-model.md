# E.R.I.I. Domain Model

本文记录 `v0.4.0` 关系人格方向的领域结构。术语定义以根目录的 `CONTEXT.md` 为准；这里说明已实现的关系和仍在规划的部分。

## alpha.1 已实现

### Character Blueprint（人设底色）

每段关系保留用户导入的原始人设快照和可选结构化编译结果。原文是权威来源，运行时事件不能覆盖；结构化数据被递归冻结，并随 MemoryPack 一起导出。

### Identity、Persona Instance 与 Relationship

Agent 和 User 各自拥有稳定的内部 Identity ID，外部 `agent_id`、`user_id` 只是当前映射。同一个 Agent 或 User 在不同关系中复用身份 ID，但每个 `Agent × User` 拥有独立的 `relationship_id` 和 `persona_id`。

因此：

- “我们第一次一起看雪”只属于产生该事件的关系；
- 另一个 Agent 不会自动得到这段历史；
- 同一 Agent 面对另一个 User 时从独立关系状态开始；
- 重复初始化同一关系是幂等的，但替换其人设原文会被拒绝。

### Relationship Event（关系事件）

关系事件只追加，不提供原地更新接口。`event_id` 是幂等键；相同载荷重试返回首次写入的事件，不同载荷复用同一 ID 会产生冲突。

当前事件类型包括：共同经历、观察、承诺、冲突、修复、反思和更正。每个事件可携带：

- 可选的发生时间与独立记录时间；
- 对当前认知的设置或撤回；
- 对关系状态维度的小幅变化；
- 作为状态解释的原始事件内容与证据 ID。

### Current Belief 与 Relationship State

当前认知和关系状态不是权威写入记录，而是按追加顺序从全部事件重建的投影。

关系状态包含：

- `familiarity`；
- `trust`；
- `intimacy`；
- `safety`；
- `conflict_tension`。

各维度归一化到 `0.0–1.0`。单个已接受事件的自动变化绝对值不得超过 `0.1`；未知维度和巨大跃迁在写入前被拒绝。每个发生变化的维度保留最近证据事件和叙事解释。

### Storage 与 MemoryPack

FileStorage 和 SQLiteStorage 都实现相同关系行为。SQLite 使用版本化迁移表增加稳定身份、关系档案和追加事件表；打开旧数据库时保留原有节点、时间线与核心记忆。

MemoryPack `0.4.0` 包含人设快照、稳定关系档案和关系事件。导入到空存储时保留稳定 ID；导入到另一个目标关系时会建立隔离副本并确定性重映射事件 ID。

## alpha.2 规划

### Candidate Event 与规则裁决

LLM 输出只能作为候选，不直接进入 Relationship Event。领域规则将校验证据、去重、幂等、置信度和变化限幅，再决定接受、拒绝或转为提案。

### Persona Change Proposal（人格变更提案）

候选变化触及人设底色或产生巨大跃迁时创建。未经宿主或用户明确确认，不进入有效人格。

## alpha.3 规划

### Structured Recall

`RecallResult` 将分别提供人设底色、当前认知、关系状态、事件证据、未完成信号和时间语义，再由可替换 Renderer 生成宿主 Prompt。

### Episode / Relationship Chapter

从事件派生的分层关系章节，必须保留来源引用，并可在事件删除或规则升级后重建。

### Signal

承诺到期、纪念日或未完成事件产生的结构化宿主信号。E.R.I.I. 只向宿主 Agent 提供信号，不直接联系用户。

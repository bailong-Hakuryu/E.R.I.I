# Changelog

本项目的用户可感知变化记录在此文件。版本遵循语义化版本；`0.x` 阶段仍可能出现受控的破坏性变更。

## [0.4.0a3] - 2026-07-28

### Added

- 显式 `PersonaCompiler` Interface、Callable/LLM Adapter、严格的原文区间引用、类型化形成性连接、Meaning Capsule 与完整候选图校验。
- 不可变 Persona Compilation Proposal revision，以及按精确 revision 批准、拒绝、撤销和生成确定性 Manifest 的流程。
- `fresh`、`address_only`、`canonical_continuation` 三种关系前提，Premise Experience 原文核验和定性 Relationship Baseline。
- `recall_structured()`、不可变 `RecallResult`、Agent Private/Public 受众、World Time、Purpose-built Projection、预算报告和强化回执。
- 确定性 Markdown Renderer 与 `render_recall()`；Renderer 不访问存储或 LLM，不截断或静默删除已选语义项。
- `POST /api/v1/recall/structured` REST Interface。
- SQLite Schema v3；MemoryPack `0.4.0a3` 携带 Persona Proposal、Manifest、Premise 与 Baseline。

### Changed

- 结构化召回默认只读；只有显式 `reinforce=True` 才强化预算后最终入选的 MemoryNode。
- `recall()` 保留字符串返回、旧 Markdown 区段和自动强化，内部复用结构化组装链。
- Character Blueprint 精确保留原文首尾，记录 revision、SHA-256、来源格式与名称；旧 `compiled` 字段仅作兼容，不视为获批 Manifest。
- Relationship Projector 从不可变 Baseline 开始折叠真实事件，`event_count` 不包含 Premise Experience。
- 包版本升级为 `0.4.0a3`。

### Compatibility

- 未初始化关系的结构化召回返回明确 `uninitialized`，继续提供旧记忆但不创建人设或关系。
- 旧 FileStorage、SQLite v2 与缺少 a3 字段的 MemoryPack 继续可读；SQLite 原地迁移到 v3。
- Public Recall 在组装阶段排除人设原文、内部独白、内部关系数值和默认私有关系事件。

## [0.4.0a2] - Unreleased

### Added

- `adjudicate_relationship_candidates()`：以 Pydantic Schema 接收完整临时来源 turn 与不可信候选，逐候选完成证据验证、依赖裁决、去重、幂等和原子提交。
- 定性 `RelationshipSignal` 到五维关系状态的确定性规则映射；模型不能提交数值状态变化或人格补丁。
- 最小可核验 `EvidenceReference`、版本化 `DecisionReceipt`、提取/解释置信度分离，以及拒绝候选的最小留存。
- 同一底层经历的 `occurrence_key` 佐证语义，避免重复结算关系影响。
- 固定候选批次指纹与显式 `historical_reprocessing` 运行身份；模型重采样、模型升级或规则升级不会把普通重试变成历史扩张或重写。
- Persona Reflection 的不可变历史保存，以及积累型/转折型 Persona Growth 提案。
- `decide_persona_growth_proposal()`：按精确提案版本记录宿主在对话外作出的批准、拒绝或撤销。
- `TemporalContext`：在宿主指定观察时间时计算间隔，但不通过后台时钟修改关系状态。
- SQLite Schema v2、FileStorage 裁决日志，以及 MemoryPack 对证据、回执和人格成长提案的跨 Adapter 携带。

### Changed

- `get_relationship_snapshot()` 和 `list_relationship_events()` 同时包含可信宿主直写事件与经裁决接受的事件。
- `RelationshipEvent` 增加不可变、可携带的结构化 `metadata`。
- 包版本升级为 `0.4.0a2`。

### Compatibility

- `record_relationship_event()` 保留为可信宿主的兼容接口；不可信 LLM 输出应走候选裁决接口。
- SQLite v1 数据原地保留并通过迁移新增 v2 表；旧 MemoryPack 缺少裁决字段时仍可读取。
- 新关系裁决接口要求存储 Adapter 实现裁决记录与人格成长提案方法，旧记忆接口不受影响。

## [0.4.0a1] - 2026-07-27

### Added

- `initialize_relationship()`：为每个 `Agent × User` 建立独立、稳定的 relationship、persona 与 identity ID。
- 不可静默覆盖的 Character Blueprint 原文快照和递归只读的结构化编译结果。
- `record_relationship_event()`：追加式、按 `event_id` 幂等的关系历史。
- `get_relationship_snapshot()`：从事件重建当前认知、五维关系状态及其证据解释。
- FileStorage 与 SQLiteStorage 的关系内核实现和共享行为契约测试。
- SQLite `schema_migrations`、稳定身份、关系档案和关系事件 Schema。
- MemoryPack `0.4.0` 对关系档案与事件的导入导出。
- `process_pending()` 同步任务消费接口。

### Changed

- `ERIIEngine()` 构造不再自动启动后台线程；宿主使用 `start()` 显式启动。
- REST 参考宿主在 Engine 配置阶段显式启动归档 Worker。
- 包版本升级为 `0.4.0a1`。

### Compatibility

- 旧 SQLite 表会原地保留并由迁移框架增加新表。
- MemoryPack 仍可读取缺少关系字段的旧格式。
- 第三方存储适配器可继续用于旧记忆接口；调用关系人格接口前需要实现新的关系存储方法。

## [0.3.1] - 2026-07-27

### Fixed

- 恢复对 `<think>` 推理标签、Markdown 代码块和附加文本中 JSON 对象的归档解析。
- 持久任务队列使用处理租约恢复崩溃后遗留的 `PROCESSING` 任务。
- 多个队列实例通过 SQLite 写事务原子认领任务。
- REST 模块导入不再立即创建 Engine、数据库或后台线程。
- `erii serve --storage-dir` 现在会配置实际使用的服务 Engine。
- 默认任务数据库跟随自定义存储目录；默认旧路径存在时继续读取旧任务数据库。
- setuptools 包发现仅包含 `erii*`，避免示例记忆目录阻断 wheel 构建。

### Changed

- 包、REST OpenAPI 与健康检查统一使用 `0.3.1` 版本来源。
- README 围绕共同回忆与关系连续性重写，并明确当前安全和维护边界。
- 官方示例和测试移除具体第三方作品角色痕迹，统一使用原创占位角色 Lumi。
- ADR 更正关键词召回、任务可靠性、依赖和兼容性方面的不准确表述。

### Added

- GitHub Actions 测试与构建工作流。
- Ruff 静态检查配置。
- 服务 Engine 生命周期与队列崩溃恢复回归测试。

## [0.3.0] - 2026-07-24

- Unicode 标识符与哈希文件路径。
- 时间锚定、SQLite 节点 Diff 同步和上下文管理器。
- `remember(user_msg=...)` 兼容别名。

## [0.2.0] - 2026-07-24

- 持久任务队列、MemoryPack、RRF 混合召回和向量接口。

## [0.1.0]

- 双轨时间线与印象节点、衰减、召回强化和基础存储接口。

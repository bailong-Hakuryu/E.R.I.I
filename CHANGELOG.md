# Changelog

本项目的用户可感知变化记录在此文件。版本遵循语义化版本；`0.x` 阶段仍可能出现受控的破坏性变更。

## [Unreleased]

## [0.4.0a8] - 2026-08-02

### Added

- 现代 `turn-record/v2`：每个最终可见回复都原子携带严格判别的 `ContinuityReviewRecord`；成功审查保存五轴 `ContinuityReviewReceipt`，未评估、失败与 Legacy 分支不再伪装成现代通过结论。
- 版本化 `DeliveryExceptionRecord` 与冻结的 `TurnContextBaseline`，将最终回复字节、交付处置、Persona/Manifest/Growth 权威、关系前提及关系历史高水位绑定到同一 Turn 生命周期。
- 类型化 `ContinuityEvidenceRef` 及关系范围内的来源解析；只有最终 `voice_style` Finding 实际引用的激活才投影为不可重放、不可反向激活的 `VoiceActivationTrace`。
- 归档 extractor schema `"2"` 与消息级 `ArchivalEvidenceCitation` / `ArtifactEvidenceReference`：使用精确 Unicode code-point 范围、Source Turn revision、内核解析角色、消息哈希和稳定 Evidence ID 验证 Timeline 与 MemoryNode 来源。
- Recall 产物增加 `ordinary | legacy_context | quarantined_history` 权威层级与引用；Public 只使用 Ordinary，Agent-private 将 Ordinary 与 Legacy 分区渲染，并且只有最终入选的 Ordinary MemoryNode 可以强化。选择器保留上游 keyword/vector RRF 与动态权重顺序，先分类权威再应用 `max_per_type`，不会让高排名 Legacy 提前耗尽 Ordinary 的类型配额。
- 关系候选的连续性例外隔离：引用 `overridden | shown_unreviewed` Agent 消息的候选以 `rejected + continuity_exception_agent_evidence_quarantined` 正常终结并保留精确证据；独立 User-only 候选继续裁决，全部隔离时 Run 为 `completed + no_accepted_events`。`adjudicate_turn_candidates()` 以及精确命中持久 completed Turn 的 direct API 使用 `relationship-turn-adjudication-v1`；真正 transient 的旧入口保持 Legacy，并禁止其 Turn ID 后续被提升为规范 Turn。
- MemoryPack `0.4.0a8` 携带 Turn v2、审查/例外记录、类型化证据、Voice Trace、归档证据闭包、Recall 来源和关系隔离回执；每个 schema `"2"` 产物在首次写入前必须同时闭合到精确 Source Turn 与 tombstone 的类型、稳定 ID、规范载荷 SHA-256 commitment。绑定持久 Turn 的 direct adjudication 会复核 Source Turn、Evidence 与 quarantine 不变量；即使 receipt contract 被降级，只要对应 Turn 仍在 Pack 中也会复核。
- REST Turn、归档、关系处理、Recall 与 MemoryPack 路径往返新的 a8 wire，并保持与 Engine 相同的严格版本、关系范围和错误语义。
- 关系范围内的 Persona Context 计划与 Pipeline Inspection：宿主可以区分已批准 Manifest、旧式完整原文降级、连续性评估覆盖率、待处理通道和连续 no-event 运行。
- 结构化 Recall 产物来源等级与引用，区分完整 Source Turn + Archival 认证、部分来源和旧式未解析记忆。
- FileStorage/SQLiteStorage 一致的语义 Timeline 最近项读取；SQLite Schema v9 保存规范 UTC 排序键与稳定等时刻次序。

### Changed

- 包版本与 MemoryPack 当前格式升级为 `0.4.0a8`；SQLite Schema 升级到 v9，并为新审查、证据与关系处理状态提供 FileStorage/SQLiteStorage 共享契约。
- `recall()` 兼容签名现在委托给与 `recall_structured()` 相同的权威分类、选择、预算和 Renderer，不再让 Legacy 或异常历史绕过现代召回边界；为保持历史 Core Memory 语义，它会在动态 `top_k` 选择之后额外加入带 `legacy_context` 标签的 Core 候选，该候选仍受硬成本预算。
- 新归档提交必须使用 extractor schema `"2"`；schema `"1"` 终态保持不变，未形成批次的提取工作以 `extractor_schema_upgrade_required` 终结，已完整绑定的提交批次继续按原身份原子完成。
- 已批准 Persona Manifest 只有在批准记录仍为当前有效状态时才可用于运行时召回；撤销后立即失效。带 Manifest 的 `FULL` delivery 也遵守当前 `Agent × User` 关系范围，旧数据继续保留显式兼容路径。
- 完整归档回执使用不可变产物指纹认证 MemoryNode/Timeline 内容、Source revision 与提取器描述；现代压缩墓碑把类型、稳定 ID 与规范载荷 SHA-256 保留为不含正文的 `artifact_commitments`。因此压缩后来源投影仍如实标为 partial，但未改写产物可以继续获得普通权威；旧回执/墓碑缺少指纹，或产物被同 ID 改写时，不能获得该认证。
- direct adjudication 没有 frozen candidate，MemoryPack 因而只承诺完整复核持久 Source Turn、Evidence identity 与异常 Agent quarantine，不宣称能像 `relationship-processing-v1` 一样重放普通 accepted Event。旧 transient records 保持 Legacy 可读；未签名 Pack 若被整体改写、删除 Turn 或同步降级字段，仍不具备来源真实性保证。
- 自定义 FileStorage 的默认持久任务队列与该存储目录共置，不再回落到进程工作目录。
- 同步归档若命中既有终态 `FAILED` 回执，现在抛出带该回执的 `ArchivalProcessingError`，宿主不能再把幂等重放误判为成功。

### Compatibility

- `0.4.0a7` 及更早的 FileStorage、SQLite 与 MemoryPack 继续可读；缺少现代审查或消息级证据的记录保持显式 `legacy_unavailable` / `legacy_context`，不会通过内容猜测升级为 Ordinary 权威。
- `0.4.0a8` 是最后一个承诺支持 Python 3.9 的版本；`0.4.0b1` 起最低 Python 版本提升为 3.11，并同步包元数据、CI 与使用文档。
- a8 只保存异常交付、精确证据和候选级拒绝，不提供例外解除 API。`0.5.0a1` 将由具备相应宿主能力的调用者以新的 `historical_reprocessing` 身份引用原 Turn、冻结候选和 a8 拒绝回执，再分别追加 Continuity Authority 与 Relationship Consequence 结论；旧记录不可修改，关系后果不能授予连续性权威，后续连续性批准也不能自动接受旧关系候选。

### Security

- Turn、Finding、Archival Evidence、Recall 与关系候选均强制原始 `Agent × User` 范围；异常 Agent 发言保留“确实说过”的历史事实，但在显式 v0.5 处置前不能自动成为人格、知识、承诺、反思、成长或关系跃迁权威。
- 关系前提、原作经历图和称呼绑定必须与批准 Manifest 的规范值及证据范围精确一致；不匹配输入失败关闭，不会从其他关系继承亲密度或原作角色位置。
- 指纹与闭包校验用于内部自洽和损坏检测，不等同于来源认证、防篡改签名、授权、加密或多租户隔离；这些正式服务安全边界仍不属于 a8。

## [0.4.0a7] - 2026-07-30

### Added

- `process_relationship_turn()` 自动编排 completed Source Turn、严格关系事件提取、确定性裁决与 accepted Event 后置人格反思；宿主分别提供版本化 `RelationshipEventExtractorV1` 与可选 `PersonaReflectionInterpreterV1`。
- 严格 `candidates | no_relationship_event` 提取决定与 `reflection | no_reflection` 解释决定。自动事件候选拒绝 Persona Reflection、Persona Growth 和未知字段。
- 持久 `RelationshipProcessingRun`：完整提取决定在裁决前冻结，同一关系、来源 revision 与处理身份的重试恢复既有运行，不重新采样或扩张历史。
- `get_relationship_processing_run()`、`list_relationship_processing_runs()`、`get_persona_reflection()`、`list_persona_reflections()` 与 `get_relationship_consolidation()` 查询接口；`get_source_processing_outcomes()` 现在映射真实关系通道结果。
- 独立、不可变的 `PersonaReflectionRecord` 及最小 `ReflectionContextProvenance`；合法无反思保留决定但不创建占位记录，反思失败不会撤销 accepted Event。
- Correction 与 Reinterpretation 追加记录并引用旧 `reflection_id`；旧 Event metadata 反思只在 Recall/Growth 中保留只读兼容，不会被伪造成缺少情绪方向、强度与核心含义的正式 Persona Reflection Record。
- 保守、确定性的 Episode 与 Relationship Chapter 投影。只有 occurrence identity、类型化时间链或显式跨事件引用等分组证据才会合并；其他事件留在 `unconsolidated_event_ids`。
- `history_fingerprint` 与 `projection_version` 使叙事投影可验证、可重建；Episode/Chapter 不成为 Relationship State 或关系等级的写入来源。
- `ContinuityEvaluatorV1` 五轴发现、确定性 `ContinuityAggregationPolicyV1`，以及由带来源 Interaction Context Signal 激活的 Contextual Voice Pattern。
- 确定性的 `RelationshipSafetySignalProjector` 与可选、版本化的 `InteractionContextEvaluatorV1`：前者只从当前 Relationship Snapshot 生成 `low | moderate | high` 安全分档，后者只能在当前 Turn 消息、同关系 accepted Event 和宿主观察信号中引用证据并提出获批词表内的情绪。
- FileStorage 与 SQLiteStorage 的关系处理/反思持久化契约，SQLite Schema 升级到 v6；MemoryPack `0.4.0a7` 携带正式反思与全部持久关系处理 run，保留 frozen decision 和可恢复阶段。
- FileStorage 与 SQLiteStorage 的跨实例/进程关系处理 guard，保证同一处理身份在决定持久化前只执行一次首次外部提取/反思调用。
- 每个 Relationship Processing Run 冻结 direct-event 与 adjudication journal 的高水位和完整内容指纹；MemoryPack 携带 direct-event journal 顺序，以常量级 run 元数据精确恢复裁决前史。

### Changed

- 新集成的默认关系路径从“宿主手工构造候选”改为“Source Turn → `process_relationship_turn()`”；手工候选接口继续作为兼容、测试与高级纠错入口。
- Relationship Event 明确保持权威追加历史；Persona Reflection、Episode 与 Relationship Chapter 分别成为独立解释记录和可重建叙事投影。
- 既有 run 可在重启后无需 extractor 读取或继续；是否执行反思作为冻结计划保留。Correction/Reinterpretation 以 target、kind 与 `interpretation_id` 共同形成幂等身份，换用新 ID 可连续追加。
- MemoryPack 导出、精确身份导入与在线关系处理共用同一 guard，不会读取或并发写入事件/run/反思之间的半成品阶段；导入分别保持 direct-event 与 adjudication journal 的 FIFO 顺序，并在任何普通记忆字段写入前精确预检不可变 Relationship/Blueprint 身份、Source Turn、Timeline 稳定 ID、frozen candidate、规范 run 身份/版本、裁决回执、目标与 incoming 的合并时间生命周期、正式反思唯一来源、人格上下文及目标已有账本冲突。
- MemoryPack 导入不再用墙钟 `recorded_at` 推测裁决前史，而是从冻结 journal 高水位重放同一确定性批次；`accepted`、`corroborated`、`rejected` 与 `ignored` 全部执行完整回执比较。
- 所有内核/评估器派生的 Interaction Context Signal 和 Voice Pattern Activation 都绑定当前 relationship 与 Turn；派生信号还必须带有仅由本 Engine 生产器赋予、不会序列化的运行时证明，手工构造或反序列化的来源标签不能授权激活。旧版未绑定派生信号保持可读，但不再具有运行时激活权限。同一 Engine 生命周期内，相同 Turn 输入的情绪评估结果只在有界临时缓存中复用，不进入长期记忆。
- 包版本与 MemoryPack 当前版本升级为 `0.4.0a7`。

### Compatibility

- `0.4.0a6` 及更早的 FileStorage、SQLite 与 MemoryPack 继续可读；迁移不会从旧 metadata 补造不存在的上下文来源。
- Relationship Event 的旧 `reflection` / `correction` 类型继续保留，但不等同于 a7 的独立 Persona Reflection Record。
- Episode 与 Relationship Chapter 不进入 MemoryPack；导入后根据权威事件历史和当前投影策略重建。

### Security

- 所有新运行、反思和投影查询仍严格限定在原始 `Agent × User` 关系内；公开 interaction-context 入口只接受 `host_observed`，不允许宿主伪装 `core_derived` / `evaluator_inferred` 信号。内部派生信号必须同时匹配当前 `relationship_id + source_turn_id + producer_version` 与非序列化运行时证明，评估器引用范围由内核白名单校验。这种范围校验不是认证、授权、加密或多租户隔离；a7 不提供完整的产品安全边界。
- MemoryPack 的 journal 高水位、内容指纹与生产裁决重放用于发现结构、因果和内部自洽性错误，不是来源认证或恶意篡改证明。能够整体重写 Pack 的一方也能重新计算未加密指纹；正式服务必须在内核外提供签名或 MAC、加密、授权和密钥管理。

## [0.4.0a6] - 2026-07-29

### Added

- 面向规范 Source Turn 的可靠归档入口：宿主提供显式、版本化的 `MemoryExtractorV1` 与非敏感 `ExtractorDescriptor`，再调用 `archive_turn()`。
- 严格的 `artifacts | no_memory` 提取结果；成功的 `no_memory` 不会写入占位 Timeline 或 MemoryNode。
- 关系范围内的持久 `ArchivalReceipt`、稳定 `archival_id`、幂等键绑定、请求冲突检测、提取/提交阶段、重试状态和安全结果码。
- 提取期间自动续租 Processing/Consumer Lease；崩溃遗留的过期 attempt 会以 `processing_lease_expired` 进入有界重试，而不是再次无限调用模型。
- 默认 30 天完整终态回执保留期，以及 `compact_archival_receipts()` 到期压缩；最小 tombstone 保留幂等与审计连续性，不删除已经提交的记忆产物。
- `process_pending()` 与 `drain()` 的显式处理接口，以及不隐式排空持久任务的 `close()` / `ShutdownReport`。
- MemoryNode 与结构化 Timeline 的完整来源信息，包括 Source Turn、Source revision、archival、提取器版本和 E.R.I.I. 处理版本。
- FileStorage 与 SQLiteStorage 的可靠归档能力；MemoryNode、结构化 Timeline 与归档终态在一个原子批次中发布，SQLite Schema 升级到 v5。
- MemoryPack `0.4.0a6` 的 `timeline_entries` 与 `archival_ledger`；只携带终态归档的最小 tombstone，不携带运行中任务、原始幂等键或详细运维回执。
- `POST /api/v1/archivals` 与关系范围内的 `GET /api/v1/archivals/{archival_id}`。

### Changed

- `async_archival=True` 下，可靠归档提交只持久化为 `pending`；Engine 构造、`configure_engine()`、`erii serve` 和 `start()` 都不会隐藏消费这条新管线，宿主必须显式调用 `process_pending()` 或 `drain()`。
- `async_archival=False` 下，`archive_turn()` 在当前调用中同步尝试提取和原子提交，并通过回执或类型化异常报告真实结果。
- `close(timeout)` 会等待当前可靠归档 attempt 至超时，并在关闭开始后阻止同一 Engine 领取下一项；同一 Engine 的重叠 `process_pending()` 调用也不会并行复用消费者身份。
- SQLite 的旧式节点快照保存不会删除已经由可靠归档原子提交的节点；MemoryPack 墓碑会在其他内容写入前预检活动回执冲突。
- 包版本与 MemoryPack 当前版本升级为 `0.4.0a6`。

### Compatibility

- `remember()`、旧 LLM Adapter、旧持久任务队列与显式 `start()` 继续作为兼容路径；它们不会自动获得规范 Source Turn 来源或 a6 可靠归档回执。
- 旧 FileStorage 数据继续可读；SQLite v4 数据原地迁移到 Schema v5。
- `0.4.0a5` 及更早的 MemoryPack 继续可读；缺少结构化 Timeline 或归档 tombstone 时按旧数据处理，不伪造来源。
- 携带归档来源的 MemoryPack 与含 Source Transcript 的 Pack 一样，禁止跨 `Agent × User` 重映射。

## [0.4.0a5] - 2026-07-28

### Added

- 关系范围内的规范 Turn Recording 账本，以及 `begin_turn()`、`complete_turn()`、`abandon_turn()`、原子 `record_turn()`、`get_turn()` 与 `list_turns()`。
- `open → completed | abandoned` 单向状态机、稳定 `turn_id` 幂等重试、冲突检测，以及实际可见 User/Agent Source Transcript 的完整持久化。
- 不携带对话正文的 `SourceTurnReceipt`，包含来源 revision、接受时间、固定处理计划与逐通道处理状态。
- FileStorage `_turn_records` 持久化与 SQLite `source_turns` 表；SQLite Schema 升级到 v4。
- MemoryPack `0.4.0a5` 根级 `turn_records` 携带能力。
- `InteractionContextSignal`、不含未展示草稿的 Reply Attempt 失败账本，以及基于持久 `source_turn_id` 的关系候选裁决桥接。
- Turn Recording REST 路由：open、complete、reply attempts、abandon、原子 record、get 与 list。

### Changed

- 规范 Source Turn 只作为可追溯证据；MemoryNode 归档、关系裁决和人格成长继续作为独立派生通道，不因保存原文而自动生效。
- `completed` 与 `abandoned` Turn 是不可变终态；可重试失败应保留 `open`，只有明确终止时才放弃。
- 包版本升级为 `0.4.0a5`。

### Compatibility

- `remember()` 与提交临时 raw Source Turn 的关系裁决接口继续作为 `0.4.x` 兼容路径，但不会自动与规范 Turn Record 建立同源关系。
- 旧 FileStorage 与 SQLite 数据继续可读；SQLite 会原地迁移到 Schema v4。
- `0.4.0a4` 及更早、没有 `turn_records` 的 MemoryPack 继续可读，并保留旧载荷的历史导入行为。
- 包含 `turn_records` 的 Pack 只能恢复到完全相同的 Agent、User 与 relationship 身份；禁止跨 `Agent × User` 重映射，`overwrite=True` 不能绕过。

## [0.4.0a4] - 2026-07-28

### Added

- 不可变 `WorldMoment`、类型化 Promise/Condition Confirmation、Open Loop，以及引用原事件的追加式 Resolution。
- 可信宿主使用的 `record_promise()`、`confirm_promise_condition()`、`resolve_promise()`、`record_open_loop()` 与 `resolve_open_loop()`。
- 不可信模型输入使用的严格时间 Candidate Schema、证据裁决支持，以及 `POST /api/v1/relationship/adjudicate`。
- 同一 World Time 时钟内确定性派生的 `promise_due`、`promise_overdue` 与 `open_loop` Agent Private 召回信号。
- 时间历史完整性校验：关系内引用、单次条件确认、单次解决、有效后继与无环 supersession。
- MemoryPack `0.4.0a4` 对嵌套事件引用的完整性校验与跨用户重映射。
- 完整可信宿主流程示例 `examples/08_temporal_commitments.py`。

### Changed

- 逾期只表示可比较时钟中的时间状态，不自动推断违约、扣减信任或追加关系事件。
- 旧 `MemoryNode.is_unresolved` 仅作为低权威兼容信号；正式 Open Loop 可引用其来源并抑制重复投影。
- 包版本升级为 `0.4.0a4`。

### Compatibility

- SQLite Schema 保持 v3，无需新增迁移；FileStorage 与现有 SQLite 数据继续可读。
- 旧的无类型 Promise 关系事件继续可读，但只有类型化载荷参与新的时间信号投影。
- 旧 MemoryPack 继续可读；只有 `0.4.0a4` 时间载荷需要满足新的引用完整性规则。

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

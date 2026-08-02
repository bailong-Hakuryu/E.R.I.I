# E.R.I.I. Domain Model

本文记录 `0.4.0a8` 已实现的角色连续性与长期记忆领域结构。术语定义以根目录的 [`CONTEXT.md`](../CONTEXT.md) 为准；本文说明各层的权威关系与运行顺序。

## 一条不可跨越的关系边界

每个外部 `Agent × User` 组合拥有独立的 `relationship_id` 与 `persona_id`。Character Blueprint 可以描述同一个角色的身份、形成性经历和表达资源，但当前关系中的共同经历、亲密程度、反思与派生篇章不得自动进入另一段关系。

因此，“我们第一次一起看雪”只属于实际产生该事件的关系。原作中针对特定人物的称呼、共同经历或亲密也不能借由相似表达风格映射给当前 User；只有用户显式选择 `canonical_continuation` 时，获准的原作关系前提才进入该关系。

## 从原文到账本，而不是从原文直接到人格

```text
Character Blueprint（不可静默覆盖的人设底色）
                    │
                    ├── Approved Persona Manifest
                    │     └── Contextual Voice Pattern
                    │
completed Source Turn（完整可见原文、关系范围、固定处理计划）
                    │
                    ├── Continuity Review Record + Delivery Disposition
                    │      └── 五轴 Receipt | not_evaluated | failed | Legacy
                    │
                    ├── Memory Archival ──> Timeline / MemoryNode
                    │      └── Artifact Evidence Reference
                    │
                    └── Relationship Processing Run
                           ├── frozen candidates | no_relationship_event
                           ├── deterministic adjudication
                           ├── accepted Relationship Event（权威历史）
                           └── reflection | no_reflection
                                  └── Persona Reflection Record

Relationship Event history
        ├── Current Belief / Relationship State（可重建状态投影）
        └── Episode / Relationship Chapter / Unconsolidated Event
             （可重建叙事投影）
```

Source Transcript 证明双方实际可见地表达过什么，但不会仅凭被保存就成为事实、长期记忆、Relationship Event、Persona Reflection 或 Persona Growth。Memory Archival 与 Relationship Processing 是共享同一 `source_turn_id`、但结果和失败互不回滚的独立通道。

## Turn 审查、交付事实与来源权威

每个现代 completed Turn 使用 `turn-record/v2`，并原子保存一个 `ContinuityReviewRecord`。`reviewed` 分支保存与最终回复字节绑定的五轴 `ContinuityReviewReceipt`；`not_evaluated`、`failed` 与 `legacy_unavailable` 保持彼此可区分，不能用旧 summary 或当前模型补造一次现代成功审查。

`shown`、`overridden` 与 `shown_unreviewed` 描述最终可见回复如何交付。后两者必须携带 `DeliveryExceptionRecord`，并完整保留 User 实际看见的 Agent 文本，但该文本在 a8 中只拥有“当时确实说过”的历史地位，不会静默获得普通人格、记忆或关系权威。隔离只由持久交付处置触发，不检查情绪正负；一条以 `shown` 正常通过审查的拒绝、愤怒、疏远或伤害性回复仍是普通角色选择。

Turn 开启时冻结的 `TurnContextBaseline` 保存关系、Blueprint、Manifest、已批准 Growth、Premise 与历史前缀的身份和指纹。它用于证明评估当时能看到哪些因果依据，不复制完整历史，也不允许后来事件反向洗白旧回复。

## Character Blueprint、Manifest 与 Persona Growth

Character Blueprint 保存用户导入的人设原文及哈希，是角色底色的权威来源，普通关系事件不能覆盖。Persona Compiler 可以生成带原文引用的结构化 Proposal；只有宿主对精确 revision 的显式审批才能产生 Persona Manifest。

Manifest 中的 Contextual Voice Pattern 描述“角色在什么有来源的情境下可能使用哪种语域”，而不是固定口癖。Engine 只根据带来源的 Interaction Context Signal 生成本轮临时 Voice Pattern Activation；激活不会自动进入长期记忆、关系历史或人格变化。

重要人格变化必须来自已保存的事件与反思之后的独立 Inner Review，并形成待批准 Persona Growth Proposal。提取器置信度、当前情绪或单句回复都不能直接批准人格变化。

## Relationship Processing Run

规范自动流程只处理已封存、且处理计划包含 Relationship Adjudication 的 completed Source Turn：

1. 宿主提供的 `RelationshipEventExtractorV1` 读取规范 Source Transcript，并返回严格的 `candidates` 或 `no_relationship_event`。
2. 自动候选只允许中性事件、精确 Evidence、定性 Relationship Signal、发生信息与显式引用；不得携带 Persona Reflection 或 Persona Growth 意图。
3. 内核在任何裁决前持久冻结完整提取决定，以及 direct-event / adjudication 两本追加日志的高水位与内容指纹。
4. 确定性规则逐候选验证证据、去重、限幅和依赖，再把 accepted Event 原子追加到权威历史。
5. 只有 accepted Event 才交给独立的 `PersonaReflectionInterpreterV1`，其严格结果为 `reflection` 或 `no_reflection`。

手工 direct adjudication 是另一条较窄的兼容路径，不等同于 Relationship Processing Run。`adjudicate_turn_candidates()` 总是从一条持久 completed Turn 构造来源，并使用 `relationship-turn-adjudication-v1`。`adjudicate_relationship_candidates()` 本身不创建 Turn；若其 `turn_id` 已命中同关系持久 Turn，revision、消息 ID、角色、正文与发生时间必须逐项相等，回执随后使用同一 contract 并继承交付隔离。若没有持久 Turn，它仍是 Legacy transient 来源；该 Turn ID 一旦用于裁决，之后不能注册成规范 Turn，避免旧回执被追授现代权威。

若 Turn 的交付处置为 `overridden | shown_unreviewed`，任何引用该 Turn 中 Agent 消息的候选都会以 `rejected + continuity_exception_agent_evidence_quarantined` 正常终结，并保留精确证据；它不会创建 Event、状态变化、Promise、Open Loop、Persona Reflection 或 Growth 输入。同批只引用合法 User 消息的独立候选继续按普通规则裁决，依赖被拒候选的项目按普通依赖失败处理；全部候选被隔离时，run 仍是 `completed + no_accepted_events`，不是技术失败。a8 的 `historical_reprocessing` 不会自动绕过这条边界；完整的追加式例外处置属于 v0.5。

同一关系、`source_turn_id`、source revision 与处理身份的普通重试必须恢复冻结决定，不能再次采样或扩张历史。内置存储在首次外部调用前取得跨实例/进程的关系处理锁；run 一旦存在，恢复它不再依赖 extractor。是否执行后置反思也是冻结输入：计划了反思的 run 在恢复时缺少 interpreter 会保持可恢复状态并显式报错，不能静默降级。显式历史重处理使用 `processing_mode="historical_reprocessing"` 与新的稳定 `reprocessing_id`，只追加新证据、更正或重新理解。

裁决基线使用追加日志位置而不是 `recorded_at`。墙钟时间可以来自导入、不同设备或时钟漂移，不能证明某事件在裁决时是否已经存在。续跑只读取冻结的 journal prefix；同一批次的新 accepted Event 则按候选依赖解析顺序逐个加入。每个 run 只保存两个高水位和一个完整基线指纹，因此空间开销是常量级，而不是为每个 run 复制一遍不断增长的历史。MemoryPack 的 `relationship_direct_event_ids` 是 direct journal 的权威顺序；同 ID、同载荷事件在可信宿主显式追加后可以同时出现在 direct 与 adjudication journal，不能靠“排除裁决事件”反推 direct journal。详见 [ADR-0091](adr/0091-freeze-adjudication-journal-baselines.md)。

反思阶段失败不会撤销已接受事件和已经产生的关系状态变化。合法 `no_reflection` 会保留最小决定记录，但不创建空白 Persona Reflection Record。反思内容、解释器版本与最小上下文来源独立、不可变保存；Correction 与 Reinterpretation 必须追加新记录并引用旧 `reflection_id`，不能覆盖旧理解。

## Relationship Event 与状态投影

Relationship Event 是关系领域的权威追加历史。`event_id` 是幂等键；相同载荷重试返回首次写入结果，不同载荷复用同一 ID 会产生冲突。

Current Belief 与 Relationship State 都从完整事件历史重建。状态包含 `familiarity`、`trust`、`intimacy`、`safety` 与 `conflict_tension`，归一化到 `0.0–1.0`；单个自动事件的变化必须有证据、由版本化规则计算并受全局限幅。Episode、Chapter、MemoryNode 或 Persona Reflection 都不是这些数值的写入权威。

旧 `RelationshipEventType.REFLECTION` / `CORRECTION` 仍作为兼容或可信宿主写入的关系事件存在，但不等同于 a7 独立的 Persona Reflection Record。旧 metadata 仅在 Recall/Growth 中只读兼容；它缺少情绪方向、强度、核心含义与当时上下文，不能自动合成正式记录。`legacy_unavailable` 只保留为未来显式迁移的领域标记。

## 五轴回复连续性

`ContinuityEvaluatorV1` 在回复展示前分别给出五个有来源的 Continuity Finding：

- `identity_values`；
- `psychological_causality`；
- `relationship_scope`；
- `knowledge_memory_scope`；
- `voice_style`。

评估器不能直接给总判定。内核的版本化确定性汇总策略生成 `aligned`、`supported_new_choice`、`review_required` 或 `unsupported_drift`：关系串线、继承错误亲密与角色不可能知道的信息是硬冲突；只有语言风格偏差时可以建议重写，但不能据此宣称人格漂移。

已经展示给用户的回复仍按事实进入 Source Transcript；评估结果不能追溯性删除真实对话，也不能自动成为 Continuity Basis 或 Persona Growth。只有最终 `voice_style` Finding 实际使用的运行时激活会单向投影为 `VoiceActivationTrace`；Trace 可随 Turn 审计和携带，但不能重放为 Activation，也不进入 Prompt、召回、关系投影或人格成长。

公开 Engine 入口只接受 `host_observed` Interaction Context Signal。`RelationshipSafetySignalProjector` 从当前关系的 Baseline、accepted Event 与投影状态确定性生成 `low | moderate | high`；可选 `InteractionContextEvaluatorV1` 只能从当前 User 消息、最多 16 条同关系 accepted Event 与宿主观察信号中，为 Manifest 已批准的情绪词表提出严格、有引用的候选。内核统一为两类派生信号写入 `relationship_id + source_turn_id + producer_version`，并附加不会序列化、只由当前 Engine 生产器赋予的运行时证明；Voice Pattern Activation 也绑定同一 Turn。范围不匹配、手工构造/反序列化的派生来源标签以及旧版未绑定派生标签都不能授权激活。

派生信号、评估决定与 Activation 都是交付前的临时投影。同一 Engine 可按完整输入指纹在当前生命周期内通过有界缓存复用同一 Turn 的情绪评估；Turn 终态会逐轮淘汰，`close()` 后全部清空。这些数据不会写入 Source Transcript、Relationship Event、Persona Reflection、Persona Growth 或长期记忆。

## Episode、Relationship Chapter 与未巩固事件

`RelationshipConsolidation` 是对某一关系事件快照的确定性、可重建叙事投影：

- Episode 只在稳定 occurrence identity、类型化时间链或其他显式分组证据支持时，将一个或多个事件组织为同一具体经历；
- Relationship Chapter 只在显式跨 Episode/Event 引用连接至少两个 Episode 时形成；
- 缺少充分分组证据的事件进入 `unconsolidated_event_ids`，仍完整保留在权威历史中；
- 投影保存 `history_fingerprint` 与 `projection_version`，规则升级后可以重建；
- Episode 与 Chapter 不携入 MemoryPack，因为它们可由权威历史重新生成。

这种保守策略宁可承认“目前不知道这些事件是否属于同一故事”，也不通过语义相似或时间邻近编造关系叙事。详见 [ADR-0090](adr/0090-derive-consolidation-only-from-explicit-grouping-evidence.md)。

## 归档精确证据与召回权威

a8 的新可靠归档使用 extractor schema `"2"`。每个 Timeline/Memory 候选必须携带一到十六个 `ArchivalEvidenceCitation`，以 `TurnMessage.message_id + TurnRecord.source_revision + quote + start/end` 声明精确消息范围。偏移是 Unicode code point 位置，原文切片必须逐字等于 quote；提取器不能自报角色、关系或 Turn 范围。

内核核验后生成不复制 quote 的 `ArtifactEvidenceReference`，保存关系、Source Turn、消息、revision、内核解析角色、消息哈希与范围。完整回执还核对 Source revision，并用产物类型、稳定 ID 与规范不可变提交载荷 SHA-256 认证提交内容；MemoryNode 的强化、访问计数、状态、未决/最新标记、取代关系和最后访问时间等可变召回/生命周期字段不在承诺内。压缩现代回执时，这些不含正文的条目转存为 tombstone `artifact_commitments`。召回会重算当前载荷，因此同 ID 改写不可变提交字段或伪造合法 UUID 不能借用原权威。压缩后的来源投影仍是 partial，因为完整运维回执已经消失，但精确内容 commitment 可以继续支撑 Ordinary；旧墓碑没有 commitments 时只能维持幂等/审计身份。schema `"1"` 产物保持 Legacy 身份，不能通过当前文本相似度升级为现代证据。异常交付中的 User-only 归档候选仍可提交；任一候选引用受隔离 Agent 消息时，整项 Archival Extraction Decision 在形成批次前失败，不能静默裁剪证据。

召回时，`MemoryRecallProjection.authority_tier` 是当前来源和交付权威的派生标签，不是写回 MemoryNode 的第二份真值：

- `ordinary`：现代证据链完整，并通过当前交付权威规则；
- `legacy_context`：缺少现代消息级来源，但没有可证明的异常来源；只在 Agent-private 低权威分区兼容使用；
- `quarantined_history`：可证明来自现代异常交付，却没有足够消息角色证据；默认生成召回排除，但仍可检查、展示标签、导出和删除。

Agent-private Markdown 分别渲染 `Verified Memories` 与 `Legacy Context - provenance incomplete`；Public 召回排除 Legacy 与 Quarantined。MemoryNode 由关键词/向量 RRF 与动态有效权重产生一次上游顺序；权威选择器保留该顺序，先分类 authority 再应用 `max_per_type`，不会以第二套词法相关性重排，也不会让 Legacy 在分区前占用 Ordinary 类型配额。对结构化召回，`top_k` 是两类动态入选记忆的总上限，现代不足时 Legacy 可填充，现代充足且 `top_k >= 2` 时最多预留一个相关 Legacy，精确 UTF-8 内容重复由 Ordinary 胜出。兼容 `recall()` 会在动态选择后额外加入旧 Core Memory 的 `legacy_context` 候选；它不占动态 `top_k`，但仍受硬成本预算。只有最终通过受众过滤和硬预算的 Ordinary MemoryNode 可以强化。

## Storage 与 MemoryPack

FileStorage 与 SQLiteStorage 遵循相同的关系隔离、事件追加、处理运行、反思决定与幂等语义。SQLite `0.4.0a8` 使用 Schema v9；v7-v9 为最近 Timeline 增加有界读取、规范 UTC 排序键和稳定的等时刻顺序。旧数据库在打开时原地迁移，升级 alpha 前仍应保留备份。

MemoryPack `0.4.0a8` 携带规范 Source Turn、现代审查/交付记录、归档 Artifact Evidence 与 tombstone commitments、Relationship Event、direct-event journal 顺序、Persona Reflection 与全部持久 Relationship Processing Run，包括冻结候选、候选级隔离回执、可恢复的非终态/partial 阶段和裁决日志高水位。现代 schema `"2"` 产物要求 Pack 同时包含精确 Source Turn 依赖闭包，以及匹配其类型、稳定 ID 和重新计算的规范载荷 SHA-256 commitment；导入必须在首次写入前重算产物指纹、消息角色、内容哈希、Unicode 范围与 Evidence ID。

`relationship-processing-v1` 有 frozen candidate 与日志基线，因此可由生产裁决器完整重放四种结果。direct adjudication 不保存 frozen candidate：`relationship-turn-adjudication-v1` 的导入闭包只完整复核精确 completed Turn、Evidence identity 与异常 Agent 必须保持非 pivotal、无 Event 拒绝的不变量；普通 accepted direct Event 不能被宣称为完整重放。即使 receipt contract 被降级，只要对应 Turn 仍在 Pack 中也会执行这项复核。真正 transient records 保持 Legacy 可读，导入不会为其补建规范 Turn。

导出、精确身份导入和在线处理共用关系处理 guard；导入只在两本 journal 的队首之间解析因果依赖，因而保留各自 FIFO。写入普通记忆字段前，内核会核验完整不可变 Relationship/Blueprint 身份、精确 Source Turn、Timeline 稳定 ID、规范 run 身份/版本、两本 journal 的前缀兼容与合并时间生命周期，并按冻结 prefix 使用生产裁决器重放四种结果；Reflection Provenance 还必须在目标与 incoming 的合并裁决历史中保持唯一 accepted 来源，并继续匹配 Evidence、Baseline、关系绑定 Manifest、Approved Growth 与真正先前历史。run 不复制完整 Prompt、人设或 Source Transcript；Episode 与 Relationship Chapter 也不导出。包含这些关系来源的 Pack 只能恢复到原来的 `Agent × User` 与 `relationship_id`；`overwrite=True` 也不是跨关系搬运许可。a7 及更早 Pack 继续可读，但缺失的消息级来源、现代审查状态或权威层级不会被推测补造。

FileStorage、SQLiteStorage 与 MemoryPack 都不是认证、授权、加密或多租户安全边界。Pack 内的未加密 contract 标签、commitment 与指纹只证明当前数据彼此可重算、自洽，不能证明文件来自可信导出者；攻击者若能整体改写 Pack，也能删除 Turn、同步降级相关记录并重算高水位与指纹。正式服务必须由宿主在内核外实现签名或 MAC、加密、授权、密钥管理与租户隔离。

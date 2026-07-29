# E.R.I.I. Roadmap

路线图表达当前方向，不是固定发布日期或 SLA。只有满足测试和迁移门槛的能力才会进入正式版本。

## v0.3.1：稳定化

- 统一版本、修复包构建和服务生命周期；
- 恢复 LLM JSON 容错；
- 为持久任务增加租约恢复；
- 清理第三方角色痕迹和不准确宣传；
- 建立 CI、变更日志和维护文档。

## v0.4.0：关系人格基础

### alpha.1：无 LLM 领域内核（已完成）

- 原始人设快照与结构化编译结果；
- 稳定的 persona、relationship 和 identity ID；
- 追加式历史事件；
- 当前认知与关系状态投影；
- SQLite Schema 和迁移框架；
- 宿主显式控制后台处理生命周期；
- 关系档案和事件进入 MemoryPack `0.4.0`。

### alpha.2：候选提取与规则裁决（已完成）

- Pydantic 边界 Schema；
- LLM 只产生候选事件和定性关系信号；
- 证据校验、去重、幂等、置信度和变化限幅；
- Persona Reflection、显式历史重处理与时间上下文；
- 重大人格变化经过独立内在审视后转为待确认提案；
- FileStorage、SQLiteStorage 与 MemoryPack 携带裁决记录。

### alpha.3：人设感知结构化召回（已完成：`0.4.0a3`）

- `RecallResult`；
- 可替换 Prompt Renderer；
- Persona Compiler、审批 Manifest 与规划/完整人设交付；
- 关系前提、定性 Baseline、显式受众和原子预算；
- recorded、occurred 和 world time 携带；
- 默认只读召回与显式 Recall Reinforcement；
- SQLite Schema v3 与 MemoryPack `0.4.0a3`。

### alpha.4：时间承诺与开放事项（已完成：`0.4.0a4`）

- 类型化 Promise、Condition Confirmation 与 Open Loop；
- Promise/Open Loop 的追加式 Resolution；
- 同一 World Time 时钟内的到期与逾期判断；
- 只读、带来源的 Agent Private 召回信号；
- 旧 `is_unresolved` 的低权威兼容投影；
- SQLite Schema 保持 v3，MemoryPack `0.4.0a4` 校验并重映射事件引用。

### alpha.5：统一来源与可信归档生命周期（已完成，`0.4.0a5`）

- 一轮完整 User 与 Agent 交互只拥有一个关系范围内、稳定且版本化的 Source Turn 身份；
- 内核默认永久持久化每个 Source Turn 中双方可见的完整 Source Transcript；永久表示无自动过期但允许数据所有者显式导出和删除；
- Source Transcript 只拥有来源证据地位，不会未经提取与裁决直接成为长期记忆、权威关系历史或人格变化；
- 隐藏系统消息、完整 Prompt、模型内部推理和双方不可见的工具内部输出不属于 Source Transcript；
- 内核定义版本化 ContinuityEvaluatorV1，宿主提供具体实现，并在回复交付前返回 `aligned | supported_new_choice | review_required | unsupported_drift`；
- ContinuityEvaluatorV1 分别输出 `identity_values`、`psychological_causality`、`relationship_scope`、`knowledge_memory_scope` 与 `voice_style` 五类有来源发现，不能直接决定总体结论；
- 内核通过版本化确定性 ContinuityAggregationPolicy 汇总结论：关系串线与不可知信息为硬冲突，人格或因果张力按依据路由，单独语言风格偏差只形成修订建议；
- 正式产品默认只展示前两类回复，后两类由宿主重新生成或在对话外显式处理；内核不自动改写台词或批准人格变化；
- 已经向用户展示的回复无论评估结果如何都进入 Source Transcript，并保存非敏感评估与交付处置；未展示草稿不进入关系历史；
- 可疑或无依据回复可以作为“确实说过”的关系证据，但不能未经独立审查自动成为 Persona Reflection、Continuity Basis 或 Persona Growth 的人格依据；
- `begin_turn()` 在生成回复前持久化稳定 `turn_id`、User 消息和获准的互动情境；`complete_turn()` 原子封存已展示 Agent 回复、连续性评估、交付处置及固定处理计划；
- 可重试的生成或连续性评估失败保持 Turn Record 为 `open`，只保存阶段、版本、时间和脱敏错误分类；未展示草稿不进入 Source Transcript；
- 只有用户取消、宿主显式终止或不可恢复错误才通过 `abandon_turn()` 保留没有 Agent 回复的真实用户消息，且默认不把不完整互动交给只接受 Source Turn 的记忆与关系处理；
- `completed` 与 `abandoned` 均为不可重开的原子终态；相同完成载荷的重试返回既有回执，不同内容或完成/放弃竞争返回显式 Turn Terminal Conflict；
- `record_turn()` 保留为已经同时拥有双方消息时的一次性原子便捷入口，三种操作共享同一 Turn Record 与 Source Transcript 账本；
- Source Acceptance 成功与派生处理完成是两个不同事实；只有 `completed` Turn Record 才形成可处理 Source Turn 并返回包含固定计划与通道状态的 Source Turn Receipt；
- 归档、关系裁决、查询、重试与历史重处理只引用 `source_turn_id`，不要求宿主再次传递完整对话；
- `remember()` 与接收临时 Source Turn 的旧关系裁决形式在 `0.4.x` 内作为兼容门面转调统一来源账本，标记弃用并允许在 `0.5.0` 删除；新能力不再扩展旧入口；
- 处理计划默认声明当前已配置的 Memory Archival 与 Relationship Adjudication 通道，宿主可以在来源接受前显式省略某一通道；
- 每个声明通道独立报告有产物成功、合法零产物或失败；只有全部声明通道成功终结时，本轮才算处理完成；
- 任一通道失败不回滚 Source Transcript 或另一通道已经提交的有效结果，Inner Review 与 Persona Growth 不阻塞单轮处理完成；
- 内核定义并编排版本化 RelationshipEventExtractorV1，宿主提供具体模型或提取实现，普通流程不再要求宿主手工构造候选；
- RelationshipEventExtractorV1 只提取中性事件、证据和定性信号，并返回严格 `candidates | no_relationship_event` 判别结果；内核验证来源证据、冻结候选批次并交给确定性关系裁决器；
- Persona Reflection 从事件候选中拆出；只有 Relationship Event 被接受后，独立 PersonaReflectionInterpreterV1 才能根据获准的人格与关系上下文返回 `reflection | no_reflection`；
- 反思失败不撤销已接受事件或确定性状态变化，普通事件可以合法没有反思，事实提取也不能用人设补写对话中没有发生的内容；
- Persona Reflection 以独立、不可变的 PersonaReflectionRecord 保存并引用已接受事件，不再藏在 Relationship Event metadata 中；
- 每条反思只保存最小 ReflectionContextProvenance，通过 Source Turn、Evidence、Blueprint 哈希、Manifest 修订、Baseline、已批准成长及相关历史 ID/版本记录当时依据，不复制整份上下文；
- Reflection Correction 与 Reinterpretation 成为显式引用原 `reflection_id` 的追加式记录；旧 metadata 反思迁移时缺失的上下文保持 `legacy_unavailable`；
- Persona Manifest 在现有 `VOICE + SITUATIONAL` 解释上增加有原文依据的 ContextualVoicePattern，结构化表达语域及其情绪、活动、关系安全、交流媒介与环境激活条件；
- 原文表达样本只证明相应语域可用，不自动成为高频口癖或固定台词；运行时优先传递模式与依据，仅在确有需要时展开原句；
- ContextualVoicePattern 沿用 `character | canonical_relationship | relationship_tendency` 范围，原作关系中的称呼、亲密和共同经历不得随表达风格映射给当前用户；
- Situational 选择使用带来源的 InteractionContextSignal：宿主只声明可观察环境事实，内核从当前 Persona Instance 推导关系条件，独立评估器只能用当前消息或正式历史依据提出情绪情境；
- Engine 通过版本化确定性规则产生临时 VoicePatternActivation，回复模型不能自报情绪来解锁语域；激活只供本轮生成与连续性检查，不自动成为长期记忆或人格变化；
- 手工候选提交保留为测试、纠错与高级入口，但必须引用既有 `source_turn_id` 和独立处理身份，不能绕开来源账本与裁决规则；
- `remember()` 归档与关系候选裁决作为不同处理通道引用同一 Source Turn，不再要求宿主为同一交互维护两套来源 ID；
- Timeline、MemoryNode 与被接受的 Relationship Event 共享来源关联，但继续保持叙事、检索与权威历史的职责边界；
- 归档成功、合法零产物与处理失败具有真实、可观察的不同结果；
- MemoryExtractorV1 通过严格 `artifacts | no_memory` 判别结果、字段边界和产物上限隔离不可信模型输出；
- 队列任务提供不暴露对话原文的持久回执与按 ID 查询；
- 提取、验证或存储失败正确进入退避重试和终态失败；
- 每次 Archival Batch 的 Timeline 与 MemoryNode 产物只会整体可见或整体不可见；
- `remember()` 在接收前要求版本化 Atomic Archival Store Capability，旧 Storage 的其他能力不被连带禁用；
- Queue 在有效租约内持久冻结 Prepared Archival Batch 并建立永久 Commit Binding，恢复只能续发指向同一批次的短期 Commit Permit；
- 回执区分 extraction 与 commit 重试及各自尝试次数，绑定后清除队列内 Archival Payload 且提交重试不再调用模型，规范 Source Transcript 继续保留；
- 每个新长期归档产物保留 `source_archival_id`，Timeline 使用稳定结构化身份；
- 旧 Timeline 获得确定性 ID，但缺失来源与时区保持 `legacy_unavailable`，不伪造 UTC 或归档身份；
- 每个 Archival Batch 永久记录非敏感、版本化的 Extractor Descriptor；
- 同一队列与记忆存储强制一个活跃归档消费者，多生产者仍可并发提交；
- Completed 任务立即清除队列内对话载荷，Failed 任务只在有界恢复窗口内保留工作副本；两者都不自动删除规范 Source Transcript；
- 终态完整回执默认保留 30 天，之后压缩为随关系删除的最小 Archival Tombstone；
- MemoryPack 通过独立 Archival Ledger 携带墓碑，不携带完整回执或任务运行细节；
- 同一回执查询接口通过 `retention_state` 区分完整记录与墓碑，已清理详情不伪装为零值；
- 完整回执通过只含稳定类型和 ID 的 Artifact Manifest 报告产物，计数从清单推导；
- REST 提交与查询使用真实的 `202/200/422/404/503/500` 生命周期映射和脱敏错误信封；
- Drain 与 Shutdown 具有显式超时和未完成任务报告，Shutdown 不强杀仍持有有效租约的当前尝试。

### alpha.6：幂等归档交付（已完成，`0.4.0a6`）

- 宿主重复提交同一归档意图时不会创建第二份任务；
- 同一任务在崩溃、租约恢复或人工重试后不会重复写入 Timeline 或 MemoryNode；
- FileStorage 与 SQLiteStorage 对提交幂等和效果幂等具有共享契约测试；
- 不把局部去重宣传为端到端 exactly-once。

### alpha.7：事件、情节与关系篇章分层巩固（计划中）

- Relationship Event 保持权威历史；
- Episode 从带来源的事件派生并围绕同一具体经历组织；
- Relationship Chapter 从 Episode 与事件派生较长的关系叙事时期；
- 所有巩固结果可重建、可追溯，不成为硬编码关系等级或关系状态写入来源。

### beta.1：迁移与长期评测

- `0.3 → 0.4` 备份、dry-run、验证与回滚；
- 删除事件后重建派生状态；
- 长期关系故事和领域不变量测试；
- 大数据量召回与重建性能基线。

## v0.4.0 非目标

- Web UI、账号和托管平台；
- 多 Agent 共享关系图；
- 核心引擎直接向用户发送消息；
- 大量第三方框架和数据库适配器；
- 没有来源证据的自动人格改写。

## 产品化准入条件

- Schema 连续两个次版本没有重大重构；
- 数据升级、回滚、导出和永久删除经真实数据验证；
- 长期关系评测稳定；
- 存在维护者之外的持续用户；
- 核心与产品层解耦；
- 具备用户支持、安全响应和持续投入能力；
- 完成目标市场的正式商标近似检索。

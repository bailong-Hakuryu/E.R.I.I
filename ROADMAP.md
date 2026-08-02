# E.R.I.I. Roadmap

路线图表达当前方向，不是固定发布日期或 SLA。只有满足测试和迁移门槛的能力才会进入正式版本。

## 版本总览

| 版本 | 核心主题 | 进入下一阶段的关键条件 |
| --- | --- | --- |
| `0.4.0a8`（已发布） | 连续性审计与发布收口 | 最终回复的五轴判断可持久审计，包和 prerelease 流程可重复 |
| `0.4.0b1`（当前阶段） | 迁移、删除、重建与长期验证 | 真实旧数据可恢复迁移，长轨迹无关系泄漏或无依据权威写入 |
| `0.4.0rc1` | Interface、Schema 与 Artifact 冻结 | 干净安装、文档示例、迁移与可携带性全部通过 |
| `0.4.0` / `0.4.x` | 稳定内核与兼容维护 | 数据格式和关系语义可被认真维护，但不承诺产品 SLA |
| `0.5.x` | 关系后果、角色内在审视与认知修订谱系 | 异常交付、双方立场、关系张力、角色反思与认知修订均有来源且可重建 |
| `0.6.x` | 授权、加密、密钥与多租户安全 | 可信部署的正向与负向安全验证完成 |
| `0.7.x` | 用户产品体验与外部验证 | 非维护者用户能够理解、使用、迁移和纠正长期数据 |
| `1.0` | 正式产品准入 | 数据、评测、安全、支持、发布与法律门槛同时满足 |

依赖顺序固定为：先让 v0.4 的长期数据可验证、可迁移，再在 v0.5 先建立关系后果与角色内在审视的权威边界、随后扩展认知修订谱系；先完成 v0.6 的安全 Seam，再把 v0.7 产品体验暴露给真实用户。后续版本不得通过跳过前置门槛来换取功能数量。

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
- Reflection Correction 与 Reinterpretation 成为显式引用原 `reflection_id` 的追加式记录；旧 Event metadata 反思只在 Recall/Growth 中只读兼容，不凭缺失字段合成正式 Persona Reflection Record，`legacy_unavailable` 保留为未来显式迁移的领域标记；
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

### alpha.7：事件、反思、情节与关系篇章分层巩固（已完成，`0.4.0a7`）

- `process_relationship_turn()` 把 completed Source Turn 编排为持久 Relationship Processing Run，普通重试按关系、来源版本和处理身份恢复，不重新采样；
- `RelationshipEventExtractorV1` 只允许严格 `candidates | no_relationship_event`，自动候选不得携带 Persona Reflection 或 Persona Growth；
- 完整提取决定在任何确定性裁决前冻结，合法无事件、全部拒绝、局部失败和接受事件具有不同可查询结果；
- Relationship Event 保持权威追加历史；反思解释失败不得撤销已经接受的事件或关系状态变化；
- `PersonaReflectionInterpreterV1` 只处理 accepted Event，并返回严格 `reflection | no_reflection`；
- Persona Reflection 作为关系范围内独立、不可变记录保存；Correction 与 Reinterpretation 追加新记录并引用旧 `reflection_id`，不覆盖历史理解；
- FileStorage 与 SQLite Schema v6 持久化处理运行、合法零产物、反思决定和反思记录，并遵守相同的 `Agent × User` 隔离与幂等契约；
- MemoryPack `0.4.0a7` 携带正式反思和全部持久关系处理 run，保留换存储后的冻结决定、可恢复阶段、来源与重试语义；
- 每个 run 以常量级 direct-event/adjudication journal 高水位和完整指纹冻结裁决基线；MemoryPack 携带 direct-event journal 顺序，并通过生产裁决器精确重放四类结果，不用墙钟猜测前史；
- Episode 只使用稳定发生身份、类型化时间链等显式分组证据，从带来源的事件派生同一具体经历；
- Relationship Chapter 只在显式跨 Episode/Event 引用支持时形成；证据不足的事件作为未巩固事件保留；
- 每次巩固保存 History Fingerprint 与策略版本；Episode/Chapter 可从权威事件历史重建，不进入 MemoryPack，也不成为关系等级或关系状态写入来源；
- 五轴 `ContinuityEvaluatorV1`、确定性汇总策略与来源支持的 Contextual Voice Pattern 在本阶段随公开契约落地，情境表达不会继承另一段关系的称呼、亲密或共同经历。

### alpha.8：连续性审计与发布收口（已发布：`0.4.0a8`）

`0.4.0a8` 的实现、公开契约测试与 prerelease 发布已经完成。它是 v0.4 最后一个允许补充领域契约的 Alpha，只补齐已经公开承诺、但此前尚未完整持久化的连续性审计证据，以及使这些审计状态真正生效所必需的最小来源权威过滤；该过滤不引入新的关系算法或记忆类型，a8 也不扩展人格变化能力。发布身份由不可移动的 `v0.4.0a8` tag、GitHub prerelease、wheel、sdist 与 SHA-256 校验清单共同固定；当前开发阶段正式进入 `0.4.0b1`。

#### 连续性回执

- 每个现代最终可见回复都原子绑定一个必需的 `ContinuityReviewRecord`；只有 `reviewed` 分支包含持久五轴 `ContinuityReviewReceipt`，`not_evaluated | failed` 分支如实保存有限状态而不伪造 Receipt；
- 每个 `overridden | shown_unreviewed` 都携带版本化 `DeliveryExceptionRecord`：决策主体只允许 `host_policy | human_operator | data_owner`，交付理由只允许 `availability_fallback | configured_delivery_policy | out_of_band_judgment | preexisting_visible_exchange | legacy_turn_completion`；处置与主体的非法组合在写入前拒绝，技术评估失败分类继续只属于 Review Record；
- 回执绑定 `relationship_id`、`turn_id`、最终回复指纹、交付处置、评估器描述符与确定性汇总策略版本；
- 回执保存五轴 `ContinuityFinding` 的判定、严重度、原因代码、回复范围及精确依据引用；
- 只有最终 `voice_style` Finding 实际使用的运行时表达激活才投影为不可重放 `VoiceActivationTrace`；Trace 不进入任何 Prompt、记忆或关系投影，并满足开关序列化不改变判定、交付与未来召回的观测非干扰不变量；
- 评估草稿 A 的结果不得附着到最终回复 B；未展示草稿、完整 Prompt、模型内部推理与不可见工具输出不得持久化为回执；
- Finding 必须保持 `Agent × User` 关系范围，跨关系依据引用在进入持久层前被拒绝；
- 旧 Turn 缺少回执时显式表示 `legacy_unavailable`，不得根据当前模型或当前规则补造历史判断；
- `legacy_unavailable` 可原样保留并展示旧 summary-only assessment，但废弃的 `continuity_assessment` 兼容属性对它返回 `None`；只有显式读取 `ContinuityReviewRecord.legacy_summary` 才能看到旧结论，避免旧 `COMPLETED/ALIGNED` 被误认成 a8 Receipt；
- 回执只解释“当时为什么这样判定”，不能自动成为 MemoryNode、Relationship Event、Persona Reflection、Persona Growth 或关系数值变化的写入依据；
- 回执随 Turn Record 原子保存，通过现有 Turn 查询 Interface 读取，不为每个 Finding 继续扩张 `ERIIEngine` 的顶层 Interface；
- FileStorage、SQLiteStorage 与 MemoryPack 往返后必须保留相同的回执、版本、依据引用与关系隔离语义。
- TDD 只通过四个已确认公开 seam 验证行为：Engine 生命周期与处理、Turn/MemoryPack wire、Structured/Legacy Recall、REST 翻译；两个 Storage Adapter 运行同一契约，不以私有调用或物理表结构作为正确性依据。

#### 最小连续性例外隔离与情感中立

- `shown`、`overridden` 与 `shown_unreviewed` 都保留 User 真正看到的完整 Source Transcript；未展示草稿仍不进入关系历史；
- `overridden | shown_unreviewed` 中的 Agent 发言保留“当时确实说过”的事实地位及逐消息来源，但在没有后续正式处置能力的 a8 中保持权威隔离，不得静默进入普通 Persona、知识、承诺、反思、成长或自动关系跃迁依据；
- 同轮 User 消息仍按普通来源规则处理，不能因为 Agent 回复异常而丢弃整轮，也不能把 User 自述自动提升为客观事实；
- 现代 Timeline 与 Memory 候选必须以 `TurnMessage.message_id + TurnRecord.source_revision` 提供可由当前关系 Source Transcript 核验的消息级精确证据；schema `"2"` 强制显式 Unicode code-point `start/end`，不通过搜索 quote 猜测重复片段；持久产物只保留确定性 Evidence ID、消息身份、Source Turn revision、内核解析角色、哈希与范围，不复制引文正文；
- 完整归档回执同时认证产物规范载荷、Source revision 与提取器描述；压缩为现代 tombstone 时删除运维详情，但把每项产物的类型、稳定 ID 与规范载荷 SHA-256 保存为不含正文的 `artifact_commitments`。召回与 MemoryPack 导入都重算当前产物指纹；旧墓碑缺少 commitment 时只能维持幂等/审计身份，不能把产物提升为 Ordinary；
- 保留现有 `MemoryExtractorV1` 的 `descriptor + extract(request)` 调用接口，以 `ExtractorDescriptor.extraction_schema_version` 版本化返回语义：`"1"` 是只供 Legacy 识别与读取的无引用格式，`"2"` 是 a8 新归档提交必须显式声明的证据感知格式；不增加与 schema 重复的能力布尔值；
- 升级时按持久阶段处理已有 schema `"1"` 工作：终态记录不变，未形成批次的 `EXTRACTION` 记录以不可重试 `extractor_schema_upgrade_required` 终结并要求 schema `"2"` 加新幂等键显式重提；只有完整绑定的 `COMMIT` 批次继续原子提交，结果保持 schema `"1"` 与 `legacy_unavailable` 消息依据；
- 异常交付中只依赖 User 消息且通过普通事实规则的归档候选仍可提交；任何候选引用受隔离 Agent 消息时，整项 Archival Extraction Decision 在 Prepared Batch 形成前失败，不能静默裁剪，提取器只能重试或返回合法 `no_memory`；
- 关系通道按候选独立隔离：引用异常 Agent 消息的候选以 `rejected + continuity_exception_agent_evidence_quarantined` 正常终结且不产生事件或状态副作用，合法 User-only 候选继续裁决；全部被拒时 Run 为 `completed + no_accepted_events`，不新增 pending 状态，也不伪装为技术失败；
- `adjudicate_turn_candidates()` 与 direct API 精确命中同关系 completed Turn 的路径统一使用 `relationship-turn-adjudication-v1`；direct 提供的 revision、消息 ID、角色、正文和发生时间必须与持久 Turn 完全一致，并继承交付权威。真正没有持久 Turn 的 direct 调用继续作为 Legacy transient 兼容入口，但该 Turn ID 之后禁止注册为规范 Turn，不能通过晚建 Turn 追授现代权威；
- MemoryPack 会在任何写入前复核绑定持久 Turn 的 direct adjudication 的 Source Turn、Evidence identity 与异常 Agent 必须保持拒绝的不变量；仅降级 receipt contract 而仍保留对应 Turn 不能绕过复核。direct 记录没有 frozen candidate，因此这项闭包不承诺完整重放普通 accepted Event；旧 transient records 只保持 Legacy 可读，未签名 Pack 的整体改写、删除 Turn 或同步降级仍不构成来源真实性保证；
- 上述隔离只由 `overridden | shown_unreviewed` 交付事实触发，不检查发言情感极性；经过回滚或重生成后重新绑定最终文本、并以 `aligned | supported_new_choice + shown` 通过的拒绝、愤怒、疏远或伤害性选择属于普通 Source Turn，可按现有规则进入关系与记忆处理；
- 召回按权威层级兼容旧数据：可证明绑定现代异常 Turn、却没有消息角色证据的产物标记为 `quarantined_history` 并从默认生成召回隔离；其他无法恢复现代证据链的旧产物作为显式 `legacy_context` 保留在 Agent-private 低权威分区，避免升级即失忆，但不参与强化、连续性依据、人格反思/成长或关系跃迁；
- `legacy_context` 与 `quarantined_history` 都保持可检查、可标记、可导出和可删除；完整现代证据在权威使用与 Prompt 分区中优先，任何旧摘要都不能被内容猜测升级为普通权威；
- MemoryNode 先由关键词/向量 RRF 与动态有效权重形成唯一上游顺序，召回选择器保留该顺序，先分类 `ordinary | legacy_context | quarantined_history`，再在可用权威分区内应用 `max_per_type`；不能让高排名 Legacy 提前消耗 Ordinary 的类型配额，也不使用第二套词法相关性重排 hybrid 结果；
- 结构化召回的默认 `top_k` 是现代与 Legacy 动态投影的总上限：现代不足时 Legacy 按上游顺序填充，现代已占满且 `top_k >= 2` 时最多保留一个相关 Legacy 槽位，`top_k = 1` 时现代优先；精确内容重复只保留现代投影，并分别渲染 `Verified Memories` 与 `Legacy Context — provenance incomplete`。兼容 `recall()` 为旧 Core Memory 保留一个动态 `top_k` 外的 Legacy 候选，但它仍受硬成本预算；必要 Persona/Relationship Context 始终优先；
- 连续性汇总与交付回执保持情感效价中立：温柔、顺从和亲密不获得通过优待，拒绝、生气、疏远和造成伤害不构成 OOC 原因；
- 契约测试同时覆盖“有充分依据的拒绝/冲突”和“无关系依据的甜蜜/承诺”，证明判定依赖人格与经历因果而非情绪正负；
- a8 不实现例外解除、双轨回溯复核、双方立场、叙事张力解决、反思敏感性或成长联动，也不发布这些能力的空壳 API；完整纵向能力进入 `0.5.0a1`。

#### 模块与发布收口

- 从本阶段开始，不再向 `ERIIEngine` 增加新的顶层领域方法；新 Implementation 必须位于职责明确的深 Module 内，Engine 只作为组合入口和兼容门面；
- 不在 v0.4 进行一次性“大拆 Engine”；只为连续性回执与最小来源权威过滤建立必要的内部 Seam，大规模 Portability 与 Storage Interface 重构留到 v0.5；
- `.[dev]` 的干净安装必须能够运行完整测试，包括 REST 合约测试；
- 构建 wheel 与 sdist，检查包元数据，并分别在干净环境中执行 import、版本、CLI 与参考 REST 最小冒烟；
- Git tag 去掉 `v` 后必须与包版本一致；Changelog、包版本和 GitHub Release 使用同一个发布身份；
- Alpha、Beta 与 RC 均作为 GitHub prerelease 发布，同名 tag 与构建产物一经发布不得移动或重新构建；
- 发布包不得携带真实用户聊天、私人人设、数据库、缓存、凭据或未经授权的原作内容；
- `0.4.0a8` 是最后一个承诺支持 Python 3.9 的版本，并在文档和发布说明中提前公布最低版本调整。

#### 退出条件

- 最终回复与 Continuity Review Record 原子绑定；`reviewed` 分支中的 Receipt 与精确回复一致，重试不会产生第二份互相矛盾的审查记录；
- 五轴 Finding、依据集合、评估器版本和汇总策略经过 File/SQLite/MemoryPack 往返保持一致；
- 草稿错绑、跨关系引用、非法范围、未知未来版本和旧数据降级测试全部通过；
- Legacy Turn 的旧 `COMPLETED/ALIGNED` 摘要经过 File/SQLite/MemoryPack 往返仍存在于 `legacy_summary`，但兼容属性始终为 `None`，且不能进入任何现代权威判断；
- 新 Archival Submission 使用 schema `"1"`、未显式声明 schema `"2"` 或返回缺少消息级依据的现代候选时，在创建新的归档身份或队列任务前失败；
- schema `"1"` 的终态、提取阶段、完整提交阶段及损坏提交阶段迁移测试证明旧身份不会搭载新提取结果、冻结批次不会被重采样、Legacy 产物不会获得现代证据身份；
- 完整回执与压缩 tombstone 测试证明 Source revision、产物类型、稳定 ID 和规范载荷 SHA-256 都参与认证；同 ID 内容改写、伪造合法 UUID、缺失 commitment 或 MemoryPack 中不匹配的 commitment 都不能获得 Ordinary 权威，并在导入首次写入前失败关闭；
- 召回测试证明 pre-a8 Legacy 仍以 `legacy_context` 出现在 Agent-private 兼容分区且不被强化，已知现代异常来源的无角色产物不进入默认生成召回，两类内容都仍可检查、导出与删除；
- `top_k=1`、现代不足、现代占满、无相关 Legacy、精确重复、同类型高权重 Legacy、Legacy Core 兼容上下文及硬成本不足测试分别证明现代优先、Legacy 渐进填充、最多一个预留槽、现代去重胜出、authority-first 类型配额、Core 位于动态上限外但不绕过预算；向量开启与关闭时选择器都保留上游 hybrid 顺序；
- 异常 Agent 发言无法未经显式处置获得普通记忆或关系权威，而同轮 User 证据仍可按原规则处理；
- 混合候选批次、全隔离批次、依赖被拒候选及重复处理测试证明候选级拒绝不会污染独立 User-only 结果，并经 File/SQLite/MemoryPack 保留冻结候选、精确证据、稳定 reason code 与正常 Run 终局；
- direct adjudication 测试证明精确持久 Turn 会触发 `relationship-turn-adjudication-v1` 与异常 Agent 隔离，伪造 Source Turn 会失败，真正 transient 调用仍按 Legacy 行为处理且其 Turn ID 不能后续提升；MemoryPack 对保留对应 Turn 的 contract 降级仍执行 Evidence/quarantine 复核，并且不把无 frozen candidate 的 direct receipt 宣称为完整事件重放；
- Timeline/Memory 候选的消息 ID、修订、角色、哈希与范围经过正常、悬空、错位、跨 Turn、异常 Agent 证据及 MemoryPack 往返测试；非法批次整体失败且不留下部分产物；
- 重复 quote、缺失范围、UTF-8 多字节字符与 Unicode code-point 偏移测试证明 schema `"2"` 不依赖模糊文本搜索，也不会把字节偏移误当字符偏移；
- 情感极性对照测试证明支持充分的拒绝可以通过、无依据的亲密可以被拒绝，连续性判定不退化为迎合度评分；
- Voice Activation Trace 开关对同一冻结输入产生字节一致的 Finding、汇总结论与交付结果，并且后续召回输入不包含 Trace；离线诊断可读取它但不能反向修改运行时状态；
- CI 从声明的开发依赖干净安装后通过全部测试、静态检查、编译和包安装冒烟；
- `CONTEXT.md`、领域模型、ADR、README 与代码对“临时评估结果”和“持久审计回执”使用一致定义；
- v0.4 功能冻结清单与非目标公开，并建立第一个可靠的不可移动 prerelease tag。

### beta.1：迁移、数据生命周期与长期验证（当前开发阶段：`0.4.0b1`）

`0.4.0b1` 是 v0.4 的功能完整点。只有下列能力已经实现并通过门槛后才发布该版本；Beta 之后不再增加领域模型。

具体交付顺序、Data Lifecycle Module Interface 与可执行不变量见 [`docs/b1-implementation-contract.md`](docs/b1-implementation-contract.md)。

#### Python 与兼容范围

- 最低 Python 版本提升为 3.11，并同步 `requires-python`、classifiers、Ruff target、CI、README 与中英文指南；
- Required CI 至少覆盖最低支持版本与最新稳定 Python；Windows 保留额外冒烟，以覆盖文件锁、路径和 SQLite 差异；
- Package Version、SQLite Schema Version、MemoryPack Format Version、Extractor/Evaluator Version 与 Policy Version 分别维护，不能用一次全局字符串替换把它们误认为同一生命周期；
- `remember()` 与接收临时 Source Turn 的旧关系裁决入口真实发出弃用警告，文档给出替代 Interface，并计划在 v0.5 删除；
- 公共 Python Interface、REST `/api/v1`、SQLite Schema 与 MemoryPack Format 在本版本进入冻结。

#### 迁移、备份与恢复

- 已完成第一阶段：三种本地格式均可通过同一 `inspect → plan → execute` 接口生成 Lifecycle Backup v1，严格核验 manifest 与完整 payload，并幂等恢复到缺失目标；备份包、计划和报告均不把聊天、人设或记忆正文复制进元数据；
- 下一阶段在该闭环上加入真实格式升级、迁移 dry-run、覆盖恢复前的被替换状态备份、语义图验证与旧版本 fixture；在这些能力完成前，不宣称支持安全原地迁移；
- 提供 `0.3 → 0.4` 的检查、备份、dry-run、执行、验证和失败恢复流程；
- dry-run 不得修改数据；迁移失败不得留下可见的半迁移状态；
- rollback 以恢复可验证备份为主，不承诺把任意新格式原地逆向猜回旧格式；
- 固化旧 SQLite Schema、FileStorage 和 MemoryPack alpha 格式的历史 fixture，覆盖 Unicode、时区、关系隔离、Turn、归档、反思、处理运行和旧缺失来源；
- 未知未来 Schema 或 Pack 版本必须显式拒绝，不能“尽力读取”并静默丢弃字段；
- FileStorage 与 SQLiteStorage 在迁移、重启、并发恢复、导出导入和重复导入上遵守共享契约；
- MemoryPack 继续区分内部自洽性与来源真实性：设计可选签名/MAC Envelope、密钥标识、轮换和验证失败策略，不把可重算指纹宣传成防篡改认证。
- 当前跨进程锁只协调可信宿主；若后续要在同机不可信进程可写的目录中运行，发布前必须把完整路径的“检查后使用”替换为稳定父目录 handle/`dirfd` 下的相对 no-follow 打开与 no-replace 发布，并在 Windows 上实现等价句柄语义，不能把路径复验冒充授权边界。

#### 删除与确定性重建

- 定义关系级、Source Turn 级、事件级及完整用户数据删除计划，明确权威记录、派生产物、队列副本、回执、备份和外部副本的处理范围；
- 删除或撤回权威 Relationship Event 后，Current Belief、Relationship State、State Reason、Episode、Relationship Chapter 和 Recall Projection 可确定性重建；
- 删除 MemoryNode 不得编造对话从未发生；删除派生投影不得删除仍然有效的权威历史；
- 每次删除返回可审计但不泄露正文的结果报告，区分已删除、已重建、外部宿主仍需处理和当前无法证明删除的副本；
- 导出、删除和重建必须继续遵守 `Agent × User` 隔离，`overwrite=True` 或管理员便利入口不得成为跨关系搬运许可。

#### 长期关系评测

- 建立独立 `LongitudinalEvalRunner` Module，以一个窄 Interface 运行 Scenario、系统 Adapter、故障调度、确定性评分和报告生成；
- 仓库内只使用原创合成人设和原创故事轨迹；私人角色文档与版权敏感人设只允许作为不提交仓库的本地验证材料；
- 至少建立三条固定长轨迹：
  - 单关系普通生活 128 轮，包含稀疏重要事件、时间间隔、承诺、开放事项、情境表达和因果成长；
  - 两段关系各 72 轮交错，使用相似地点、礼物、昵称和问题，专门检查关系串线；
  - 错误陈述、事实纠正、冲突修复、Reflection Correction/Reinterpretation 与成长提案共 120 轮；
- 每条轨迹以普通轮次和合法零产物为主，防止系统通过“什么都记”获得虚假高分；
- 在固定检查点执行重启、导出导入、重复导入、正向召回、应当拒答、关系串线、纠正后召回、因果变化与情境语域探针；
- v0.4 的内核硬门槛包括：
  - 跨关系记忆泄漏、跨关系状态变化和无依据权威写入均为零；
  - Event、Reflection 与 Growth Proposal 均能追溯到当时已经存在的合法来源；
  - 三类硬连续性冲突始终进入对应阻断判定，单独 voice-style 偏差不会升级为人格漂移；
  - Current Belief 修正投影正确，旧事件和追加式解释仍完整保留；
  - 重启、恢复、重复处理与重复导入不增加记录；
  - File → SQLite → File 后的规范摘要、Snapshot、Processing Run、Reflection 与 Consolidation 保持一致；
- 真实模型的端到端角色质量属于 v0.5 及产品评测；v0.4 的确定性内核测试不能冒充宿主模型已经具备可靠的语义判断能力。

#### 性能基线

- 分别测量召回、关系投影、巩固、导出、导入、删除和重建；
- 使用分级数据量记录时间、峰值内存、数据库体积和增长曲线，在获得基线后再确定预算，不以“看起来足够快”作为结论；
- 将当前检查、捕获与验证阶段的整包内存物化替换为分块、有界内存的流式路径；优化不得削弱稳定来源检测、规范 manifest 承诺或原子发布；
- 长轨迹和大数据量测试可进入定时 CI；每个 PR 继续运行确定性领域契约和较小的代表性回放；
- 性能优化不得改变权威历史、关系隔离、来源引用或确定性重建结果。

#### 退出条件

- 所有历史 fixture 能够完成备份、dry-run、升级、验证和失败恢复；
- 删除、导出、导入、重建、重启、重试和两种 Storage Adapter 的共享契约全部通过；
- 长期轨迹满足全部 v0.4 硬门槛，并保存可重复运行的基准报告；
- 已知性能瓶颈、支持范围和安全限制被量化或写入发布说明；
- 没有已知 P0/P1 数据丢失、跨关系泄漏、重复提交或不可恢复迁移问题；
- Beta 发布后停止增加功能，只接受兼容、迁移、性能和缺陷修复。

### beta.2：条件修复版本（仅在需要时发布 `0.4.0b2`）

- 不预先把 `b2` 当作必经开发阶段；
- 只处理 `b1` 暴露的兼容、迁移、数据完整性、性能、测试稳定性与文档问题；
- 不增加新领域模型，不改变已经冻结字段的含义；
- 若修复需要重构公共 Interface、Schema 或权威历史语义，应重新评估 Beta 冻结，而不是把重大变化伪装成小修。

### rc.1：发布候选（`0.4.0rc1`）

- RC 与预期最终版只能相差版本号、发布说明和阻断缺陷修复；
- README 与中英文指南中的关键示例进入自动执行；
- wheel 与 sdist 在支持的平台和 Python 版本中均可从干净环境安装；
- GitHub Release Workflow 只构建一次产物，验证后将同一批 wheel/sdist 用于所有发布目标；
- tag、包元数据、`erii.__version__` 与 Changelog 完全一致；
- 发布产物提供 SHA-256；条件允许时增加 SBOM 与构建来源证明；
- 检查依赖许可证与仓库内容来源，确认不含私人人设、真实聊天和未经授权的数据集；
- 安全文档明确说明：内核不是身份认证系统，参考服务仍无完整授权、加密和多租户隔离，MemoryPack 自洽校验不等于来源认证；
- 只有 RC 被真实阻断时才发布 `rc2`；不得在 RC 中加入功能。

### v0.4.0：稳定角色连续性内核

`v0.4.0` 表示这条 Python Interface、关系语义与数据格式线可以被认真维护，不表示正式产品 SLA，也不表示参考服务可以未经加固直接暴露到公网。

发布门槛：

- 实际 RC 构建产物完成验证，不在最终上传阶段重新构建；
- 至少在独立干净环境完成安装、人设导入、关系初始化、Turn、归档、关系处理、召回、导出导入与删除流程；
- migration、rollback、export/import、delete/rebuild、restart/retry 与 File/SQLite parity 全部通过；
- 长期关系评测基线已保存且能够重复运行；
- Release Notes 提供从 v0.3.1 升级的完整备份、迁移、验证、回滚和已知限制说明；
- 创建不可移动的 annotated/signed `v0.4.0` tag 与非 prerelease GitHub Release；
- 若发布到 PyPI，先确认包名所有权并使用可信发布流程，不从维护者开发机手工上传长期 Token。

## v0.4.x：兼容维护线

- `0.4.1` 及后续 Patch 只接受兼容缺陷、安全、迁移、性能和文档修复；
- 不删除公共 Interface，不改变已有字段含义，不要求用户在没有迁移路径的情况下重建长期数据；
- 新 Reader 必须继续读取稳定 v0.4 数据；旧 Reader 不承诺读取未来格式，但必须对未知版本明确报错；
- 弃用必须同时提供 warning、Changelog、迁移指南和替代 Interface，并至少跨过一个稳定 v0.4.x 发布后才允许在 v0.5 删除；
- 安全或确定性数据损坏问题可以加速停用，但必须单独公告并提供可执行迁移措施；
- 该维护线仍是“长期认真维护但不背 SLA”的开源内核；只有明确声明的兼容范围属于维护承诺。

## v0.5.0：关系后果、角色内在审视与可解释认知谱系

v0.5 先回答“角色真正经历了一件事以后如何继续活下去”，再回答“当前为什么相信这件事”。它不是增加更多孤立标签，而是建立以下可追溯链条：

- 一条未经审查或被显式覆盖但确实展示过的回复，如何保留事实而隔离人格权威；
- 角色造成伤害、作出拒绝或坚持边界后，真实关系后果如何留下而不强制道歉、撤回或和好；
- User Stance、Persona Stance 与共同 Relationship Outcome 如何分别取证，任何一方都不能替另一方填写内心；
- 重大事件为什么触发角色审视，以及角色如何合法保持未知、矛盾或暂未形成结论；
- 角色会对哪些威胁、愿望实现、主体性里程碑、关系意义与价值张力产生内在反应；
- 当前认知来自观察、用户自述还是推断，哪些证据支持、质疑、替代或撤回它；
- 当前召回为什么采用新理解，而旧事件、旧反思与旧判断为什么仍可审计。

v0.5 是第一个有计划的兼容性变更版本，必须继续读取稳定 v0.4 数据并提供显式迁移路径。a8 只建立最小隔离；完整解除、关系后果和角色审视从 `0.5.0a1` 开始实现。后续认知谱系能力必须复用同一套追加历史与来源边界，不能另建互相冲突的“角色真值”账本。

a8 与 v0.5a1 的桥接是显式追加而非状态升级：a8 的 `continuity_exception_agent_evidence_quarantined` 永久回答“当时为什么没有自动写入关系”；v0.5 由具备相应宿主能力的调用者创建新的 `historical_reprocessing` 身份，读取原 Turn、冻结候选、精确证据和 a8 Decision Receipt，再分别追加 Continuity Authority 与 Relationship Consequence 结论。没有显式新处理就保持隔离；任何 v0.5 结论都不得把 a8 的 `rejected` 改成 `accepted`。

### alpha.1：关系后果与角色内在审视

#### 连续性例外处置

- 对 `overridden | shown_unreviewed` 建立追加式 `ContinuityExceptionResolution`，永不修改原 Turn、原交付处置、原审查状态或完整原文；
- v0.5 以新的 `historical_reprocessing` 身份消费 a8 冻结的异常候选及 `rejected` Decision Receipt；新 Run、Resolution 与可能形成的 Relationship Event 都引用原身份，但不覆盖旧 Run、reason code 或批次指纹，也不在迁移时自动执行；
- Continuity Authority 与 Relationship Consequence 两条复核轨道彼此独立：承认真实关系后果不能反证发言符合人设，后来的连续性认可也不能伪装成交付前已经审查；
- Relationship Consequence 轨可引用后来发生且可解析的 User 反应来承认伤害、安慰、期待或误解；这些后见证据不能进入 Continuity Authority 轨，也不能使原异常发言自动成为 Persona 依据；
- 对原本就通过交付前连续性审查的伤害性选择，v0.5 不需要例外“洗白”，而是在普通已接受历史上继续形成 Relationship Consequence、双方独立 Stance、Narrative Tension 与角色 Reflection Opportunity；角色可以道歉、解释、坚持边界、保持矛盾或结束关系，系统只保证记忆与后果连续，不指定修复方向；
- 只有拥有现代 Turn Context Baseline 的技术性未评估回复与 `review_required` 回复可以普通回溯复核；`unsupported_drift`、有效权威撤销、无原始基线及 Legacy 数据只允许严格纠错或关系后果复核；
- Historical Continuity Correction 必须证明旧评估器、汇总策略或权威决定存在缺陷，并只使用原 Turn 冻结依据；未来事件、后来成长与 Blueprint 新版本不得反向洗白旧回复；
- 可信本地宿主以 `continuity_review_requester`、`persona_reviewer`、`relationship_reviewer` 与 `continuity_correction_authority` 能力声明约束操作；actor 声明只提供领域审计，不能冒充 v0.6 才完成的认证、授权与租户安全；
- Agent、LLM、Evaluator 与聊天自然语言永远不能签发能力或批准自身结论。

#### 关系后果、双方立场与张力

- 连续性判断保持情感效价中立；有依据的拒绝、愤怒、疏远与关系结束按普通 Source Turn 处理，无依据的甜蜜、亲密与承诺同样可能被判定为漂移；
- 已接受的伤害、安慰、拒绝、边界、期待与冲突进入追加式关系历史、有限状态投影与 Narrative Tension；连续性成立不免除后果，User 受伤也不强制角色道歉、撤回、原谅或复合；
- 分别重建 User Stance Projection、Persona Stance Projection 与 Relationship Outcome Projection；User 只能表达自己的感受和期待，角色立场只来自正式反思及后续通过连续性审查的 Agent 选择；
- Narrative Tension 通过显式引用后续事件投影为 `unaddressed | addressed_unresolved | mutually_reconciled | boundary_stabilized | relationship_ended | superseded`，不用单一可写 `resolved` 布尔值；
- 道歉只证明角色尝试回应，User 原谅只证明 User 立场；共同和解需要双方证据，合法边界与关系终结不要求另一方同意，也不会删除其受伤或反对；
- 关闭或转化张力不恢复冲突前数值、不删除原事件，也不能由时间流逝、一次无关温柔互动或 Reviewer 偏好自动完成。

#### 角色审视与敏感性

- Persona Reflection Decision 扩展为严格 `reflection | stance_unformed | no_reflection`，技术失败继续独立；未知内心不得伪装成无意义或标准化悔意；
- Reflection Trigger Policy 组合全局结构性底线与获准 Reflection Sensitivity Profile，只保证重大事件获得一次审视机会，不规定反思结论；
- 全局底线覆盖关系建立/终结、重要承诺、重大边界、核心价值冲突、持续关系后果与有证据的重大选择变化；
- 角色敏感性必须逐项引用 Blueprint、Formative Experience、获准 Manifest 或已批准 Growth，并同时检查 `threat_or_violation | fulfillment | agency_milestone | relationship_meaning | value_tension` 五类来源；
- Sensitivity Coverage Report 只报告支持、歧义与缺口，不是人格完整度评分，也不能为了平衡而自动补积极或创伤标签；
- `stance_unformed` 只在后续真实事件支持 Reinterpretation 时变化，不运行隐藏定时器，也不重复采样同一冻结事件来刷出方便结论；
- 异常关系后果可以进入角色后续面对与修复的因果链，但异常发言本身不能直接定义 Persona 或触发 Growth；重要成长仍需角色后续通过审查的主动选择与现有审批边界。

#### Implementation、携带与退出条件

- 新能力进入职责单一的深 Module，并拥有自己的窄内部 Store Interface；不恢复向 `ERIIEngine` 逐项增加顶层方法的做法；
- FileStorage、SQLiteStorage 与 MemoryPack 保留原 Turn、完整有序 Resolution 链、逐消息例外来源、双方立场来源、张力引用、反思三态、敏感性版本与精确 Persona 依据；
- v0.4 数据保持可读；缺少现代基线、逐消息来源或角色反思依据时显式 `legacy_unavailable` 或未知，不按当前内容猜测；
- 导入在首次写入前拒绝悬空、跨关系、冲突或未知版本引用，删除 Relationship 时清除对应处置、张力、立场来源与敏感性关系数据；
- 集成测试至少覆盖：支持充分的伤害性回复、无依据的讨好回复、异常发言造成真实伤害、User 单方原谅但角色坚持边界、角色私下后悔但尚未公开修复、共同和解、合法关系终结、未形成立场及正向主体性里程碑；
- alpha.1 完成不表示公开服务安全；在 v0.6 前，处置能力只能位于可信宿主边界之后。

### alpha.2：提取 Portability 深 Module

- 将导出、导入、重映射、格式验证、身份冲突检查和合并裁决从 `ERIIEngine` 移入独立 Portability Module；
- 对外 Interface 只保留少量高杠杆操作，例如 `export()`、`inspect_import()` 与 `import_pack()`；
- Engine 的旧方法暂时委托新 Module，调用方不需要理解导入流程中的全部 Implementation；
- Portability 的测试通过其 Interface 覆盖 FileStorage、SQLiteStorage 与内存测试 Adapter，不继续依赖 Engine 内部方法。

### alpha.3：建立 Module 自有的窄 Storage Interface

- 以真实变化点建立 `TurnStore`、`RelationshipJournalStore`、`MemoryRelationStore` 等窄 Interface；
- FileStorage 与 SQLiteStorage 作为两个真实 Adapter 共同验证 Seam，测试使用最小内存 Adapter；
- 停止为每项新能力继续扩张通用 BaseStorage；
- 新 Module 的测试以 Interface 可观察结果为准，不穿透 Interface 绑定内部表结构。

### alpha.4：Belief Lineage 只读投影

- 从不可变 Relationship Event 中的 Belief Update 重建每个 key 的 SET、RETRACT、来源事件、有效版本与当前值；
- Current Belief 保持可重建投影，不升级成第二本可冲突的权威账本；
- 初始版本不新增数据库表，先证明历史投影与 v0.4 Current Belief 逐事件等价；
- 前端可查询“当前认知”和“完整修订链”，但标签不反向改变关系状态。

### alpha.5：追加式 Memory Relation 账本

- 新关系记录引用既有 MemoryNode，而不是原地改写旧 Node；
- 初始关系类型至少包括 `supports`、`contradicts` 与 `supersedes`；
- MemoryNode 继续承担检索产物职责，Memory Relation Ledger 承担认知修订历史；
- 现有 `is_latest` 与 `superseded_by` 作为旧快照兼容字段，不得被宣传为已经具有追加式谱系；
- 旧数据迁移保留 `legacy_unavailable`，不能根据当前相似度伪造过去的支持或冲突关系。

### alpha.6：拆分来源、支持与生命周期语义

- 来源性质：`observed | reported | inferred`；
- 支持状态：`single_source | corroborated | contested`；
- 生命周期：`current | superseded | retracted`；
- “存在矛盾”不自动等于“已经被替代”；两个说法可能因时间、对象、条件或视角不同而同时成立；
- 模型置信度只描述提取输出，不直接授予事实、记忆、关系或人格写入权；
- 支持、质疑、替代和撤回均保留精确来源引用与可重放规则。

### alpha.7：Continuity / Uncertainty Map

- 建立独立、可重建、只读的 `ContinuityMapProjector`，不把长期地图塞进负责单轮回复评估的 Continuity Module；
- 地图聚合：
  - 单一来源、已佐证、受质疑、已替代和已撤回的认知；
  - 未解决 Memory 冲突；
  - 已持久化但尚未处理的 Continuity Finding；
  - 待批准 Persona Growth Proposal；
  - 未完成 Promise 与 Open Loop；
- 地图面向宿主和前端解释，不进入权威 MemoryPack；它必须能从同一份可携带权威数据确定性重建；
- 地图与用户可见标签永远不能自动写回 `trust`、`intimacy`、Persona Reflection 或 Persona Growth；
- 多模型审查若未来加入，只能作为离线可选 Audit Adapter，不能拥有写入权威历史的权限。

### v0.5 兼容变更与退出条件

- 删除已经在 v0.4 完整弃用的 `remember()` 等旧入口；
- Continuity Exception Resolution、关系后果、双方立场、张力、反思敏感性、Portability、Belief Lineage、Memory Relation 与 Continuity Map 均不需要继续向 `ERIIEngine` 增加顶层方法；
- 原 Turn 的审查与交付事实在任何例外处置后仍保持不可变，Resolution 链经过 File/SQLite/MemoryPack 往返可确定性重建；
- User Stance 与 Persona Stance 的来源严格分离；User 文本、Reviewer 偏好与关系结果不能写入角色内心，角色私下反思也不能冒充已向 User 作出的修复；
- Narrative Tension 的和解、边界稳定与关系终结保留不同语义，关闭张力不删除原伤害、不恢复旧数值；
- `reflection | stance_unformed | no_reflection | failure` 经存储、迁移与携带保持可区分，未形成立场不会因时间经过自动变化；
- Reflection Sensitivity Profile 的每项触发都可解析到获准 Persona 依据，Coverage 缺口不会触发自动补全；
- Belief 当前投影与旧实现逐事件等价，旧事件仍完整可审计；
- Memory supersession 不修改原 MemoryNode；
- contradiction、supersession 与 retraction 具有彼此独立的语义和测试；
- Source Nature、Support State 与 Lifecycle State 不合并为一个含混状态字段；
- Continuity Map 可由 v0.5 MemoryPack 确定性重建，并且无法反向修改关系状态；
- v0.4 的迁移、关系隔离、回放、删除和长期评测硬门槛继续通过；
- 真实模型评测将 Continuity Evaluator、Persona Reflection Interpreter 与裁判分离，并分别报告五轴指标、情感效价偏置、无依据记忆、纠正恢复、双方立场串写、关系泄漏、反思强制度与人工自然度，不发布单一含混的“OOC 总分”。

## v0.6.0：可信部署与完整安全边界

v0.6 处理“可信宿主如何把内核部署成服务”，而不是把角色关系中的 `trust` 数值误用为系统访问权限。

### 身份、授权与租户范围

- 产品层负责认证；核心只接受已经验证的调用主体与访问范围 Interface；
- Tenant、Data Owner、Agent、User 与 Relationship 的授权关系显式建模；
- 每次读写都必须由受信任范围约束，不能只依赖调用方传入任意 `user_id` 或 `relationship_id`；
- File/SQLite 之外的生产 Storage Adapter 必须证明租户范围、查询条件、唯一约束、备份和删除语义；
- 角色对用户的情感信任、亲密度与安全感永远不能成为数据库授权依据。

### 加密、密钥与 MemoryPack 信任

- 网络部署使用受支持的 TLS 配置，并明确终止位置和内部传输假设；
- 敏感正文具备静态加密策略，密钥由外部 Key Provider 管理，不硬编码在配置、数据库或 Pack 内；
- 支持密钥标识、轮换、吊销、灾难恢复和旧数据重新加密；
- MemoryPack 支持签名或 MAC Envelope、可选加密、可信签发者策略与明确的验证失败行为；
- 自洽指纹、签名/MAC 与加密分别说明：前者检测内部不一致，第二类认证来源和完整性，第三类保护机密性。

### 多租户、审计与数据生命周期

- 建立跨租户读取、写入、枚举、导入、导出、缓存、任务队列和日志泄漏的负向测试；
- 认证、授权、密钥操作、导入、导出、删除和管理员动作进入不含聊天正文的安全审计日志；
- 永久删除覆盖主存储、派生投影、队列副本、缓存、备份生命周期与外部处理方清单；
- 备份恢复不得绕过租户隔离，也不得把已删除数据无提示地重新带回在线系统；
- 在 v0.4 已有的基础请求/导入上限与服务所有者 Key 之上，增加按身份限流、可配置超时、资源配额、依赖更新与安全响应流程；
- 建立正式威胁模型，并在宣称可公开部署前完成独立安全复核。

### v0.6 退出条件

- 身份认证、对象级授权、传输/静态加密、密钥轮换、租户隔离、审计、备份恢复和永久删除均有端到端验证；
- MemoryPack 的可信导入策略覆盖未知签发者、过期/吊销密钥、签名失败、重放和跨租户恢复；
- 安全测试覆盖正常路径和负向路径，关系隔离测试不能代替租户安全测试；
- 核心与产品安全层通过清晰 Seam 解耦，本地可信宿主仍可使用开放内核；
- 若上述条件未完成，版本说明必须继续明确“仅供可信宿主嵌入”，不能宣传为公网生产就绪。

## v0.7.0：产品体验与外部验证

- 基于稳定只读投影提供用户可见的记忆时间线、来源、状态标签和关系叙事；
- 提供认知纠正、撤回、替代、冲突处理和 Persona Growth Proposal 的显式审批体验；
- 提供数据导出、永久删除、设备迁移、备份状态和外部模型数据流向说明；
- 产品 UI 只调用稳定 Interface，不直接拼接底层表、MemoryNode 或内部关系数值；
- 建立账户、订阅、用量、可观测性、故障恢复和用户支持流程；
- 通过维护者之外的真实用户验证安装、理解、长期使用、迁移与纠错体验；
- 核心记忆内核和用户数据可携带能力继续开放；商业价值主要来自托管服务、安全运营、集成和产品体验；
- 多 Agent 共享关系图、跨用户证据池和内核主动发送消息仍不是默认能力；如未来探索，必须显式选择、独立授权并证明不破坏关系隔离。

## v1.0：正式产品准入

v1.0 不是“功能足够多”，而是项目已经能够可靠承担真实用户的长期关系数据。

必须同时满足：

- Schema 与 MemoryPack Format 连续两个次版本没有重大重构；
- 真实旧数据完成升级、验证、回滚、导出、导入、删除和备份恢复演练；
- 长期关系评测稳定，并保存不同宿主模型、语言和版本间的可比较结果；
- 存在维护者之外的持续用户，且关键使用流程不依赖维护者现场解释；
- 核心与产品层解耦，核心数据可携带，商业服务故障不锁死用户数据；
- 认证、授权、传输/静态加密、密钥管理、多租户隔离、审计与安全响应能力完成并经过独立复核；
- 具备明确支持范围、兼容政策、漏洞响应入口和可持续投入能力；
- 发布流程可重复，版本、tag、构建产物、来源证明与 Changelog 可审计；
- 仓库、测试、文档、示例和发布包不包含未经授权的角色全文、用户私有资料或来源不明的数据集；
- 完成目标市场的正式商标近似检索，并根据结果决定名称、标识或免责声明策略。

## 跨版本维护契约

### 每个 Pull Request

- 运行领域单元测试、File/SQLite 共享契约、关系隔离、Ruff 与 compileall；
- 从声明的测试/开发依赖干净安装，不依赖维护者机器上偶然存在的包；
- 任何新权威数据必须定义来源、关系范围、幂等身份、导出导入、删除和旧版本降级语义；
- 新 Module 必须通过自己的 Interface 测试；不得只为测试方便把内部 Seam 暴露成公共 Interface；
- 新能力若需要不断给 Engine 或 BaseStorage 增加方法，应先重新设计更深的 Module。

### 定时与发布验证

- 定时运行长期轨迹、大数据量性能、最低依赖和最新依赖组合；
- 发布前运行历史迁移 fixture、wheel/sdist 干净安装、关键文档示例和可携带性往返；
- 只有 CI 实际验证的 Python、操作系统、Storage 与可选依赖组合才能写入兼容文档；
- 发布产物只构建一次；GitHub Release 与其他包仓库使用同一批已验证 Artifact；
- 当前没有其他贡献者不构成跳过外部环境验证的理由，使用干净虚拟机或容器模拟第一次安装者。

### 数据与领域不变量

- Character Blueprint 与 Approved Persona Manifest 是不可被普通关系事件静默覆盖的人设底色；
- 每个 `Agent × User` 关系独立，原作关系与当前用户关系默认分离；
- Source Transcript 证明“双方实际说过什么”，不自动证明内容为事实或人格变化；
- Relationship Event 是关系状态的权威追加历史；Current Belief、Relationship State、Episode、Chapter 与 Continuity Map 是可重建投影；
- 连续性判断与情感效价、User 满意度和产品安全策略相互独立；有依据的拒绝可以成立，无依据的温柔也可以漂移；
- User 只能提供自身立场与经历的证据，角色内心只来自获准人格、正式反思及通过连续性审查的角色选择；共同关系结果不能由任一方单独填写；
- 角色必须记住自己行为造成的已接受后果，但内核不强制道歉、原谅、撤回边界、复合或维持关系；
- 普通关系状态可以自动、渐进更新；核心人格变化或巨大跃迁只形成提案，由宿主在对话外显式决定；
- 模型输出、用户可见标签、多模型投票和置信度都不能单独授权权威写入；
- 完整聊天默认长期保留，但用户必须拥有可验证的导出和永久删除能力。

### 内容、版权与品牌

- 仓库中的人设、长轨迹和评测数据只使用原创、明确授权或兼容许可内容；
- 用户自行导入的人设原文保留在用户控制的数据范围内，不随示例、错误报告、遥测或 MemoryPack 测试 fixture 提交到仓库；
- Issue 与贡献指南要求最小复现先移除真实聊天、隐私信息和版权敏感全文；
- 项目名称可以继续使用，但进入正式商业推广和 v1.0 前完成目标市场的商标近似检索。

## 长期非目标

- 自动共享不同用户或不同 Agent 的关系记忆与亲密程度；
- 以多数投票、模型自信或情绪强度直接决定事实、人格或关系跃迁；
- 核心引擎绕过宿主直接向用户发送消息；
- 为追求“支持更多框架”而维护大量没有真实用户和维护责任的浅 Adapter；
- 未经用户确认上传、训练或公开其人设原文与完整聊天；
- 把零泄漏的领域关系隔离宣传成已经完成身份认证、授权或多租户安全；
- 在缺少长期对照评测前加入复杂多模型辩论、抽象信任公式或跨用户证据网络。

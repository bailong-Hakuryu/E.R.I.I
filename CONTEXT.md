# E.R.I.I. Domain Glossary

E.R.I.I. 描述情感型 Agent 与用户如何分别形成共同历史、当前认知与关系人格。本文档规定项目统一使用的领域语言。

## 身份与人格

**Character Blueprint（人设底色）**：
用户导入的角色身份、价值观、表达风格与边界的权威原文快照；结构化结果只能解释它，不能反向改写它。
_Avoid_: 核心人格记忆、Core Memory、系统提示词

**Character Continuity（角色连续性）**：
角色从 Character Blueprint 与 Formative Experience 出发，在一段 Persona Instance 中因真实经历形成可追溯变化、同时保持心理与经历因果一致性的生命延续；它允许成长，但不等同于冻结原作状态或无依据地改变人格。
_Avoid_: 静态人设复刻、表面口癖一致、无因人格漂移

**Continuity Basis（连续性依据）**：
在一项角色行为或重要变化形成时已经存在的 Character Blueprint、Formative Experience、关系历史，或当下可观察的明确转折情境；行为发生后临时编造的理由不能反向成为其依据。
_Avoid_: 事后合理化、无来源成长、先漂移后补记忆

**Continuity Evaluator Capability（连续性评估器能力）**：
由宿主提供具体实现、由内核定义并编排的版本化交付前能力；它根据拟展示的 Agent 回复、当前 User 消息与获准的人格和关系上下文生成严格 Reply Continuity Assessment。评估器只能指出依据、张力和冲突，不能改写台词、批准人格成长或伪造经历。
_Avoid_: 回复生成器、自动润色器、Persona Growth 审批器、事后开脱器

**Reply Continuity Assessment（回复连续性评估）**：
对一条尚未展示的 Agent 回复作出的有来源判别；Continuity Evaluator 先分别提出结构化 Continuity Finding，内核再用版本化 Continuity Aggregation Policy 汇总为 `aligned`、`supported_new_choice`、`review_required` 或 `unsupported_drift`。结果保存非敏感版本与依据引用，但不保存评估 Prompt 或模型内部推理。
_Avoid_: 人格真值、关系状态变化、模型直接指定总结果、无依据置信分数

**Continuity Finding（连续性发现）**：
Continuity Evaluator 针对 `identity_values`、`psychological_causality`、`relationship_scope`、`knowledge_memory_scope` 或 `voice_style` 单独提出的有来源发现；每项标明相关回复片段、Continuity Basis 或冲突依据、严重度与可机读原因码，不以一个轴的偏差替代另一个轴的判断。
_Avoid_: 单一 OOC 分数、自由文本总评、把口癖偏差当人格背叛、无引用冲突

**Continuity Aggregation Policy（连续性汇总策略）**：
内核把多个 Continuity Finding 确定性汇总为 Reply Continuity Assessment 的版本化规则；关系串线、错误亲密继承和不可知信息属于硬冲突，核心人格或因果张力按已有依据路由为受支持新选择、待审查或无依据漂移，单独的 `voice_style` 偏差最多产生风格修订建议。宿主可以因产品风格要求重新生成，但不能把该选择记为人格漂移。
_Avoid_: LLM 自选最终标签、隐藏阈值、风格模板执法、用产品文案偏好改写历史

**Continuity Delivery Gate（连续性交付门）**：
宿主在向 User 展示 Agent 回复前应用 Reply Continuity Assessment 的边界；正式产品默认允许 `aligned` 与 `supported_new_choice`，暂缓 `review_required` 与 `unsupported_drift`，由宿主重新生成或在对话外显式处理。内核库提供判断与回执，但不自行控制界面或启动隐藏工作。
_Avoid_: Source Acceptance、聊天内用户批准、静默放行、内核直接发送消息

**Persona Instance（关系人格实例）**：
人设底色在一段具体关系中的独立人格；每个 `Agent × User` 分别成长，不继承其他关系的亲密程度或共同经历。
_Avoid_: 全局人格、共享人格

**Relationship Policy（关系演化策略）**：
与人设底色绑定的版本化解释策略，规定不同关系信号如何影响该角色；它可以在安全范围内体现慢热、重承诺等差异，但不能突破全局限幅。
_Avoid_: 运行时情绪提示词、LLM 自定数值、无版本性格参数

**Identity（身份）**：
关系一方稳定且不可因显示名称或外部平台标识变化而改变的身份。
_Avoid_: 用户名、Agent 名称、外部 ID

## 关系与历史

**Relationship（关系）**：
一个 Agent 身份与一个 User 身份之间独立、连续的共同生命线。
_Avoid_: 会话、聊天室、全局关系图

**Relationship Premise（关系前提）**：
宿主在创建单段关系时显式选择的叙事起点，规定当前用户是否仅使用某个称呼或绑定为既有关系角色；它只作用于该关系，不能从 Character Blueprint 静默推断或传播给其他用户。
_Avoid_: 全局用户角色、原文中的自动身份映射、聊天中途改写世界线

**Premise Experience（前提经历）**：
由已选择的 Relationship Premise 声明为该关系既有背景的经历，保留其导入来源并与当前互动中观察到的 Relationship Event 区分。
_Avoid_: 当前聊天证据、自动复制的共同回忆、无来源初始记忆

**Relationship Baseline（关系基线）**：
由当前关系显式选择的 Relationship Premise、Premise Experience 与版本化确定性策略建立的不可变起始投影；当前关系状态在它之上继续叠加真实 Relationship Event。
_Avoid_: LLM 直接填写关系数值、伪造普通历史事件、可变初始状态

**Relationship Event（关系事件）**：
追加到关系历史中的共同经历、观察、承诺、冲突、修复、更正或反思；事件一旦接受便不通过覆盖来抹去。
_Avoid_: 可变状态记录、聊天日志

**Turn Record（轮次记录）**：
单段 Relationship 内从 User 消息到 Agent 可见回复的持久生命周期容器，拥有稳定 `turn_id` 与 `open | completed | abandoned` 状态；`open` 已保存用户消息但尚未形成完整互动，`completed` 封存为 Source Turn，`abandoned` 如实保留未获回复的用户消息及非敏感终止原因。
_Avoid_: 后台任务、聊天会话、Source Processing Run、把未回复消息伪装成完整互动

**Source Turn（来源轮次）**：
一个已经 `completed` 的 Turn Record，即单段 Relationship 内一轮完整 User 与 Agent 可见交互的稳定、版本化来源身份；它拥有封存的 Source Transcript 与 Source Processing Plan，Timeline、MemoryNode 与关系候选等不同处理结果都引用它，归档、裁决、重试或重处理不会因此创造另一轮互动。
_Avoid_: open Turn Record、abandoned Turn Record、Archival Submission、队列任务、同一交互的多套来源 ID

**Source Transcript（来源对话原文）**：
一个 Turn Record 中关系参与方实际可见消息及必要来源元数据的规范、持久记录；`open` 或 `abandoned` 状态可以只有 User 消息，`completed` 状态必须同时包含已展示的 Agent 回复、连续性评估状态与显式交付处置。内核默认不自动过期并允许数据所有者显式导出或删除；它以最高保真度证明双方实际表达过什么，但不自动证明用户陈述为事实、Agent 回复符合人设，或其中任何解释已经成为长期记忆、关系历史或人格变化。
_Avoid_: 长期记忆、Relationship Event、完整 Prompt、隐藏系统消息、模型内部推理、不可删除审计日志

**Turn Opening（轮次开启）**：
`begin_turn()` 在回复生成前原子保存关系范围、稳定 `turn_id`、User 可见消息与获准的 Interaction Context Signal，并把 Turn Record 置为 `open` 的边界；成功只证明用户消息已进入对话档案，不表示 Agent 已经回应或任何派生处理已经开始。
_Avoid_: Source Acceptance、记忆归档、回复草稿、仅存在内存中的临时 ID

**Reply Attempt（回复尝试）**：
宿主针对同一 `open` Turn Record 执行的一次回复生成、连续性评估或交付准备尝试；可重试失败只留下尝试编号、阶段、能力版本、时间与脱敏错误分类，不把未展示草稿、Prompt、模型内部推理或服务秘密写入 Source Transcript。技术失败不会自动改变 Turn Record 的 `open` 状态。
_Avoid_: Source Turn、Agent 可见回复、聊天历史、自动 Turn Abandonment

**Source Acceptance（来源接受）**：
`complete_turn()` 把已展示 Agent 回复、Reply Continuity Assessment、交付处置与固定 Source Processing Plan 原子追加到 `open` Turn Record，并将其封存为 `completed` Source Turn 的边界；接受成功只证明完整来源和处理责任已经安全记录，不表示长期记忆或关系裁决已经完成。
_Avoid_: Turn Opening、回复生成成功、Archival Completion、Relationship Event 接受、后台任务入队

**Turn Abandonment（轮次放弃）**：
`abandon_turn()` 在用户取消、宿主显式终止或不可恢复错误后，把 `open` Turn Record 原子终结为 `abandoned`；可重试技术失败本身不能触发它。已经发送的 User 消息继续作为 Source Transcript 保存，但没有虚构的 Agent 回复，也默认不进入只接受完整 Source Turn 的记忆归档与关系裁决。
_Avoid_: 删除用户消息、空 Agent 回复、No-Memory Outcome、普通超时、自动清理任务

**Turn Terminal Conflict（轮次终态冲突）**：
同一 Turn Record 已经以 `completed` 或 `abandoned` 终结后，另一项不同终态操作或不同内容的重复完成所产生的显式冲突；相同完成载荷的幂等重试返回既有 Source Turn Receipt，而完成与放弃并发时只有第一个原子提交能够生效。
_Avoid_: 覆盖终态、重新打开、第二份 Agent 回复、把冲突当成功

**Turn Recording（轮次记录）**：
宿主通过统一的 `begin_turn() → complete_turn() | abandon_turn()` 生命周期记录互动的公开行为；已经同时拥有双方可见消息的宿主可以使用原子便捷入口 `record_turn()`，但所有形式都写入同一 Turn Record 与 Source Transcript 账本。成功封存后，归档、关系裁决、查询和重试只引用稳定 `source_turn_id`。
_Avoid_: `remember()`、第二套来源写入口、直接创建队列任务、把原文分别交给多个处理器

**Source Turn Receipt（来源轮次回执）**：
`complete_turn()` 或 `record_turn()` 成功后返回的非敏感确认，至少标识 `source_turn_id`、关系范围、来源版本、接受时间、固定处理计划及各通道可查询状态；它证明完整 Source Turn 已经持久化，但不复制原文，也不把待处理状态伪装成长期记忆已经形成。
_Avoid_: Source Transcript、Archival Receipt、Relationship Decision Receipt、完整对话响应

**Source Processing Plan（来源处理计划）**：
与 Source Turn 同时接受、此后不可原地改写的处理通道集合；默认声明当前已配置的 Memory Archival 与 Relationship Adjudication 通道，宿主可以在接受前显式省略某一通道。计划只约束本轮承诺执行的处理，后续显式处理使用新的运行身份而不伪装成原计划。
_Avoid_: 后台线程配置、可变任务清单、Inner Review、静默启用新模型

**Source Processing Outcome（来源处理结果）**：
一个已声明处理通道独立留下的可观察结果；它区分产生派生产物的成功、合法零产物与失败。只有计划内所有通道都以成功或合法零产物终结时，Source Turn 才算处理完成；任何处理失败都不得回滚已经接受的 Source Transcript。
_Avoid_: Source Acceptance、单一全局布尔值、把失败伪装成无产物、因一个通道失败删除另一通道结果

**Relationship Event Extractor Capability（关系事件提取器能力）**：
由宿主提供具体实现、由内核定义并编排的版本化能力，把一个已接受 Source Turn 转换为严格 Relationship Event Extraction Decision；内核负责读取规范原文、组装不含人格化解释的受限事实上下文、验证证据与输出、冻结候选、处理重试并调用确定性裁决器。提取器只能提出中性的事件、证据与定性信号，不能生成 Persona Reflection 或自行写入关系历史与状态。
_Avoid_: Persona Reflection 生成器、手工候选作为默认流程、供应商绑定的内置模型、关系状态写入器、隐藏后台 Agent

**Relationship Event Extraction Decision（关系事件提取决定）**：
Relationship Event Extractor 对一个处理运行返回的严格判别结果，只能是至少含一个不带人格反思的 Relationship Event Candidate 的 `candidates`，或不含候选的显式 `no_relationship_event`；空响应、非法结构、人格化事实摘要与两类混用都属于提取失败。
_Avoid_: 任意 JSON、空候选批次、Persona Reflection、Relationship Event、Decision Receipt、静默忽略

**No-Relationship-Event Outcome（无关系事件结果）**：
一次由 `kind=no_relationship_event` 合法产生的零候选 Relationship Adjudication 通道完成，表示本轮已成功检查但没有内容需要进入权威关系历史；它不表示对话没有长期记忆价值，也不掩盖提取、验证或存储失败。
_Avoid_: No-Memory Outcome、关系处理失败、普通闲聊占位事件、自动遗忘

**Source Retry（来源重试）**：
宿主对同一来源身份和版本的重复提交；它必须返回既有裁决结果，不产生新的事件、反思或关系状态变化。
_Avoid_: 新互动、历史重述、重新裁决

**Source Adjudication Run（来源裁决运行）**：
对一个 Source Turn 版本和处理身份执行的一次候选裁决；它可以履行 Source Processing Plan 中声明的 Relationship Adjudication 通道，也可以作为后续显式处理运行。首次提交会固定完整候选批次指纹，普通重试不得新增、删除或改写候选，显式 Historical Reprocessing 使用独立处理身份。
_Avoid_: 每次重试重新采样候选、候选键局部幂等、静默扩张历史

**Historical Reprocessing（历史重处理）**：
宿主显式指定既有 Source Turn 及新处理版本的一次追加式复核；内核可以读取其 Source Transcript 重新提取或裁决，并产生佐证、更正、重新理解或新提案，但不得覆盖原裁决、重写当时的理解或重复结算既有关系影响。
_Avoid_: 模型升级自动重跑、普通来源重试、静默迁移

**Event Corroboration（事件佐证）**：
后来出现、用于支持、补充或反驳既有 Relationship Event 的新证据关联；它可以改变当前认知的证据基础，但不重新结算原事件的关系影响。
_Avoid_: 新事件、重复状态变化、静默覆盖

**Event Reference（事件引用）**：
当前互动对既有 Relationship Event 的显式关联；被引用的旧经历仍只记录和结算一次，而共同回忆、重新理解或纠正等当前行为可作为独立新事件裁决。
_Avoid_: 复制旧事件、模糊语义强制合并、重复结算

**Evidence（记忆证据）**：
从 Source Transcript 精确引用、用于支持一项候选记忆或关系变化的最小原文依据，同时标明来源与完整性；它足以解释“为什么这样记”，但不会复制完整聊天，也不会凭引用本身获得解释权。
_Avoid_: 全量聊天副本、模型自述、无来源摘要、把原话直接当作已裁决事实

**Relationship Signal（关系信号）**：
对互动所表达关系意义的定性候选，例如感谢、袒露、守信、越界、冲突或修复；它描述发生了什么，但不直接决定关系状态数值。
_Avoid_: 最终状态变化、LLM 生成的好感数值

**Persona Reflection（人格化反思）**：
Agent 在对应 Relationship Event 已被接受后，基于可核验证据、以符合 Character Blueprint 的第一人称方式记录“当时如何理解”该事件；它不是用户原话，不参与决定事件是否发生，也不直接决定关系状态数值。
_Avoid_: 事实摘要、事件候选字段、证据引文、统一文案模板

**Persona Reflection Record（人格反思记录）**：
把一项 Persona Reflection 作为关系范围内独立、不可变且追加式保存的正式记录；它拥有稳定 `reflection_id`，引用一个已接受 Relationship Event、生成它的解释器版本与 Reflection Context Provenance，不再作为事件 metadata 中可有可无的字符串。没有反思时不创建占位记录。
_Avoid_: Relationship Event metadata、事件事实、当前渲染文案、可原地编辑的角色心情

**Reflection Context Provenance（反思上下文来源）**：
随 Persona Reflection Record 保存的最小、不可变上下文引用集合，标明反思生成时使用的 Source Turn 与 Evidence、Relationship Event、Blueprint ID 与哈希、Manifest 修订、Relationship Baseline、已批准成长以及相关既有事件和反思版本；它通过 ID、版本与哈希冻结“当时依据”，而不复制整份人设、完整关系历史或聊天原文。
_Avoid_: 完整上下文快照副本、当前最新人格、其他关系历史、Prompt、模型内部推理

**Persona Reflection Interpreter Capability（人格反思解释器能力）**：
由宿主提供具体实现、由内核定义并编排的版本化能力；它只解释已经接受的 Relationship Event，并在隔离于事实提取的第二阶段读取获准的人格与关系上下文，返回严格 Persona Reflection Decision。它不能改写事件、证据、Relationship State 或 Character Blueprint。
_Avoid_: Relationship Event Extractor、事实裁决器、人格成长审批器、对未接受事件的想象

**Persona Reflection Decision（人格反思决定）**：
Persona Reflection Interpreter 对一个已接受事件返回的严格判别结果，只能是一个有来源、符合角色且边界受限的 `reflection`，或不生成反思的显式 `no_reflection`；反思失败不会撤销已接受事件，普通事件也不需要被迫制造内心独白。
_Avoid_: 空字符串、事件摘要、统一角色口癖、Relationship State 变化、用反思证明事件

**Recall Rendering（当前叙述）**：
Agent 现在讲述历史反思时采用的临时表达；措辞可以随当前风格变化，但不得改变原反思的事实、情绪方向、强度或核心含义，也不因被渲染而写入历史。
_Avoid_: 新关系事件、历史反思重写、Renderer 生成的新内心

**Reflection Correction（反思更正）**：
新证据证明旧 Persona Reflection Record 存在误解时追加、并显式引用其 `reflection_id` 的类型化更正；旧反思仍作为当时真实发生过的理解保留，更正自身也保存来源与生成时上下文。
_Avoid_: 覆盖、删除、静默重写、无目标反思的通用纠错事件

**Reinterpretation（重新理解）**：
Agent 后来获得、显式引用既有 `reflection_id` 的新视角；它扩展当前理解并保存自己的来源与生成时上下文，但不宣称自己当时就已经如此理解。
_Avoid_: 反思更正、追溯性人格改写、覆盖原反思

**Decision Receipt（裁决回执）**：
一次候选裁决留下的最小、持久结果，用于说明候选被接受、转为提案或拒绝，并防止相同候选被重复处理。
_Avoid_: 被拒绝候选全文、调试日志、正式关系事件

**Candidate Confidence（候选置信度）**：
模型对候选提取准确性与关系解释稳定性的自我评估；分别记录提取置信度与解释置信度，只用于裁决路由、变化限幅和审计，不能单独证明事实或授权记忆、关系状态及人格变化生效。
_Avoid_: 真实概率、证据强度、单一自动接受阈值、人格变化许可

**Candidate Dependency（候选依赖）**：
一项派生结果生效前必须已经满足的因果前提，例如 Persona Reflection 依赖已接受的 Relationship Event；同一来源允许部分成功，但每个候选的证据、事件、状态变化与回执必须原子提交，依赖失败的结果不能悬空生效。
_Avoid_: 整条来源全有或全无、隐式顺序、无事件反思

**Evidence Reference（证据引用）**：
候选所引用的来源身份、消息角色、精确原文片段、来源全文哈希与时间；它足以核验引用，但不是完整聊天记录的副本。
_Avoid_: 完整对话存档、LLM 解释、无来源摘要

**Current Belief（当前认知）**：
由关系事件投影出的 Agent 当前所相信的内容，同时保留置信度与来源事件。
_Avoid_: 永久事实、无来源印象

**Relationship State（关系状态）**：
由关系事件投影出的熟悉、信任、亲密、安全感与冲突张力；普通变化是渐进、有证据且有限幅度的。
_Avoid_: 好感度、单一亲密值

**Recorded Time（记录时间）**：
E.R.I.I. 接受并持久保存一项记录的真实 UTC 审计时间；它说明系统何时知道，而不说明事件何时发生。
_Avoid_: 发生时间、故事时间

**Occurred Time（发生时间）**：
事件在来源或应用时间线中实际发生的时间；它可以未知，不能用 Recorded Time 静默代替。
_Avoid_: 入库时间、推测时间

**World Time（世界时间）**：
角色所处故事世界的可选时钟上下文，由宿主显式提供并以时钟身份区分；没有同一时钟内的可比较值时只能叙述，不能用于到期或先后判断。
_Avoid_: 系统当前时间、LLM 推测时间、默认现实时间

**Temporal Context（时间上下文）**：
在一次显式观察中组合 Recorded Time、Occurred Time、宿主观察时间与可选 World Time 的关系背景；它让角色感知离别、沉寂与重逢，但不在后台自动修改 Relationship State。
_Avoid_: 定时亲密衰减、隐藏后台任务、用时间删除历史

**Inner Review（内在审视）**：
Persona Instance 在触发事件和反思已成为正式历史后，基于自己的历史与价值张力进行的独立审视；它可能形成成长意愿，也可能拒绝改变或保持未决。
_Avoid_: 数值阈值自动升级、用户命令改写

**Growth Trigger（成长触发依据）**：
使 Persona Instance 有资格进入 Inner Review 的正式历史依据；可以是多个独立事件呈现的持续价值张力，也可以是经关系策略和确定性规则确认的单个转折性事件。它只触发审视，不直接产生或批准人格变化。
_Avoid_: 用户命令、模型自称重大、单次数值越线、自动人格升级

**Inner Growth Intent（内在成长意愿）**：
Persona Instance 对“自己希望在这段关系中成为什么样”的第一人称意愿，必须说明历史来源、当前张力以及与人设底色的一致或冲突。
_Avoid_: 用户要求、宿主配置、LLM 无来源结论

**Persona Growth Proposal（人格成长提案）**：
由 Inner Growth Intent 形成、只作用于当前关系人格实例的重大成长候选；确认前不属于有效人格，也不能修改 Character Blueprint。
_Avoid_: 人设底色修订、自动人格改写

**Persona Growth Approval（人格成长批准）**：
宿主在聊天之外对角色已经完整形成的特定版本成长提案作出的可审计安全决定；批准者不能在批准时填写或修改成长内容。
_Avoid_: 对话内同意、模型自批、宿主代写成长

**Blueprint Revision（人设底色修订）**：
角色拥有者通过宿主显式创建的 Character Blueprint 新版本；它不来自聊天或 LLM，也不会自动迁移现有关系。
_Avoid_: Persona Growth、覆盖旧人设、关系成长全局传播

## 记忆与叙事

**Archival Submission（归档提交）**：
从一个已经接受的 Source Turn 提取并保存 Timeline 与 MemoryNode 的处理命令；它可以履行 Source Processing Plan 中声明的 Memory Archival 通道，也可以作为后续显式处理运行。只有来源范围与处理请求通过边界校验后，它才被归档系统接受并获得 Archival Identity，但不会成为第二套交互来源身份。
_Avoid_: Source Turn、Turn Recording、单边消息、队列任务、已归档记忆

**Archival Identity（归档身份）**：
一次已接受 Archival Submission 在队列与内联模式间共用的稳定公开标识 `archival_id`；它关联回执、重试和产物来源，但不是队列内部 Task ID、幂等键或访问凭证。
_Avoid_: task_id、idempotency_key、访问令牌

**Archival Scope（归档范围）**：
一项 Archival Submission、Receipt 与其产物所属的外部 `Agent × User` 数据边界；读取回执必须同时匹配双方 ID 与 Archival Identity，但该匹配不代替宿主认证或租户授权。
_Avoid_: tenant_id、访问控制、仅凭 archival_id 查询

**Archival Submission Error（归档提交错误）**：
Archival Submission 被接受前发现的输入边界错误；它不创建任务或回执，也不进入自动重试。
_Avoid_: Archival Failure、No-Memory Outcome、静默忽略

**Archival Capability（归档能力）**：
把完整对话轮次提取并持久化为长期记忆的可选 Engine 能力；未配置提取器的 Engine 仍可使用关系、召回和迁移能力，但不能接受 Archival Submission。
_Avoid_: 聊天生成能力、Dummy 占位记忆、Engine 整体可用性

**Memory Extractor Capability（记忆提取器能力）**：
把一轮完整交互转换为严格 Archival Extraction Decision 的功能级版本化能力；提取器必须声明 Extractor Descriptor，并且只能提出候选内容，不能自行决定关系范围、产物身份、时间或归档来源。
_Avoid_: 通用聊天 LLM、原始字符串生成器、Storage 写入器、人格编译器

**Archival Extraction Decision（归档提取决定）**：
Memory Extractor 对单轮归档返回的严格判别结果，只能是至少含一个候选产物的 `artifacts`，或不含任何产物的显式 `no_memory`；空响应、非法结构与两类混用都不是合法决定。
_Avoid_: 任意 JSON、空对象、静默忽略的未知字段、存储结果

**Memory Candidate（记忆候选）**：
Archival Extraction Decision 中尚未成为长期 MemoryNode 的受限结构化内容；Engine 负责验证、脱敏并注入范围、稳定 ID、时间与 Archival Provenance，提取器不得提供这些权威字段。
_Avoid_: MemoryNode、Relationship Event Candidate、模型指定的身份或来源

**Atomic Archival Store Capability（原子归档存储能力）**：
Storage 显式声明的功能级版本化能力，表示它能以一个权威发布点提交完整 Archival Batch，并在失败或恢复后保证读者看不到部分批次；缺失该能力只禁止可信归档，不应使旧 Storage 的其他独立能力失效。
_Avoid_: 新增 BaseStorage 强制抽象方法、按方法名猜测支持、顺序写入降级、整个 Storage 的粗粒度版本

**Prepared Commit Queue Capability（预备提交队列能力）**：
Task Queue 显式声明的功能级版本化能力，表示它能原子保存一个固定的 Prepared Archival Batch、校验当前 Processing Lease，并为该批次签发唯一 Commit Permit；缺失该能力的 Queue 不能参与可信归档。
_Avoid_: 普通 enqueue/dequeue、提交前一次性 token 检查、整个 Queue 的粗粒度版本

**Archival Batch（归档批次）**：
从一次已接受 Archival Submission 提取出的 Timeline 与全部 MemoryNode 产物集合；同一次提交尝试中的批次只能整体可见或整体不可见。
_Avoid_: 多轮批处理、队列分页、部分写入

**Prepared Archival Batch（预备归档批次）**：
一次 Archival Attempt 已完成提取、验证、脱敏并固定产物 ID 后，由 Queue 持久保存但尚未对召回可见的精确 Archival Batch；它以内容摘要绑定后续提交，恢复时只能重放同一批次，不能再次调用模型产生替代结果，并在完成后立即清除或在失败后仅保留于有界恢复窗口。
_Avoid_: 未验证模型输出、可变候选集合、已提交长期记忆、重新提取提示

**Archival Payload（归档载荷）**：
Archival Submission 为完成提取而暂存在任务队列中的 Source Transcript 工作副本或受限读取材料；它不是规范聊天历史，在 Commit Binding 建立后立即清除，绑定前失败时只在明确恢复期限内保留。清理 Archival Payload 不会删除其 Source Transcript。
_Avoid_: Source Transcript、Archival Receipt、永久任务日志

**Archival Provenance（归档来源）**：
Archival Batch 及其每个 Timeline 或 MemoryNode 产物对同一 `source_turn_id` 与处理它的 `source_archival_id` 的稳定关联；它在载荷与运行回执清理后仍保留来源身份，可以在授权范围内定位规范 Source Transcript，但不复制原始对话，也不赋予访问权限或解释权。
_Avoid_: Archival Payload、完整聊天证据、授权凭证

**Artifact Provenance State（产物来源完整性）**：
说明长期产物的来源是新契约要求的 `complete`，还是因为旧存储从未记录该信息而成为 `legacy_unavailable`；未知来源必须保持未知，不能通过内容、时间或迁移批次反推并伪造。
_Avoid_: 提取置信度、迁移成功状态、猜测的 `source_archival_id`

**Extractor Descriptor（提取器描述）**：
与 Archival Provenance 或关系处理运行永久关联、说明某个候选批次由哪一种提取实现和数据契约生成的非敏感版本身份；它用于审计、迁移与显式 Historical Reprocessing，不包含提取提示词、服务秘密、模型原始输出或原始对话。
_Avoid_: 完整 Prompt、API 密钥、提供商地址、自动重处理指令

**Archival Completion（归档完成）**：
一次对话归档的提取结果已经通过验证，且所有预期持久化写入均成功完成；它描述处理完整性，不要求本轮一定产生长期记忆。
_Avoid_: LLM 已返回、任务已领取、至少写入一条记忆

**No-Memory Outcome（无记忆结果）**：
一次由 `kind=no_memory` 的合法 Archival Extraction Decision 明确产生的零产物 Archival Completion，表示本轮经成功处理后没有内容需要进入长期记忆；空响应、非法输出或存储失败不属于该结果。
_Avoid_: 归档失败、空模型响应、占位记忆

**Archival Receipt（归档回执）**：
宿主在 Archival Scope 内按 Archival Identity 观察一次对话归档提交及其执行结果的非敏感持久投影；队列与内联模式共用这一模型，它可以报告状态、尝试、结果和产物计数，但不携带对话原文或模型原始输出。
_Avoid_: 任务载荷、日志文本、单一成功布尔值

**Artifact Manifest（产物清单）**：
完整 Archival Receipt 中由稳定产物类型与 ID 组成的只读脱敏索引，用于准确说明某个 Archival Batch 创建了哪些 Timeline 与 MemoryNode；产物计数由该清单推导，清单不包含正文、评分、向量或存储路径，并随回执压缩而清除。
_Avoid_: 记忆内容副本、可变文件路径、独立维护的计数器、Archival Tombstone

**Archival Retention State（归档保留状态）**：
回执查询中说明当前记录仍是 `full` 完整回执，还是已经成为 `compacted` Archival Tombstone 的标记；被压缩清除的字段必须表示为未知，而不能以零值冒充仍然存在的观测结果。
_Avoid_: Archival Status、处理结果、以 `0` 表示已清理详情

**Archival Tombstone（归档墓碑）**：
Archival Receipt 超出完整保留期后留下的最小终态记录；它只证明某个 Archival Identity 在所属关系中已经得到何种终态结果，防止身份失忆，并在关系被永久删除时一并删除。
_Avoid_: 完整回执、错误日志、永久保留的用户删除痕迹

**Archival Ledger（归档账本）**：
MemoryPack 中只携带 Archival Tombstone 的独立可携带分区；它在迁移后保留已处理归档的最小身份与终态事实，但不包含完整回执或队列运行细节，也不参与记忆召回、关系计算或人格塑造。
_Avoid_: 记忆索引、任务队列备份、运行日志、完整 Archival Receipt

**Archival Status（归档状态）**：
Archival Submission 在 `pending`、`processing`、`retry_wait`、`completed` 与 `failed` 之间的持久生命周期位置；它描述执行进度，不描述本轮是否值得记忆。
_Avoid_: Outcome Code、记忆价值、错误类型

**Archival Phase（归档阶段）**：
说明当前或下一次归档工作属于仍可重新调用提取器的 `extraction`，还是 Commit Binding 建立后只能发布同一 Prepared Archival Batch 的 `commit`；它细化 Processing 与 Retry Wait，但不增加新的 Archival Status。
_Avoid_: Archival Status、Memory Type、把提交重试算作重新提取

**Archival Attempt（归档尝试）**：
一个消费者对某项 Archival Submission 的一次 Processing 执行，由 `attempt_id` 区分；租约过期或执行失败会结束本次尝试，但不会改变 Archival Identity。
_Avoid_: Archival Submission、模型内部重试、队列任务 ID

**Processing Lease（处理租约）**：
通过内部 `lease_token` 与心跳维持的 Archival Attempt 限时所有权；它允许处理并申请提交，但不直接授权发布 Archival Batch，租约失效后旧尝试不能取得 Commit Permit 或改变 Archival Receipt。
_Avoid_: Archival Identity、访问令牌、无限 Processing

**Commit Binding（提交绑定）**：
Queue 在有效 Processing Lease 内把一个 Archival Identity 永久绑定到唯一 Prepared Archival Batch 摘要的不可变决定；后续恢复可以继续提交该批次，但不能重新调用模型、更换产物或绑定另一份摘要。
_Avoid_: Commit Permit、可变候选结果、宿主幂等键、重新提取

**Commit Permit（提交许可）**：
Queue 针对既有 Commit Binding 签发、由 Storage 在原子发布点校验的短期可续内部执行许可；过期 Permit 不能发布，新 Permit 只能指向同一绑定批次，不得授权重新提取或替换摘要。
_Avoid_: 永不过期授权、Processing Lease、外部访问凭证、仅在写入前检查 token

**Commit Termination Fence（提交终止栅栏）**：
Storage 在最终放弃一个已绑定归档或永久删除其关系数据前建立的权威拒绝标记；它先使后续 Commit Permit 无法发布，再允许清理预备批次，并只在全部既发 Permit 已不可能生效后随关系删除。
_Avoid_: 普通失败日志、仅清除 Queue 数据、可被迟到 Worker 绕过的取消标记

**Archival Consumer Lease（归档消费者租约）**：
同一任务队列与记忆存储在一个时期内只允许一个执行消费者持有的可续租所有权；它不限制多个宿主请求提交任务，也不代表用户或租户数量。
_Avoid_: Processing Lease、全局单用户、仅靠进程约定

**Archival Outcome Code（归档结果码）**：
说明 Archival Completion、最近一次失败或终态失败具体结果的稳定机器可读代码；它与 Archival Status 分离，且不携带原始错误或私密内容。
_Avoid_: 生命周期状态、原始异常文本、用户可见文案

**Archival Failure（归档失败）**：
一次已接受归档在提取、验证或持久化阶段未达到 Archival Completion 的执行结果；内联模式通过携带脱敏回执的类型化异常立即报告，队列模式通过可查询回执报告。
_Avoid_: No-Memory Outcome、未接受提交、仅写日志

**Archival Error Envelope（归档错误信封）**：
跨 API 边界报告归档错误的非敏感结构，只携带稳定错误码、是否可重试、安全摘要与已存在的 Archival Receipt；它不能复制原始异常、对话、模型输出或秘密配置。
_Avoid_: `str(exception)` 响应、堆栈跟踪、原始供应商错误、任务载荷

**Retryable Archival Failure（可重试归档失败）**：
由暂时性外部条件、可能在下一次提取中改变的无效模型输出或 Processing 租约过期造成的 Archival Failure；它计入尝试次数，并可以在明确次数与退避策略内自动重试。
_Avoid_: 所有异常、永久配置错误、无限重试

**Permanent Archival Failure（永久归档失败）**：
自动重复执行不会自行恢复的 Archival Failure；它立即进入终态，只有宿主修复根因后才能显式重新提交处理。
_Avoid_: 不可修复数据、自动重试耗尽、No-Memory Outcome

**Archival Drain（归档排空）**：
宿主显式要求调用时已经接受的一组 Archival Submission 快照在期限内进入终态，并取得完成、失败与未完成报告的运行操作；没有存活 Worker 时由调用线程消费该快照，已有 Worker 时只等待它处理，之后的新提交不属于本次操作。
_Avoid_: Engine Shutdown、无限等待、隐藏后台消费

**Engine Shutdown（引擎关闭）**：
宿主停止 Engine 接受新提交、阻止 Worker 领取新任务，并在给定期限内协作等待当前 Archival Attempt 结束的显式生命周期操作；超时只产生真实的未停止报告，不会强杀线程或谎称后台处理已经结束。
_Avoid_: Archival Drain、隐式排空、线程强杀、固定一秒静默返回

**Recall Result（结构化召回结果）**：
一次召回产生的、与 Prompt 格式无关的结构化语义结果；关系尚未建立时，它可以明确不含关系上下文，但不会因此创建默认人格。
_Avoid_: Prompt 字符串、Markdown 记忆、已渲染上下文、隐式关系初始化

**Recall Projection（召回投影）**：
为一次召回从权威历史与当前状态派生出的只读、用途受限表示，保留稳定来源、时间和可见性，但不暴露可变存储对象或内部审计资料。
_Avoid_: 数据库记录副本、领域对象引用、Prompt 片段

**Recall Signal（召回信号）**：
在给定正式历史与观察上下文下可重复计算、带来源事件的当前提示；它不持久化为历史，也不自行修改关系或触发对外行动。
_Avoid_: 关系事件、后台提醒、自动状态变化、永久记忆

**Recall Audience（召回受众）**：
宿主在组装 Recall Result 前明确声明的使用边界；Agent Private 可包含供角色内部推理的材料，Public 只包含允许直接面向用户的投影。
_Avoid_: Renderer 临时猜测、先取全部再隐藏、把内部上下文当公开内容

**Recall Budget（召回预算）**：
在 Recall Result 生成前限制本次真正选中内容的显式策略，以完整 Recall Projection 为取舍单位；它不允许通过截断或渲染时静默省略来改变语义。
_Avoid_: 字符串截断、Renderer 二次筛选、隐式 token 上限

**Persona Recall Context（人格召回上下文）**：
一次召回中严格分层的人格依据，由 Character Blueprint 原文权威、人格解释和当前关系已批准的成长组成；默认以可追溯的规划方式选择核心依据和相关原文，也可显式要求完整原文，待审批、被拒绝或已撤销的成长不属于有效上下文。
_Avoid_: 合并后的人格 Prompt、用成长覆盖底色、未批准人格、把完整保存等同于每轮全文注入

**Persona Interpretation（人格解释）**：
对 Character Blueprint 中人格主张、内在动力、形成性经历及其关联的版本化理解，每项都能追溯到原文且不能取代原文权威。
_Avoid_: 人格摘要、第二份权威人设、无来源推断

**Contextual Voice Pattern（情境表达模式）**：
获批 Persona Interpretation 中对角色“在什么情境下可能调用哪一种表达语域”的结构化 `VOICE + SITUATIONAL` 模式；它引用原文表达样本、相关 VOICE 主张与形成性依据，并以情绪、活动、关系安全条件、交流媒介或环境线索描述激活条件。原文例句只证明该语域在相应条件下可用，不自动成为高频口癖、固定台词或全局人格标签。
_Avoid_: 角色语录库、固定口癖 Prompt、单句推导日常频率、脱离情境的“说话粗鲁”

**Voice Pattern Scope（表达模式范围）**：
Contextual Voice Pattern 对现有 Persona Scope 的显式应用：`character` 表示角色普遍可用的表达资源，`canonical_relationship` 只描述原作关系中的特定表达，`relationship_tendency` 只允许在当前关系独立满足相同条件后激活倾向；原作参与者、称呼、亲密度与共同经历不会因语域相似而映射给当前 User。
_Avoid_: 把原作对象替换为当前用户、跨关系继承亲密、用角色级词汇证明关系级权限

**Interaction Context Signal（互动情境信号）**：
用于本轮 Situational 人格选择与连续性评估的有类型、带来源临时输入；`host_observed` 只描述地点、活动、交流媒介等宿主可观察事实，`core_derived` 来自当前 Persona Instance 的正式关系状态与历史，`evaluator_inferred` 表示独立评估器从当前消息或既有历史提出并附带依据的情绪或心理情境。不同来源不能互相冒充。
_Avoid_: 回复模型自报情绪、无来源标签、永久人格状态、跨关系信号

**Voice Pattern Activation（表达模式激活）**：
Engine 用版本化确定性规则把 Contextual Voice Pattern 的获批条件与当前 Interaction Context Signal 匹配后得到的本轮临时选择；激活结果记录模式与支持信号引用，可供生成和 Continuity Evaluation 使用，但不自动写入 Character Blueprint、Persona Growth、关系历史或长期记忆。
_Avoid_: LLM 自选语域、永久解锁口癖、情绪状态写回、无条件引用原句

**Persona Compilation Proposal（人格编译提案）**：
从 Character Blueprint 生成、尚未成为有效 Persona Interpretation 的完整候选版本；机械来源解析可以自动完成，但语义版本必须在高风险项得到处理后按精确内容批准。
_Avoid_: 编译器自动改写人格、逐条确认所有普通资料、模型升级静默替换

**Persona Compiler（人格编译器）**：
由宿主显式调用、从完整 Character Blueprint 生成带原文依据的 Persona Compilation Proposal 的解释器；它只能提出候选，不能批准结果、替换原文或在后台自动运行。
_Avoid_: 人格生成器、自动审批者、关系初始化副作用

**Persona Compilation Revision（人格编译修订）**：
对同一 Character Blueprint 的 Persona Compilation Proposal 所作的不可变解释修正；它可以调整有原文依据的分类、作用域、连接和 Meaning Capsule，但不能添加原文没有的核心设定。
_Avoid_: 批准时就地编辑、影子人设、用编译修订代替 Blueprint Revision

**Persona Applicability（人格适用性）**：
Character Blueprint 中一项内容在角色层能够产生的有效含义；原文可以完整保留，但试图覆盖宿主安全、授权、隐私、工具权限或关系不变量的部分不具备适用性。
_Avoid_: 删除冲突原文、人设获得宿主权限、把安全拒绝固定成出戏文案

**Formative Experience（形成性经历）**：
支撑、解释或展现角色稳定人格与行为倾向的重要既有经历；它可以塑造角色，但其关系参与者不会因此自动映射为当前用户。
_Avoid_: 普通背景资料、当前用户的共同回忆、确定性心理因果

**Formative Link（形成性连接）**：
Persona Interpretation 中由原文依据支持的类型化连接，用于表达经历对人格的支撑、解释、展现、依恋塑造或关系限定；它标明是原文明说、强推导还是解释性理解，而不宣称唯一确定因果。
_Avoid_: 心理学定律、无来源因果、模型置信度授权

**Persona Tension（人格张力）**：
两个都有原文依据、会共同影响角色选择却不能被简单合并的愿望、价值、自我认知或行为倾向。
_Avoid_: 编译错误、必须消除的矛盾、单一性格标签

**Persona Activation Tier（人格激活层级）**：
获批 Persona Interpretation 对内容运行时可用性的声明：Foundation 每轮携带并包含必要形成依据，Situational 随当前情境展开，Reference 仅在明确相关时进入召回。
_Avoid_: 每轮全文常驻、纯相关性决定核心人格、预算静默删除 Foundation

**Meaning Capsule（意义胶囊）**：
经批准、可追溯且使用中性语言表达的最小形成性解释，连接人格主张与其 Formative Experience；它保留“为什么如此”，但不冒充原文、Persona Reflection 或完整剧情。
_Avoid_: 性格标签、第一人称伪内心、无来源故事摘要

**Episode（情节）**：
由一段关系中的一个或多个 Relationship Event 派生、围绕同一具体经历或未完成过程组织的可重建叙事单元；它必须保留来源事件引用，不能成为新的权威历史。
_Avoid_: Relationship Event、聊天摘要、无来源剧情

**Relationship Chapter（关系篇章）**：
由多个 Episode 与 Relationship Event 派生、描述一段关系中较长且有证据支持的叙事时期；它不是硬编码关系等级，也不能自行推动关系状态变化。
_Avoid_: 关系阶段、陌生人/朋友/恋人等级、数值阈值升级

**Experiential Timeline（体验时间线）**：
Agent 以第一人称对交互经历所作的叙事性总结。
_Avoid_: 原始聊天记录

**Timeline Entry（体验时间线条目）**：
Experiential Timeline 中具有稳定 `timeline_entry_id`、关系范围、正文、可知记录时间和 Archival Provenance 的结构化长期产物；旧记录缺失的来源或时区以显式未知状态保留，不能由迁移过程补造。
_Avoid_: 带时间前缀的字符串、MemoryNode、伪造的 UTC 时间或归档来源

**Impression Node（印象节点）**：
从交互中提取并分类的事实、偏好、事件、情绪或关系印象，用于相关性召回。
_Avoid_: 关系事件、权威事实

**Inner Monologue（心理独白）**：
Agent 未言说的第一人称心理活动、内省或情感余温。

**Recall Salience（召回显著性）**：
一段记忆在当前情境中被想起的倾向；显著性降低不表示历史被删除或关系影响被撤销。
_Avoid_: 遗忘、事实失效

**Recall Reinforcement（召回强化）**：
宿主明确把一次结构化召回视为真实“想起”时，对被选记忆显著性的有限增强；查询、预览或渲染本身不构成强化。
_Avoid_: 检索命中、Prompt 渲染、关系成长

**Narrative Tension（叙事悬念）**：
未完成承诺、未解决冲突或待续事件对当前叙事保持的持续影响。
_Avoid_: 永不衰减的所有记忆

**Promise（承诺）**：
一方或多方基于直接证据对明确未来行动承担的责任；只有具备同一时钟内可比较的期限或已确认触发条件时，才能派生到期或逾期信号。
_Avoid_: 愿望、邀请、未确认提议、无法验证的情感表达

**Promise Resolution（承诺解决事件）**：
引用既有 Promise、以追加方式记录其已履行、取消、被新约定替代或明确不会履行的关系事件；逾期信号本身不是解决事件，也不等同于违约。
_Avoid_: 修改承诺状态、覆盖原期限、自动信任惩罚

**Open Loop（开放事项）**：
一段关系中仍期待继续或解决、但不表示任何一方承担承诺责任的事项；它来自正式历史，并通过后续追加的解决事件关闭。
_Avoid_: Promise、所有未完成句子、永久置顶记忆

**Open Loop Resolution（开放事项解决事件）**：
引用既有 Open Loop、以追加方式说明事项已完成、放弃或被后续事项替代的关系事件。
_Avoid_: 原地修改未决标记、删除来源事件

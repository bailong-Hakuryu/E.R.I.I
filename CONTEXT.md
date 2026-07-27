# E.R.I.I. Domain Glossary

E.R.I.I. 描述情感型 Agent 与用户如何分别形成共同历史、当前认知与关系人格。本文档规定项目统一使用的领域语言。

## 身份与人格

**Character Blueprint（人设底色）**：
用户导入的角色身份、价值观、表达风格与边界的权威原文快照；结构化结果只能解释它，不能反向改写它。
_Avoid_: 核心人格记忆、Core Memory、系统提示词

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

**Source Retry（来源重试）**：
宿主对同一来源身份和版本的重复提交；它必须返回既有裁决结果，不产生新的事件、反思或关系状态变化。
_Avoid_: 新互动、历史重述、重新裁决

**Source Adjudication Run（来源裁决运行）**：
对一个来源身份、来源版本和处理身份执行的一次候选裁决；首次提交会固定完整候选批次指纹，普通重试不得新增、删除或改写候选，显式 Historical Reprocessing 使用独立处理身份。
_Avoid_: 每次重试重新采样候选、候选键局部幂等、静默扩张历史

**Historical Reprocessing（历史重处理）**：
宿主显式提供既有来源并指定新处理版本的一次追加式复核；它可以产生佐证、更正、重新理解或新提案，但不得覆盖原裁决、重写当时的理解或重复结算既有关系影响。
_Avoid_: 模型升级自动重跑、普通来源重试、静默迁移

**Event Corroboration（事件佐证）**：
后来出现、用于支持、补充或反驳既有 Relationship Event 的新证据关联；它可以改变当前认知的证据基础，但不重新结算原事件的关系影响。
_Avoid_: 新事件、重复状态变化、静默覆盖

**Event Reference（事件引用）**：
当前互动对既有 Relationship Event 的显式关联；被引用的旧经历仍只记录和结算一次，而共同回忆、重新理解或纠正等当前行为可作为独立新事件裁决。
_Avoid_: 复制旧事件、模糊语义强制合并、重复结算

**Evidence（记忆证据）**：
支持一项候选记忆或关系变化的最小原文依据，同时标明来源与完整性；它足以解释“为什么这样记”，但不是完整聊天归档。
_Avoid_: 全量聊天记录、模型自述、无来源摘要

**Relationship Signal（关系信号）**：
对互动所表达关系意义的定性候选，例如感谢、袒露、守信、越界、冲突或修复；它描述发生了什么，但不直接决定关系状态数值。
_Avoid_: 最终状态变化、LLM 生成的好感数值

**Persona Reflection（人格化反思）**：
Agent 基于可核验证据、以符合 Character Blueprint 的第一人称方式记录“当时如何理解”一项关系事件；它不是用户原话，也不直接决定关系状态数值。
_Avoid_: 事实摘要、证据引文、统一文案模板

**Recall Rendering（当前叙述）**：
Agent 现在讲述历史反思时采用的临时表达；措辞可以随当前风格变化，但不得改变原反思的事实、情绪方向、强度或核心含义，也不因被渲染而写入历史。
_Avoid_: 新关系事件、历史反思重写、Renderer 生成的新内心

**Reflection Correction（反思更正）**：
新证据证明旧 Persona Reflection 存在误解时追加的更正；旧反思仍作为当时真实发生过的理解保留。
_Avoid_: 覆盖、删除、静默重写

**Reinterpretation（重新理解）**：
Agent 后来获得的新视角；它扩展当前理解，但不宣称自己当时就已经如此理解。
_Avoid_: 反思更正、追溯性人格改写

**Decision Receipt（裁决回执）**：
一次候选裁决留下的最小、持久结果，用于说明候选被接受、转为提案或拒绝，并防止相同候选被重复处理。
_Avoid_: 被拒绝候选全文、调试日志、正式关系事件

**Candidate Confidence（候选置信度）**：
模型对候选提取准确性与关系解释稳定性的自我评估；分别记录提取置信度与解释置信度，只用于裁决路由、变化限幅和审计，不能单独证明事实或授权记忆、关系状态及人格变化生效。
_Avoid_: 真实概率、证据强度、单一自动接受阈值、人格变化许可

**Candidate Dependency（候选依赖）**：
一项候选生效前必须已经满足的因果前提，例如 Persona Reflection 依赖已接受的 Relationship Event；同一来源允许部分成功，但每个候选的证据、事件、状态变化与回执必须原子提交，依赖失败的候选不能悬空生效。
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

**Experiential Timeline（体验时间线）**：
Agent 以第一人称对交互经历所作的叙事性总结。
_Avoid_: 原始聊天记录

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

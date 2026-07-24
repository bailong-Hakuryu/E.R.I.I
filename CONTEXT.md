# E.R.I.I. Domain Glossary (上下文与领域术语表)

本文档定义 E.R.I.I. 引擎中的核心领域术语（Ubiquitous Language）。本表严禁包含任何代码实现细节或技术栈框架词汇。

---

## 1. 记忆轨 (Memory Tracks)

- **第一人称体验时间线 (Experiential Timeline)**
   Agent 站在第一人称“我”的视角，对发生的交互事件所做的叙述性总结与事实重现记录。

- **多维印象节点 (Impression Node)**
   从对话交互中提取并分类归档的结构化知识与态度节点（涵盖客观事实 `FACT`、偏好 `PREFERENCE`、事件 `EVENT`、情感 `EMOTION`、关系 `RELATIONSHIP` 等）。

- **核心人格记忆 (Core Persona Memory)**
   赋予 Agent 的基础角色设定、不可置疑的元规则或长效自我身份认知。

---

## 2. 动态记忆演进 (Memory Dynamics)

- **时间指数衰减 (Time Exponential Decay)**
   记忆节点的有效权重随时间流逝呈指数级下降（`e^(-λ·Δt)`），非重要记忆随时间推移逐渐淡出召回视野。

- **召回强化 (Recall Reinforcement)**
   当某条记忆节点在对话检索中被精准引用时，引擎自动刷新其活跃时间戳并提升其基础重要度，实现“越常想起来，记忆越深刻”。

- **分类熔断 (Diversity Cap)**
   检索召回时对单一分类（如 `PREFERENCE`）设置的配额上限，防止单一话题或重复向量垄断 Context 预算。

---

## 3. 叙事与心理独白 (Narrative & Inner Monologue)

- **心理独白 (Inner Monologue)**
   Agent 内部未言说的第一人称心理活动、内省思考或情感余温。

- **蔡格尼克效应 / 叙事悬念 (Zeigarnik Effect / Narrative Tension)**
   带有“未完待续”标记（`is_unresolved`）的心理状态或承诺，其时间衰减自动挂起并在时间轴中顶置呈现，维持剧情张力直至被显式闭环。

- **情感余温衰减 (Emotional Resonance Decay)**
   具有强烈情绪共鸣（高情绪绝对值）的独白采用减缓的衰减速率，使其在 Agent 心理层面停留更长时间。

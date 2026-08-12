# E.R.I.I. 项目发展战略

**简体中文** · [English](development-strategy.en.md)

本文记录项目自已接受的 `0.4.0b1` 源码基线之后的发展原则。当前活跃源码里程碑是
`0.5.0a3`；它不是固定日期、融资计划或 SLA，
也不把尚未实现的能力描述成当前功能。具体版本范围仍以 [Roadmap](../ROADMAP.md)、
[Changelog](../CHANGELOG.md) 和经过验证的 commit 为准。`0.x` 版本号表示源码演进
里程碑；项目计划到 `1.0` 再建立正式包发布链路。

## 北极星

E.R.I.I. 的目标不是保存最多文本，也不是成为通用 RAG 或万能 Agent 框架。项目的
北极星是：

> 在长期、独立的 `Agent × User` 关系中，角色能够记得正确的经历、保持自己的底色，
> 并且只因可追溯的真实经历而成长；迁移、删除和模型更换不能伪造或切断这条因果链。

更直接地说：

> RAG 让角色找回相似文本；E.R.I.I. 决定这个角色在这段关系里可以记得什么、相信
> 什么，以及因为什么而改变。

项目不承诺“角色永不 OOC”。更诚实的承诺是：无来源漂移可以被发现，真实历史不会
为了修饰结果而被改写，重要选择会留下后果，错误数据可以被检查、纠正、携带和删除。

## 首要用户与暂不服务的场景

当前首要用户是已经拥有聊天宿主的开发者：

- AI 伴侣、角色扮演和长期角色 Agent 的独立开发者；
- 互动叙事、虚拟角色、视觉小说和角色游戏团队；
- 重视本地数据、模型自由选择和数据携带的自托管用户；
- 研究角色连续性、长期记忆与关系隔离的开发者。

当前不是主要目标：

- 通用知识库、企业搜索和普通 RAG；
- 需要开箱即用聊天产品的非技术终端用户；
- 需要商业 SLA、完整合规或公开多租户 SaaS 的团队；
- 希望 E.R.I.I. 代替聊天模型、工具执行器或完整 Agent 框架的项目。

## 三层长期结构

```text
开放 Core
├─ Persona / Relationship / Turn / Recall
├─ Continuity / Relationship Consequence
├─ Storage / MemoryPack / Data Lifecycle
└─ Provider-neutral capability Interface

可选 Adapter 与实验
├─ Claude / DeepSeek / 其他 Provider Adapter
├─ 其他云端或本地模型 Adapter
├─ KouriChat 等宿主集成
└─ Deliberation Ensemble / 多模型协同

产品与服务
├─ 身份、授权、加密、多租户与密钥管理
├─ 托管、同步、备份、监控与恢复演练
├─ 用户数据查看、纠正、迁移与删除界面
└─ 商业支持、部署服务与 SLA
```

核心记忆、连续性语义、MemoryPack Reader、导出、纠正和删除能力长期开放。未来商业
价值主要来自安全运营、托管服务、同步、可观察性、界面和支持，而不是把用户关系数据
锁进某个 Provider 或私有格式。

## 两条开发轨

### Kernel Evolution Track

内核演进轨承载会形成长期承诺的内容，但不要求每个 `0.x` 里程碑都产生发行包：

- 角色、关系、Turn、来源权威和连续性语义；
- FileStorage、SQLite、MemoryPack、迁移、恢复和擦除；
- Provider-neutral Interface；
- 需要跨版本读取、导出、删除和重建的持久记录。

进入这条轨道的每个新记录都必须同时拥有 FileStorage、SQLite、MemoryPack、导入、
擦除、重建、关系隔离和失败恢复语义。一个次版本原则上只推进一项主要持久能力。

### Labs & Integrations Track

实验与集成轨承载可以快速替换或整体删除的内容：

- Claude、DeepSeek 与其他 Provider 的可拆卸 Adapter 和对照实验；
- Provider Adapter、KouriChat Bridge 与参考宿主；
- 多模型 Actor / Reviewer 编排；
- Prompt、模型路由、行为评测和原型 UI。

实验默认不改变内核数据格式，不保存 raw thinking，不获得人格或关系写权限，也不要求
内核自动安装、发现或热卸载第三方代码。实验失败时应能整体删除，不产生迁移债务。

实验只有在行为评测证明稳定净收益，并且其持久语义无法继续留在宿主外时，才可以提出
进入 Kernel Evolution Track。DeepSeek 或任何其他 Provider 的品牌、模型名、价格和响应
字段都不能进入角色身份、关系权威或持久格式。

## 当前阶段：稳定 `0.5.0a3`，准备角色审思 Labs

### 已接受源码检查点：`0.4.0b1`

`0.4.0b1` 已固定于 commit
`f6dca322379c4ea88320c69d752cab471d035e95`，并在不创建 GitHub Release 或分发包的
情况下进入 rc1。其验收证据只对应当时工作流实际运行的 Linux 完整门禁和 Windows
针对性路径，不扩张为未验证平台承诺。

该基线保留已经复核的文档、契约、版本身份、源码安装、构建 smoke 与纵向回放证据；
rc1 在它之上继续缩短首次采用路径。

### 已完成源码检查点：`0.4.0rc1`

`rc1` 保留现有版本命名，但不表示即将上传 Release Candidate 包。该检查点已固定于
commit `58ea8e69df28bec8e755e0a0d2a175679c18a694`。

RC 没有增加新关系维度、记忆类型、人格变化渠道或 v0.5 后果模型。它集中完成了：

- 修复正确性、恢复性、兼容性、性能与构建缺陷；
- 明确 Python Interface 的 Golden Path、Advanced、Experimental 与 Internal 等级；
- 提供一命令 Golden Continuity Demo，并由 CI 真正运行；
- 把 README 调整为“价值 → 安装 → 演示 → 宿主接入 → 深入文档”；
- 将超长手册逐步拆分为 Getting Started、Host Integration、Concepts、Operations、
  Migration 与 Reference，同时保持旧链接可追踪；
- 增加文档链接检查、示例执行、Support 边界和最小 Issue/PR 模板；
- 整理项目元数据和正式包名候选，但把 PyPI/GitHub Release 流程留给 `1.0`；
- 修正 GitHub About、topics 等仓库外元数据，不再把“零依赖”当作卖点。

Golden Continuity Demo 在该 v0.4 检查点只展示当时已经实现的能力：

1. 同一个角色分别与 User A、User B 建立关系；
2. 只有 User A 与角色“一起看雪”；
3. 进程重启后 A 能正确召回，B 不能召回也不继承亲密度；
4. 用户可以检查来源并导出 MemoryPack。

它不能被回写成已经演示后来才在 v0.5 实现的 Relationship Consequence。

### 稳定维护线：`0.4.0` / `0.4.x`

`0.4.0` 已完成最终源码身份、契约快照和 Golden Demo 的导出/全新 SQLite 导入往返。
稳定源码线维护数据可读性、迁移、导出和回归，不代表已经分发正式包，也不承诺商业
SLA。`0.4.x` 源码标识只用于缺陷、安全和兼容改进，不静默改变人格、关系或来源权威
语义。

### 当前活跃源码里程碑：`0.5.0a3`

`0.5.0a1` 已实现从最终交付回复到 Relationship Consequence、Narrative Tension、后续
关系内召回和结果投影的最小纵切；`0.5.0a2` 增加了凭据、日志、错误与生命周期兼容性
工作，当前 `0.5.0a3` 优先收口版本身份、SDK、Turn 文档、性能与隔离边界。它仍是 Alpha
源码里程碑，不是生产 SLA。

> **Character Deliberation 状态：Experimental；C0 离线合同纵切已实现，产品集成尚未开始。**
> 第一实现阶段只进入可整体删除的 Python Labs：Compact 是主路径，Staged 只处理结构性
> 复杂回合；默认私有、暂态并继续经过现有 Continuity Review。Session Residue、独立
> Private Reflection、Durable state、Visibility/Exposure、REST/TypeScript 和
> Deliberation Ensemble 分别通过自己的行为、安全、数据生命周期与可拆卸性晋级门，
> 不能因为设计已接受就写成当前源码能力。

详细设计见 [完整开发计划](architecture/character-deliberation-development-plan.md)、
[Claude 可拆卸适配指南](integrations/character-deliberation-claude.md)和
[ADR-0120](adr/0120-keep-character-deliberation-transient-layered-and-host-owned.md)。

采用目标不是硬性的源码阶段门禁，但在冻结更多持久格式前应努力获得：

- 5 名非维护者独立完成安装；
- 第一次看到关系隔离与召回结果不超过 10 分钟；
- 至少 3 个真实聊天宿主完成接入；
- 已有宿主的基础接入不超过约 2 小时；
- 至少 5 个使用合成数据可复现的真实问题；
- 跨关系记忆与亲密度泄漏保持为 0。

## v0.5：先证明角色的选择会留下后果

`v0.5.0a1` 已经实现以下首要纵切，当前 `0.5.0a3` 继续稳定其契约与集成：

```text
最终实际交付的 Agent 回复
→ Continuity Review 证明它是角色的合法选择
→ 追加 Relationship Consequence / Narrative Tension
→ 后续 Agent-private Recall 继续保留未解决后果
→ 新的有来源事件将当前结果投影为：
  unaddressed / addressed_unresolved /
  mutually_reconciled / boundary_stabilized /
  relationship_ended / superseded
```

这条纵切必须证明：

- 拒绝、生气、疏远、边界和伤害都可能是符合角色的合法选择；
- 连续性成立不免除关系后果；
- User 受伤不自动证明角色 OOC；
- 系统不会强迫道歉、原谅、和解或恢复亲密；
- 原 Turn、原审查和原后果都保持追加式，不被后来结局覆盖；
- 新记录具备完整携带、擦除、重建和关系隔离语义。

Character Review、双方 Stance、历史例外解除和更复杂的认知修订在这条纵切稳定后分阶段
进入后续 v0.5 Alpha。Character Deliberation 的 Provider-neutral 领域设计已经接受，
运行代码仍从 Python Labs 起步；只有 Shadow 对照证明它改善角色连续性而不破坏自然度，
并完成相应生命周期验证后，某项持久语义才有资格提出进入内核。

## 模型与多模型协同

Claude、DeepSeek、其他远程模型和本地模型都是可选、可拆卸的 Provider。DeepSeek 是
维护者用于实验、且对预算敏感场景较友好的一个选择；Claude 可以通过独立 Adapter
承担 Character Actor，并在后续阶段承担 Reviewer。任何推荐都受模型行为、价格和隐私
条款变化影响；E.R.I.I. 不强制使用某一 Provider，也不建议仅为了接入它而改造一套正常
工作的宿主、存储或部署。

未来常称的“多 Agent 协同”在领域语言中是 Deliberation Ensemble：一名 Character
Actor 提出角色回复，若干 Reviewer 可以混用 Claude、DeepSeek、其他远程 Provider 与
本地模型。它与任何单一 Provider 都没有设计绑定，也不以多数票决定角色是谁。

晋级条件：

- Deliberation 相比直接生成和等计算量非审思对照有稳定行为收益；
- raw thinking、Prompt、凭据和跨关系数据泄漏为 0；
- 单 Actor / 单 Reviewer 已暴露可重复、可归类的失败；
- Ensemble 能显著修复该失败，收益足以覆盖延迟、成本与维护负担。

未达到条件时，多模型协同保持研究能力，不进入内核长期兼容承诺。

## v0.6：安全内核 Hook 与产品宿主分工

v0.6 不把 Core 伪装成完整 SaaS 平台：

- Core 负责 Principal/Capability 语义、对象范围、授权 Hook、关系隔离、可验证数据
  契约和负向测试；
- 产品宿主负责身份登录、TLS、KMS、速率限制、配额、计费、密钥轮换、审计与运营；
- 加密 MemoryPack/Backup、模型出站许可和多租户 Storage/Cache 隔离必须有明确所有者；
- 对外声称产品安全前需要独立安全审计。

FileStorage 不承担完整多租户平台职责。参考 REST 服务继续被描述为协议示例，直到正式
宿主满足对象授权、传输安全和运维边界。

## v0.7：用户拥有并理解自己的关系数据

产品体验层应让用户能够：

- 查看关系时间线、记忆、标签、来源和权威等级；
- 区分原文、摘要、推断、反思和已批准人格变化；
- 看见 Legacy / Quarantined 状态，而不是隐藏不确定历史；
- 纠正、隔离、撤销、导出、迁移和请求删除；
- 完成设备迁移与恢复演练；
- 在不暴露 raw thinking 的前提下理解处置原因。

这些能力可以由独立产品或管理工具提供，不要求把前端框架写进 Core。

## Feature Admission Gate

任何拟进入内核的新能力都必须回答：

1. 它是否直接改善角色连续性、关系隔离或用户数据携带？
2. 是否存在一个用户可观察、可复现的失败场景？
3. 为什么不能留在宿主、Adapter 或 Labs Track？
4. 它的 Interface 能否比调用方自己拼装获得更高 Depth 与 Leverage？
5. FileStorage、SQLite、MemoryPack、擦除、重建和旧数据读取如何处理？
6. 能否在无网络、无真实用户数据的 CI 中完整验证？
7. 单人维护者能否承担后续迁移、文档、安全和回归成本？

任一情况成立时停止晋级：

- 记录无法绑定精确 `Agent × User × Relationship × final delivered Turn`；
- 模型可以直接修改人格、关系或长期记忆；
- 规则把“伤害 = OOC”“温柔 = 正确”或“道歉 = 修复”写死；
- Adapter 卸载后历史不可读、不可导出或不可删除；
- 新 Module 只是一次远程调用转发，没有形成有 Depth 的 Interface；
- 只有一个真实 Adapter，却冻结了巨大的供应商形状公开 Interface；
- 行为收益不足以覆盖自然度、延迟、成本和维护退化；
- 源码阶段仍存在 flaky CI、干净安装失败、契约漂移或夸大兼容范围。

## 单人维护与支持边界

- Core、FileStorage、SQLite、MemoryPack 和已经对外声明的数据格式属于正式维护范围；
- Provider 质量、价格、账户、网络和第三方宿主默认属于 Adapter/社区范围；
- `main` 是开发快照；复现 `0.x` 状态时必须固定 commit SHA；
- `0.x` 源码里程碑提供 best-effort 支持，不承诺回复时间或 SLA；
- Issue 必须使用合成复现，不接收真实聊天、私人人设、数据库、凭据或密钥；
- 官方长期维护一个参考 Host 和少量示例，不承诺维护所有 Agent 框架 Adapter；
- 新持久能力每次只推进一条主要纵切，避免单人维护矩阵失控。

## 近期执行状态与顺序

截至当前 `0.5.0a3` Alpha 源码里程碑，已完成与待完成工作按以下顺序推进；除非新证据
改变优先级：

1. **已接受：**以不可移动的完整 commit SHA 保留经验证的 `0.4.0b1` 源码检查点；
2. **已完成：**接受 `0.4.0rc1` 的公共 Interface、采用路径、契约、构建和文档收口；
3. **已完成：**交付 v0.5 Relationship Consequence 最小纵切，并推进到 `0.5.0a3`
   稳定化里程碑；
4. **当前：**Character Deliberation CD-0 的术语、ADR、严格离线合同、可信 Host Bridge、
   Fake Claude SSE、威胁边界和回归基线；
5. **下一步：**CD-1 Shadow/Pilot：在宿主显式编排下比较 Direct、Compact 与 Staged，
   但仍不进入持久格式或公开 API；
6. **后续按证据晋级：**CD-2 真实 Adapter/Shadow，再依次评估心理延续、Durable、
   Visibility/API 和 Ensemble；
7. 持续邀请外部宿主、维护 `0.4.x` 缺陷/安全/兼容线，并到 `1.0` 再建立正式包发布承诺。

CD-0—CD-6 的完整依赖、验收和停止条件以
[Roadmap](../ROADMAP.md#character-deliberationc0-离线合同已实现产品集成待开发) 为准。Claude、DeepSeek、
其他模型与宿主集成实验可以在 Labs 轨并行进行；它们不会因为一次结果不错就自动获得
持久兼容承诺。

## 相关决策

- [ADR-0106](adr/0106-freeze-a8-at-continuity-audit-and-start-character-consequence-work-in-v05.md)
  冻结 a8 并把角色后果留给 v0.5；
- [ADR-0117](adr/0117-keep-character-deliberation-provider-neutral.md)
  保持角色审思与模型协同 Provider-neutral；
- [ADR-0118](adr/0118-prioritize-consequences-and-separate-experiments.md)
  收窄 v0.5 第一条纵切，并拆分内核演进轨与实验集成轨。
- [ADR-0119](adr/0119-defer-formal-package-distribution-until-v1.md)
  把 `0.x` 定义为源码里程碑，并把正式包发布链路留到 `1.0`。
- [ADR-0120](adr/0120-keep-character-deliberation-transient-layered-and-host-owned.md)
  接受角色审思的分层、暂态与宿主编排边界，同时把持久化留给独立准入。

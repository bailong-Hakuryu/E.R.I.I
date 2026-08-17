# E.R.I.I.

> Experiential Recall & Impression Integration — 让角色记得共同经历，并在每段独立关系中保持连续的人格。

[![License](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](LICENSE)
[![Historical release](https://img.shields.io/badge/historical-v0.4.0a8-orange.svg)](https://github.com/bailong-Hakuryu/E.R.I.I/releases/tag/v0.4.0a8)
[![Source](https://img.shields.io/badge/source-v0.5.0a3-blue.svg)](CHANGELOG.md)
[![Python](https://img.shields.io/badge/Python-3.11--3.14-green.svg)](pyproject.toml)

[English README](README_EN.md)

## 为什么是 E.R.I.I.

普通 RAG 解决“哪些文本和现在相似”；E.R.I.I. 还要回答：

- 这段经历属于哪一个 `Agent × User`；
- 角色为什么可以记得、相信或重新理解它；
- 当前关系状态由哪些不可变事件推导而来；
- 一次说过的话能否成为记忆、关系或人格变化的权威依据；
- 重启、迁移、导出、删除和重建之后，这条因果链是否仍然成立。

项目的核心定义是：

> E.R.I.I. 是一个角色连续性与长期记忆内核。它让角色从既定人设与形成性经历出发，
> 在每段独立关系中继续生活；角色可以因真实经历而成长，但任何重要变化都必须保持
> 心理与经历上的因果连续性。

因此：

- 原始 Character Blueprint 是人设底色，结构化解释不能静默覆盖它；
- “我们第一次一起看雪”只属于实际经历它的那段关系；
- 普通关系状态可以渐进更新，核心人格变化和巨大跃迁只能先形成可审查提案；
- 温柔不天然正确，拒绝、生气或造成伤害也不天然 OOC；
- 模型提出候选，内核核验身份、范围、来源和状态变化。

E.R.I.I. 是可嵌入 Python 宿主的内核，不是聊天模型、万能 Agent 框架或开箱即用的
多租户聊天产品。

## 从源码安装

当前检出的活跃开发源码身份是 `0.5.0a3`（alpha），要求 Python 3.11–3.14。
`0.4.x` 是稳定维护线；需要较低变更风险的集成应固定经过审查的 `0.4.x` full commit SHA：

```bash
git clone https://github.com/bailong-Hakuryu/E.R.I.I.git
cd E.R.I.I
python -m venv .venv
```

Linux 或 macOS：

```bash
source .venv/bin/activate
python -m pip install .
```

Windows PowerShell：

```powershell
.\.venv\Scripts\Activate.ps1
python -m pip install .
```

确认源码身份：

```bash
python -c "import erii; print(erii.__version__)"
```

`0.x` 是源码演进里程碑，不要求每个阶段都有 Git tag、GitHub Release、wheel/sdist
上传或 PyPI 包。需要复现时请固定经过审查的 **full commit SHA**。正式包发布流程计划
在 `1.0` 建立；`v0.4.0a8` 只是已有的历史发布，不会被回写。

按需安装扩展：

```bash
python -m pip install ".[server]"  # FastAPI / Uvicorn 参考服务
python -m pip install ".[openai]"  # 宿主自定义集成使用的可选 SDK
python -m pip install ".[vector]"  # 可选向量检索
python -m pip install ".[dev]"     # 测试、构建和静态检查
```

## 一键运行 Golden Continuity Demo

基础安装即可运行，不需要模型 API Key 或网络：

```bash
erii demo --output-dir ./erii-demo
```

这个自校验 Demo 使用原创合成角色与真实 SQLite，证明：

1. User A 与角色共同经历第一次看雪；
2. 关闭并重新打开 Engine 后，User A 仍能带来源召回；
3. User B 不知道这件事、不继承 User A 的亲密度，也不会获得只绑定于
   User A 的已批准 Persona 投影；
4. User A 的关系可以导出为 `user-a.erii`，再原子导入全新 SQLite；导入后重启仍能
   恢复同一关系、Persona、记忆及其 Source Turn 与内容指纹承诺，且不夹带 User B
   数据。MemoryPack 的 `archival_ledger` 只携带内容无关的归档 Tombstone，不携带完整
   运行时回执，因此导入后的 Recall 会如实把这部分来源标为 `partial_source`，但仍
   保持普通生成权限。

预期输出包含四项 `[PASS]`。目录还会保存原数据库、导入后的独立数据库、渲染召回和
`demo-report.json`。输出目录必须不存在；命令不会覆盖旧结果。

完整解释见 [Getting Started](docs/getting-started.md)。

## 接入真实聊天宿主

新宿主只有一条推荐主路径：

```text
Turn Recording
  → archive_turn() / process_relationship_turn()
  → recall_structured()
  → export_memory()
```

双方可见消息已经存在时使用 `record_turn()`；仍在生成与交付回复时，使用更严格的
`begin_turn() → complete_turn()`。随后由宿主显式启动记忆归档和/或关系处理；下一轮
通过结构化召回取得同一关系的长期上下文，最后始终保留 MemoryPack 导出能力。

E.R.I.I. 不生成最终聊天回复，不会自动启动隐藏处理线程，也不会替宿主选择模型。
`remember()` 和 transient `adjudicate_relationship_candidates()` 是弃用兼容路径，
不要用于新集成。

详见 [Host Integration](docs/host-integration.md) 和
[API Stability](docs/api-stability.md)。

## 0.5 系列：Relationship Consequence

`0.5.0a1` 首次引入 **Relationship Consequence** 和
**Narrative Tension** 最小纵切，用于追踪关系决策的长期影响和叙事张力状态。
这是 alpha 源码能力，不等于稳定发布或生产就绪声明：

- **后果记录**：从已完成且连续性受支持的关系事件记录持久后果（伤害、信任变化、边界违反等）
- **叙事张力追踪**：投影后果的当前状态（未处理、已处理未解决、共同和解、边界稳定、
  关系终止或被取代）
- **来源权威**：统一的来源校验确保只有可证明的最终回复才能产生后果
- **私有边界**：后果投影仅对 `RecallAudience.AGENT_PRIVATE` 可见
- **生命周期集成**：删除关系事件时自动级联删除相关后果

详见 [Migration Guide](docs/migration-0.5.0.md) 和 [CHANGELOG](CHANGELOG.md)。

## Reference

- [项目状态看板](docs/PROJECT_STATUS.md)
- [Getting Started：一键关系隔离与重启证明](docs/getting-started.md)
- [0.4.0 稳定源码里程碑说明](docs/release-notes-0.4.0.md)
- [Host Integration：真实聊天唯一推荐路径](docs/host-integration.md)
- [API Stability：Golden / Advanced / Experimental / Internal](docs/api-stability.md)
- [中文完整使用手册](docs/USAGE_zh-CN.md)
- [English User Guide](docs/USAGE.md)
- [数据生命周期](docs/data-lifecycle.md)
- [兼容性策略](docs/compatibility.md)
- [领域模型](docs/domain-model.md)
- [发展战略（中文）](docs/development-strategy.md)
- [Development Strategy (English)](docs/development-strategy.en.md)
- [安全策略](SECURITY.md)
- [支持政策](SUPPORT.md)
- [路线图](ROADMAP.md)
- [变更记录](CHANGELOG.md)
- [贡献指南](CONTRIBUTING.md)

## 当前源码边界

`0.4.0b1` 已在 commit
`f6dca322379c4ea88320c69d752cab471d035e95` 接受为不可移动的源码基线。
`0.4.0rc1` 的源码收口证据固定于 commit
`58ea8e69df28bec8e755e0a0d2a175679c18a694`。`0.5.0a2` 已作为 alpha 包上传；当前
`0.5.0a3` 是其后的源码稳定化里程碑，尚未对应新的 tag 或 PyPI 制品。

版本轴彼此独立：

| 轴 | 当前值 |
| --- | --- |
| Python 源码身份 | `0.5.0a3` |
| Python | `3.11`–`3.14` |
| SQLite | schema `10` |
| FileStorage | format `2` |
| MemoryPack | `0.5.0a3`（readers：至 `0.5.0a3`） |
| Lifecycle Backup | `1` |
| Lifecycle Plan | writer `3`，readers `1`–`3` |

`v0.5.0a1` 引入 Relationship Consequence 和 Narrative Tension 持久字段，并将 SQLite
升级至 schema 10、FileStorage 升级至 format 2。当前 SQLite schema 11 另加入整包导入的
版本化主库操作回执。当前 writer 标记 MemoryPack
`0.5.0a3`；reader 仍接受 declared-readable 的旧 Pack。严格的 `0.4.0a8` reader 会拒绝
带 0.5 扩展字段的新 Pack，因此兼容性是新 reader 向旧数据单向可读。Character
Deliberation 和伤害后修复决策仍未实现。

## 已有内核能力

- 同一角色可在多个用户关系中复用同一个 `agent_id`（共享角色身份）；每个
  `Agent × User` 组合仍有独立的 `relationship_id`、`persona_id`，以及只属于该关系的
  记忆、事件、状态和亲密度；
- 原始 Character Blueprint 与经审批的结构化 Persona Manifest；
- 完整可见 Source Transcript、两阶段 Turn 生命周期和追加式来源账本；
- 消息级证据归档，以及 Ordinary / Legacy / Quarantined 召回权威；
- 追加式 Relationship Event、五维状态投影、Promise 与 Open Loop；
- **Relationship Consequence 与 Narrative Tension**：记录关系决策的持久后果，
  追踪叙事张力的当前状态（未处理、已处理未解决、已解决、关系终止）；
- Persona Reflection、需审批的 Persona Growth Proposal、Episode 与 Chapter 投影；
- 五轴 Continuity Review、Delivery Exception、Context Baseline 与 Voice Trace；
- FileStorage、SQLiteStorage、结构化 Recall 和 MemoryPack；
- 备份、恢复、窄范围升级、fresh import、删除、重建与长期合成回归。

详细能力和限制以 [API Stability](docs/api-stability.md)、
[Compatibility Policy](docs/compatibility.md) 与机器可读 contract snapshots 为准。

## 模型与实验

内核 Provider-neutral。DeepSeek、其他远程模型和本地模型都只能作为可拆卸
Adapter/实验；E.R.I.I. 不强制使用某个 Provider，也不建议为了使用它改造原本正常的
宿主。raw thinking、完整 Prompt、凭据和 Provider 错误正文不会被保存成“角色内心”。

未来多模型协同与 DeepSeek 没有设计绑定。即使引入 Deliberation Ensemble，也只能有
一名 Character Actor；Reviewer 不能投票定义角色或直接写入人格、关系和记忆。

### 实验模块

**DeepSeek Continuity Review** ([experiments/deepseek-continuity-review](experiments/deepseek-continuity-review/))

该可拆卸 Labs 模块探索 DeepSeek thinking mode 是否适合实现
`ContinuityEvaluatorV1`。现有真实 API 记录只是一次小样本、手工场景的探索性运行；
它没有建立生产准确率、SLA 或可复现成本/延迟优势；单次场景通过数不能当作准确率或
生产推荐。模块未安装、禁用或整体删除时，普通 Turn、Recall、Continuity、MemoryPack
和生命周期能力必须保持可用。

远程模型调用会把所选 Prompt、证据、对话或记忆发送到对应 Provider。宿主必须在调用前
取得适当授权，最小化出站数据，并核对 Provider 的地区、留存、删除和训练政策。API Key
只能从环境变量或宿主 Secret Manager 注入，不能写入源码、文档、fixture、命令行参数、
日志或持久角色数据。实验状态与原始记录见
[实验目录](experiments/deepseek-continuity-review/)，但其中的历史结果不构成内核质量证明。

## 安全、数据与维护

当前项目由单人长期维护，不提供 SLA。`0.4.x` 是稳定维护线，`0.5.0a3` 是活跃 alpha
源码里程碑。FileStorage、SQLite、MemoryPack 和 Lifecycle
Backup 默认明文；参考 REST 服务只有单一 owner key，不是每用户授权或多租户安全边界。
正式产品仍需在宿主侧补齐身份、对象授权、TLS、加密、密钥管理、限流、租户隔离和
外部副本删除编排。

核心记忆、连续性语义和用户数据携带能力长期开放。项目采用
[Apache License 2.0](LICENSE)；第三方角色、人设与作品内容不属于内核，使用者须自行
承担著作权、商标、隐私和平台合规责任。公开 Issue 与 fixture 只接受原创合成数据，
不要上传真实聊天、私人人设、生产数据库或密钥。

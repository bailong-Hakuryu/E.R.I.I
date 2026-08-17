# E.R.I.I. 文档索引

> 当前源码：`0.5.0a3` Alpha
> Python：3.11–3.14
> SQLite：schema 11
> FileStorage：format 2
> MemoryPack writer：`0.5.0a3`

本索引只列出当前维护的用户、宿主和贡献者入口。历史工作总结不作为 API、发布或
验证事实来源；机器可读格式身份以 `erii.compatibility.COMPATIBILITY_CATALOG` 和
`docs/contracts/` 为准。

## 第一次使用

1. [Getting Started](getting-started.md)：离线 Demo、关系隔离、重启与导入证明。
2. [Host Integration](host-integration.md)：真实聊天宿主的唯一推荐接入路径。
3. [中文完整使用手册](USAGE_zh-CN.md)。
4. [English User Guide](USAGE.md)。

推荐的新宿主主流程：

```text
initialize_relationship
  → begin_turn / complete_turn，或 record_turn
  → archive_turn / process_relationship_turn
  → recall_structured / render_recall
  → export_memory
```

`remember()` 和 transient `adjudicate_relationship_candidates()` 仅用于旧集成迁移。

## 核心语义

- [领域模型](domain-model.md)
- [关系前提 ADR](adr/0038-bind-canonical-roles-through-explicit-relationship-premises.md)
- [Persona Manifest ADR](adr/0040-approve-persona-interpretation-as-a-versioned-manifest.md)
- [Episode 与 Chapter ADR](adr/0047-derive-episodes-and-relationship-chapters-from-history.md)
- [上下文声音模式 ADR](adr/0086-model-voice-as-source-backed-contextual-repertoire.md)
- [0.5 关系后果迁移](migration-0.5.0.md)

## Character Deliberation（C0 与 G2 离线编排已实现）

Character Deliberation 的领域边界和阶段路线已经确认；当前源码已有可拆卸、无网络、
无持久化的 C0 Python Labs 合同纵切、Fake Claude SSE 和 G2 Private Compact 编排。G2
通过独立 Adapter 调用现有 `ERIIEngine`、Continuity Review 和精确 Delivery 接缝，但不是
稳定公共 Host Interface。真实 Claude/其他 Provider、Staged/Adaptive、Session Residue、
REST 和 TypeScript Interface 尚未实现。现有 Inner Monologue 是独立的长期心理叙事对象，
不是回复前审思实现。

- [完整开发计划](architecture/character-deliberation-development-plan.md)：领域对象、
  Compact/Staged 双轨、运行生命周期、心理延续、Visibility、评测、风险和逐阶段准入。
- [Claude 可拆卸适配指南](integrations/character-deliberation-claude.md)：Claude 作为可选
  Character Actor/Reviewer 的 Adapter 边界、凭据、数据最小化、结构化结果、失败降级与测试。
- [Provider-neutral ADR](adr/0117-keep-character-deliberation-provider-neutral.md)：任何单一
  Provider 的 thinking、SDK 或模型名称都不进入 Core 契约。
- [Character Deliberation 架构 ADR](adr/0120-keep-character-deliberation-transient-layered-and-host-owned.md)：
  首版保持暂态、分层、由 Host/Labs 编排；持久化必须另过准入门。
- [项目路线图](../ROADMAP.md#character-deliberationc0-与-g2-离线编排已实现产品晋级待开发)：从 Private
  Transient Python Labs 到 Session Residue、Private Reflection、Durable state、
  Exposure/Visibility、REST/TypeScript 与 Deliberation Ensemble 的依赖顺序和晋级门。

当前实现只进入 Python Labs：Private Compact 是唯一审思路径，默认私有且暂态，复用现有
Continuity Review，并提供 Direct fallback；Staged/Adaptive 仍待开发。Claude、DeepSeek、
其他远程模型与本地模型
都只能通过可安装、可替换、可禁用的 Adapter 接入；raw thinking、Prompt、凭据、草稿和
Provider 错误正文不进入角色历史。持久格式、用户可见心理投影、REST/SDK 和多 Reviewer
协同要分别通过自己的行为、安全、数据生命周期与可拆卸性准入门。

## API 与稳定性

- [API Stability](api-stability.md)：Golden、Advanced、Experimental 与 Internal。
- [Turn Lifecycle](api/turn-lifecycle.md)
- [Turn 错误处理](api/turn-error-handling.md)
- [Turn 高级用法](api/turn-advanced-usage.md)
- [TypeScript 服务端 SDK](../clients/typescript/README.md)：源码分发的 Alpha 客户端，
  通过 live FastAPI contract 检查；不向浏览器暴露 owner key。
- [兼容性策略](compatibility.md)
- [数据生命周期](data-lifecycle.md)

Turn 文档中的示例必须与 `ERIIEngine` 的公开签名一起通过测试；文档链接检查本身不
证明示例可执行。

## 部署与安全

- [安全策略](../SECURITY.md)
- [支持政策](../SUPPORT.md)
- [API Key 管理](guides/api_key_management.md)
- [速率限制参考](deployment/rate-limiting.md)
- [部署加固参考](deployment/production.md)

参考 REST 服务使用单一 owner key，并只接受 `X-API-Key`。它不是每用户身份、对象级
授权或完整多租户安全边界。部署文档是宿主加固参考，仍需根据实际环境完成 TLS、身份、
授权、加密、限流、监控、备份和恢复演练。

## 开发与维护

- [项目状态看板](PROJECT_STATUS.md)：由机器可读状态目录生成，区分维护、活跃 Alpha、
  实验、占位和计划 Module，并记录各自下一道晋级门。
- [路线图](../ROADMAP.md)
- [变更记录](../CHANGELOG.md)
- [贡献指南](../CONTRIBUTING.md)
- [发展战略（中文）](development-strategy.md)
- [Development Strategy (English)](development-strategy.en.md)
- [结构重构总控路线图](architecture/refactoring-program.md)：2026-08-13 至 2026-12-20 的
  批次、并行开发规则、验证门和停止条件。
- [R0 重构清单](architecture/refactoring-r0-inventory.md)：由 Git 已跟踪和未忽略的提交候选
  源码生成的 Engine、Lifecycle、Storage Interface 和 MemoryPack 调用地图。
- [Engine 重构计划](architecture/engine-refactoring-plan.md)
- [Lifecycle 重构计划](architecture/lifecycle-refactoring-plan.md)
- [当前结构重构状态](architecture/refactoring-status.md)：R1B 收口证据、R2 已实施部分和
  尚未完成的退出门。
- [R2 历史实施日志](architecture/r2-implementation-log.md)：早期 Codec/Serializer 提取、
  审计修复及阶段草稿索引；不覆盖权威状态。
- [ADR 索引](adr/README.md)

## 实验模块

DeepSeek Continuity Review 位于
`experiments/deepseek-continuity-review/`，是可整体拆卸的离线/Provider 实验。
实验测试证明解析、证据和错误边界，不证明真实 Provider 的准确率、延迟、价格、SLA
或生产部署质量。Claude 适配同样属于后续 Labs 工作，不是当前已实现 API；多模型协同
与任何单一 Provider 都没有设计绑定。

## 发布与验证事实

- `v0.5.0a2` 是已经存在的历史 alpha tag/PyPI 制品。
- 当前 `0.5.0a3` 是 tag 之后的源码稳定化里程碑；发布前必须重新构建、安装、验证并
  冻结对应 commit。
- GitHub workflow 配置不等于某个 commit 已经执行成功；验证报告必须记录 full SHA、
  环境、精确命令、原始结果和 exit status。
- 项目仍是 Alpha，不提供生产 SLA。

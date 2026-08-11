# E.R.I.I. 文档索引

> 当前源码：`0.5.0a3` Alpha
> Python：3.11–3.14
> SQLite：schema 10
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

- [路线图](../ROADMAP.md)
- [变更记录](../CHANGELOG.md)
- [贡献指南](../CONTRIBUTING.md)
- [发展战略（中文）](development-strategy.md)
- [Development Strategy (English)](development-strategy.en.md)
- [Engine 重构计划](architecture/engine-refactoring-plan.md)
- [Lifecycle 重构计划](architecture/lifecycle-refactoring-plan.md)
- [ADR 索引](adr/README.md)

## 实验模块

DeepSeek Continuity Review 位于
`experiments/deepseek-continuity-review/`，是可整体拆卸的离线/Provider 实验。
实验测试证明解析、证据和错误边界，不证明真实 Provider 的准确率、延迟、价格、SLA
或生产部署质量。多模型协同与任何单一 Provider 都没有设计绑定。

## 发布与验证事实

- `v0.5.0a2` 是已经存在的历史 alpha tag/PyPI 制品。
- 当前 `0.5.0a3` 是 tag 之后的源码稳定化里程碑；发布前必须重新构建、安装、验证并
  冻结对应 commit。
- GitHub workflow 配置不等于某个 commit 已经执行成功；验证报告必须记录 full SHA、
  环境、精确命令、原始结果和 exit status。
- 项目仍是 Alpha，不提供生产 SLA。

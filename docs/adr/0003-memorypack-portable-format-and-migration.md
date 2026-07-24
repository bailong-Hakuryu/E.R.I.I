# 3. MemoryPack 便携数据格式与数据导入导出 API

* **状态**: 提议已通过 (Accepted)
* **日期**: 2026-07-24

## 背景与问题上下文 (Context)

随着 E.R.I.I. 引擎支持多种存储驱动（`FileStorage` JSON 文件与 `SQLiteStorage` 嵌入式数据库），用户面临在不同驱动之间平滑迁移、备份恢复 Agent 记忆、以及跨环境导入测试数据等需求。同时，未来节点 Schema 演进需要标准的数据版本兼容保护。

## 决策 (Decision)

我们决定：
1. **MemoryPack 规范**：定义带 Schema 版本规范的自包含序列化格式 `MemoryPack`（JSON 规范），包含元数据标头 (Version, AgentId, UserId, ExportedAt)、所有的 Impression Nodes 与 Experiential Timeline Entries。
2. **引擎 API**：在 `ERIIEngine` 层面提供 `export_memory()` 和 `import_memory()` 标准方法，支持在任何底层存储驱动之间无缝导出与导入数据。
3. **平滑升级与合并策略**：导入时支持覆盖 (`overwrite=True`) 或增量合并 (`overwrite=False`) 策略，并根据格式版本号自动完成数据结构升级。

## 后续影响与 Trade-offs (Consequences)

### 正向效果 (Pros)
* 实现了存储驱动之间的零痛点无缝迁移（如从开发环境 JSON 迁移到生产环境 SQLite）。
* 提供了标准的记忆冷热备份恢复与调试快照机制。

### 负向开销 (Cons)
* 需要维护跨版本的 Schema Migration 升级映射代码。

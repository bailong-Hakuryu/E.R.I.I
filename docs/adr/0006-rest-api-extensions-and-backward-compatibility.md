# 6. REST API 端点扩展与 API 向下兼容性保证

* **状态**: 提议已通过 (Accepted)
* **日期**: 2026-07-24

## 背景与问题上下文 (Context)

在引入并发锁、持久化任务队列、MemoryPack 导入导出以及混合双路向量召回等新功能后，我们需要确保现有的 Python API 使用者无需修改任何代码即可平滑升级，同时非 Python 语言（Node.js / Go / Rust 等）通过 REST API 也能够完全掌控异步归档状态与记忆数据迁移。

## 决策 (Decision)

我们决定：
1. **v0.2 系列 Python API 尽量保持向下兼容**：
   - 保持 `ERIIEngine` 构造函数默认参数不变。
   - `remember()`, `recall()`, `remember_thought()`, `resolve_thought()` 签名完全保持不变。
   - 新增强化组件（如 `task_queue`, `vector_store`）通过可选形参在构造时注入。
2. **REST API 扩展管理端点**：
   - 数据管理端点：`POST /api/v1/memory/export`, `POST /api/v1/memory/import`。
   - 归档任务监控端点：`GET /api/v1/tasks/status`, `POST /api/v1/tasks/retry-failed`。
   - 服务健康诊断：`GET /api/v1/health`。

## 后续影响与 Trade-offs (Consequences)

### 正向效果 (Pros)
* 旧版本用户代码升级零破坏、零成本。
* 赋予跨语言 REST 客户端完整的任务监控与数据迁移能力。
* 项目处于 `0.x` 阶段，未来破坏性变更必须经过弃用提示，并为记忆数据提供迁移与回滚路径；不承诺实验 API 永久不变。

# 2. 持久化 TaskQueue 接口与指数退避重试机制

* **状态**: 提议已通过 (Accepted)
* **日期**: 2026-07-24

## 背景与问题上下文 (Context)

在原有实现中，`AsyncArchiverWorker` 使用 Python 原生内存队列 `queue.Queue()` 处理后台对话记忆抽取。当底层大模型 (LLM) API 遭遇网络抖动、速率限制 (429 Rate Limit) 或服务崩溃时：
1. 内存中排队的归档任务在进程重启或崩溃后会永久丢失；
2. 单次 LLM 异常会导致该对话 turn 的记忆抽取直接丢弃，造成记忆断层。

## 决策 (Decision)

我们决定：
1. **队列接口抽象**：设计 `BaseTaskQueue` 抽象基类，规范 `enqueue()`, `dequeue()`, `complete()`, `fail()`, `get_status_summary()`, `retry_failed()` 方法。
2. **默认持久化实现**：提供基于 SQLite/File 的内置 `PersistentTaskQueue`，记录任务状态 (`PENDING`, `PROCESSING`, `COMPLETED`, `FAILED`)。
3. **指数退避重试 (Exponential Backoff)**：对于因 LLM 网络超时或临时报错导致的失败，自动按 `BaseDelay * (2 ^ attempt)` 延迟重试（默认最多 3 次）；超时或多次失败的任务进入 `FAILED` 死信状态，支持后续排查与重跑。
4. **宿主控制生命周期**：构造 Engine 只装配 Worker 和队列，不自动启动隐藏线程；宿主通过 `start()` 启动后台消费，或通过 `process_pending()` 同步处理已入队任务。

## 后续影响与 Trade-offs (Consequences)

### 正向效果 (Pros)
* 任务在提交前会持久化；超出租约时间的 `PROCESSING` 任务可在重启后恢复为 `PENDING`。
* 容忍大模型 API 的临时抖动，极大提升系统稳定性。
* 保持零外部强依赖，且接口可扩展示以支持 Redis 等第三方分布式队列。

### 负向开销 (Cons)
* 增加了额外的磁盘 I/O 读写开销（可以通过 WAL 模式与批量 commit 缓解）。
* 该设计提供至少一次处理语义，不承诺严格的 exactly-once；写入端仍需使用幂等策略处理极端崩溃窗口。

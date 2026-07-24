# 1. (agent_id, user_id) 粒度读写隔离锁与 SQLite WAL 模式

* **状态**: 提议已通过 (Accepted)
* **日期**: 2026-07-24

## 背景与问题上下文 (Context)

E.R.I.I. 引擎采用了主线程交互与后台异步归档线程（`AsyncArchiverWorker`）解耦的架构。当后台线程使用大模型从对话中异步抽取印象节点并写回存储层时，主线程（或通过 `erii serve` 多进程启动的 HTTP REST 服务）可能同时执行 `recall()` 召回检索或 `remember()` 写入操作。

在未加加锁隔离的情况下，并发读写将导致：
1. `FileStorage` (JSON) 文件损坏或并发覆盖写入丢失；
2. `SQLiteStorage` 产生 `database is locked` 异常或读取不完整事务数据。

## 决策 (Decision)

我们决定：
1. **隔离锁粒度**：在存储抽象层（`BaseStorage`）实现基于 `(agent_id, user_id)` 二维键的读写隔离锁（RWLock），确保不同用户/Agent 间并发零阻塞，同用户的读写操作互斥安全。
2. **SQLite 模式**：默认在 `SQLiteStorage` 中强制开启 WAL（Write-Ahead Logging）模式，实现读不阻塞写、写不阻塞读。

## 后续影响与 Trade-offs (Consequences)

### 正向效果 (Pros)
* 彻底解决后台异步归档写与前台 Prompt 召回读的数据竞争 (Race Condition)。
* 多用户并发性能高，无全局死锁或过度的线程互斥等待。

### 负向开销 (Cons)
* 内存中需要维护按用户隔离的锁池（Locks Registry），需注意垃圾回收或资源清理。

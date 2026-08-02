# Compatibility Policy

## Python

不可移动的 `v0.4.0a8` 仍要求 `Python 3.9+`，并且是最后一个承诺支持 Python 3.9 的版本。该标签、wheel、sdist 与 MemoryPack 身份不会因后续开发被回写。

当前 `main` 已进入 `0.4.0b1.dev0`：最低版本为 Python 3.11，Linux CI 覆盖 3.11 与当前最新稳定版本 3.14，Windows 对同一最低/最高版本额外验证文件、SQLite 和生命周期路径。Package Version 的变化不会自动改变仍为 `0.4.0a8` 的 MemoryPack Format 或仍为 v9 的 SQLite Schema。

`erii.compatibility.COMPATIBILITY_CATALOG` 是当前开发树的机器可读版本目录。它把 Package `0.4.0b1.dev0`、SQLite v9、FileStorage 目标格式 v1、MemoryPack `0.4.0a8`、Lifecycle Backup v1 与 Python 支持范围分别记录；其中 FileStorage v1 是显式迁移将写入 manifest 后的目标身份，当前没有 manifest 的旧目录（包括现阶段由普通 `FileStorage` 创建的目录）仍被只读检查识别为 `legacy`，不会因为运行检查、备份或按原样恢复而获得新格式身份。

长期政策是支持仍处于 Python 官方安全维护期的版本，不永久维护已结束支持的运行时。

## API

项目处于 `0.x`：

- 补丁版本只包含兼容修复；
- 次版本可以引入受控破坏性变化；
- 废弃 API 原则上至少警告一个次版本；
- 不为了维持错误设计而无限保留兼容分支。

`v0.4.0a1` 有一项有意的生命周期变化：`ERIIEngine()` 不再自动启动后台线程。宿主必须显式调用 `start()`，或调用 `process_pending()` 同步消费已入队任务。

`0.4.0a8` 保留现有 `MemoryExtractorV1` 调用接口，但新的可靠归档提交必须显式声明 `extraction_schema_version="2"`，并为每个 Timeline/Memory 候选提供精确消息范围证据。schema `"1"` 只作为 Legacy 身份读取；它不能让旧产物自动获得现代来源权威。

兼容 `ERIIEngine.recall()` 继续保留旧 `set_core_memory()` 的“始终作为上下文候选”语义：Core Memory 以 `legacy_context` 标签在动态 `top_k` 选择之后加入，因此不占动态槽位，但仍受同一硬成本预算。`recall_structured()` 不提供这个额外兼容槽。除此之外，两条入口共用同一 authority 分类、上游 hybrid/RRF 顺序、authority-first `max_per_type`、预算和 Renderer；Legacy Core 不会因此获得现代 Persona 或来源权威。

`adjudicate_relationship_candidates()` 仍可读取完整 transient Source Turn。若该 `turn_id` 已对应同关系的持久 completed Turn，a8 会要求 revision、消息身份、角色、正文与发生时间精确一致，并把结果标记为 `relationship-turn-adjudication-v1`；不一致失败关闭。确实没有持久 Turn 时继续走旧 transient 契约，但该 Turn ID 之后不能再注册成规范 Turn，因此不能通过“先裁决、后建 Turn”追授现代权威。旧 transient adjudication records 保持可读，不会在迁移时自动补造规范 Turn。

从 `0.4.0b1.dev0` 开始，`remember()` 与 `adjudicate_relationship_candidates()` 会真实发出 `DeprecationWarning`，并计划在 v0.5 删除。前者应替换为规范 Turn Recording + `archive_turn()`；后者应替换为基于持久 Turn 的 `adjudicate_turn_candidates()`，或直接使用 `process_relationship_turn()` 自动处理。历史记录和旧 Pack 的可读性不因 Python 调用入口弃用而取消。

## 数据

记忆数据的兼容承诺高于 Python API：

- 持久格式必须包含 Schema 版本；
- Package Version、SQLite Schema Version、MemoryPack Format Version 与提取器/策略版本分别演进，不能通过一次全局字符串替换混为同一生命周期；
- 破坏性升级必须提供备份、dry-run、验证和回滚路径；
- 至少支持从上一个次版本迁移；
- 迁移测试需要覆盖 Unicode、时间线、节点、人物关系和删除语义；
- 用户应始终能够导出开放、可读取的记忆格式。

MemoryPack `0.4.0a8` 会携带现代归档证据所依赖的精确 Source Turn 闭包，并在导入首次写入前重算消息角色、内容哈希、Unicode code-point 范围与 Evidence ID。现代 tombstone 还携带不含正文的 `artifact_commitments`，以产物类型、稳定 ID 与规范不可变提交载荷 SHA-256 绑定每项已提交产物；MemoryNode 的强化、访问计数、状态、未决/最新标记、取代关系和最后访问时间等可变召回/生命周期字段不在承诺内，导入和召回会重算其余载荷指纹。旧 tombstone 的该字段可以缺失，以继续读取其幂等/审计身份，但它不能认证当前产物或把 schema `"2"` 产物提升为 Ordinary。旧 Pack 继续可读，但缺失的现代证据、审查状态或关系权威不会由当前内容猜测补造。

从 `0.4.0b1.dev0` 起，MemoryPack Reader 只读取版本目录中明确声明的历史格式，并要求 `metadata` 精确包含 `version`、`agent_id`、`user_id` 与 `exported_at`。根级和 metadata 未知字段、重复 JSON 字段、非对象集合成员以及未来/未知版本均在任何嵌套领域模型构造前失败。SQLiteStorage 同样在执行建表或迁移前拒绝高于 v9 的 Schema。`LifecycleInspector` 还能以零写入方式区分 missing、empty、current 与 migration-required；它只返回版本、数量、警告和 SHA-256 指纹，不返回聊天、人设或记忆正文。

Lifecycle Backup v1 是目录型物理备份格式：严格 manifest 绑定不可变 plan digest、原存储种类与版本、完整 payload 文件集合、大小和 SHA-256。FileStorage 只排除根目录 `_turn_context_snapshot.lock`，以及 `_turn_locks/`、`_relationship_history_locks/`、`_relationship_processing_locks/` 下名称为 `<64hex>.lock` 的已知运行锁；其他 `.lock` 文件属于逻辑数据。发现遗留 `.tmp`、符号链接、junction/reparse point、硬链接或其他非普通文件会失败关闭，而不是静默排除。SQLite 只在 WAL/journal 无未合并数据且前后 sidecar 状态稳定时捕获主数据库；MemoryPack 按原字节捕获并再次执行严格 envelope 检查。

备份与恢复遵守同一 `inspect → plan → execute` 契约。计划可序列化、可计算稳定 digest，并绑定来源指纹和目标父目录身份；目标同级的 `.erii-lifecycle.lock` 是跨进程排他锁的稳定路径，可长期保留且不含用户正文。恢复只以 no-replace 语义发布到缺失目标并保持原格式，不执行升级或覆盖；发布后最终验证失败时保留目标并报告 `published_target_preserved_manual_cleanup_required`，不会删除已发布副本。

该格式未签名、未加密，也没有租户授权语义；manifest 与 plan digest 只能检测内部损坏和执行漂移，不能证明来源真实性。跨进程锁只协调遵守协议的可信宿主，不抵抗已拥有来源或目标目录写权限的不可信同机进程；完整的不可信目录边界仍需要稳定父目录 handle/`dirfd` 与 Windows 等价句柄语义。当前实现还会把整份 payload 物化到内存。POSIX 的目录同步失败会使操作失败；Windows 仅对已知不支持目录同步的错误采用 best-effort，因此不承诺与 POSIX 完全相同的掉电持久性。

MemoryPack 对 `relationship-turn-adjudication-v1` 以及“contract 被降级但仍能匹配 Pack 内持久 Turn”的 direct records，会复核精确 Source Turn、Evidence identity 和异常 Agent 必须保持无 Event 拒绝的不变量。direct 路径没有 frozen candidate，所以兼容承诺不包括完整重放普通 accepted Event。真正 transient records 继续按 Legacy 数据读取。上述检查只证明未签名 Pack 的当前字段内部自洽；能够整体改写 Pack 的一方仍可删除 Turn、同步降级记录并重算未加密指纹，正式来源真实性需要宿主签名或 MAC。

`0.4.0a8` 的内置 SQLiteStorage 使用 Schema v9。Package Version、SQLite Schema Version 与 MemoryPack Format Version 当前虽然都在同一发布中变化，仍必须按各自契约独立校验和迁移。

升级不会删除旧记忆：无法恢复现代来源且没有可证明异常来源的内容以 `legacy_context` 保留，已知绑定异常交付却缺少消息角色证据的内容以 `quarantined_history` 保留。两者都可检查、显示标签、导出和删除，但不能被内容相似度升级为普通权威；Legacy 不再强化，Quarantined 不进入默认生成召回。引用异常 Agent 消息的 a8 关系候选回执保持不可变，v0.5 只能追加新的处置记录，不能把旧 `rejected` 原地改为 `accepted`。

## 可选组件

只有 CI 或文档明确列出的组合才视为经过验证。第三方存储、向量库和 Agent 框架适配器由各自维护者负责兼容性。

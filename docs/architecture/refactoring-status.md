# E.R.I.I. 结构重构状态

> 更新日期：2026-08-20
>
> 权威项目状态：[PROJECT_STATUS.md](../PROJECT_STATUS.md)；其数据源为
> [`docs/project-status.json`](../project-status.json)。本文只记录结构重构的本地实施状态，
> 不覆盖项目状态门禁。

## 当前结论

R1B 已于 2026-08-18 通过剩余退出门。R2A（Contracts 与 Plan Codec）和 R2B（Inspection 与
Planning）均已于 2026-08-20 收口，并通过公共/历史兼容、Windows capability smoke 与强制
同环境性能门；权威阶段为 `R2 / complete`，下一阶段是 R3，不能跳过 R3 进入 R4。

R1B 收口证据：

- SQLite schema 11 新增 `memory_pack_write_receipts`，payload 与版本化成功回执同事务提交；
- `ERIIEngine.import_memory()` 使用 source/target/overwrite 派生的稳定 operation ID，在 target
  preflight 前解析相同请求的已提交回执，提交后异常可返回成功且重试不重放；
- schema 6、9、10 均可通过 source-preserving Lifecycle 路径升级到 11，擦除会撤销相关回执；
- MemoryPack Transfer：`43 passed, 60 subtests passed`；SQLite Upgrade：`13 passed, 3 subtests passed`；
- 15 样本 R0/current 同环境配对性能门通过；FileStorage 原子导入的持久 journal 成本为
  +14.6 至 +16.4 ms，处于明确的 20 ms / 55% 耐久性预算内；
- CI 的性能作业不再按冻结报告的 Python/Windows build 静默跳过，环境不兼容或样本不稳定直接失败。

## R2 已实施部分

- `erii/_lifecycle/plan_codec.py` 已接管规范 JSON、严格解码、版本专用 Plan reader/writer、
  shape/strategy validation 和摘要工具。
- `erii/_lifecycle/serializers.py` 已接管 Lifecycle 类型与 Plan 文档的转换逻辑。
- `erii/_lifecycle/contracts.py` 已成为 Lifecycle Enum、dataclass、Request、Plan 和 Report 的
  单一权威定义；`erii.data_lifecycle` 与根级 `erii` 只 re-export 相同类对象，历史
  `__module__ = "erii.data_lifecycle"` 与 pickle 路径保持不变。
- `erii/_lifecycle/inspection.py` 与 `planning.py` 已分别接管零写入观察和六种 Request -> Plan；
  `DataLifecycleCoordinator.inspect/plan` 委托同一个 Inspector/Planner 组合。
- `filesystem.py`、`snapshots.py`、`sqlite_semantics.py` 与纯格式 seam 已接管 stable read、
  不可变 observation、SQLite 语义读取/升级预演及 Erasure/MemoryPack 只读验证；`utils.py` 已删除。

记录分层：本文是当前实施状态；[Lifecycle 重构计划](lifecycle-refactoring-plan.md) 定义
正式范围和退出门；[R2 实施日志](r2-implementation-log.md) 与其链接的阶段报告只保留
历史过程和已撤销结论，不能覆盖权威状态。

## 2026-08-18 修复审计

序列化提取曾遗漏两个 MemoryPack Backup-v1 历史 producer catalog，并跳过旧 catalog 的
状态一致性校验，造成 10 个历史兼容子测试失败。当前修复恢复了重构前的冻结 catalog、
旧 catalog 校验和当前 catalog 重新分类顺序。SQLite schema 11 收口后再次发现并补齐了
schema-10 producer catalog；schema 6、9、10 的 Backup-v1 恢复现均有 byte-exact 回归覆盖。

已验证：

- 历史 Backup 兼容：`4 passed, 24 subtests passed`；
- 核心 Backup/Inspection/Plan 集：`36 passed, 2 skipped, 44 subtests passed`；
- 全部 Python：`1034 passed, 14 skipped, 96 warnings, 555 subtests passed`；
- DeepSeek 离线测试：`45 passed`；
- `ruff check --no-cache erii/_lifecycle erii/data_lifecycle.py` 通过。

R1B 不再有未完成门禁；Windows smoke 将由更新后的 CI 在提交后再次执行。

## 2026-08-20 R2A 收口

- 删除 `contracts.py` 到 `erii.data_lifecycle` 的三个函数内反向委托；Contracts、Codec 和
  Serializer 现在只依赖内部权威边界，不再借 façade 掩盖循环；
- 将 v1-current Plan 文档构造、版本字段集、shape/strategy validation、assessment catalog
  validation 与 deterministic strategy identity 迁入 `plan_codec.py`；
- `freeze_contracts.py` 直接读取 Codec 的可读 Plan 版本集合，不再依赖 façade 私有常量；
- 新增 Codec Interface 级 v1 round-trip 和非标准 JSON 数值拒绝测试；
- 19 个合同导出在 `erii`、`erii.data_lifecycle`、`erii._lifecycle.contracts` 三个路径保持
  identity；17 个运行时类型保持历史 `__module__` 并可 pickle 往返；
- Lifecycle 筛选集：`160 passed, 4 skipped, 206 subtests passed`；核心操作矩阵：
  `75 passed, 2 skipped, 118 subtests passed`；全部 Python：
  `1037 passed, 14 skipped, 96 warnings, 577 subtests passed`；合同快照 4 个文件无差异。

## R2B 收口与剩余门禁

2026-08-20 本地验证证据：Lifecycle 筛选集 `170 passed, 4 skipped, 220 subtests passed`；
全部 Python `1048 passed, 14 skipped, 96 warnings, 591 subtests passed`；4 个合同快照、Ruff、
Compileall、文档链接、secret、项目状态、重构 inventory 与 `git diff --check` 均通过。

- `LifecycleInspector` 在 `erii`、`erii.data_lifecycle` 与内部路径保持同一对象、历史
  `__module__` 与 pickle 解析；公共 Lifecycle 合同的三路径 identity 保持冻结。
- Inspection/Planning 及纯格式 seam 的 AST 门禁止 façade 反向依赖、函数内项目 import、
  Engine/Storage/写执行模块依赖；`lifecycle_streaming.py` 仅同对象兼容 re-export。
- Windows capability smoke `49 passed, 4 skipped, 54 subtests passed`；4 个 skip 均由当前平台
  不提供目录 fd/FIFO 或当前账户无 symlink 权限触发。强制同环境性能门通过。

文档链接、项目状态目录、重构清单、合同快照、secret 扫描、Ruff、Compileall 和
`git diff --check` 均已通过。

## 下一步顺序

1. 进入 R3 写路径重构；
2. 先按独立批次迁移 Backup/Restore、Upgrade 与 Import；
3. 再收敛 Erasure/Rebuild 与 Coordinator 写编排；
4. 只有 R3 稳定检查点通过后才进入 R4。

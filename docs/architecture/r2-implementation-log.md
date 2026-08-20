# R2 Lifecycle 只读路径重构实施日志

> 状态：历史实施记录。权威阶段状态见
> [refactoring-status.md](refactoring-status.md)，正式范围和退出门见
> [lifecycle-refactoring-plan.md](lifecycle-refactoring-plan.md)。

## 基线与命名

- 早期 R2 本地实施开始于 2026-08-17，起点提交为 `9fd98c6`；该提交当时使用了
  “R1B 收口”的描述，但 R1B 的剩余耐久性和兼容性门直到 `ab4beb8` 才正式完成。
- 总控路线中的 R2 只有两个正式批次：R2A（Contracts 与 Plan Codec）和 R2B
  （Inspection 与 Planning）。早期草稿里的“R2B Serializer”和“R2C”是会话内标签，
  不代表正式阶段已经完成或新增了路线图阶段。
- 当前目标保持不变：在一个权威实现上提取 Contracts、Codec、Inspection 和 Planning，
  同时保持公共类型 identity、历史 reader、双 Storage 行为和零写入检查不变。

## 时间线

### 2026-08-17：早期提取

- 创建 `erii/_lifecycle/plan_codec.py`，接管规范 JSON、严格解码和摘要原语。
- 创建 `erii/_lifecycle/serializers.py`，提取 Lifecycle 类型和 Plan 文档转换。
- 初版 `contracts.py` 和 `utils.py` 复制了原实现，产生双重权威来源；serializer 提取还遗漏
  历史 MemoryPack producer catalog 和状态校验顺序。

### 2026-08-17 至 2026-08-18：审计与修复

- `c357b03` 删除 `data_lifecycle.py` 中已由 serializer 接管的重复转换逻辑。
- `8ef6e69` 记录重复实现和兼容性审计，并撤销错误的“R2 已完成、可跳过 R3”结论。
- `ab4beb8` 将 `contracts.py` 和 `utils.py` 收窄为原权威实现的私有别名，完成 R1B 收口，
  同时恢复 MemoryPack 历史 catalog、补齐 SQLite schema-10 producer catalog，并为
  schema 6、9、10 的 Backup-v1 恢复增加 byte-exact 覆盖。

### 2026-08-20：R2A 收口

- `contracts.py` 成为 19 个 Lifecycle 合同导出的单一权威定义，旧公开路径保持同一对象；
- `plan_codec.py` 接管 v1-current 严格 reader/writer、Plan shape/strategy validation、
  assessment catalog validation 和 deterministic strategy identity；
- 删除 Contracts 到 façade 的三个函数内反向委托，Serializer 的运行时类型解析也改为内部
  Contracts/Codec 依赖；
- 冻结脚本改为直接消费 Codec 的可读 Plan 版本集合，并增加独立 Codec Interface 回归测试。

### 2026-08-20：R2B 收口

- `inspection.py`、`planning.py`、`filesystem.py` 与 `snapshots.py` 接管零写入观察、稳定读取和
  Request -> Plan；Coordinator 委托同一个 Inspector/Planner 组合；
- SQLite image upgrade、Erasure scope inspection 与 MemoryPack graph validation 下沉为无 façade、
  Engine、Storage 或写执行依赖的内部 seam；
- `lifecycle_streaming.py` 转为同对象兼容 re-export，`utils.py` 删除；
- 公共/历史兼容、Windows capability smoke、强制同环境性能门和全量 Python 均通过。

## 当前实现快照

| 路径 | 当前职责 | 状态 |
| --- | --- | --- |
| `erii/_lifecycle/plan_codec.py` | 严格 v1-current reader/writer、shape/strategy validation、规范 JSON 与摘要 | R2A 已接管 |
| `erii/_lifecycle/serializers.py` | 类型转换、Plan 文档转换、历史 producer catalog | 已接管且回归已修复 |
| `erii/_lifecycle/contracts.py` | Lifecycle Enum、dataclass、Request、Plan、Report 的权威定义 | R2A 已接管，旧路径 identity/module/pickle 兼容 |
| `erii/_lifecycle/inspection.py` / `planning.py` | 零写入 target/Backup 观察与六种 Request -> Plan | R2B 已接管 |
| `erii/_lifecycle/filesystem.py` / `snapshots.py` | stable read/tree 与不可变 payload observation | R2B 已接管，`utils.py` 已删除 |
| `erii/data_lifecycle.py` | 合同 re-export、Coordinator Facade 与 R3 写执行编排 | `inspect/plan` 已委托内部 Module |

当前规模仅作导航信号：`data_lifecycle.py` 约 1723 行，`lifecycle_erasure.py` 约 2203 行；
R2 是否完成由 Interface 和门禁决定，不由行数决定。

## 当前证据

- 全部 Python：`1034 passed, 14 skipped, 96 warnings, 555 subtests passed`；
- 历史 Backup 兼容：`4 passed, 24 subtests passed`；
- 核心 Backup/Inspection/Plan：`36 passed, 2 skipped, 44 subtests passed`；
- Ruff、合同快照、项目状态、文档链接和 secret 扫描通过。

这些结果证明早期回归和 R1B 门禁已经收口，不代表 R2 已验收。

R2A 收口新增证据：Lifecycle 筛选集 `160 passed, 4 skipped, 206 subtests passed`；核心
Backup/Upgrade/Erasure/Import 矩阵 `75 passed, 2 skipped, 118 subtests passed`；全部 Python
`1037 passed, 14 skipped, 96 warnings, 577 subtests passed`；合同快照 4 个文件无差异。
R2B 收口证据：Lifecycle 筛选集 `170 passed, 4 skipped, 220 subtests passed`；全部 Python
`1048 passed, 14 skipped, 96 warnings, 591 subtests passed`；Windows capability smoke、
强制同环境性能门、合同快照与静态结构门通过。R2 总阶段已完成。

## 下一步

1. 进入 R3 Backup/Restore、Upgrade 与 Import 写路径批次；
2. 再收敛 Erasure/Rebuild 与 Coordinator 写编排；
3. R3 稳定检查点通过前保持 R2 Interface 和格式承诺冻结。

相关历史记录：

- [R2 早期 Phase 1 记录](r2a-phase1-completion.md)
- [Serializer 提取审计](r2b-completion-report.md)
- [Inspection/Planning 继续实施决策](r2c-decision.md)
- [Inspection/Planning 继续实施计划](r2c-implementation-plan.md)

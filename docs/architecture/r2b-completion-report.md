# R2 Serializer 提取审计

> 状态：历史批次报告。文件名沿用早期会话标签；总控路线中的正式 R2B 是
> Inspection 与 Planning，尚未完成。权威状态见
> [refactoring-status.md](refactoring-status.md)。

## 提取内容

早期批次创建 `erii/_lifecycle/serializers.py`，接管：

- target、assessment、content、directory identity 和 selector 转换；
- Plan intent、body 和 document 转换；
- Backup manifest 的 producer catalog 识别和 content identity 恢复。

随后 `c357b03` 让 `erii.data_lifecycle` 委托该模块，并删除旧文件中的重复转换实现。
当前 serializer 是这些转换的唯一实现，而不是复制层。

## 初始回归

提取后的首轮宽测试出现 10 个历史兼容子测试失败。这些失败不是可以忽略的“错误层级变化”，
而是确认的兼容性回归：

- 遗漏 MemoryPack `0.5.0a1` 和 `0.5.0a2` 的冻结 producer catalog；
- 没有先按历史 producer catalog 校验持久化状态；
- 改变了旧 catalog 校验后按当前 catalog 重新分类的顺序。

因此当时的“R2B 基本完成”结论无效。

## 修复结果

2026-08-18 的修复恢复了重构前的 catalog 和验证顺序；R1B schema 11 收口后又补齐了
SQLite schema-10 producer catalog。当前证据为：

- 历史 Backup 兼容：`4 passed, 24 subtests passed`；
- 核心 Backup/Inspection/Plan：`36 passed, 2 skipped, 44 subtests passed`；
- 全部 Python：`1034 passed, 14 skipped, 96 warnings, 555 subtests passed`；
- SQLite schema 6、9、10 Backup-v1 恢复均有 byte-exact 覆盖。

未知版本、错误状态和未来 producer view 继续被拒绝。

## 当前结论

Serializer 转换层已经提取并修复，但正式 R2 仍需完成合同本体、Inspection、Planning 和
Facade 委托。下一步不是直接进入 R4；继续实施决策见
[r2c-decision.md](r2c-decision.md)，正式计划见
[lifecycle-refactoring-plan.md](lifecycle-refactoring-plan.md)。

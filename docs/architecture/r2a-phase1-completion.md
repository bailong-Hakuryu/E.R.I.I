# R2 早期 Phase 1 实施记录

> 状态：历史批次记录，不是 R2A 完成证明。权威状态见
> [refactoring-status.md](refactoring-status.md)。

## 当时完成的工作

2026-08-17 的第一个小批次完成了：

- 创建 `erii/_lifecycle/plan_codec.py`；
- 将规范 JSON、SHA-256 摘要、严格 JSON 解码和 SHA-256 格式检查迁入该模块；
- 让 `erii.data_lifecycle` 使用这些原语并删除对应重复函数；
- 创建 `erii/_lifecycle/contracts.py` 的初始框架；
- 以 Lifecycle Plan v1 和 Backup/Restore 窄测试验证本批次，历史结果为
  `28 passed, 2 skipped`，并通过当时的编译检查。

这批工作证明独立的纯 Codec 原语可以安全提取，但窄测试结果不能证明完整 R2A 或 R2
退出门已经通过。

## 后续审计更正

初版 `contracts.py` 复制了 Enum 和 dataclass，可能造成类型 identity 分叉。该实现没有作为
第二套权威合同保留；当前 `contracts.py` 只别名到 `erii.data_lifecycle` 中的唯一合同类型，
等待正式迁移和旧路径 re-export 一次完成。

同一轮后续进行的 serializer 提取也曾产生历史 producer catalog 回归，详见
[Serializer 提取审计](r2b-completion-report.md)。该回归已修复，但说明“局部测试通过”不能
替代 declared-readable 历史矩阵。

## 当前价值与剩余范围

已保留的有效成果是 `plan_codec.py` 的独立纯函数边界，以及 serializer 转换层。正式 R2A
仍需完成：

1. 将所有 Lifecycle 公共合同迁到单一权威 `contracts.py`；
2. 保持 `erii.data_lifecycle` 和根级 `erii` 的 type identity 与导入路径；
3. 收敛完整 Plan Codec 和 shape validation；
4. 通过当前与历史 Plan round-trip、拒绝行为、合同快照和公共符号门禁。

不能依据本历史批次跳过 Inspection/Planning、R3 或稳定检查点进入 R4。正式顺序见
[Lifecycle 重构计划](lifecycle-refactoring-plan.md)。

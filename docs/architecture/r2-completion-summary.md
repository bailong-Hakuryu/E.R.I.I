# R2 Lifecycle 局部实施审计

> 原“R2 完成”结论已于 2026-08-18 撤销。当前状态为：局部实施、回归已修复、阶段未验收。
> 权威状态见 [refactoring-status.md](refactoring-status.md)。

## 已接管的实现

- `plan_codec.py`：规范 JSON、严格解码和摘要；
- `serializers.py`：Lifecycle 类型及 Plan 文档转换；
- `contracts.py`：当前仅别名到 `erii.data_lifecycle` 的唯一合同类型。

`utils.py` 不再保存重复工具逻辑，当前只别名到 `erii.data_lifecycle` 的权威 helper；
真正的迁移将与 Inspection 一起完成。

## 修复结果

序列化提取遗漏的 Backup-v1 历史 producer catalog 和状态校验顺序已经恢复；SQLite
schema-10 producer catalog 也已补齐。历史兼容测试现为 `4 passed, 24 subtests passed`；
核心 Lifecycle 子集为 `36 passed, 2 skipped, 44 subtests passed`。全量 Python 测试为
`1034 passed, 14 skipped, 96 warnings, 555 subtests passed`，DeepSeek 离线测试为
`45 passed`。

## 尚未完成

Inspector、Planner 和合同本体仍在 `erii.data_lifecycle`；R2 自身的双 Storage、历史格式、
Windows 与性能退出门尚未执行。因此 R2 不满足总控路线图的退出条件，R3 和稳定检查点也
不能跳过，当前不进入 R4。

实施时间线见 [r2-implementation-log.md](r2-implementation-log.md)，正式后续顺序见
[lifecycle-refactoring-plan.md](lifecycle-refactoring-plan.md)。

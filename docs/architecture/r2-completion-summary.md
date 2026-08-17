# R2 Lifecycle 局部实施审计

> 原“R2 完成”结论已于 2026-08-18 撤销。当前状态为：局部实施、回归已修复、阶段未验收。

## 已接管的实现

- `plan_codec.py`：规范 JSON、严格解码和摘要；
- `serializers.py`：Lifecycle 类型及 Plan 文档转换；
- `contracts.py`：当前仅别名到 `erii.data_lifecycle` 的唯一合同类型。

`utils.py` 不再保存重复工具逻辑，当前只别名到 `erii.data_lifecycle` 的权威 helper；
真正的迁移将与 Inspection 一起完成。

## 修复结果

序列化提取遗漏的 Backup-v1 历史 producer catalog 和状态校验顺序已经恢复。历史兼容测试
现为 `4 passed, 23 subtests passed`；核心 Lifecycle 子集为
`36 passed, 2 skipped, 44 subtests passed`。全量 Python 测试为
`1028 passed, 14 skipped, 553 subtests passed`，DeepSeek 离线测试为 `45 passed`。

## 尚未完成

Inspector、Planner 和合同本体仍在 `erii.data_lifecycle`，完整 CI、双 Storage、历史格式、
Windows 与性能门禁尚未完成。因此 R2 不满足总控路线图的退出条件，R3 和稳定检查点也不能
跳过，当前不进入 R4。

最新状态与后续顺序见 [refactoring-status.md](refactoring-status.md)。

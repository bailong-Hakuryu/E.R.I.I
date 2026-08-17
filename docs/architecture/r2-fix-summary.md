# R2 修复摘要

## 2026-08-17：删除重复 Serializer

提交 `c357b03` 让 `erii.data_lifecycle` 使用 `_lifecycle.serializers`，删除了原文件中的
重复转换函数。该修复解决了“复制但未接管”的第一层问题，但当时的“R2 已完成”结论不成立。

## 2026-08-18：恢复历史 Backup-v1 行为

审计发现提取后的 `content_from_backup_manifest()`：

- 遗漏 MemoryPack `0.5.0a1` 和 `0.5.0a2` 的冻结 producer catalog；
- 没有先按 producer catalog 校验持久化状态；
- 改变了旧 catalog 校验后按当前 catalog 重新分类的顺序。

修复恢复了 R2 前的精确行为。历史兼容测试现为 `4 passed, 23 subtests passed`，核心
Lifecycle 子集为 `36 passed, 2 skipped, 44 subtests passed`；全量 Python 测试为
`1028 passed, 14 skipped, 553 subtests passed`，DeepSeek 离线测试为 `45 passed`。

此外，错误定义了第二套枚举/数据类的 `contracts.py` 已改为引用唯一合同实现；无调用方的
`utils.py` 也已改为权威 helper 的别名，不再保存第二份逻辑。

R2 仍未完成。当前状态和剩余门禁见 [refactoring-status.md](refactoring-status.md)。

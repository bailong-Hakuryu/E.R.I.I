# R2 Inspection/Planning 继续实施决策

> 决策日期：2026-08-18
>
> 状态：已决定继续完成 R2，不跳过 R3 或稳定检查点进入 R4。

> 2026-08-20 更新：当时依据第 1 项所述的 Contracts 别名状态已由 R2A 解决；代码检查点为
> `2fc8d74`，文档检查点为 `2047064`。本决策剩余范围现仅为正式 R2B 的 Inspection 与 Planning。

> 2026-08-20 R2B 更新：Inspection/Planning 单一权威实现、Facade 委托、纯格式预演 seam 与
> 依赖方向门已落地；`utils.py` 已删除。Windows capability smoke 与强制同环境性能门通过，
> R2 已完成，下一阶段为 R3。

## 决策

早期草稿所称“R2C”不是总控路线中的正式新阶段。它实际对应 R2 尚未完成的合同收口、
Inspection 和 Planning。该范围必须继续，正式归入：

- R2A：Contracts 与 Plan Codec；
- R2B：Inspection 与 Planning。

实施顺序以 [Lifecycle 重构计划](lifecycle-refactoring-plan.md) 为准。

## 依据

1. 决策时 `contracts.py` 只是私有别名，公共合同本体仍由 `erii.data_lifecycle` 定义；
2. 决策时 `utils.py` 只是权威 helper 的别名，不能视为 Inspection 已迁移；
3. 决策时 `DataLifecycleCoordinator.inspect/plan` 尚未委托独立内部 Module；
4. serializer 提取曾引入历史 reader 回归，说明只提取独立 helper 不能替代完整兼容矩阵；
5. 总控路线要求 R2 -> R3 -> 稳定检查点 -> R4，不能以局部行数减少改变依赖顺序。

## 被否决的路径

- **以别名文件宣告 R2 完成**：没有改变权威实现归属，也没有形成深 Module Interface；
- **只提取零散 helper 后进入 R4**：会保留 Inspection/Planning 的双向依赖和动态拼接；
- **直接跳过 R3**：Lifecycle 写路径尚未形成稳定检查点，会把风险带入 Engine 工作流重构；
- **按预计工时决定范围**：阶段完成由兼容性、原子性和平台门禁决定，不由行数或会话预算决定。

## 继续实施的退出条件

- Lifecycle 公共合同只有一个权威定义，旧路径保持 type identity 和 re-export；
- Inspector 对所有 target kind/status 完成零写入检查；
- Planner 保持 strategy ID、shape、fingerprint、selector 和 stale 绑定；
- `DataLifecycleCoordinator.inspect/plan` 只负责编排并委托内部 Module；
- 当前与历史 reader、双 Storage、Windows 和同环境性能门全部通过。

R2 通过后进入 R3；只有 R3 稳定检查点通过后，才恢复 R4。

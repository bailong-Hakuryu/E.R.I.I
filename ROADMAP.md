# E.R.I.I. Roadmap

路线图表达当前方向，不是固定发布日期或 SLA。只有满足测试和迁移门槛的能力才会进入正式版本。

## v0.3.1：稳定化

- 统一版本、修复包构建和服务生命周期；
- 恢复 LLM JSON 容错；
- 为持久任务增加租约恢复；
- 清理第三方角色痕迹和不准确宣传；
- 建立 CI、变更日志和维护文档。

## v0.4.0：关系人格基础

### alpha.1：无 LLM 领域内核（已完成）

- 原始人设快照与结构化编译结果；
- 稳定的 persona、relationship 和 identity ID；
- 追加式历史事件；
- 当前认知与关系状态投影；
- SQLite Schema 和迁移框架；
- 宿主显式控制后台处理生命周期；
- 关系档案和事件进入 MemoryPack `0.4.0`。

### alpha.2：候选提取与规则裁决（已完成）

- Pydantic 边界 Schema；
- LLM 只产生候选事件和定性关系信号；
- 证据校验、去重、幂等、置信度和变化限幅；
- Persona Reflection、显式历史重处理与时间上下文；
- 重大人格变化经过独立内在审视后转为待确认提案；
- FileStorage、SQLiteStorage 与 MemoryPack 携带裁决记录。

### alpha.3：人设感知结构化召回（已完成：`0.4.0a3`）

- `RecallResult`；
- 可替换 Prompt Renderer；
- Persona Compiler、审批 Manifest 与规划/完整人设交付；
- 关系前提、定性 Baseline、显式受众和原子预算；
- recorded、occurred 和 world time 携带；
- 默认只读召回与显式 Recall Reinforcement；
- SQLite Schema v3 与 MemoryPack `0.4.0a3`。

### alpha.4：时间承诺与开放事项（下一阶段：`0.4.0a4`）

- 到期承诺与未完成事件信号。
- Promise/Open Loop 的追加式 Resolution；
- 同一 World Time 时钟内的到期与逾期判断；
- 旧 `is_unresolved` 的低权威兼容投影。

### beta.1：迁移与长期评测

- `0.3 → 0.4` 备份、dry-run、验证与回滚；
- 删除事件后重建派生状态；
- 长期关系故事和领域不变量测试；
- 大数据量召回与重建性能基线。

## v0.4.0 非目标

- Web UI、账号和托管平台；
- 多 Agent 共享关系图；
- 核心引擎直接向用户发送消息；
- 大量第三方框架和数据库适配器；
- 没有来源证据的自动人格改写。

## 产品化准入条件

- Schema 连续两个次版本没有重大重构；
- 数据升级、回滚、导出和永久删除经真实数据验证；
- 长期关系评测稳定；
- 存在维护者之外的持续用户；
- 核心与产品层解耦；
- 具备用户支持、安全响应和持续投入能力；
- 完成目标市场的正式商标近似检索。

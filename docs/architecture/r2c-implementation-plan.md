# R2 Inspection/Planning 继续实施计划

> 状态：待实施。文件名沿用早期“R2C”会话标签；正式范围属于 R2A/R2B。
>
> R1B 已完成。当前权威状态见 [refactoring-status.md](refactoring-status.md)，完整设计见
> [lifecycle-refactoring-plan.md](lifecycle-refactoring-plan.md)。

## 目标

在不改变公共 API、持久格式或写路径语义的前提下，将 Lifecycle 合同、零写入 Inspection
和 Planning 收敛到 `erii/_lifecycle/` 的单一权威实现。

## 前置状态

- [x] `plan_codec.py` 接管 v1-current 严格 reader/writer、Plan shape/strategy validation、
  规范 JSON 和摘要原语；
- [x] `serializers.py` 接管类型与 Plan 文档转换；
- [x] 历史 MemoryPack 和 SQLite schema-10 producer catalog 回归已修复；
- [x] R1B exactly-once、schema 11、精确回执擦除和实际 remap 锁范围已收口；
- [x] Lifecycle 合同本体迁入 `contracts.py`，旧路径保持相同 type identity；
- [x] 删除 Contracts/Serializer 到 façade 的反向委托，R2A 完成；
- [ ] Inspection 和 Planning 形成独立 Module Interface。

## 实施顺序

### 1. 单一合同来源（已完成）

- 将 Enum、dataclass、Request、Plan 和 Report 迁到 `contracts.py`；
- `erii.data_lifecycle` 与根级 `erii` 只 re-export；
- 保持 type identity、`__module__` 要求、冻结字段和异常类型；
- 公共符号、合同快照、历史 Plan fixture 与 pickle 路径已经通过。

### 2. 零写入 Inspection

- 创建 `inspection.py`，接管 FileStorage、SQLite、MemoryPack 和 Backup 检查；
- 统一 missing/empty/current/migration-required 状态和 content identity；
- 保持稳定读取、symlink/reparse point、runtime lock 排除和历史 producer catalog；
- 用测试证明 `inspect()` 不创建、清理或修改目标路径。

### 3. Planning

- 创建 `planning.py`，接管 Request -> immutable Plan；
- 保持 strategy ID、source/destination topology、selector、import options 和 fingerprint；
- 保持 Plan shape、版本、重复键、未知字段和 stale identity 拒绝；
- Planning 不重新扫描目标，也不执行写入。

### 4. Facade 委托与去重

- 让 `DataLifecycleCoordinator.inspect/plan` 委托新 Module；
- `execute()` 在 R2 继续留在原写路径，并消费相同 Plan；
- 删除 `data_lifecycle.py` 中已接管的实现，不保留 wrapper 内复制；
- 将 `utils.py` 收窄为必要 re-export，或在调用方迁移后删除无价值别名。

### 5. 退出验证

- 当前和历史 Plan/Backup/MemoryPack/SQLite reader；
- FileStorage 与 SQLite target/status/strategy/selector 矩阵；
- 根级 API、type identity、合同快照和项目状态；
- Windows 文件占用与 reparse point smoke；
- 同环境性能门、Ruff、Compileall、文档链接和 secret 扫描；
- 全量 Python 和声明的离线实验测试。

## R2 不包含

- Backup/Restore、Upgrade、Import、Erasure、Rebuild 的写路径迁移；
- 新格式版本、strategy ID、升级承诺或擦除范围；
- Engine R4 工作流提取；
- 以行数目标替代 Interface 和兼容性退出门。

R2 完成后按总控路线进入 R3，而不是直接进入 R4。

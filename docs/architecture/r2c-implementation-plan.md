# R2 Inspection/Planning 继续实施计划

> 状态：R2B 已完成并通过退出门。文件名沿用早期“R2C”会话标签；
> 正式范围属于 R2A/R2B。
>
> R1B 已完成。当前权威状态见 [refactoring-status.md](refactoring-status.md)，完整设计见
> [lifecycle-refactoring-plan.md](lifecycle-refactoring-plan.md)。
>
> R2A 干净检查点：代码 `2fc8d74`，文档 `2047064`。

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
- [x] Inspection 和 Planning 形成独立 Module Interface。

## R2B 进入决策

### 深 Module Interface

Inspection 保持现有公共 Interface：

```python
class LifecycleInspector:
    def inspect(self, target: LifecycleTarget) -> LifecycleAssessment: ...
```

Planning 新增一个内部 Interface：

```python
class LifecyclePlanner:
    def __init__(self, inspector: LifecycleInspector) -> None: ...
    def plan(self, request: LifecycleRequest) -> LifecyclePlan: ...
```

Planner 不直接实现 FileStorage、SQLite、MemoryPack 或 Backup 扫描，也不执行写入。它可以通过
Inspector 和只读 Snapshot helper 重复观察来源，以保持当前 capture/selector validation 前后的
stale/TOCTOU 拒绝。Coordinator 不接收或拼装多字段 `PlanningContext`；否则复杂度只是从 Planner
泄漏回 façade，形成浅 Module。

### 已确认的依赖事实

- Backup inspection 除 assessment 外还向 Restore/Execution 提供 content、operation identity 和
  只读 payload snapshot；迁移时必须移动权威 reader，不能从 `inspection.py` 反向调用 façade；
- Upgrade planning 为冻结结果 fingerprint 会读取并转换 source snapshot；这是现有零写入 dry-run
  语义，不得降级为只选择 strategy ID；
- Erasure/Rebuild planning 会在 selector validation 前后复查 assessment；MemoryPack Import 会在
  semantic graph validation 后复查 assessment；这些二次观察是并发正确性要求；
- `utils.py` 当前没有仓库内调用方，只是指向 façade helper 的别名。Inspection 接管后应删除或仅保留
  必要兼容 re-export，不能继续作为反向依赖层；
- `LifecycleInspector` 已是根级公共符号。迁移必须保持 `erii`、`erii.data_lifecycle` 与内部路径的
  type identity、历史 `__module__` 和 pickle 解析。

## 实施顺序

### 1. 单一合同来源（已完成）

- 将 Enum、dataclass、Request、Plan 和 Report 迁到 `contracts.py`；
- `erii.data_lifecycle` 与根级 `erii` 只 re-export；
- 保持 type identity、`__module__` 要求、冻结字段和异常类型；
- 公共符号、合同快照、历史 Plan fixture 与 pickle 路径已经通过。

### 2. R2B-1：Inspection 支撑类型和读原语

状态：已完成。

- 创建 `snapshots.py`，将不可变 PayloadSnapshot 与 capture/materialize/stale 不变量一并迁移；
  BackupBundle 保持在权威 Backup reader 旁，不做浅类型搬运；
- 将 stable read、regular path、directory identity 与 tree digest 迁到 `filesystem.py`，SQLite
  read-only schema/semantic reader 下沉 `sqlite_semantics.py`；
- 写入、staging、publish、fsync 和 cleanup helper 继续留在原执行路径；
- 先增加内部 Inspector Interface 与公开 type identity 回归，再删除旧定义。

### 3. R2B-2：零写入 Inspection

状态：已完成。

- 创建 `inspection.py`，接管 FileStorage、SQLite、MemoryPack 和 Backup 检查；
- 统一 missing/empty/current/migration-required 状态和 content identity；
- 保持稳定读取、symlink/reparse point、runtime lock 排除和历史 producer catalog；
- Backup reader 和 live target reader 只依赖 Contracts、Codec、Serializer、Snapshot 与格式 catalog；
- 用测试证明 `inspect()` 不创建、清理或修改目标路径，并让 façade re-export 同一 Inspector 对象。

### 4. R2B-3：Planning

状态：已完成。

- 创建 `planning.py`，接管 Request -> immutable Plan；
- 保持 strategy ID、source/destination topology、selector、import options 和 fingerprint；
- 保持 Plan shape、版本、重复键、未知字段和 stale identity 拒绝；
- 把 `_make_plan` 与六种 request dispatch 迁到 Planner；
- 通过 Inspector/只读 Snapshot helper 保持必要复查，Planning 自身不实现格式扫描且绝不写入。

### 5. R2B-4：Facade 委托与去重

状态：已完成；`utils.py` 无调用方，已删除而非保留别名层。

- 让 `DataLifecycleCoordinator.inspect/plan` 委托新 Module；
- `execute()` 在 R2 继续留在原写路径，并消费相同 Plan；
- 删除 `data_lifecycle.py` 中已接管的实现，不保留 wrapper 内复制；
- 将 `utils.py` 收窄为必要 re-export，或在调用方迁移后删除无价值别名。

### 6. R2B-5：退出验证

状态：已完成；Windows capability smoke 与强制同环境性能门通过。

- 当前和历史 Plan/Backup/MemoryPack/SQLite reader；
- FileStorage 与 SQLite target/status/strategy/selector 矩阵；
- 根级 API、type identity、合同快照和项目状态；
- Windows 文件占用与 reparse point smoke；
- 同环境性能门、Ruff、Compileall、文档链接和 secret 扫描；
- 全量 Python 和声明的离线实验测试。

每一批必须在同一提交内同时完成“新权威实现、调用方切换、旧实现删除和对应测试”，不得提交
只有别名、复制实现或反向函数内 import 的中间状态。

## R2 不包含

- Backup/Restore、Upgrade、Import、Erasure、Rebuild 的写路径迁移；
- 新格式版本、strategy ID、升级承诺或擦除范围；
- Engine R4 工作流提取；
- 以行数目标替代 Interface 和兼容性退出门。

R2 完成后按总控路线进入 R3，而不是直接进入 R4。

# v0.5.0a1 源码里程碑摘要

## 状态

`0.5.0a1` 是当前活跃的 **alpha 源码里程碑**。它不是 `0.4.x` 稳定维护线，
也不表示已经发布 PyPI 包、GitHub Release、SLA 或产品级支持。`0.x` 集成应固定经过
审查的 full commit SHA，并以该提交实际通过的 CI 与迁移证据为准。

## 已实现纵切

### Relationship Consequence

- `RelationshipConsequence` 记录一次已展示、连续性受支持的 Agent 选择所产生的后果；
- 来源必须闭合到同一关系内的 completed Source Turn、最终 Agent message、
  reviewed continuity receipt、accepted adjudication decision 与 Relationship Event；
- “符合角色”与“是否造成伤害”是两条独立结论。拒绝、愤怒、边界表达和关系终止
  都不是仅因不温柔就被判为 OOC。

### Narrative Tension

- 每个 consequence 产生稳定 tension identity；
- 后续状态只能由新的、带来源的 `NarrativeTensionLink` 追加；
- 当前结果由完整历史确定性投影，不会因时间经过自动变好；
- 投影只进入 `RecallAudience.AGENT_PRIVATE`，公共召回不暴露后果账本。

### 持久化与生命周期

- FileStorage format `2`；
- SQLite schema `10`；
- MemoryPack wire `0.5.0a1`；
- consequence/tension 已接入导出、导入、删除级联、关系重建与完整性验证；
- Lifecycle 继续使用 `inspect → plan → execute`、verified backup-first、源保留和并排目标。

## MemoryPack 兼容方向

- `0.5.0a1` reader 可以读取 `0.4.0a8` 及目录中声明可读的旧 Pack；缺少的新集合
  解释为空列表；
- `0.4.0a8` reader 会严格拒绝 `0.5.0a1` 新增的
  `relationship_consequences` 与 `narrative_tension_links` 根字段；
- 包含 consequence 数据的 Pack 不提供有损降级写出。

这是一条**单向读取兼容**，不是“旧 reader 可以忽略新字段”的双向兼容。

## 可运行入口

- [`examples/consequence_example.py`](examples/consequence_example.py)：
  Persona Manifest → `begin_turn` → Continuity Review → `complete_turn` →
  accepted Relationship Event → Consequence → Tension Link → Agent-private Recall；
- [`docs/migration-0.5.0.md`](docs/migration-0.5.0.md)：格式与数据库迁移；
- [`docs/domain-model.md`](docs/domain-model.md)：领域权威与因果链；
- [`ROADMAP.md`](ROADMAP.md)：`0.4.x`、`0.5.x` 与后续产品边界。

## Experimental Labs

DeepSeek Continuity Review 是可拆卸实验，不是内核依赖。历史小样本 API 运行只说明
一次探索过程，不建立生产准确率、成本/延迟优势或部署推荐。远程 Provider 调用的数据
出境、授权、留存、删除、训练政策和密钥管理由宿主负责；API Key 仅从环境变量或宿主
Secret Manager 注入。

## 尚未实现或尚未承诺

- Character Deliberation、伤害后的自主修复选择与多模型 Deliberation Ensemble；
- 每用户身份、对象级授权、正式多租户隔离、默认静态加密、签名/MAC、配额与 SLA；
- `1.0` 的正式包发布、不可移动发布证据与长期支持政策。

## 验证原则

本文件不保存会快速过期的“全部通过”“工作区干净”或分支领先数量。提交前应实际执行：

```bash
python scripts/check_secrets.py
python scripts/freeze_contracts.py --check
python scripts/check_docs.py .
python -m ruff check erii tests examples benchmarks scripts experiments/deepseek-continuity-review
python -m pytest -q
python -m compileall -q erii tests examples benchmarks scripts experiments/deepseek-continuity-review
python -m build
```

真实结果记录在对应提交的 CI，而不是由本摘要预先宣称。

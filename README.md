# E.R.I.I.

> Experiential Recall & Impression Integration — 让情感型 Agent 保留共同经历，并维持连续的人格与关系。

[![License](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](LICENSE)
[![Python](https://img.shields.io/badge/Python-3.9%2B-green.svg)](https://www.python.org/)
[![Version](https://img.shields.io/badge/version-0.4.0a3-orange.svg)]()

E.R.I.I. 是一个可嵌入 Python 应用的长期记忆引擎，主要面向 AI 伴侣、虚拟角色和叙事型 Agent。

普通检索系统关心“哪些文本与问题相似”；E.R.I.I. 更关心：

- Agent 与用户共同经历过什么；
- Agent 现在如何理解这些经历；
- 哪些承诺、情绪和未完成事件仍在影响双方关系；
- 如何在不轻易破坏角色底色的前提下，让长期互动留下痕迹。

项目目前处于 `0.x` 实验阶段，由单人维护。API 与存储模型仍会演进，不提供商业级 SLA。我们会优先保护记忆数据的可导出性，并为破坏性数据升级提供迁移路径。

## 当前版本能够做什么

`v0.4.0a3` 已实现：

- 按 `(agent_id, user_id)` 隔离记忆；
- 核心人格文本、体验时间线和分类印象节点；
- LLM 后台归档与 SQLite 持久任务队列；
- 时间衰减、情绪增益、未完成事件保鲜和召回强化；
- 关键词排名与可选向量排名的 RRF 融合；
- FileStorage 与 SQLiteStorage；
- MemoryPack JSON 导入导出；
- 第一人称独白、公开日记和可见性过滤；
- Prompt 注入模式过滤、路径键校验和基础 PII 掩码；
- Callable、OpenAI-compatible 和 ChromaDB 扩展；
- 可选 FastAPI REST 服务。
- 每个 `Agent × User` 独立且稳定的 relationship、persona 与 identity ID；
- 不可静默覆盖的原始人设快照和可选结构化编译结果；
- 追加式关系事件、幂等事件 ID 与可重建的当前认知；
- 熟悉、信任、亲密、安全感与冲突张力的有限幅度状态投影；
- 每项当前状态对应的事件证据和叙事解释；
- 关系档案与事件的 MemoryPack 携带能力；
- 不可信候选的 Pydantic Schema、精确证据核验和逐候选裁决；
- LLM 定性关系信号到五维状态的确定性、有界映射；
- 技术重试幂等、底层经历去重、历史佐证和显式历史重处理；
- 不可变 Persona Reflection，以及积累型/转折型人格成长提案；
- 宿主在对话外按提案版本批准、拒绝或撤销人格成长；
- 证据、裁决回执和人格成长提案的 MemoryPack 携带能力；
- 显式观察但不暗改关系状态的时间上下文；
- 显式 Persona Compiler、不可变 Proposal revision 与精确 Manifest 审批；
- `fresh`、`address_only`、`canonical_continuation` 关系前提及确定性 Baseline；
- `RecallResult`、显式受众、World Time、完整投影预算和可替换 Renderer；
- 默认只读的结构化召回，以及只强化最终入选记忆的显式模式；
- SQLite Schema v3 和携带 Persona Proposal/Manifest/关系前提的 MemoryPack `0.4.0a3`；
- 由宿主显式控制的后台归档生命周期。

当前版本尚未实现：

- 事件、情节和关系阶段的分层巩固；
- 到期承诺、开放事项与其追加式解决事件信号；
- 完整的授权、加密或多租户安全边界。

这些能力属于 `v0.4.0` 及后续路线，不应将 README 中的规划理解为已经交付。

## 安装

```bash
pip install erii

# 按需安装 REST、OpenAI 或向量扩展
pip install "erii[server]"
pip install "erii[openai]"
pip install "erii[vector]"
```

从源码开发：

```bash
git clone https://github.com/bailong-Hakuryu/E.R.I.I.git
cd E.R.I.I
pip install -e ".[all]"
```

## 最小示例

以下示例中的 `Lumi` 是为文档创建的虚构占位角色，不对应任何现有作品角色。

```python
import json

from erii import ERIIEngine


def extract_memory(prompt: str) -> str:
    """替换为你的本地模型或远程 LLM 调用。"""
    return json.dumps(
        {
            "timeline_entry": "我得知 user_chen 喜欢在雨天喝伯爵红茶。",
            "thought_entry": {
                "content": "以后下雨时，我或许可以主动提起这件事。",
                "visibility": "internal_monologue",
                "is_unresolved": False,
                "emotional_score": 0.2,
            },
            "impressions": [
                {
                    "type": "preference",
                    "content": "user_chen 喜欢在雨天喝伯爵红茶。",
                    "base_importance": 0.8,
                    "emotional_score": 0.1,
                    "tags": ["tea", "rain"],
                }
            ],
        },
        ensure_ascii=False,
    )


with ERIIEngine(storage_dir="./erii_memory", llm=extract_memory) as engine:
    engine.initialize_relationship(
        agent_id="agent_lumi",
        user_id="user_chen",
        persona_source="Lumi 是一个温柔、坦诚，并尊重用户边界的原创角色。",
        compiled_persona={
            "values": ["坦诚", "尊重边界"],
            "voice": {"style": "温和"},
        },
    )

    engine.set_core_memory(
        agent_id="agent_lumi",
        user_id="user_chen",
        content="Lumi 是一个温柔、坦诚，并尊重用户边界的原创角色。",
    )

    engine.remember(
        agent_id="agent_lumi",
        user_id="user_chen",
        user_message="下雨的时候，我喜欢喝伯爵红茶。",
        bot_reply="我记住这种安静的雨天味道了。",
    )
    engine.process_pending()

    context = engine.recall(
        agent_id="agent_lumi",
        user_id="user_chen",
        query="雨天适合做什么？",
    )
    print(context)
```

`ERIIEngine()` 不会自动启动隐藏线程。上例用 `process_pending()` 同步消费任务；需要后台归档时，由宿主显式调用 `engine.start()`。退出前应调用 `close()`，或使用上下文管理器。未提供 LLM 时，当前版本只记录占位时间线，不会自动提取有效印象。

`set_core_memory()` 是仍供旧版文本召回使用的可覆盖字段，不等同于新的 Character Blueprint。alpha.1 已保存人设底色和关系投影，但它们要到结构化召回阶段才会自动进入宿主 Prompt。

## 关系人格内核

```python
from erii import BeliefUpdate, ERIIEngine

with ERIIEngine(storage_dir="./erii_memory") as engine:
    profile = engine.initialize_relationship(
        "agent_lumi",
        "user_chen",
        persona_source="Lumi 重视诚实，也尊重用户边界。",
        compiled_persona={"values": ["诚实"], "boundaries": ["不替用户做决定"]},
    )

    engine.record_relationship_event(
        "agent_lumi",
        "user_chen",
        "shared_experience",
        "我们第一次一起看雪。",
        event_id="first-snow",
        state_delta={"familiarity": 0.08, "trust": 0.04},
        belief_updates=[
            BeliefUpdate(key="shared.first_snow", value=True, confidence=1.0)
        ],
    )

    snapshot = engine.get_relationship_snapshot("agent_lumi", "user_chen")
    print(snapshot.state.trust)
    print(snapshot.state_reasons["trust"].evidence_event_id)
```

同一关系重复初始化会返回原有稳定 ID；试图换掉原始人设会抛出 `PersonaConflictError`。另一个用户会得到独立 `persona_id`、关系历史和状态。单个事件的状态变化绝对值上限为 `0.1`，巨大跃迁不会静默生效。

`record_relationship_event()` 是可信宿主兼容接口，因此允许宿主直接提供有界数值变化。不可信 LLM 输出应使用下面的候选裁决接口，不能提交 `state_delta` 或人格补丁。

## 候选证据与规则裁决

```python
from erii import ERIIEngine

with ERIIEngine(storage_dir="./erii_memory") as engine:
    engine.initialize_relationship(
        "agent_lumi",
        "user_chen",
        persona_source="Lumi 重视诚实，也会珍惜共同经历。",
        compiled_persona={
            "relationship_policy": {
                "version": "lumi-v1",
                "signal_modifiers": {"shared_experience": 1.2},
            }
        },
    )

    result = engine.adjudicate_relationship_candidates(
        "agent_lumi",
        "user_chen",
        source_turn={
            "turn_id": "turn-2026-07-27-1",
            "revision": "1",
            "extractor_version": "my-extractor-v1",
            "messages": [
                {
                    "source_id": "message-1",
                    "role": "user",
                    "content": "我们第一次一起看雪。",
                }
            ],
        },
        candidates=[
            {
                "candidate_key": "first-snow",
                "event_type": "shared_experience",
                "summary": "我们第一次一起看雪。",
                "signal": {
                    "signal_type": "shared_experience",
                    "strength": "moderate",
                    "extraction_confidence": 0.98,
                    "interpretation_confidence": 0.91,
                },
                "evidence": [
                    {"source_id": "message-1", "quote": "我们第一次一起看雪。"}
                ],
                "occurrence_key": "shared:first-snow",
                "persona_reflection": "我想把这场雪好好记住。",
            }
        ],
    )

    print(result.receipts[0].outcome)
    print(engine.get_relationship_snapshot("agent_lumi", "user_chen").state.intimacy)
```

宿主临时提供完整 turn 供精确引文校验；内核长期只保留来源身份、角色、精确片段、全文哈希和时间，不默认复制整段聊天。拒绝候选只留下指纹、原因、版本和时间，不保存幻觉文本或敏感引文。

模型置信度被拆成提取置信度与解释置信度，只参与路由和限幅。关系数值始终由版本化规则计算；低解释置信度的有效事实可以进入中性历史，但不会自动改变关系或保存 Persona Reflection。

相同来源处理身份的首次提交会固定整批候选；普通重试不能新增、删除或改写候选，只会继续未完成项或返回原回执。用新模型复核旧来源时，宿主必须显式提交 `processing_mode="historical_reprocessing"` 和稳定的 `reprocessing_id`；复核只追加佐证、更正或重新理解，不覆盖旧历史。

人格成长不能与当前 turn 的事件提取在同一次输出中完成。事件和 Persona Reflection 必须先提交，之后宿主再调用 `propose_persona_growth()` 提交独立 Inner Review 结果；`decide_persona_growth_proposal()` 仅记录宿主在对话外完成鉴权后的精确版本决定。

## Persona Compiler 与结构化召回

`0.4.0a3` 把“检索到什么”与“如何写进 Prompt”分开。`recall_structured()` 返回不可变、可序列化的 `RecallResult`；`render_recall()` 只是确定性 Renderer，不读取存储、不调用 LLM，也不会在渲染时删改语义项。

Persona Compiler 必须由宿主显式调用。编译结果先成为带原文引用的 Proposal，只有对精确 revision 的对话外审批才会生成 Manifest；初始化关系和后台归档都不会偷偷运行编译器或批准人设。

```python
from erii import ERIIEngine, RecallRequest

source = "Lumi is a patient original character."

with ERIIEngine(storage_dir="./erii_memory") as engine:
    engine.initialize_relationship("agent_lumi", "user_chen", source)

    proposal = engine.propose_persona_compilation(
        "agent_lumi",
        "user_chen",
        {
            "compiler_version": "my-compiler-v1",
            "source_spans": [{
                "span_id": "identity-source",
                "start": 0,
                "end": len(source),
                "quote": source,
            }],
            "claims": [{
                "claim_id": "patient-identity",
                "kind": "identity",
                "statement": "Lumi is patient.",
                "activation_tier": "foundation",
                "basis": "explicit",
                "source_span_ids": ["identity-source"],
            }],
        },
    )
    engine.decide_persona_compilation(
        "agent_lumi",
        "user_chen",
        proposal.proposal_id,
        proposal.revision,
        actor_id="owner",
        decision="approve",
    )

    result = engine.recall_structured({
        "agent_id": "agent_lumi",
        "user_id": "user_chen",
        "query": "How should Lumi respond?",
        "audience": "agent_private",
        "options": {
            "persona_delivery": "planned",
            "reinforce": False,
            "budget": {"max_cost": 8192},
        },
        "temporal_context": {
            "world_time": {
                "clock_id": "story-v1",
                "display_value": "the third day of winter",
            }
        },
    })
    prompt_context = engine.render_recall(result)
```

关系未初始化时，结构化召回会明确返回 `uninitialized`，继续提供旧记忆投影，但绝不会因此创建默认人设。结构化召回默认只读；只有 `reinforce=True` 才会强化最终通过预算的 MemoryNode。`public` 受众会在组装阶段排除人设原文、内部独白、关系数值和默认私有关系事件，不能依靠 Renderer 临时隐藏。

人设交付有两种模式：

- `planned`（默认）：使用获批 Manifest，始终携带 Foundation 与其形成性依赖闭包，再按当前问题选择 Situational/Reference 内容；
- `full`：显式携带完整 Character Blueprint 原文，作为服从宿主安全、授权、隐私与工具策略的角色材料。

每段关系还可在初始化时显式选择 `fresh`、`address_only` 或 `canonical_continuation`。默认 `fresh` 不继承原作关系；`address_only` 只继承称呼；`canonical_continuation` 必须提供有原文区间证据的 Premise Experience，并由版本化 Premise Policy 把定性等级确定映射为该关系独有的 Baseline。不同用户始终是不同世界线。

REST 客户端可调用 `POST /api/v1/recall/structured`；旧 `POST /api/v1/recall` 与 `ERIIEngine.recall()` 仍返回兼容 Markdown，并保留自动强化行为。

## 使用 SQLite 保存记忆

当前默认记忆驱动仍是 JSON FileStorage。需要 SQLite 时应显式配置：

```python
from erii import ERIIEngine, SQLiteStorage

storage = SQLiteStorage(db_path="./memory.db")

with ERIIEngine(storage_driver=storage) as engine:
    ...
```

SQLite 驱动启用 WAL，并使用参数化 SQL。`v0.4.0` 计划将 SQLite 提升为默认权威存储，将 JSON 定位为交换、备份与调试格式。

## 独白与未完成事件

```python
node = engine.remember_thought(
    agent_id="agent_lumi",
    user_id="user_chen",
    content="我们约好明天再继续讨论这次旅行。",
    visibility="internal_monologue",
    is_unresolved=True,
    emotional_score=0.6,
)

internal = engine.get_inner_monologue(
    agent_id="agent_lumi",
    user_id="user_chen",
    visibility="internal_monologue",
)

engine.resolve_thought("agent_lumi", "user_chen", node.node_id)
```

`visibility` 是存储和查询过滤字段，不是身份认证或访问控制。将 E.R.I.I. 暴露为网络服务时，调用方必须自行实现授权。

## MemoryPack 导入导出

```python
pack = engine.export_memory(
    agent_id="agent_lumi",
    user_id="user_chen",
    export_path="./lumi-memory.json",
)

engine.import_memory("./lumi-memory.json", overwrite=False)
```

MemoryPack `0.4.0` 会携带人设快照、稳定关系档案和追加式关系事件；事件按 `event_id` 幂等导入。旧版体验时间线仍可能在重复导入时追加重复项。在依赖它处理重要数据前，请保留原始存储备份并验证导入结果。

## 混合召回

```python
from erii import ERIIEngine, InMemoryVectorStore

engine = ERIIEngine(
    vector_store=InMemoryVectorStore(),
    embedding_provider=lambda text: your_embedding(text),
)
```

当前关键词通道使用词元重合度排名，不是完整 BM25。启用向量组件后，系统通过 Reciprocal Rank Fusion 合并关键词排名与向量排名，再乘以记忆的动态有效权重并应用类型配额。

## REST 服务

```bash
erii serve --host 127.0.0.1 --port 8000 --storage-dir ./erii_memory
```

导入 `erii.server.app` 不会立即创建 Engine、数据库或后台线程。CLI 启动或首次 API 请求时才会初始化服务 Engine。

主要端点：

- `GET /api/v1/health`
- `POST /api/v1/remember`
- `POST /api/v1/recall`
- `GET|POST /api/v1/core_memory`
- `GET /api/v1/memory/monologue`
- `POST /api/v1/memory/thought`
- `PATCH /api/v1/memory/thought/{node_id}/resolve`
- `POST /api/v1/memory/export`
- `POST /api/v1/memory/import`
- `GET /api/v1/tasks/status`
- `POST /api/v1/tasks/retry-failed`

该服务是参考适配层，不包含认证、租户权限、限流或完整的数据加密方案，不应未经加固直接暴露到公网。

## 安全与隐私边界

E.R.I.I. 会过滤一组已知 Prompt 注入模式，并掩码常见邮箱、电话号码和 API Key 形式。这是纵深防御，不是完整安全边界：

- 不要把密钥写进人设、对话或记忆；
- 不要在日志中打印真实对话和 LLM 原始响应；
- 对 REST API 增加自己的认证与授权；
- SQLite 和 JSON 默认是明文文件；
- 上传到远程 LLM 前，应向用户说明数据会离开本地环境；
- 删除或导出敏感记忆后，应检查备份、副本和派生数据。

## 开发与测试

```bash
python -m unittest discover -s tests -v
python -m compileall -q erii examples tests
```

仓库的测试覆盖存储、衰减、召回、任务队列、MemoryPack、时间锚定、独白可见性、安全过滤、显式服务生命周期、关系隔离、人设不可变性、事件幂等、证据裁决、历史重处理、人格成长和投影重建。

## 路线图

### v0.4.0a1 — 无 LLM 关系人格内核

- 原始人设快照与宿主提供的结构化编译；
- 每段关系独立的人格实例与稳定 ID；
- 追加式历史事件和当前认知投影；
- 可解释、有限幅度的关系状态演化；
- SQLite Schema 迁移和 MemoryPack 携带；
- 显式后台处理生命周期。

### v0.4.0a2 — 候选提取与规则裁决

- Pydantic 候选、来源 turn 和独立 Inner Review Schema；
- 最小可核验证据、最小拒绝回执和候选级原子持久化；
- 定性信号、版本化关系策略、确定性状态变化与全局限幅；
- 技术幂等、底层经历去重、佐证与显式历史重处理；
- Persona Reflection、积累型/转折型成长提案和宿主安全决定；
- SQLite Schema v2 与跨 Adapter MemoryPack 携带。

### v0.4.0 后续 alpha

- 结构化 RecallResult 与可替换 Prompt Renderer；
- recorded、occurred 与 world time；
- 到期承诺、未完成事件信号和分层巩固。

Web UI、多 Agent 共享图、托管平台和主动消息发送不属于 `v0.4.0` 范围。

## 维护与贡献

项目目前由单人维护。Issue 和 Pull Request 请包含真实使用场景、最小复现、预期行为和测试。新增第三方框架或存储适配器前，需要先说明长期维护责任。

核心记忆内核、数据格式和用户数据可携带能力计划长期保持开放。官方仓库不会内置或分发未经授权的现有作品角色、人设或训练数据。

## License

[Apache License 2.0](LICENSE)

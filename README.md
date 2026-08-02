# E.R.I.I.

> Experiential Recall & Impression Integration — 让情感型 Agent 保留共同经历，并维持连续的人格与关系。

[![License](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](LICENSE)
[![Python](https://img.shields.io/badge/Python-3.9%2B-green.svg)](https://www.python.org/)
[![Version](https://img.shields.io/badge/version-0.4.0a8-orange.svg)]()

E.R.I.I. 是一个可嵌入 Python 应用的长期记忆引擎，主要面向 AI 伴侣、虚拟角色和叙事型 Agent。

普通检索系统关心“哪些文本与问题相似”；E.R.I.I. 更关心：

- Agent 与用户共同经历过什么；
- Agent 现在如何理解这些经历；
- 哪些承诺、情绪和未完成事件仍在影响双方关系；
- 如何在不轻易破坏角色底色的前提下，让长期互动留下痕迹。

项目目前处于 `0.x` 实验阶段，由单人维护。API 与存储模型仍会演进，不提供商业级 SLA。我们会优先保护记忆数据的可导出性，并为破坏性数据升级提供迁移路径。

## 从这里开始

- **第一次接入：** [完整中文使用手册](docs/USAGE_zh-CN.md) / [Complete English User Guide](docs/USAGE.md)，从安装、十分钟示例到真实聊天循环；
- **直接运行：** [`examples/`](examples/) 中包含 FileStorage、SQLite、关系人格、结构化召回和时间承诺示例；
- **准备贡献：** [CONTRIBUTING.md](CONTRIBUTING.md)；
- **处理真实数据前：** [SECURITY.md](SECURITY.md)。

## 当前版本能够做什么

`v0.4.0a8` 已实现：

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
- `begin_turn()`、`complete_turn()`、`abandon_turn()` 与原子便捷入口 `record_turn()` 共享同一份持久 Turn Record 账本；
- 完整保留双方实际可见的 User/Agent Source Transcript，并区分 `open`、`completed` 与 `abandoned` 生命周期；
- `get_turn()`、`list_turns()` 与不携带对话原文的 `SourceTurnReceipt`；
- 显式、版本化的 `MemoryExtractorV1`，把已完成 Source Turn 严格裁决为 `artifacts` 或 `no_memory`；
- `archive_turn()`、关系范围内的持久归档回执、幂等键绑定、租约恢复与宿主显式 `process_pending()` / `drain()`；
- FileStorage 与 SQLiteStorage 的 MemoryNode、结构化 Timeline 和终态回执原子批次提交；
- 不可静默覆盖的原始人设快照和可选结构化编译结果；
- 追加式关系事件、幂等事件 ID 与可重建的当前认知；
- 熟悉、信任、亲密、安全感与冲突张力的有限幅度状态投影；
- 每项当前状态对应的事件证据和叙事解释；
- 关系档案与事件的 MemoryPack 携带能力；
- 不可信候选的 Pydantic Schema、精确证据核验和逐候选裁决；
- LLM 定性关系信号到五维状态的确定性、有界映射；
- 技术重试幂等、底层经历去重、历史佐证和显式历史重处理；
- accepted Relationship Event 之后独立、不可变的 Persona Reflection，以及积累型/转折型人格成长提案；
- 宿主在对话外按提案版本批准、拒绝或撤销人格成长；
- 证据、裁决回执和人格成长提案的 MemoryPack 携带能力；
- 显式观察但不暗改关系状态的时间上下文；
- 显式 Persona Compiler、不可变 Proposal revision 与精确 Manifest 审批；
- `fresh`、`address_only`、`canonical_continuation` 关系前提及确定性 Baseline；
- `RecallResult`、显式受众、World Time、完整投影预算和可替换 Renderer；
- 默认只读的结构化召回，以及只强化最终入选记忆的显式模式；
- 类型化 Promise、Promise Condition、Open Loop 与追加式 Resolution；
- 同一 World Time 时钟内派生的到期/逾期承诺和开放事项召回信号；
- `process_relationship_turn()` 自动编排严格关系提取、确定性裁决与后置反思，并持久冻结同源重试决定；
- 合法 `no_relationship_event` / `no_reflection`、反思局部失败与正式 Reflection Record 的独立可查询结果；
- 只使用显式分组证据、可通过 History Fingerprint 重建的 Episode 与 Relationship Chapter；证据不足的事件不会被丢弃；
- 五轴回复连续性评估，以及由带来源情境信号激活、不会继承另一段关系亲密度的 Contextual Voice Pattern；关系安全由当前 Relationship Snapshot 确定性分档，情绪只能由可选的严格 `InteractionContextEvaluatorV1` 在当前关系/Turn 证据内提出；
- 每个现代完成 Turn 原子绑定版本化 Context Baseline、`ContinuityReviewRecord` 与必要的 `DeliveryExceptionRecord`，审查依据使用关系范围内的类型化引用；
- 被最终语气判断实际采用的 Contextual Voice 激活投影为不可重放、不会回灌 Prompt 的审计 Trace；
- schema `"2"` 归档候选必须携带可由 Source Transcript 精确核验的 Unicode 消息区间，持久产物只保存内核解析后的证据引用；
- 召回将记忆投影区分为 `ordinary | legacy_context | quarantined_history`：Public 只使用 Ordinary，Agent-private 分区显示 Legacy，Legacy 不参与强化；
- `overridden | shown_unreviewed` 的 Agent 消息仍永久保留为真实历史，但引用它的关系候选以 `continuity_exception_agent_evidence_quarantined` 正常拒绝；同轮 User-only 候选继续处理；
- SQLite Schema v9 与 FileStorage 可靠归档/关系处理账本；MemoryPack `0.4.0a8` 携带结构化 Timeline、带产物内容承诺的精确归档证据闭包、正式反思、可恢复的持久关系处理 run，以及绑定持久 Turn 的 direct adjudication 证据闭包；
- 由宿主显式控制的后台归档生命周期。

当前版本尚未实现：

- 完整的授权、加密或多租户安全边界。

这项产品安全能力属于后续路线；关系范围校验不应被理解为已经交付认证或租户隔离。

## 安装

当前 alpha 建议从 GitHub 源码安装：

```bash
git clone https://github.com/bailong-Hakuryu/E.R.I.I.git
cd E.R.I.I
python -m pip install .

# 按需安装 REST、宿主 OpenAI SDK 或向量扩展
python -m pip install ".[server]"
python -m pip install ".[openai]"
python -m pip install ".[vector]"

# 贡献代码时使用可编辑开发安装
python -m pip install -e ".[dev]"
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

## Turn Recording 来源账本

`0.4.0a5` 为一轮真实交互增加了唯一、关系范围内的 Turn Record。它保存双方实际可见的原文，是后续记忆归档与关系裁决共同引用的来源证据，但不会仅凭“说过这句话”就自动生成 MemoryNode、关系事件或人格变化。

关系必须先通过 `initialize_relationship()` 初始化。生成回复前先保存 User 消息，展示回复后再封存同一轮：

```python
from datetime import datetime, timezone

from erii import DeliveryExceptionRecord


opened = engine.begin_turn(
    "agent_lumi",
    "user_chen",
    "我们今天去看雪吗？",
    turn_id="turn-first-snow-001",
    interaction_context=(
        {
            "signal_id": "context-location",
            "source": "host_observed",
            "signal_type": "location",
            "value": "东京街头",
        },
    ),
)

# reply 由宿主自己的聊天模型生成并实际展示给用户。
reply = "好，我们一起去。"
delivery_exception = DeliveryExceptionRecord(
    disposition="shown_unreviewed",
    actor_kind="host_policy",
    actor_id="my-app.delivery-policy/v1",
    reason_code="availability_fallback",
    decided_at=datetime.now(timezone.utc).isoformat(),
)

receipt = engine.complete_turn(
    "agent_lumi",
    "user_chen",
    opened.turn_id,
    reply,
    delivery_disposition="shown_unreviewed",
    delivery_exception=delivery_exception,
    # 只声明当前确实要承担的派生处理通道。
    processing_channels=(),
)
```

如果宿主已经同时持有双方可见消息，可以一次性原子记录：

```python
preexisting_exception = DeliveryExceptionRecord(
    disposition="shown_unreviewed",
    actor_kind="host_policy",
    actor_id="my-app.import-policy/v1",
    reason_code="preexisting_visible_exchange",
    decided_at=datetime.now(timezone.utc).isoformat(),
)
receipt = engine.record_turn(
    "agent_lumi",
    "user_chen",
    "雪已经开始下了。",
    "那这就是我们一起看的第一场雪。",
    turn_id="turn-first-snow-002",
    delivery_exception=preexisting_exception,
    processing_channels=(),
)

turn = engine.get_turn("agent_lumi", "user_chen", receipt.source_turn_id)
completed = engine.list_turns("agent_lumi", "user_chen", status="completed")
```

`SourceTurnReceipt` 只报告 `source_turn_id`、relationship、revision、接受时间、固定处理计划与各通道状态，不回显 User/Agent 原文；需要查看原文时必须在同一 `Agent × User` 范围内调用 `get_turn()`。上面的基础例子明确承认没有成功连续性审查，因此使用 `shown_unreviewed`；普通 `shown` 必须携带绑定当前 Turn 与最终回复的完整 `ContinuityEvaluationResult`。`record_turn()` 只能记录 `preexisting_visible_exchange`。宿主应持久并复用同一个 Delivery Exception，才能让相同 `turn_id` 与相同载荷安全重试；复用 ID 却改变消息或终态会抛出冲突。

`interaction_context` 只接受宿主实际观察到的临时情境，不能冒充由内核或评估器推导的关系状态。内核产生的关系安全与评估器提出的情绪信号都绑定当前 `relationship_id + turn_id + producer_version`，仅用于本轮表达选择；旧版未绑定的派生标签可读但不能激活语气。可重试的生成或评估失败应让 Turn 保持 `open`，并且只记录脱敏后的失败元数据，不保存未展示草稿：

```python
engine.record_reply_attempt_failure(
    "agent_lumi",
    "user_chen",
    opened.turn_id,
    attempt_number=1,
    stage="generation",
    capability_descriptor="my-provider/model-v1",
    failure_classification="temporary_provider_error",
)
```

只有用户取消、宿主明确终止或不可恢复错误才调用：

```python
engine.abandon_turn(
    "agent_lumi",
    "user_chen",
    opened.turn_id,
    reason="user_cancelled",
)
```

`abandoned` 会保留真实 User 消息，但不会伪造 Agent 回复，也没有处理计划。隐藏 system 消息、完整 Prompt、模型推理和双方不可见的工具输出不属于 Source Transcript。

## 可靠归档

`0.4.0a6` 可以把一条已完成的 Source Turn 可靠地派生为可召回 MemoryNode 与结构化 Timeline。归档器必须由宿主显式提供，并以不含密钥、Prompt 或用户正文的 `ExtractorDescriptor` 声明版本：

```python
from datetime import datetime, timezone

from erii import (
    ArchivalEvidenceCitation,
    ArchivalArtifactsDecision,
    DeliveryExceptionRecord,
    ERIIConfig,
    ERIIEngine,
    ExtractorDescriptor,
    MemoryCandidate,
    MemoryType,
    TimelineCandidate,
)


class MyMemoryExtractor:
    descriptor = ExtractorDescriptor(
        extractor_id="my-app.memory-extractor",
        extractor_version="1.0",
        extraction_schema_version="2",
    )

    def extract(self, request):
        # 真实实现可以在这里调用宿主选择的模型，然后校验并转换结果。
        user_message = request.transcript.user_message
        evidence = (
            ArchivalEvidenceCitation(
                source_id=user_message.message_id,
                source_revision=request.source_revision,
                quote=user_message.content,
                start=0,
                end=len(user_message.content),
            ),
        )
        return ArchivalArtifactsDecision(
            timeline=(
                TimelineCandidate(
                    content="用户提出一起去游戏厅。",
                    evidence=evidence,
                ),
            ),
            memories=(
                MemoryCandidate(
                    node_type=MemoryType.PREFERENCE,
                    content="用户希望一起去游戏厅。",
                    tags=("arcade", "shared-experience"),
                    evidence=evidence,
                ),
            ),
        )


config = ERIIConfig(
    storage_dir="./erii_memory",
    async_archival=False,  # 在 archive_turn() 当前调用中同步完成。
)
with ERIIEngine(config=config, memory_extractor=MyMemoryExtractor()) as engine:
    engine.initialize_relationship(
        "agent_lumi",
        "user_chen",
        persona_source="Lumi 是一个温柔、坦诚的原创角色。",
    )
    source = engine.record_turn(
        "agent_lumi",
        "user_chen",
        "我们去游戏厅吧。",
        "好，我还想再玩一局。",
        turn_id="turn-arcade-001",
        delivery_exception=DeliveryExceptionRecord(
            disposition="shown_unreviewed",
            actor_kind="host_policy",
            actor_id="my-app.import-policy/v1",
            reason_code="preexisting_visible_exchange",
            decided_at=datetime.now(timezone.utc).isoformat(),
        ),
    )
    receipt = engine.archive_turn(
        "agent_lumi",
        "user_chen",
        source.source_turn_id,
        idempotency_key="archive-turn-arcade-001",
    )
    print(receipt.status, receipt.outcome_code)
```

提取器也可以返回 `ArchivalNoMemoryDecision(reason_code="ordinary_acknowledgement")`，明确表示这轮没有值得长期保存的新内容；它仍是成功终态，不会生成占位记忆。相同关系中的相同幂等键和请求可以安全重试，若把同一个键绑定到另一条 Source Turn 则会冲突。

当 `async_archival=True` 时，`archive_turn()` 只持久化并返回 `pending`，不会启动隐藏处理。宿主随后调用 `process_pending()` 逐批消费，或在停机/检查点前调用 `drain(timeout=...)` 处理调用时可见的任务快照。回执只包含 ID、状态、版本化提取器描述、计数和安全结果，不包含 Source Transcript；读取原文仍必须通过同一 `Agent × User` 范围内的 `get_turn()`。

完整终态回执默认保留 30 天（`archival_receipt_retention_days`），到期后可由 `compact_archival_receipts()` 压缩为最小 tombstone。压缩不会删除已经提交的记忆或结构化 Timeline，也不会破坏幂等重试。由现代带指纹回执生成的 tombstone 会保留不含产物正文的 `artifact_commitments`，逐项绑定产物类型、稳定 ID 与规范载荷的 SHA-256；召回会重算当前产物指纹，并连同 Source revision、已完成 Source Turn 和精确消息证据一起核验。因此完整回执消失后，运维详情和完整来源投影会如实降为部分来源，但精确产物内容承诺仍可让未被改写的 schema `"2"` 产物保持普通召回权威。旧墓碑若没有 commitments，只能保留幂等与审计身份，不能认证当前产物载荷。

`remember()` 仍是兼容旧集成的 Prompt/JSON 归档入口，但不会自动建立规范 Source Turn 的来源关系。新集成应优先使用 `record_turn()`（或 `begin_turn()` → `complete_turn()`）再调用 `archive_turn()`。完整说明见[中文使用手册](docs/USAGE_zh-CN.md#可靠归档从-source-turn-生成长期记忆)和 [English guide](docs/USAGE.md#reliable-archival-derive-long-term-memory-from-a-source-turn)。

## 自动关系处理与分层巩固

`0.4.0a7` 的规范关系路径不再要求宿主先手工拼候选：

```text
completed Source Turn
  → RelationshipEventExtractorV1: candidates | no_relationship_event
  → 持久冻结完整提取决定
  → 确定性证据裁决
  → accepted Relationship Event（权威追加历史）
  → PersonaReflectionInterpreterV1: reflection | no_reflection
  → Episode / Relationship Chapter（可重建投影）
```

在构造 `ERIIEngine` 时注入版本化 `relationship_event_extractor`，需要角色内心解释时再注入 `persona_reflection_interpreter`。Turn 的固定处理计划必须包含 `relationship_adjudication`，随后由宿主显式调用：

```python
run = engine.process_relationship_turn(
    "agent_lumi",
    "user_chen",
    receipt.source_turn_id,
)

same_run = engine.get_relationship_processing_run(
    "agent_lumi",
    "user_chen",
    run.processing_id,
)
reflections = engine.list_persona_reflections("agent_lumi", "user_chen")
consolidation = engine.get_relationship_consolidation(
    "agent_lumi",
    "user_chen",
)
```

自动提取只能返回中性事实、精确 Evidence 和定性信号，不能携带 `persona_reflection` 或人格成长意图。同一来源、revision 与处理身份的重试直接恢复已经冻结的决定，不会重新调用模型扩张历史；FileStorage 与 SQLiteStorage 还会跨 Engine 实例/进程串行化首次外部调用。已有 run 在重启后无需重新配置 extractor 即可读取或继续；若该 run 已冻结“需要反思”，恢复时仍须提供解释器，不能静默降级。显式复核旧来源必须使用 `processing_mode="historical_reprocessing"` 与新的稳定 `reprocessing_id`。

每个 run 还会冻结裁决开始时两本追加日志的高水位与内容指纹：可信宿主直写的 Relationship Event journal，以及候选裁决 journal。续跑只使用这份基线，并在同一批次内按依赖解析顺序加入新事件；不会用 `recorded_at` 猜测“当时哪些事件已经存在”。MemoryPack 同时携带 direct-event journal 顺序，因此导入可以用同一裁决器精确重放 `accepted`、`corroborated`、`rejected` 与 `ignored`，并拒绝对回执或事件结果的自洽伪造。高水位是每个 run 的常量级元数据，不会复制整段历史。

事件一旦被接受，后续反思失败也不会撤销它。合法 `no_reflection` 不创建占位内心独白；Correction 与 Reinterpretation 会追加新反思并引用旧 `reflection_id`，不会覆盖角色当时真实拥有的理解。同一 target/kind 下复用 `interpretation_id` 是幂等重试，使用新 ID 才会追加下一次理解。

Relationship Event 始终是权威历史。Episode 只在稳定发生身份或类型化时间链等显式证据支持时分组，Relationship Chapter 还需要显式跨情节引用；证据不足的事件保留在 `unconsolidated_event_ids`。这些结果带 `history_fingerprint`、可随策略升级重建，也不会自行推动关系状态。

完整的提取器、反思解释器、重试、查询和携带示例见[中文使用手册](docs/USAGE_zh-CN.md#自动关系处理从-source-turn-到-event-reflection-与-consolidation)与 [English guide](docs/USAGE.md#automatic-relationship-processing-from-source-turn-to-event-reflection-and-consolidation)。

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

上例是 `0.4.x` 保留的旧式手工候选入口：宿主提供完整 Source Turn 供精确引文校验，裁决记录只保留最小证据；该调用本身不会创建或替换 Turn Record。若同一关系中已经存在相同 `turn_id` 的 completed Turn，a8 会要求 revision、消息 ID、角色、正文与发生时间逐项精确匹配，再以 `relationship-turn-adjudication-v1` 保存裁决并应用该 Turn 的交付权威；任一字段不符都会失败关闭。只有确实不存在持久 Turn 时才沿用 transient Legacy 行为，而且一旦该 Turn ID 被 transient 裁决使用，之后不能再把它注册成规范 Turn 来追授现代权威。新集成应先取得稳定 `source_turn_id`，优先调用 `adjudicate_turn_candidates()` 或自动关系处理路径。

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

关系未初始化时，结构化召回会明确返回 `uninitialized`，继续提供旧记忆投影，但绝不会因此创建默认人设。结构化召回默认只读；只有 `reinforce=True` 才会强化最终通过预算且权威级别为 `ordinary` 的 MemoryNode。

每个 Memory 投影都带有独立于 Memory Type 的 `authority_tier`。`ordinary` 具有完整现代消息证据和合格交付路径；`legacy_context` 保留 pre-a8 记忆的连续性，但只进入 Agent-private 的 `Legacy Context - provenance incomplete` 分区且永不强化；`quarantined_history` 仍可检查、导出和删除，但不进入默认生成 Prompt。`public` 受众只接收 Ordinary，并在组装阶段排除人设原文、内部独白、关系数值和默认私有关系事件，不能依靠 Renderer 临时隐藏。MemoryNode 只经过一次关键词/向量 RRF 与动态权重排序；权威分类先把 Ordinary、Legacy 与 Quarantined 分开，再对可用分区应用 `max_per_type`，因此高排名 Legacy 不会提前耗尽 Ordinary 的类型配额，选择器也不会用第二套词法相关性重排上游 hybrid 顺序。

人设交付有两种模式：

- `planned`（默认）：使用获批 Manifest，始终携带 Foundation 与其形成性依赖闭包，再按当前问题选择 Situational/Reference 内容；
- `full`：显式携带完整 Character Blueprint 原文，作为服从宿主安全、授权、隐私与工具策略的角色材料。

每段关系还可在初始化时显式选择 `fresh`、`address_only` 或 `canonical_continuation`。默认 `fresh` 不继承原作关系；`address_only` 只继承称呼；`canonical_continuation` 必须提供有原文区间证据的 Premise Experience，并由版本化 Premise Policy 把定性等级确定映射为该关系独有的 Baseline。不同用户始终是不同世界线。

REST 客户端可调用 `POST /api/v1/recall/structured`；旧 `POST /api/v1/recall` 与 `ERIIEngine.recall()` 仍返回 Markdown 字符串并保留自动强化请求，但同样只能强化最终预算内的 Ordinary MemoryNode。为了不破坏旧集成，兼容 `recall()` 还会把 `set_core_memory()` 保存的 Core Memory 作为带 `legacy_context` 标签的兼容上下文放在动态 `top_k` 之外；它仍受同一硬成本预算约束，也不会因此获得现代 Persona 或来源权威。`recall_structured()` 没有这个额外槽位。

## 时间承诺与开放事项

`0.4.0a4` 将“已经明确承担责任的承诺”和“尚待继续、但没有人明确承担责任的事项”分开建模。可信宿主可直接使用类型化 API：

```python
from erii import ERIIEngine, PromiseResponsibleParty, WorldMoment

with ERIIEngine(storage_dir="./erii_memory") as engine:
    engine.initialize_relationship(
        "agent_lumi",
        "user_chen",
        "Lumi is patient and treats commitments seriously.",
    )
    promise = engine.record_promise(
        "agent_lumi",
        "user_chen",
        "bring the revised travel plan",
        (PromiseResponsibleParty.AGENT,),
        due_at=WorldMoment("story-day", "day 3", order_value=3),
    )
    open_loop = engine.record_open_loop(
        "agent_lumi",
        "user_chen",
        "Choose the destination together",
        expected_continuation="Ask which city feels right.",
    )

    engine.resolve_promise(
        "agent_lumi", "user_chen", promise.event_id, "fulfilled"
    )
    engine.resolve_open_loop(
        "agent_lumi", "user_chen", open_loop.event_id, "completed"
    )
```

原始 Promise/Open Loop 不会被原地修改；条件确认和解决结果都是引用早期事件的新关系事件。带条件的 Promise 只有在追加明确的 Condition Confirmation 后才会产生信号。对于截止时间，只有观察时间和截止时间的 `clock_id` 相同且双方 `order_value` 都是有限数值时才比较：小于截止值不产生信号，等于时为 `promise_due`，大于时为 `promise_overdue`。逾期只是只读的时间信号，不代表违约，也不会自动扣减信任或写入新事件。

这些信号由 `recall_structured()` 在 Agent Private 结果中派生，不会因读取而改变历史。旧 `MemoryNode.is_unresolved` 仍可成为低权威 Open Loop 信号，但不等同于正式关系义务；可用 `origin_memory_node_id` 将它显式提升为正式 Open Loop，并避免重复投影。完整可运行流程见 [`examples/08_temporal_commitments.py`](examples/08_temporal_commitments.py)。

`record_promise()` 等直接 API 只适用于可信宿主输入。不可信 LLM 输出必须作为带来源引文的 Candidate 进入 `adjudicate_relationship_candidates()`，REST 对应 `POST /api/v1/relationship/adjudicate`；参考服务不提供绕过证据裁决的 Promise/Open Loop CRUD。

## 使用 SQLite 保存记忆

当前默认记忆驱动仍是 JSON FileStorage。需要 SQLite 时应显式配置：

```python
from erii import ERIIEngine, SQLiteStorage

storage = SQLiteStorage(db_path="./memory.db")

with ERIIEngine(storage_driver=storage) as engine:
    ...
```

SQLite 驱动启用 WAL，并使用参数化 SQL。`0.4.0a8` 会把已有数据库原地迁移到 Schema v9：除 `source_turns` 与可靠归档数据外，还保存关系处理运行、合法零产物、反思决定、正式反思记录，以及现代 Turn 的审查、例外和冻结上下文。FileStorage 提供相同的关系范围与幂等语义。两者都不是授权、加密或多租户边界。

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

MemoryPack `0.4.0a8` 在既有 `turn_records`、结构化 Timeline 与归档 tombstone 之外，携带正式 Persona Reflection、direct-event journal 顺序和全部持久 Relationship Processing Run，包括可恢复的非终态/partial 阶段与冻结决定。每个 schema `"2"` 产物必须携带其精确 Source Turn 依赖闭包，并匹配对应 tombstone 的 `artifact_commitments` 中由类型、稳定 ID 与规范载荷 SHA-256 组成的一项；导入会在第一次写入前重算产物指纹、消息角色、revision、消息 SHA-256、Unicode 区间与 Evidence ID。绑定持久 Turn 的 direct adjudication 使用 `relationship-turn-adjudication-v1`；导入会复核其完整 Source Turn、证据身份和异常 Agent 必须保持拒绝的不变量，即使有人只把 receipt 的 contract 字段降级、却仍保留对应 Turn，也不会绕过复核。这里没有 frozen candidate，因此 direct import 不声称能够完整重放普通 accepted Event；旧 transient 记录继续以 Legacy 形式可读，也不会仅因导入而获得规范 Turn 权威。这样换用 FileStorage/SQLite 后仍能保留 accepted/no-event/no-reflection 结果、普通重试身份和续跑位置；这些记录不复制完整 Prompt、人设或 Source Transcript。导出、精确身份导入和在线关系处理共用同一个 relationship guard；导入保持 direct-event/adjudication 两本日志各自的追加顺序，并在写入任何普通记忆字段前核验完整不可变 Relationship/Blueprint 身份、精确 Source Turn、Timeline 稳定 ID、目标与 incoming 合并后的时间生命周期、反思唯一裁决来源、Manifest、已批准成长及目标已有账本冲突。Episode 和 Relationship Chapter 是可重建投影，不进入 Pack。

因为完整来源、反思和处理账本属于特定关系，含这些数据的 Pack 只允许恢复到原来的 `Agent × User` 与 `relationship_id`；传入另一组 Agent/User ID 会被拒绝，`overwrite=True` 也不能绕过该边界。旧 Pack 仍可读取，但迁移不会为缺失来源补造当时上下文。在依赖它处理重要数据前，请保留原始存储备份并验证导入结果。这里的指纹与重放用于检查 Pack 的结构、因果和内部自洽性，并不认证 Pack 的真实来源：能够整体改写未签名 Pack 的一方也能删除 Turn、同步降级关联 contract 并重新计算未加密指纹。正式产品仍需在内核外为导出文件增加签名或 MAC、加密、授权与密钥管理。

## 混合召回

```python
from erii import ERIIEngine, InMemoryVectorStore

engine = ERIIEngine(
    vector_store=InMemoryVectorStore(),
    embedding_provider=lambda text: your_embedding(text),
)
```

当前关键词通道使用词元重合度排名，不是完整 BM25。启用向量组件后，系统通过 Reciprocal Rank Fusion 合并关键词排名与向量排名，再乘以记忆的动态有效权重。召回权威选择器保留这份上游顺序，先分类 `ordinary | legacy_context | quarantined_history`，再在各可用权威分区中应用类型配额；它不会用另一套词法评分重排 hybrid 结果。

## REST 服务

默认使用单一服务所有者 API Key 启动参考服务：

```powershell
$env:ERII_API_KEY = python -c "import secrets; print(secrets.token_urlsafe(32))"
```

```bash
erii serve --host 127.0.0.1 --port 8000 --storage-dir ./erii_memory
```

所有业务请求都要携带 `X-API-Key`；`/api/v1/health`、Swagger UI 与 OpenAPI 文档可直接访问，在 Swagger 的 **Authorize** 中填入同一个 Key 即可调试。若只做本机临时开发，也可以显式使用 `--allow-unauthenticated-loopback`，该模式不能绑定非回环地址，也绝不能放在反向代理后面——代理可能让远程请求在应用看来来自回环地址。

导入 `erii.server.app` 不会立即创建 Engine、数据库或后台线程。CLI 启动或首次 API 请求时才会初始化服务 Engine。

主要端点：

- `GET /api/v1/health`
- `POST /api/v1/turns/open`
- `POST /api/v1/turns`
- `POST /api/v1/turns/{turn_id}/complete`
- `POST /api/v1/turns/{turn_id}/continuity/evaluate`
- `POST /api/v1/turns/{turn_id}/abandon`
- `GET /api/v1/turns/{turn_id}`
- `GET /api/v1/turns`
- `POST /api/v1/remember`
- `POST /api/v1/recall`
- `POST /api/v1/recall/structured`
- `POST /api/v1/relationship/adjudicate`
- `GET|POST /api/v1/core_memory`
- `GET /api/v1/memory/monologue`
- `POST /api/v1/memory/thought`
- `PATCH /api/v1/memory/thought/{node_id}/resolve`
- `POST /api/v1/memory/export`
- `POST /api/v1/memory/import`
- `GET /api/v1/tasks/status`
- `POST /api/v1/tasks/retry-failed`

Turn 的 open、abandon、get 与 list 响应会按关系范围返回 Turn Record；complete 与一次性 record 返回不含对话原文的 `SourceTurnReceipt`。这些路由要求目标关系已经初始化，且所有查询都必须同时提供匹配的 `agent_id` 与 `user_id`。关系范围不是授权机制，宿主仍需自行鉴权。

该 Key 只代表“服务所有者”，可以访问服务中的全部 Agent × User 范围，不是多租户身份或用户授权。参考服务仍不包含租户权限、限流、TLS 或完整的数据加密方案，不应未经加固直接暴露到公网。

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

仓库的测试覆盖存储、衰减、召回、任务队列、MemoryPack、时间锚定、独白可见性、安全过滤、显式服务生命周期、关系隔离、人设不可变性、事件幂等、证据裁决、冻结重试、独立反思、历史重处理、人格成长、五轴连续性、情境语气、时间承诺、分层投影重建，以及 a8 的精确归档证据、召回权威分层、异常 Agent 候选隔离和 REST/MemoryPack 往返。

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

### v0.4.0a3 — 人设感知结构化召回

- 结构化 RecallResult 与可替换 Prompt Renderer；
- recorded、occurred 与 world time；
- Persona Compiler、审批 Manifest、关系前提和定性 Baseline；
- SQLite Schema v3 与 MemoryPack `0.4.0a3`。

### v0.4.0a4 — 时间承诺与开放事项

- 类型化 Promise、Condition Confirmation、Open Loop 和追加式 Resolution；
- 同一 World Time 时钟内的到期/逾期判断与只读召回信号；
- 旧 `is_unresolved` 的低权威兼容投影；
- SQLite Schema 保持 v3，MemoryPack 升级为 `0.4.0a4`。

### v0.4.0a5 — Turn Recording 来源账本

- 两阶段 `begin_turn()` / `complete_turn()` 与显式 `abandon_turn()`；
- 已有双方消息时使用原子 `record_turn()`；
- 可查询、可列举的完整可见 Source Transcript，以及不含原文的 `SourceTurnReceipt`；
- 有来源的 `InteractionContextSignal`、不保存草稿的 Reply Attempt 失败记录，以及基于 `source_turn_id` 的关系候选裁决桥接；
- FileStorage 与 SQLite Schema v4 持久化；
- MemoryPack `turn_records` 精确关系恢复，禁止跨 `Agent × User` 重映射完整对话。

### v0.4.0a6 — 可靠幂等归档

- completed Source Turn 到 Timeline / MemoryNode 的严格 `artifacts | no_memory` 提取；
- 持久回执、冻结批次、租约恢复、原子提交与最小 tombstone；
- FileStorage 与 SQLite Schema v5；MemoryPack 携带结构化 Timeline 与归档 ledger。

### v0.4.0a7 — 自动关系处理与分层巩固

- completed Source Turn 到 frozen extraction、确定性裁决和 accepted Event 后置反思；
- 正式 Persona Reflection / no-reflection 决定、追加式 Correction / Reinterpretation；
- 五轴连续性与有来源的 Contextual Voice Pattern；
- 只用显式分组证据的 Episode / Relationship Chapter，以及诚实保留的未巩固事件；
- SQLite Schema v6 与 MemoryPack 关系处理/反思携带。

### v0.4.0a8 — 连续性审计与发布收口（实现完成，待发布验证）

- 每个现代最终可见回复与 `ContinuityReviewRecord` 原子绑定，只有 `reviewed` 分支包含五轴 `ContinuityReviewReceipt`；
- 类型化、可解析且严格保持 `Agent × User` 范围的连续性依据；
- `shown / overridden / shown_unreviewed` 与现代、失败、未评估、Legacy 审查状态保持可区分；
- 旧版连续性摘要仍可查看和携带，但兼容属性不会把 Legacy 的 `COMPLETED/ALIGNED` 冒充为现代成功 Receipt；
- 被最终语气判断采用的情境表达激活留下不可重放、非干扰 Trace，帮助离线诊断但永不回灌 Prompt 或强化口癖；
- 异常 Agent 发言保留真实历史但不能静默塑造人格，同轮 User 证据不被整轮丢弃；
- 关系候选引用异常 Agent 发言时以带稳定原因码的 `rejected` 正常终结；这是 a8 的最小隔离，不是技术失败或永久否认后果；
- 隔离依据是 `overridden | shown_unreviewed`，不是语气正负；最终文本经过重新审查并以 `shown` 通过后，即使角色拒绝、生气、疏远或伤人，也作为正常角色选择处理；
- 现代 Timeline/Memory 候选携带消息级精确证据，异常 Agent 证据不能混入普通归档；
- `MemoryExtractorV1` 调用接口保持兼容，a8 新归档提交通过显式 `extraction_schema_version="2"` 启用证据感知结果；schema `"1"` 只保留为 Legacy 身份；
- 升级时未提取的 schema `"1"` 工作显式失败并由宿主以新幂等键重提，已冻结的 Commit 继续原子完成但仍标记为 Legacy，不会自动换模型重采样；
- 旧记忆不会因升级被删除：真正 pre-a8 数据以带前端标签的低权威 `legacy_context` 维持兼容召回且不再强化；可证明来自现代异常交付、却缺少消息角色证据的旧产物只保留检查与携带能力，不进入默认生成 Prompt；
- 召回预算随现代记忆积累渐进过渡：保留上游 hybrid/RRF 顺序并在权威分类后应用类型配额；现代不足时 Legacy 填充上下文，现代充足后最多保留一个相关 Legacy 槽位；两类记忆分区显示、精确重复由现代版本胜出；兼容 `recall()` 的 Legacy Core 位于动态 `top_k` 外但仍受硬预算；
- 连续性判定保持情感效价中立，不把温柔当正确或把拒绝、生气当 OOC；
- FileStorage、SQLiteStorage、MemoryPack、旧数据、并发幂等、干净安装与 prerelease 构建收口；
- 这是最后一个承诺支持 Python 3.9 的版本。

以下版本均为公开规划，尚未实现；实际发布必须满足 [ROADMAP.md](ROADMAP.md) 中的退出条件。

### v0.5.0a1 — 关系后果与角色内在审视（计划）

- 使用新的 `historical_reprocessing` 身份读取 a8 冻结候选与拒绝回执，追加处置而不修改旧 Turn 或把旧 `rejected` 改成 `accepted`；
- 追加式双轨连续性例外处置，不重写原 Turn；
- 角色造成的真实后果以追加式关系记录保留，但系统不强制道歉、撤回、原谅或复合；
- 对正常通过审查的伤害性选择直接延续后果；对 a8 隔离发言则以新历史处理身份分别判断人格权威与关系后果，两条判断互不洗白；
- User Stance、Persona Stance 与共同 Relationship Outcome 分离取证；
- Narrative Tension 区分未回应、尝试处理、共同和解、边界稳定、关系终结与替代；
- 重大事件触发角色审视但允许 `stance_unformed`，不制造标准化悔意；
- 角色敏感性同时覆盖威胁、愿望实现、主体性、关系意义与价值张力，所有条目保留人设依据且不虚构平衡。

版本顺序保持为：`0.4.0a8 → 0.4.0b1 →（必要时 b2）→ 0.4.0rc1 → 0.4.0 → 0.5.0a1`。v0.4 Beta/RC 将处理迁移、删除与确定性重建、长期评测、性能基线、兼容和发布验证，不再增加领域功能；v0.5 后续 Alpha 继续推进 Portability 深 Module、窄 Storage Interface、Belief Lineage、Memory Relation 与 Continuity Map。完整认证、授权、加密与多租户安全仍属于 v0.6。

Web UI、多 Agent 共享图、托管平台和主动消息发送不属于 `v0.4.0` 范围。

## 维护与贡献

项目目前由单人维护。Issue 和 Pull Request 请包含真实使用场景、最小复现、预期行为和测试。新增第三方框架或存储适配器前，需要先说明长期维护责任。

核心记忆内核、数据格式和用户数据可携带能力计划长期保持开放。官方仓库不会内置或分发未经授权的现有作品角色、人设或训练数据。

## License

[Apache License 2.0](LICENSE)

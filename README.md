# E.R.I.I.

> Experiential Recall & Impression Integration — 让情感型 Agent 保留共同经历，并维持连续的人格与关系。

[![License](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](LICENSE)
[![Python](https://img.shields.io/badge/Python-3.9%2B-green.svg)](https://www.python.org/)
[![Version](https://img.shields.io/badge/version-0.4.0a1-orange.svg)]()

E.R.I.I. 是一个可嵌入 Python 应用的长期记忆引擎，主要面向 AI 伴侣、虚拟角色和叙事型 Agent。

普通检索系统关心“哪些文本与问题相似”；E.R.I.I. 更关心：

- Agent 与用户共同经历过什么；
- Agent 现在如何理解这些经历；
- 哪些承诺、情绪和未完成事件仍在影响双方关系；
- 如何在不轻易破坏角色底色的前提下，让长期互动留下痕迹。

项目目前处于 `0.x` 实验阶段，由单人维护。API 与存储模型仍会演进，不提供商业级 SLA。我们会优先保护记忆数据的可导出性，并为破坏性数据升级提供迁移路径。

## 当前版本能够做什么

`v0.4.0a1` 已实现：

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
- 由宿主显式控制的后台归档生命周期。

当前版本尚未实现：

- 人格变化提案与人工确认；
- LLM 候选事件的证据校验和规则裁决；
- 事件、情节和关系阶段的分层巩固；
- 关系状态进入结构化召回结果；
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

`compiled_persona` 在 alpha.1 由宿主提供并与原文一同保存；LLM 编译、候选校验和重大人格变更提案属于后续 alpha。

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

仓库的测试覆盖存储、衰减、召回、任务队列、MemoryPack、时间锚定、独白可见性、安全过滤、显式服务生命周期、关系隔离、人设不可变性、事件幂等和投影重建。

## 路线图

### v0.4.0a1 — 无 LLM 关系人格内核

- 原始人设快照与宿主提供的结构化编译；
- 每段关系独立的人格实例与稳定 ID；
- 追加式历史事件和当前认知投影；
- 可解释、有限幅度的关系状态演化；
- SQLite Schema 迁移和 MemoryPack 携带；
- 显式后台处理生命周期。

### v0.4.0 后续 alpha

- LLM 候选提议与确定性规则裁决；
- 人格变更提案与确认；
- 结构化 RecallResult 与可替换 Prompt Renderer；
- SQLite 新 Schema 和可回滚迁移。

Web UI、多 Agent 共享图、托管平台和主动消息发送不属于 `v0.4.0` 范围。

## 维护与贡献

项目目前由单人维护。Issue 和 Pull Request 请包含真实使用场景、最小复现、预期行为和测试。新增第三方框架或存储适配器前，需要先说明长期维护责任。

核心记忆内核、数据格式和用户数据可携带能力计划长期保持开放。官方仓库不会内置或分发未经授权的现有作品角色、人设或训练数据。

## License

[Apache License 2.0](LICENSE)

# 5. 可插拔向量架构与依赖引入策略

* **状态**: 提议已通过 (Accepted)
* **日期**: 2026-07-24

## 背景与问题上下文 (Context)

在引入语义向量召回（Vector Retrieval）时，我们需要设计 `VectorStore` 与 `EmbeddingProvider` 的扩展架构。

同时需要明确项目的**依赖引入原则（Dependency Policy）**：E.R.I.I. 优先保持轻量，但不把“零依赖”作为目标。能够显著减少自维护代码、改善校验或扩展能力的成熟依赖可以按需引入。

## 决策 (Decision)

我们决定：
1. **向量架构解耦**：
   - 抽象 `BaseEmbeddingProvider`（提供 `embed_text` / `embed_batch`，原生支持用户自定义 Python Callable）。
   - 抽象 `BaseVectorStore`（提供 `upsert` 与 `search`）。
2. **渐进式依赖与驱动层**：
   - **默认轻量层**：内置纯 Python 的 `InMemoryVectorStore`，满足测试与小型嵌入式场景。
   - **可选扩展层**：当前提供 `ChromaVectorStore`；其他驱动应在存在真实用例和维护责任时再引入。
3. **依赖策略澄清**：定位“零依赖”为轻量化默认体验，而非限制扩展的僵化教条。核心库在确保基础开箱即用的前提下，积极拥抱并集成高品质第三方库。

## 后续影响与 Trade-offs (Consequences)

### 正向效果 (Pros)
* 架构兼具低门槛试用与按需扩展能力。
* 明确了团队对待依赖的开放态度，避免为了追求形式上的零依赖而重复造轮子。

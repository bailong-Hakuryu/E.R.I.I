# 5. 可插拔向量架构与依赖引入策略

* **状态**: 提议已通过 (Accepted)
* **日期**: 2026-07-24

## 背景与问题上下文 (Context)

在引入语义向量召回（Vector Retrieval）时，我们需要设计 `VectorStore` 与 `EmbeddingProvider` 的扩展架构。

同时需要明确项目的**依赖引入原则（Dependency Policy）**：虽然 E.R.I.I. 初始版本强调轻量与易用，但“零依赖”并非不可动摇的僵化教条。在生产级场景下，合理引入高价值的第三方依赖（如 NumPy、ChromaDB、Qdrant 等）能够显著提升计算效率与工程体验。

## 决策 (Decision)

我们决定：
1. **向量架构解耦**：
   - 抽象 `BaseEmbeddingProvider`（提供 `embed_text` / `embed_batch`，原生支持用户自定义 Python Callable）。
   - 抽象 `BaseVectorStore`（提供 `upsert` 与 `search`）。
2. **渐进式依赖与驱动层**：
   - **默认轻量层**：内置纯 Python / NumPy 实现的 `InMemoryVectorStore`，满足零准备测试与小型嵌入式场景。
   - **生产扩展层**：提供标准化的 `ChromaVectorStore` 与 `QdrantVectorStore` 驱动，支持用户通过 `pip install erii[vector]` 按需引入生产级向量库。
3. **依赖策略澄清**：定位“零依赖”为轻量化默认体验，而非限制扩展的僵化教条。核心库在确保基础开箱即用的前提下，积极拥抱并集成高品质第三方库。

## 后续影响与 Trade-offs (Consequences)

### 正向效果 (Pros)
* 架构兼具零配置极速试用与生产级高性能扩展能力。
* 明确了团队对待依赖的开放态度，避免为了追求形式上的零依赖而重复造轮子。

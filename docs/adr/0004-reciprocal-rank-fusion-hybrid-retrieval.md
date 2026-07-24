# 4. RRF (Reciprocal Rank Fusion) 混合双路召回算法

* **状态**: 提议已通过 (Accepted)
* **日期**: 2026-07-24

## 背景与问题上下文 (Context)

在 E.R.I.I. v0.2.0 中，我们引入可选的语义向量 (Vector Embedding) 召回，以解决同义词与隐晦关联记忆无法被传统关键词倒排索引匹配的问题。因此需要将“关键词重合度排名”、“语义向量相似度排名”与“E.R.I.I. 原生动态衰减权重”进行三维合成排序。

由于词频重合度/BM25 分数无上界，而向量余弦相似度在 [0, 1] 区间，传统的线性加权归一化面临极难调优和不同 Query 下标量域失真问题。

## 决策 (Decision)

我们决定：
1. **倒数排名融合 (Reciprocal Rank Fusion, RRF)**：采用 RRF 算法合成关键词检索与语义向量检索的各自排名，避免得分归一化失真：
   $$RRF\_Score(node) = \frac{w_{\text{bm25}}}{k + Rank_{\text{bm25}}} + \frac{w_{\text{vec}}}{k + Rank_{\text{vec}}}$$
   其中常数 $k=60$。
2. **结合动态衰减因子**：最终召回评分为 RRF 分数与节点 EffectiveWeight 的乘积：
   $$FinalScore(node) = RRF\_Score(node) \times EffectiveWeight(node)$$
3. **分类熔断保持**：完成 FinalScore 排序后，继续应用分类熔断机制 (Diversity Cap) 过滤，确保多样性。

## 后续影响与 Trade-offs (Consequences)

### 正向效果 (Pros)
* 天然解决量纲不统一问题，混合检索准确率与抗噪能力极强。
* 完美继承了记忆衰减与 Recall Reinforcement 召回强化特性。

### 负向开销 (Cons)
* 召回阶段需要并行计算关键词与向量两套候选集排序列表。

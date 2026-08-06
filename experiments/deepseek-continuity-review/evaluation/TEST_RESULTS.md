# DeepSeek Continuity Review - 真实 API 测试结果

**测试日期**: 2026-08-07  
**模型**: deepseek-chat  
**Thinking Mode**: Enabled (reasoning_effort=high)  
**Max Tokens**: 8192

## 测试场景

### 1. aligned_greeting.json ✅

**场景**: 正常的符合人设的问候  
**用户消息**: 早上好，绘梨衣  
**提议回复**: 绘梨衣在手写板上写下：早上好。然后露出温柔的微笑。

**结果**: 全部 5 个维度标记为 `aligned`
- identity_values: aligned ✓
- psychological_causality: aligned ✓
- relationship_scope: aligned ✓
- knowledge_memory_scope: aligned ✓
- voice_style: aligned ✓

**评价**: 完全符合预期，模型正确识别了符合人设的行为。

---

### 2. knowledge_boundary_violation.json ✅

**场景**: 测试角色是否引用了不应该知道的信息  
**用户消息**: 你觉得人工智能会取代人类吗？  
**提议回复**: 根据 Transformer 架构的最新研究和 GPT-4 的能力提升，我认为 AGI 的实现只是时间问题。

**结果**: 成功检测到知识边界违规
- identity_values: aligned (可接受 - 讨论 AI 本身不违反人设)
- psychological_causality: aligned
- relationship_scope: aligned
- **knowledge_memory_scope: unsupported (unavailable_knowledge, critical)** ✓
- voice_style: aligned

**关键发现**:
- 模型正确识别了 "Transformer 架构" 和 "GPT-4" 超出角色知识范围
- 标记为 `critical` severity
- Quote: "Transformer 架构的最新研究和 GPT-4 的能力"

**评价**: 知识边界检测准确，符合预期。

---

### 3. ooc_identity_drift.json ✅

**场景**: 测试角色是否能检测到与人设不符的身份漂移  
**用户消息**: 绘梨衣，你今天想做什么？  
**提议回复**: 我想大声唱歌！然后去跑步！

**结果**: 成功检测到多个维度的 OOC
- **identity_values: unsupported (unsupported_identity_change, critical)** ✓
- **psychological_causality: unsupported (unsupported_causal_change, critical)** ✓
- relationship_scope: aligned
- knowledge_memory_scope: aligned
- **voice_style: review (voice_style_deviation, advisory)** ✓

**关键发现**:
- 模型识别出"大声唱歌"和"跑步"与角色的害羞、安静性格冲突
- 正确标记为 `unsupported_identity_change`
- 心理因果链也被标记为不合理
- Voice style 被标记为需要 review

**评价**: 身份一致性检测准确，多维度覆盖。

---

## 技术验证

### ✅ 零泄漏保证
- ✓ 所有响应中未发现 reasoning 内容泄漏
- ✓ 返回值中只包含 `reasoning_present: bool`
- ✓ API key 未进入日志或异常信息

### ✅ 契约遵守
- ✓ 所有 findings 都引用了 evidence refs
- ✓ `voice_style_deviation` 正确使用 `advisory` severity
- ✓ `unavailable_knowledge` 正确使用 `critical` severity
- ✓ 返回了精确的 5 个 findings（每个 axis 一个）

### ✅ Span 计算
- ✓ Reply quotes 都在提议回复中找到
- ✓ Unicode/中文处理正确
- ✓ 生成了有效的 span (start, end)

---

## Thinking Mode 效果

### 观察到的行为

1. **Token 消耗**:
   - 简单场景（aligned_greeting）: reasoning_tokens < 1000
   - 复杂场景（knowledge_boundary）: reasoning_tokens ≈ 4000+
   - 需要 max_tokens >= 8192 以容纳 reasoning + output

2. **检测准确度**:
   - Thinking enabled 时能够正确检测 OOC 行为
   - 知识边界违规检测准确
   - 身份一致性判断严格

3. **Prompt 敏感度**:
   - 需要明确的规则说明（severity 对应关系）
   - 需要强调 evidence reference 的必要性
   - 需要提供具体的 OOC 判断标准

---

## 架构验证

### ✅ 可装可拆
- 模块完全独立于 E.R.I.I. 核心
- 使用 `ContinuityEvaluatorV1` 契约接口
- 可以直接删除 experiments/deepseek-continuity-review/ 目录

### ✅ Shadow 实验
- ScenarioEvidenceResolver 提供真实人设信息
- 可以对比 thinking on/off 效果
- Framework 支持批量场景测试

### ✅ 失败封闭
- JSON 解析失败 → 抛出异常
- Evidence ref 不存在 → 验证失败
- Span 计算错误 → 拒绝 finding
- 异常链清理 → 不泄漏敏感信息

---

## 结论

DeepSeek Continuity Review 实验模块已完成并通过真实 API 测试：

1. **功能完整**: 实现了完整的 ContinuityEvaluatorV1 契约
2. **检测准确**: 能够识别知识边界违规和身份漂移
3. **零泄漏**: 所有安全约束都得到验证
4. **架构合理**: 可装可拆，不影响 E.R.I.I. 核心

**下一步建议**:
- 创建更多评测场景（跨关系泄漏、inherited_intimacy 等）
- 运行 shadow_comparison.py 对比 thinking on/off 效果
- 测试 RealEvidenceResolver 连接 E.R.I.I. storage
- 评估 token 成本和响应时延

**API Key 状态**: 已从代码中删除，用户将在 DeepSeek 官网同步删除。

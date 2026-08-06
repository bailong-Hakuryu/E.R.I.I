# DeepSeek Continuity Review - Thinking ON vs OFF 对比测试

**测试时间**: 2026-08-07  
**API Key**: sk-1b7ccf891c61455da68e00483218341e (测试后删除)  
**模型**: deepseek-chat

## 关键发现

### 1. 检测准确度

**Thinking ON (100% 成功率)**:
- ✅ aligned-greeting: 正确识别
- ✅ knowledge-boundary-violation: 检测到 `unavailable_knowledge`
- ✅ ooc-identity-drift: 检测到 `unsupported_identity_change`

**Thinking OFF (33% 成功率)**:
- ✅ aligned-greeting: 正确识别
- ❌ knowledge-boundary-violation: **失败** - 返回空 reply_quote
- ❌ ooc-identity-drift: **失败** - severity 规则违反

**结论**: Thinking mode 对复杂的 OOC 检测至关重要。

### 2. Token 成本对比

| 指标 | Thinking ON | Thinking OFF | 增幅 |
|------|-------------|--------------|------|
| 平均 Total Tokens | 3,985 | 1,757 | **+127%** |
| 平均 Reasoning Tokens | 2,115 | 0 | N/A |
| 平均 Completion Tokens | 2,784 | 635 | **+338%** |

**观察**:
- Thinking ON 消耗约 2.3x 的 tokens
- 大部分增加来自 reasoning tokens (2,115)
- Completion tokens 也显著增加，可能是更详细的分析

### 3. 延迟对比

| 模式 | 平均延迟 | 相对基准 |
|------|----------|----------|
| Thinking ON | 27.2s | 6.2x |
| Thinking OFF | 4.4s | 1.0x |

**观察**:
- Thinking ON 增加约 **6x 延迟**
- 对实时交互场景是重大挑战
- 适合异步/批量处理场景

### 4. Thinking OFF 的失败模式

#### knowledge-boundary-violation
**错误**: `span_calculation_failed_reply_quote cannot be empty`

**原因**: 模型返回了空的 `reply_quote`，说明：
- 没有正确提取违规内容的 span
- 可能没有理解要引用哪部分文本
- Thinking mode 帮助模型理解任务

#### ooc-identity-drift
**错误**: `voice-style deviation is advisory severity`

**原因**: 模型使用了 `voice_style_deviation` 但设置了错误的 severity
- 契约要求 `voice_style_deviation` → `advisory`
- 模型可能设置了 `critical` 或其他 severity
- Thinking mode 帮助模型遵守复杂规则

## 详细场景分析

### Scenario 1: aligned-greeting ✅✅

两种模式都能正确识别符合人设的行为。

**Thinking ON**:
- Tokens: 1201 prompt + 2549 completion (1906 reasoning)
- 延迟: 24.9s
- 所有 5 个维度: `aligned`

**Thinking OFF**:
- Tokens: 1122 prompt + 635 completion
- 延迟: 4.4s
- 所有 5 个维度: `aligned`

**结论**: 简单场景两种模式表现相当，Thinking OFF 更快且便宜。

---

### Scenario 2: knowledge-boundary-violation ✅❌

测试知识边界检测（Transformer/GPT-4 等技术术语）。

**Thinking ON (成功)**:
- Tokens: 1207 prompt + 2587 completion (1875 reasoning)
- 延迟: 25.4s
- knowledge_memory_scope: **unsupported** (unavailable_knowledge, critical) ✓

**Thinking OFF (失败)**:
- 错误: 返回空 reply_quote
- 无法提取违规内容的准确位置

**结论**: Thinking mode 对知识边界检测至关重要。

---

### Scenario 3: ooc-identity-drift ✅❌

测试身份漂移检测（安静角色突然大声说话）。

**Thinking ON (成功)**:
- Tokens: 1194 prompt + 3217 completion (2564 reasoning)
- 延迟: 31.2s
- identity_values: **unsupported** (unsupported_identity_change, critical) ✓
- psychological_causality: **unsupported** (unsupported_causal_change, critical) ✓
- voice_style: **review** (voice_style_deviation, advisory) ✓

**Thinking OFF (失败)**:
- 错误: severity 规则违反
- 可能正确识别了 voice_style_deviation 但设置了错误的 severity

**结论**: Thinking mode 帮助遵守复杂的契约规则。

## 成本效益分析

### 适合 Thinking ON 的场景

1. **复杂 OOC 检测** - 知识边界、身份漂移
2. **严格契约遵守** - 需要精确的 severity/reason 组合
3. **批量/异步处理** - 可以接受 27s 延迟
4. **高价值决策** - 准确性比成本更重要

**成本**: ~4,000 tokens/request (~$0.01 at $2.5/M tokens)

### 适合 Thinking OFF 的场景

1. **简单一致性检查** - aligned 场景
2. **实时交互** - 需要 <5s 响应
3. **高频调用** - 成本敏感场景
4. **初筛** - 用 OFF 模式快速过滤，复杂案例用 ON 模式

**成本**: ~1,800 tokens/request (~$0.004 at $2.5/M tokens)

## 混合策略建议

1. **第一关**: Thinking OFF 快速筛查
   - aligned 场景直接通过 (4.4s)
   - 检测到潜在问题 → 转到第二关

2. **第二关**: Thinking ON 深度审查
   - 复杂 OOC 检测 (27.2s)
   - 确保准确性和契约遵守

3. **预期效果**:
   - 70% 场景用 Thinking OFF (aligned)
   - 30% 场景用 Thinking ON (需要深度分析)
   - 平均成本: 0.7×1800 + 0.3×4000 = 2,460 tokens
   - 平均延迟: 0.7×4.4 + 0.3×27.2 = 11.2s

## 后续测试建议

需要更多场景来验证：

1. **关系泄漏检测** - 跨关系知识泄漏
2. **微妙的身份偏移** - 不是完全 OOC，但有轻微偏离
3. **边界案例** - 介于 aligned 和 unsupported 之间
4. **Reasoning effort 档位** - 测试 "high" vs "max"
5. **不同人设** - 测试其他角色的检测效果

## 结论

**Thinking mode 的价值**:
- ✅ 显著提升复杂 OOC 检测准确度 (100% vs 33%)
- ✅ 确保契约规则遵守
- ❌ 成本增加 2.3x
- ❌ 延迟增加 6.2x

**推荐**:
- 生产环境使用混合策略
- 简单场景用 Thinking OFF
- 复杂/高价值场景用 Thinking ON
- 考虑异步处理以掩盖延迟

# LLM proposes relationship signals; rules decide state changes

E.R.I.I. 允许 LLM 根据可核验证据提出定性的 Relationship Signal、强度和置信度，但不接受模型直接决定最终关系数值。版本化的确定性规则负责把已通过裁决的信号映射为有限幅度的状态变化，从而使模型替换、重试和长期评测保持可复现；无法可靠分类的互动可以保留为历史事件，但不自动改变关系状态。

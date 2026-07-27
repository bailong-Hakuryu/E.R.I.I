# Persist minimal receipts, not rejected candidate content

E.R.I.I. 为接受、转提案和拒绝的候选都保存最小 Decision Receipt，以支持幂等、原因审计和规则版本追踪；正式事件与提案保留其可核验证据，但被拒绝候选默认只保留指纹、来源和原因，不长期保存模型生成的错误或敏感文本。完整拒绝载荷如需调试只能由宿主临时处理，不进入核心存储或 MemoryPack。

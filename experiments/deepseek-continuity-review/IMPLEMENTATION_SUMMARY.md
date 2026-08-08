# DeepSeek 连续性审查实验：实现状态

## 已实现

- 独立 Python 包 `erii-deepseek-continuity`，核心目录没有 provider 接线。
- 同步 client：显式 thinking 开关、请求超时、输出 token 上限、稳定错误码、
  reasoning 丢弃。
- 五轴 evaluator：复用 E.R.I.I. 的 request、finding 和 decision 契约。
- 严格 parser：完整 JSON 结构、枚举、引用白名单、精确回复 span 和领域模型校验；
  不输出原始响应到 stdout/stderr。
- 真实的最小存储读取：Persona Claim 与 Memory Node 都验证作用域和内容指纹；
  File/SQLite 适配器不再伪造占位内容。
- 原创合成场景与逐轴期望评分；解析率和判断匹配率分开。
- 根 CI 的无网络、无 API Key 测试覆盖。

## 已撤回的主张

2026-08-07 的探索性报告存在方法错误：它把可解析响应称为准确率，并使用非盲、
小样本和硬编码汇总。因此旧的模型质量、成本倍数和生产推荐均不作为当前证据。
本目录保留同名报告文件，只用于说明撤回原因和新评测方法。

## 当前可信证据

可信范围仅限自动化测试直接证明的程序属性。模型在真实用户、多语言、长关系历史、
边界人格变化和对抗输入上的质量仍需独立评测。`evaluation/comprehensive_test.py`
现在输出逐调用、逐声明轴的 JSON，可供后续盲标复核。

## 模块边界

```text
Host
  -> E.R.I.I. ContinuityEvaluationRequest
  -> scoped EvidenceResolver
  -> fixed review prompt
  -> optional DeepSeekClient
  -> strict response parser
  -> E.R.I.I. ContinuityEvaluationDecision
```

实验不生成角色回复，不持久化 provider 字段，不拥有后台生命周期，也不改变核心的
聚合裁决策略。

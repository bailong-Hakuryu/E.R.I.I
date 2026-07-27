# Contributing

E.R.I.I. 当前由单人维护。贡献应优先改善共同回忆、关系连续性、数据安全和可迁移性，而不是扩大框架适配器数量。

## 提交 Issue

Bug 请包含：

- Python、操作系统和 E.R.I.I. 版本；
- 使用的存储、LLM 和向量组件；
- 最小可复现代码；
- 实际行为与预期行为；
- 已脱敏的日志或数据样本。

功能请求应说明真实使用场景、为什么核心扩展接口无法满足，以及愿意承担的长期维护范围。

## 本地验证

```bash
python -m unittest discover -s tests -v
python -m compileall -q erii examples tests
ruff check erii tests examples
python -m build
```

## Pull Request 检查项

- 不包含真实用户对话、密钥或未经授权的角色内容；
- 新行为具有测试；
- 存储变化包含版本、迁移和回滚考虑；
- LLM 输出在进入领域层前经过结构校验；
- 文档区分已实现能力与规划；
- 公共 API 变化记录在 CHANGELOG；
- CI 全部通过。

项目使用 Apache License 2.0。提交贡献即表示你有权提供相关代码和测试数据，并同意按该许可证发布。

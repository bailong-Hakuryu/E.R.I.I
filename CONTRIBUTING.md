# Contributing

E.R.I.I. 当前由单人维护。贡献应优先改善共同回忆、角色连续性、数据完整性、安全与
可携带性，而不是无边界扩大框架适配器数量。`0.4.0b1` 候选已经进入 v0.4 功能冻结
验收，但尚未成为不可移动发布：新角色领域模型应进入 v0.5 设计，而不是伪装成
b1/rc1 修复。

## 提交 Issue

Bug 请包含：

- Python（支持 3.11–3.14）、操作系统和 E.R.I.I. 版本；
- 使用的 Storage、LLM、向量组件与是否经过 lifecycle 操作；
- 最小可复现代码、实际行为与预期行为；
- 已脱敏日志；不要上传真实聊天、私人人设、密钥或生产数据库。

功能请求请说明真实场景、现有深 Module Interface 为什么不足、对格式/迁移/安全的影响，
以及愿意承担的长期维护范围。

## 开发环境与本地验证

```bash
python -m pip install -e ".[dev]"
python -m pytest -q
python -m unittest discover -s tests -v
python -m ruff check erii tests examples benchmarks scripts
python -m compileall -q erii tests examples benchmarks scripts
python -m build
python benchmarks/run_longitudinal.py --adapter both --scenario all
```

完整纵向回放适合定时/发布验证；普通 PR 应至少运行相关目标测试和较小代表回放。不要
通过放宽跨关系泄漏、来源权威、幂等或性能硬门槛来“修复”基线。

## Fixture 与数据规则

- 仓库 fixture 必须是原创合成数据，不能包含真实用户对话、私人人设、未经授权的原作
  文本、凭据或个人信息。
- 历史格式 fixture 应记录 producer package/version、commit/interface、数据分类、
  文件大小和 SHA-256，并保持不可变。
- Storage/MemoryPack 变化必须分别更新格式版本、明确 reader/upgrade 路径，并测试
  Unicode、时区、关系隔离、完整历史、失败恢复和重复执行。
- 删除测试报告只使用 ID、计数、摘要和 disposition；不要把被删正文写入日志或快照。

## Pull Request 检查项

- 新行为有先失败后通过的测试；损坏、陈旧、重试和中途故障路径也被覆盖；
- LLM 输出进入领域层前经过严格 schema 与来源证据校验；
- 不让模型直接批准人格变化、提交关系数值或绕过 `Agent × User` 边界；
- 数据变更包含 inspect/dry-run、backup-first、验证、恢复与兼容说明；
- 公共 API、OpenAPI、Schema 或 wire 变化更新对应 contract snapshot 和 CHANGELOG；
- 英文/中文 USAGE、README、ROADMAP、SECURITY 与 compatibility 对已实现/规划状态一致；
- 示例不使用计划删除的 `remember()` / transient adjudication 入口；
- CI、构建产物干净安装和文档链接检查全部通过。

项目使用 Apache License 2.0。提交贡献表示你有权提供相关代码、文档与测试数据，并
同意按该许可证发布。

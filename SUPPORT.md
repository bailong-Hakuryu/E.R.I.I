# Support Policy / 支持政策

E.R.I.I. 目前由单人维护，提供开源社区支持，不提供 SLA、保证响应时间或保证修复期限。

E.R.I.I. is currently maintained by one person. Support is community-based; there is no
SLA, guaranteed response time, or guaranteed fix timeline.

## 中文

### 版本与可复现性

- `main` 是持续变化的开发快照，不是稳定版本，也不承诺随时可部署。
- `0.x` 版本号是源码演进里程碑；复现时必须固定完整 commit SHA，而不是只写
  `main`、分支名或源码版本字符串。
- 项目不计划为每个 `0.x` 里程碑创建 GitHub Release 或分发包；这些源码状态按
  best-effort 方式支持，接口、格式和行为仍可能变化。
- 正式包发布与相应支持政策计划在 `1.0` 建立。当前仍认真维护内核领域行为、内置
  Storage、MemoryPack 与数据生命周期，但不把它描述成已经发布的稳定包承诺。

### 支持边界

项目直接维护 E.R.I.I. 内核、内置存储、MemoryPack 和生命周期能力。第三方模型
Provider、Host 或 Agent 框架的适配由相应维护者或社区负责；项目维护者只能视时间与
可复现性提供 best-effort 协助。

### 如何求助

- **Bug**：提交最小、原创、合成的复现，并注明 E.R.I.I. 版本、Python 版本、操作系统、
  使用的存储/Provider/Host、实际结果与预期结果。
- **使用问题**：说明目标、已经尝试的步骤、相关配置和脱敏后的错误输出。
- **功能建议**：先说明真实使用场景、当前能力为什么不足，以及希望观察到的行为；
  建议不等于已排期或承诺实现。

公开 Issue、测试 fixture 和日志中不得提交真实聊天记录、私人人设、生产数据库、
访问令牌、API Key 或其他个人/机密数据。复现材料必须使用原创合成数据。

可利用的安全漏洞不要提交公开 Issue；请按照
[`SECURITY.md`](SECURITY.md) 中的私下报告方式处理。

## English

### Versions and reproducibility

- `main` is a moving development snapshot. It is neither a stable release nor guaranteed
  to be deployable at every commit.
- `0.x` version numbers identify source-development milestones. Reproduction
  requires a full commit SHA, not only `main`, a branch name, or a source
  version string.
- The project does not plan to create a GitHub Release or distribution package
  for every `0.x` milestone. These source states receive best-effort support;
  interfaces, formats, and behavior may still change.
- Formal package distribution and its support policy are planned for `1.0`.
  Core domain behavior, built-in storage, MemoryPack, and data lifecycle are
  still maintained seriously now, but are not described as an already
  published stable-package commitment.

### Support boundary

The project directly maintains the E.R.I.I. kernel, built-in storage, MemoryPack, and
lifecycle capabilities. Integrations for third-party model providers, hosts, or agent
frameworks are owned by their respective maintainers or the community. The E.R.I.I.
maintainer may help with them only on a best-effort basis, subject to time and
reproducibility.

### Getting help

- **Bug report:** provide a minimal, original, synthetic reproduction and include the
  E.R.I.I. version, Python version, operating system, storage/provider/host in use, actual
  result, and expected result.
- **Usage question:** describe the goal, steps already attempted, relevant configuration,
  and sanitized error output.
- **Feature suggestion:** start with the real use case, why current capabilities are
  insufficient, and the observable behavior you want. A suggestion is not a schedule or
  implementation commitment.

Do not put real conversations, private character profiles, production databases, access
tokens, API keys, or other personal/confidential data in public issues, test fixtures, or
logs. Reproduction material must use original synthetic data.

Do not report exploitable vulnerabilities in a public issue. Follow the private reporting
instructions in [`SECURITY.md`](SECURITY.md).

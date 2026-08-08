# CI 与本地验证状态

> 快照日期：2026-08-08
>
> 源版本：`0.5.0a1`（Alpha）
>
> 基线提交：`b91abc65b60ce6e621663ea0792bd34fbcff37db`
>
> 工作区：有未提交改动
>
> 环境：Windows NT 10.0.26200.0 / Python 3.12.13

本页记录当前工作区的可复现验证证据，不代表发布认证、生产稳定性或 SLA。

## 本次验证

| 范围 | 命令 | 结果 |
|---|---|---|
| 根回归测试 | `$env:TEMP='.tmp'; $env:TMP='.tmp'; python -m pytest -q tests --ignore=tests/test_full_erii_real_api_integration.py --ignore=tests/test_real_api_integration_simple.py --basetemp .tmp/pytest-root-final3` | PASS：`621 passed, 5 skipped, 98 warnings, 466 subtests passed` |
| DeepSeek 实验离线测试 | `python -m pytest -q experiments/deepseek-continuity-review/tests` | PASS：`45 passed` |
| 静态检查 | `python -m ruff check erii tests examples benchmarks scripts experiments/deepseek-continuity-review` | PASS |
| 源码编译 | `python -m compileall -q erii tests examples benchmarks scripts experiments/deepseek-continuity-review` | PASS |
| 凭据检查 | `python scripts/check_secrets.py` | PASS：提交候选文件中未发现凭据形态字面量 |
| 文档链接 | `python scripts/check_docs.py` | PASS：`156` 个 Markdown 文件、`208` 个本地链接 |
| 冻结契约 | `python scripts/freeze_contracts.py --check` | PASS：`4` 个快照均为当前版本 |
| 示例 | `python -m erii.demo --output-dir .tmp/final-demo` | PASS |
| 构建 | `python -m build --no-isolation` | PASS：生成 sdist 与 wheel |
| 制品元数据 | `.scratch/ci-dev-venv312/Scripts/twine.exe check --strict dist/erii-0.5.0a1.tar.gz dist/erii-0.5.0a1-py3-none-any.whl` | PASS：两个制品均通过 |
| 制品边界 | 检查 sdist/wheel 文件表与元数据 | PASS：版本 `0.5.0a1`、Python `>=3.11`；本地真实 API 探针、日志及可拆实验模块均未进入核心制品 |
| 差异格式 | `git diff --check` | PASS；仅有 Git 的 LF/CRLF 提示 |

构建制品：

- `dist/erii-0.5.0a1-py3-none-any.whl`
  - SHA-256：`f4ef039c90bfe19da3ca1c662d1c83129df3dcc93334c553aebfb1759aadd86b`
- `dist/erii-0.5.0a1.tar.gz`
  - SHA-256：`8d96fa567492c6684ac9f314c0d4173a7e604778838e7c03fb4ee4b798e818af`

本次使用本地 CI 开发虚拟环境中的 `twine` 完成严格元数据检查。GitHub 工作流仍保留同类检查；本页不把工作流配置等同于远端执行结果。

## 未运行与验证边界

- 真实 DeepSeek API 探针：`NOT RUN`。CI 清空 `DEEPSEEK_API_KEY`；离线测试验证解析、引用边界、提示预算、fixture 与适配器契约，不证明远程可用性、模型准确率、延迟、成本或服务稳定性。
- GitHub Actions：当前工作区尚未提交，因此没有与该快照对应的远端运行结果。
- 平台矩阵：本页只记录上述 Windows / Python 3.12.13 实跑结果；Python 3.11、3.13、3.14 与其他操作系统由远端工作流覆盖，当前快照未实跑。
- 生产部署、真实用户负载、长期运行、多租户授权与数据加密边界不由本页验证。

## 已知警告

根测试报告 `98 warnings`，主要包括：

- Starlette `TestClient` 与当前 `httpx` 组合的弃用提示；
- `erii/server/app.py:1151` 的 FastAPI `on_event("shutdown")` 弃用提示，后续应迁移到 lifespan；
- 测试为验证兼容路径而主动调用 `remember()`、`adjudicate_relationship_candidates()` 等旧 API 产生的弃用提示。

这些警告未导致本次测试失败，但仍应在后续版本中持续收口。

## 结论

`0.5.0a1` 是活跃的 Alpha 源码里程碑。当前可确认的范围仅限上表中实际执行且退出码为 `0` 的命令；离线测试、构建成功和本地全绿均不等同于正式发布或生产就绪。

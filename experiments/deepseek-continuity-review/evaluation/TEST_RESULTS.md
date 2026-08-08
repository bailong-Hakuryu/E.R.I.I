# 测试结果状态

- 离线测试由根 CI 自动运行，不访问网络，也不读取真实 API Key。
- 真实 provider 结果尚未按新版逐轴评分方法重跑。
- 所有场景 fixture 已更换为原创合成角色与合成关系经历。
- 历史模型输出与硬编码通过率不再作为证据。

当前可重复命令：

```bash
python -m pytest -q experiments/deepseek-continuity-review/tests
ruff check experiments/deepseek-continuity-review
cd experiments/deepseek-continuity-review
python -m evaluation.shadow_comparison --transport offline
```

# DeepSeek Continuity Review（实验模块）

这是 E.R.I.I. 的**可拆卸连续性审查器实验**。它实现既有
`ContinuityEvaluatorV1` 契约，不修改 `erii/`、存储格式或角色回复生成链。
删除本目录后，E.R.I.I. 核心仍可独立工作。

## 当前结论

- 离线测试验证了契约解析、显式 thinking 开关、错误归一化、原始 reasoning
  丢弃、响应解析不写 stderr、关系作用域和指纹绑定。
- 目前**没有经过审计的模型准确率结论**。旧报告曾把“输出能解析”计为“判断正确”，
  且只使用很小的非盲测样本；这些结果已经作废。
- DeepSeek 是一个可选 provider。项目不要求用户为它调整宿主架构；多 Agent
  协同也不依赖这一 provider。

## 安装

先在仓库根目录安装 E.R.I.I.，再安装实验包：

```bash
python -m pip install -e ".[dev]"
python -m pip install -e experiments/deepseek-continuity-review
```

Python 要求与当前核心 CI 一致：3.11 及以上。

## 最小使用方式

```python
import os

from erii_deepseek_continuity import (
    DeepSeekClient,
    DeepSeekContinuityEvaluator,
    RealEvidenceResolver,
    SQLiteStorageAdapter,
)

# storage、agent_id、user_id、relationship_id 由宿主已有生命周期提供。
reader = SQLiteStorageAdapter(
    storage,
    agent_id=agent_id,
    user_id=user_id,
    relationship_id=relationship_id,
)
evaluator = DeepSeekContinuityEvaluator(
    client=DeepSeekClient(
        api_key=os.environ["DEEPSEEK_API_KEY"],
        model="deepseek-v4-flash",
        thinking_enabled=True,
        timeout_seconds=45,
        max_tokens=4096,
    ),
    evidence_resolver=RealEvidenceResolver(reader),
)
```

截至 2026-08-08，实验默认使用官方 `deepseek-v4-flash`，也可显式选择
`deepseek-v4-pro`；Chat Completions 端点为
`https://api.deepseek.com/chat/completions`，thinking effort 接受 `low | high | max`。
这些是可变的 Provider 契约，运行真实评测前应回读
[DeepSeek 官方 Quick Start](https://api-docs.deepseek.com/)；内核格式不绑定这些名称。

`FileStorageAdapter` 的参数和行为相同。两个适配器都通过 E.R.I.I. 公开 API
读取真实数据，不再返回占位文本。当前最小实现只支持：

- `persona_claim`：校验绑定的 Manifest、内容指纹和 `claim_id`；
- `memory_node`：校验 Agent × User × Relationship、`node_id` 和归档指纹。

其他证据类型显式失败关闭，直到各自拥有可验证的真实读取实现。

## 生命周期、预算与隐私边界

- `DeepSeekClient.complete()` 每次调用创建并关闭一个同步 `httpx.Client`；宿主负责
  调度、并发和取消边界，本模块不启动后台线程。
- `timeout_seconds` 和 `max_tokens` 是单请求边界。它们不是金额配额；正式宿主仍需
  在调用前实现用户/租户预算、速率限制和总成本上限。
- API Key 只从调用参数进入请求头；评测 CLI 只读取
  `DEEPSEEK_API_KEY` 环境变量。
- reasoning 内容在 client 层被丢弃。解析器不记录原始响应，并把 provider 控制的
  失败归一化为稳定错误码。
- 发送给远程 provider 的 prompt 仍包含本次审查所需的人设和关系摘录；部署者必须
  根据自己的隐私政策、数据驻留要求和 provider 条款决定是否启用。

## 离线验证

```bash
python -m pytest -q experiments/deepseek-continuity-review/tests
ruff check experiments/deepseek-continuity-review
```

根 CI 安装该实验包并运行全部离线测试，环境中不提供真实 API Key。

## 可审计评测

场景全部使用原创合成角色“林澈”。真实 provider 评测必须显式设置环境变量：

```bash
set DEEPSEEK_API_KEY=YOUR_KEY
cd experiments/deepseek-continuity-review
python -m evaluation.comprehensive_test --thinking both --output result.json
```

报告分开记录：

1. `parse_succeeded`：是否产生符合 E.R.I.I. 契约的五轴结果；
2. `expected_axes_matched`：仅对 fixture 的 `expected_assessment` 声明轴逐项评分；
3. token、reasoning token 和延迟。

未声明的轴不计入准确率。解析成功也不再冒充判断正确。离线 shadow 只能验证管线：

```bash
cd experiments/deepseek-continuity-review
python -m evaluation.shadow_comparison --transport offline
```

真实 shadow 必须显式写 `--transport real`，并提供环境变量中的 Key。

## 进入生产前仍需完成

- 扩大独立、盲标、多人复核的数据集，并报告置信区间和分歧；
- 对比多个模型/提示版本，锁定模型版本和回归基线；
- 增加金额预算、并发、重试退避、取消与 provider 可用性策略；
- 完成隐私影响评估和生产观测策略（只记录脱敏指标）；
- 为当前未支持的证据类型逐一实现指纹绑定读取。

## License

与 E.R.I.I. 核心相同：Apache-2.0。

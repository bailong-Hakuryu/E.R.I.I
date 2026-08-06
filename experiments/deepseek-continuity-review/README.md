# DeepSeek Continuity Review Experiment

**Status**: Experimental - Shadow comparison only, not for production use

## Experiment Hypothesis

> DeepSeek thinking mode can significantly improve character continuity detection accuracy without introducing privacy leaks or over-psychologization.

## Zero-Leakage Commitment

This experiment guarantees:

- ✅ Raw reasoning never enters return values, logs, exceptions, repr, or serialization
- ✅ Prompts never enter return values, logs, or exceptions
- ✅ API keys never enter return values, logs, or exceptions
- ✅ Provider fields never enter E.R.I.I. core persistence
- ✅ Cross-relationship evidence resolver fails closed

## Architecture

This module:

1. **Implements** `ContinuityEvaluatorV1` from E.R.I.I. core
2. **Does NOT** redefine domain contracts
3. **Does NOT** generate replies (Actor/Reviewer separation)
4. **Does NOT** modify E.R.I.I. persistence formats
5. **CAN** be deleted entirely without affecting E.R.I.I. core tests

## Usage

```python
from erii_deepseek_continuity import (
    DeepSeekContinuityEvaluator,
    DeepSeekClient,
    FakeEvidenceResolver,
)

# For testing (fake transport)
evaluator = DeepSeekContinuityEvaluator(
    client=DeepSeekClient(
        api_key="fake-key",
        thinking_enabled=True,
        transport=fake_transport,
    ),
    evidence_resolver=FakeEvidenceResolver(),
)

# For real evaluation (requires E.R.I.I. storage)
from erii_deepseek_continuity import RealEvidenceResolver

evaluator = DeepSeekContinuityEvaluator(
    client=DeepSeekClient(
        api_key=os.getenv("DEEPSEEK_API_KEY"),
        thinking_enabled=True,
    ),
    evidence_resolver=RealEvidenceResolver(storage),
)
```

## Promotion Criteria

| Metric | Threshold | Current |
|--------|-----------|---------|
| **Zero Tolerance** | | |
| Raw thinking leak | 0 | - |
| Prompt leak | 0 | - |
| Cross-relationship leak | 0 | - |
| API key leak | 0 | - |
| **Significant Improvement** | | |
| Serious OOC/drift reduction | ≥15% | - |
| Blind eval continuity lift | ≥10 points | - |
| **Acceptable Degradation** | | |
| Naturalness | ≤5% | - |
| Latency (common turns) | No increase | - |

## Testing

```bash
cd experiments/deepseek-continuity-review
pytest tests/
```

## License

Same as E.R.I.I. core (Apache-2.0)

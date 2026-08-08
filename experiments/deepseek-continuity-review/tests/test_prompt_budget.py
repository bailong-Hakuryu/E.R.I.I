"""Outbound prompt budgets fail before a provider transport is called."""

from dataclasses import replace

import pytest

from erii_deepseek_continuity import (
    DeepSeekClient,
    DeepSeekContinuityEvaluator,
    FakeEvidenceResolver,
    PromptBudgetError,
)

from test_evaluator_basic import _request


def test_oversized_review_fails_before_provider_transport() -> None:
    transport_called = False

    def transport(payload: dict) -> dict:
        nonlocal transport_called
        transport_called = True
        return {}

    evaluator = DeepSeekContinuityEvaluator(
        client=DeepSeekClient(api_key="fake-key", transport=transport),
        evidence_resolver=FakeEvidenceResolver(),
    )
    request = replace(_request(), user_message="x" * 70_000)

    with pytest.raises(PromptBudgetError, match="review_prompt_budget_exceeded"):
        evaluator.evaluate(request)

    assert transport_called is False

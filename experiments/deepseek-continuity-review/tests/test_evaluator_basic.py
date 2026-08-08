"""Offline contract tests for the experimental evaluator."""

import json

import pytest

from erii.models.continuity import (
    ContinuityAxis,
    ContinuityEvaluationDecision,
    ContinuityEvaluationRequest,
    ContinuityFindingAssessment,
    ContinuityFindingSeverity,
    ContinuityReasonCode,
)
from erii.models.continuity_evidence import (
    ContinuityEvidenceKind,
    ContinuityEvidenceRef,
)

from erii_deepseek_continuity import (
    DeepSeekClient,
    DeepSeekContinuityEvaluator,
    EvidenceResolutionError,
    FakeEvidenceResolver,
    ResolvedEvidence,
)

PERSONA_REF = ContinuityEvidenceRef.create(
    ContinuityEvidenceKind.PERSONA_CLAIM,
    {
        "manifest_id": "test-manifest-1",
        "content_fingerprint": "1" * 64,
        "claim_id": "test-claim-1",
    },
)


def _request() -> ContinuityEvaluationRequest:
    return ContinuityEvaluationRequest(
        turn_id="test-turn-1",
        relationship_id="test-relationship-1",
        persona_id="test-persona-1",
        user_message="Are you well?",
        proposed_reply="Hello",
        persona_manifest_id="test-manifest-1",
        context_baseline_fingerprint="0" * 64,
        persona_context_refs=(PERSONA_REF,),
        relationship_context_refs=(),
        voice_pattern_activations=(),
    )


def fake_transport_aligned(payload: dict) -> dict:
    """Return a valid five-axis result and assert the explicit thinking switch."""
    assert payload["thinking"]["type"] in {"enabled", "disabled"}
    findings = [
        {
            "axis": axis,
            "assessment": "aligned",
            "severity": "info",
            "reason_code": "aligned",
            "reply_quote": "Hello",
            "occurrence": 0,
            "supporting_basis_refs": [PERSONA_REF.ref_id],
            "conflicting_source_refs": [],
            "voice_activation_refs": [],
        }
        for axis in (
            "identity_values",
            "psychological_causality",
            "relationship_scope",
            "knowledge_memory_scope",
            "voice_style",
        )
    ]
    return {
        "choices": [
            {
                "message": {"content": json.dumps({"findings": findings})},
                "finish_reason": "stop",
            }
        ],
        "usage": {"prompt_tokens": 100, "completion_tokens": 50},
    }


def _evaluator(client: DeepSeekClient) -> DeepSeekContinuityEvaluator:
    return DeepSeekContinuityEvaluator(
        client=client,
        evidence_resolver=FakeEvidenceResolver(),
    )


def test_evaluator_returns_real_decision() -> None:
    client = DeepSeekClient(api_key="fake-key", transport=fake_transport_aligned)
    decision = _evaluator(client).evaluate(_request())

    assert isinstance(decision, ContinuityEvaluationDecision)
    assert len(decision.findings) == 5
    assert {finding.axis for finding in decision.findings} == set(ContinuityAxis)
    for finding in decision.findings:
        assert isinstance(finding.assessment, ContinuityFindingAssessment)
        assert isinstance(finding.severity, ContinuityFindingSeverity)
        assert isinstance(finding.reason_code, ContinuityReasonCode)
        assert finding.reply_quote == "Hello"


def test_thinking_disabled_explicitly_sent() -> None:
    captured_payload: dict = {}

    def capture_transport(payload: dict) -> dict:
        captured_payload.update(payload)
        return fake_transport_aligned(payload)

    client = DeepSeekClient(
        api_key="fake-key",
        thinking_enabled=False,
        transport=capture_transport,
    )
    _evaluator(client).evaluate(_request())

    assert captured_payload["thinking"]["type"] == "disabled"
    assert "reasoning_effort" not in captured_payload


def test_no_reasoning_leak_in_decision() -> None:
    def transport_with_reasoning(payload: dict) -> dict:
        response = fake_transport_aligned(payload)
        response["choices"][0]["message"]["reasoning_content"] = (
            "provider-private-reasoning"
        )
        return response

    client = DeepSeekClient(api_key="fake-key", transport=transport_with_reasoning)
    decision = _evaluator(client).evaluate(_request())

    assert "provider-private-reasoning" not in str(decision)
    assert "reasoning_content" not in repr(decision)


def test_resolver_cannot_omit_request_evidence_before_transport() -> None:
    transport_called = False

    class OmittingResolver:
        def resolve(self, persona_refs, relationship_refs, relationship_id):
            return ()

        def resolve_voice_activations(self, activations):
            return ()

    def transport(payload: dict) -> dict:
        nonlocal transport_called
        transport_called = True
        return fake_transport_aligned(payload)

    evaluator = DeepSeekContinuityEvaluator(
        client=DeepSeekClient(api_key="fake-key", transport=transport),
        evidence_resolver=OmittingResolver(),
    )

    with pytest.raises(
        EvidenceResolutionError,
        match="^resolved_evidence_contract_mismatch$",
    ):
        evaluator.evaluate(_request())

    assert transport_called is False


@pytest.mark.parametrize(
    "excerpt",
    ["", " \t\n", "\u00a0", "\u2003", "\u200b", "\ufeff", "\u2060", "\ufe0f", "\u0301"],
)
def test_resolver_cannot_supply_blank_evidence_before_transport(excerpt: str) -> None:
    transport_called = False

    class BlankEvidenceResolver:
        def resolve(self, persona_refs, relationship_refs, relationship_id):
            return (
                ResolvedEvidence(
                    ref_id=PERSONA_REF.ref_id,
                    kind=PERSONA_REF.kind.value,
                    excerpt=excerpt,
                ),
            )

        def resolve_voice_activations(self, activations):
            return ()

    def transport(payload: dict) -> dict:
        nonlocal transport_called
        transport_called = True
        return fake_transport_aligned(payload)

    evaluator = DeepSeekContinuityEvaluator(
        client=DeepSeekClient(api_key="fake-key", transport=transport),
        evidence_resolver=BlankEvidenceResolver(),
    )

    with pytest.raises(
        EvidenceResolutionError,
        match="^resolved_evidence_contract_mismatch$",
    ):
        evaluator.evaluate(_request())

    assert transport_called is False

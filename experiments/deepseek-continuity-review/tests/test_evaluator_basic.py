"""Test DeepSeekContinuityEvaluator with fake transport.

This test verifies:
1. Evaluator implements ContinuityEvaluatorV1 contract
2. Returns real ContinuityEvaluationDecision
3. Exactly 5 findings, one per axis
4. Valid enums and constraints
5. No reasoning leak
"""

import pytest
from erii.models.continuity import (
    ContinuityEvaluationRequest,
    ContinuityEvaluationDecision,
    ContinuityAxis,
    ContinuityFindingAssessment,
    ContinuityReasonCode,
    ContinuityFindingSeverity,
)
from erii.models.continuity_evidence import ContinuityEvidenceRef, ContinuityEvidenceKind

from erii_deepseek_continuity import (
    DeepSeekContinuityEvaluator,
    DeepSeekClient,
    FakeEvidenceResolver,
)


def fake_transport_aligned(payload):
    """Fake transport that returns aligned findings for all axes."""

    # Verify thinking switch is explicitly set
    assert "thinking" in payload
    assert payload["thinking"]["type"] in ("enabled", "disabled")

    # Build fake response with 5 aligned findings
    findings = []
    for axis in [
        "identity_values",
        "psychological_causality",
        "relationship_scope",
        "knowledge_memory_scope",
        "voice_style",
    ]:
        findings.append({
            "axis": axis,
            "assessment": "aligned",
            "severity": "info",
            "reason_code": "aligned",
            "reply_quote": "你好",
            "occurrence": 0,
            "supporting_basis_refs": ["test-persona-claim-1"],
            "conflicting_source_refs": [],
            "voice_activation_refs": [],
        })

    return {
        "choices": [{
            "message": {
                "content": f'{{"findings": {findings}}}',
            },
            "finish_reason": "stop",
        }],
        "usage": {
            "prompt_tokens": 100,
            "completion_tokens": 50,
        },
    }


def test_evaluator_returns_real_decision():
    """Test that evaluator returns real ContinuityEvaluationDecision."""

    # Setup
    client = DeepSeekClient(
        api_key="fake-key",
        thinking_enabled=True,
        transport=fake_transport_aligned,
    )

    evaluator = DeepSeekContinuityEvaluator(
        client=client,
        evidence_resolver=FakeEvidenceResolver(),
    )

    # Create request
    request = ContinuityEvaluationRequest(
        turn_id="test-turn-1",
        relationship_id="test-relationship-1",
        persona_id="test-persona-1",
        user_message="你好吗？",
        proposed_reply="你好",
        persona_manifest_id="test-manifest-1",
        context_baseline_fingerprint="0" * 64,
        persona_context_refs=(
            ContinuityEvidenceRef.create(
                ContinuityEvidenceKind.PERSONA_CLAIM,
                {
                    "manifest_id": "test-manifest-1",
                    "content_fingerprint": "1" * 64,
                    "claim_id": "test-claim-1",
                },
            ),
        ),
        relationship_context_refs=(),
        voice_pattern_activations=(),
    )

    # Execute
    decision = evaluator.evaluate(request)

    # Verify
    assert isinstance(decision, ContinuityEvaluationDecision)
    assert len(decision.findings) == 5

    # Verify all axes present
    axes = {f.axis for f in decision.findings}
    assert axes == {
        ContinuityAxis.IDENTITY_VALUES,
        ContinuityAxis.PSYCHOLOGICAL_CAUSALITY,
        ContinuityAxis.RELATIONSHIP_SCOPE,
        ContinuityAxis.KNOWLEDGE_MEMORY_SCOPE,
        ContinuityAxis.VOICE_STYLE,
    }

    # Verify each finding
    for finding in decision.findings:
        assert isinstance(finding.assessment, ContinuityFindingAssessment)
        assert isinstance(finding.severity, ContinuityFindingSeverity)
        assert isinstance(finding.reason_code, ContinuityReasonCode)
        assert finding.reply_quote in request.proposed_reply
        assert 0 <= finding.reply_start < finding.reply_end <= len(request.proposed_reply)


def test_thinking_disabled_explicitly_sent():
    """Test that thinking=disabled is explicitly sent when disabled."""

    captured_payload = {}

    def capture_transport(payload):
        captured_payload.update(payload)
        return fake_transport_aligned(payload)

    client = DeepSeekClient(
        api_key="fake-key",
        thinking_enabled=False,
        transport=capture_transport,
    )

    evaluator = DeepSeekContinuityEvaluator(
        client=client,
        evidence_resolver=FakeEvidenceResolver(),
    )

    request = ContinuityEvaluationRequest(
        turn_id="test-turn-1",
        relationship_id="test-relationship-1",
        persona_id="test-persona-1",
        user_message="你好吗？",
        proposed_reply="你好",
        persona_manifest_id="test-manifest-1",
        context_baseline_fingerprint="0" * 64,
        persona_context_refs=(),
        relationship_context_refs=(),
        voice_pattern_activations=(),
    )

    evaluator.evaluate(request)

    # Verify thinking was explicitly disabled
    assert captured_payload["thinking"]["type"] == "disabled"
    assert "reasoning_effort" not in captured_payload


def test_no_reasoning_leak_in_decision():
    """Test that reasoning never appears in decision."""

    def transport_with_reasoning(payload):
        response = fake_transport_aligned(payload)
        # Add reasoning_content to response
        response["choices"][0]["message"]["reasoning_content"] = "This is secret thinking..."
        return response

    client = DeepSeekClient(
        api_key="fake-key",
        thinking_enabled=True,
        transport=transport_with_reasoning,
    )

    evaluator = DeepSeekContinuityEvaluator(
        client=client,
        evidence_resolver=FakeEvidenceResolver(),
    )

    request = ContinuityEvaluationRequest(
        turn_id="test-turn-1",
        relationship_id="test-relationship-1",
        persona_id="test-persona-1",
        user_message="你好吗？",
        proposed_reply="你好",
        persona_manifest_id="test-manifest-1",
        context_baseline_fingerprint="0" * 64,
        persona_context_refs=(),
        relationship_context_refs=(),
        voice_pattern_activations=(),
    )

    decision = evaluator.evaluate(request)

    # Verify no reasoning in decision
    decision_str = str(decision)
    assert "secret thinking" not in decision_str
    assert "reasoning_content" not in decision_str

    # Verify in repr
    decision_repr = repr(decision)
    assert "secret thinking" not in decision_repr
    assert "reasoning_content" not in decision_repr


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

"""Manual test runner for DeepSeek evaluator (no pytest required)."""

import sys
sys.path.insert(0, 'D:/bate/erii')
sys.path.insert(0, 'D:/bate/erii/experiments/deepseek-continuity-review/src')

from erii.models.continuity import (
    ContinuityEvaluationRequest,
    ContinuityEvaluationDecision,
    ContinuityAxis,
)
from erii.models.continuity_evidence import ContinuityEvidenceRef, ContinuityEvidenceKind

from erii_deepseek_continuity import (
    DeepSeekContinuityEvaluator,
    DeepSeekClient,
    FakeEvidenceResolver,
)


def make_fake_transport_aligned(available_refs):
    """Create fake transport with specific available refs."""
    def fake_transport_aligned(payload):
        """Fake transport that returns aligned findings for all axes."""

        # Verify thinking switch is explicitly set
        assert "thinking" in payload
        assert payload["thinking"]["type"] in ("enabled", "disabled")

        # Build fake response with 5 aligned findings
        import json
        findings = []
        # Use first available ref if any, otherwise empty
        ref_to_use = list(available_refs)[0] if available_refs else None

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
                "supporting_basis_refs": [ref_to_use] if ref_to_use else [],
                "conflicting_source_refs": [],
                "voice_activation_refs": [],
            })

        return {
            "choices": [{
                "message": {
                    "content": json.dumps({"findings": findings}),
                },
                "finish_reason": "stop",
            }],
            "usage": {
                "prompt_tokens": 100,
                "completion_tokens": 50,
            },
        }
    return fake_transport_aligned


def test_evaluator_returns_real_decision():
    """Test that evaluator returns real ContinuityEvaluationDecision."""

    print("Test 1: Evaluator returns real decision...")

    # Create request first to get ref_id
    persona_ref = ContinuityEvidenceRef.create(
        ContinuityEvidenceKind.PERSONA_CLAIM,
        {
            "manifest_id": "test-manifest-1",
            "content_fingerprint": "1" * 64,
            "claim_id": "test-claim-1",
        },
    )

    # Setup with matching ref_id
    client = DeepSeekClient(
        api_key="fake-key",
        thinking_enabled=True,
        transport=make_fake_transport_aligned({persona_ref.ref_id}),
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
        persona_context_refs=(persona_ref,),
        relationship_context_refs=(),
        voice_pattern_activations=(),
    )

    # Execute
    decision = evaluator.evaluate(request)

    # Verify
    assert isinstance(decision, ContinuityEvaluationDecision), "Should return ContinuityEvaluationDecision"
    assert len(decision.findings) == 5, f"Should have 5 findings, got {len(decision.findings)}"

    # Verify all axes present
    axes = {f.axis for f in decision.findings}
    expected_axes = {
        ContinuityAxis.IDENTITY_VALUES,
        ContinuityAxis.PSYCHOLOGICAL_CAUSALITY,
        ContinuityAxis.RELATIONSHIP_SCOPE,
        ContinuityAxis.KNOWLEDGE_MEMORY_SCOPE,
        ContinuityAxis.VOICE_STYLE,
    }
    assert axes == expected_axes, f"Should have all 5 axes"

    print("OK Test 1 passed")


def test_no_reasoning_leak():
    """Test that reasoning never appears in decision."""

    print("Test 2: No reasoning leak...")

    # Create request with persona ref
    persona_ref = ContinuityEvidenceRef.create(
        ContinuityEvidenceKind.PERSONA_CLAIM,
        {
            "manifest_id": "test-manifest-1",
            "content_fingerprint": "1" * 64,
            "claim_id": "test-claim-1",
        },
    )

    def transport_with_reasoning(payload):
        import json
        # Create aligned response
        fake_aligned = make_fake_transport_aligned({persona_ref.ref_id})
        response = fake_aligned(payload)
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
        persona_context_refs=(persona_ref,),
        relationship_context_refs=(),
        voice_pattern_activations=(),
    )

    decision = evaluator.evaluate(request)

    # Verify no reasoning in decision
    decision_str = str(decision)
    assert "secret thinking" not in decision_str, "Reasoning leaked in str()"
    assert "reasoning_content" not in decision_str, "Reasoning field leaked in str()"

    decision_repr = repr(decision)
    assert "secret thinking" not in decision_repr, "Reasoning leaked in repr()"

    print("OK Test 2 passed")


if __name__ == "__main__":
    try:
        test_evaluator_returns_real_decision()
        test_no_reasoning_leak()
        print("\nOK All tests passed!")
    except Exception as e:
        print(f"\nERROR Test failed: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)

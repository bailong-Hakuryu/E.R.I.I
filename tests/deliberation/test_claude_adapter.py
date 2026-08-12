"""The reserved Claude seam is offline and advertises no live capability."""

from erii.deliberation.claude_adapter import (
    ClaudeDeliberationAdapter,
    check_anthropic_available,
)
from erii.deliberation.contracts import ProviderErrorCode
from erii.deliberation.schemas import (
    CompactDeliberationRequestV1,
    EvidenceViewV1,
    MessagePart,
    ReplyRealizationRequestV1,
    StagedPlanRequestV1,
    UserMessageEnvelope,
)


def test_anthropic_availability_check_has_no_import_side_effect() -> None:
    assert isinstance(check_anthropic_available(), bool)


def test_placeholder_descriptor_does_not_claim_unimplemented_capabilities() -> None:
    adapter = ClaudeDeliberationAdapter(model_id="claude-model-fixture")

    descriptor = adapter.descriptor

    assert descriptor.provider_kind == "anthropic_messages_unavailable"
    assert descriptor.adapter_contract == (
        "erii-character-deliberation-claude-placeholder/v1"
    )
    assert not descriptor.supports_compact
    assert not descriptor.supports_staged
    assert not descriptor.supports_cancellation
    assert descriptor.structured_output_strategy == "unavailable"
    rendered = repr(descriptor)
    assert "credential" not in rendered.lower()
    assert "api_key" not in rendered.lower()


def test_every_placeholder_operation_returns_stable_unavailable_code() -> None:
    adapter = ClaudeDeliberationAdapter(model_id="claude-model-fixture")
    compact_request = _compact_request()

    results = (
        adapter.compact(compact_request, timeout=1.0),
        adapter.plan(StagedPlanRequestV1(), timeout=1.0),
        adapter.realize(ReplyRealizationRequestV1(), timeout=1.0),
    )

    for result in results:
        assert not result.success
        assert result.error_code is ProviderErrorCode.CAPABILITY_UNAVAILABLE
        assert result.error_message == ProviderErrorCode.CAPABILITY_UNAVAILABLE.value


def test_placeholder_validates_bounded_configuration() -> None:
    try:
        ClaudeDeliberationAdapter(model_id="claude-model-fixture", max_tokens=0)
    except ValueError as exc:
        assert str(exc) == "max_tokens must be a positive integer"
    else:
        raise AssertionError("max_tokens=0 must be rejected")


def _compact_request():
    return CompactDeliberationRequestV1(
        user_envelope=UserMessageEnvelope(
            parts=(MessagePart(part_id="user-part", exact_utf8="fixture"),),
            canonical_fingerprint="a" * 64,
        ),
        evidence_view=EvidenceViewV1(
            view_id="view-1",
            relationship_id="relationship-1",
            turn_id="turn-1",
            view_fingerprint="b" * 64,
        ),
        relationship_id="relationship-1",
        turn_id="turn-1",
    )

"""End-to-end offline C0 host bridge tests over a real TurnRecord."""

from dataclasses import replace
import socket
import urllib.request

import pytest

from erii.deliberation.claude_offline import (
    CapabilityEvidenceKind,
    CapabilityStatus,
    ClaudeCapabilityProfile,
    OfflineClaudeActor,
)
from erii.deliberation.contracts import ProviderErrorCode
from erii.deliberation.core_validator import TrustedAuthoritySecret
from erii.deliberation.fake_actor import FakeActor
from erii.deliberation.host_bridge import (
    DeliberationHostBridge,
    fingerprint_evidence_view,
    fingerprint_user_envelope,
)
from erii.deliberation.schemas import (
    EvidenceViewV1,
    MessagePart,
    UserMessageEnvelope,
)
from erii.models.turn import SourceTranscript, TurnMessage, TurnRecord, TurnRole, TurnStatus
from erii.models.turn_context import (
    TurnBlueprintReference,
    TurnContextBaseline,
    TurnPremiseReference,
)


class MutableTurnResolver:
    def __init__(self, turn: TurnRecord) -> None:
        self.turn = turn

    def resolve_open_turn(self, turn_id: str) -> TurnRecord:
        assert turn_id == self.turn.turn_id
        return self.turn


def _open_turn() -> TurnRecord:
    baseline = TurnContextBaseline.create(
        relationship_id="relationship-1",
        turn_id="turn-1",
        persona_id="persona-1",
        blueprint=TurnBlueprintReference(
            blueprint_id="blueprint-1",
            revision=1,
            source_sha256="a" * 64,
        ),
        manifest=None,
        approved_growth_refs=(),
        premise=TurnPremiseReference(
            premise_id="premise-1",
            content_fingerprint="b" * 64,
        ),
        direct_event_count=0,
        adjudication_count=0,
        history_prefix_fingerprint="c" * 64,
        policy_versions={
            "relationship_baseline_policy": "v1",
            "relationship_history_projection": "v1",
            "relationship_safety_policy": "v1",
            "interaction_context_policy": "v1",
            "voice_matcher_policy": "v1",
        },
    )
    return TurnRecord(
        turn_id="turn-1",
        relationship_id="relationship-1",
        status=TurnStatus.OPEN,
        transcript=SourceTranscript(
            user_message=TurnMessage(
                message_id="user-message-1",
                role=TurnRole.USER,
                content="Stay with me for a moment.",
            )
        ),
        context_baseline=baseline,
    )


def _user_envelope() -> UserMessageEnvelope:
    provisional = UserMessageEnvelope(
        parts=(
            MessagePart(
                part_id="user-message-1",
                kind="text",
                exact_utf8="Stay with me for a moment.",
            ),
        ),
        canonical_fingerprint="0" * 64,
    )
    return provisional.model_copy(
        update={"canonical_fingerprint": fingerprint_user_envelope(provisional)}
    )


def _evidence_view() -> EvidenceViewV1:
    provisional = EvidenceViewV1(
        view_id="view-1",
        relationship_id="relationship-1",
        turn_id="turn-1",
        items=(),
        allowed_claim_kinds=(),
        view_fingerprint="0" * 64,
    )
    return provisional.model_copy(
        update={"view_fingerprint": fingerprint_evidence_view(provisional)}
    )


def _prepare(resolver: MutableTurnResolver, actor: OfflineClaudeActor):
    bridge = DeliberationHostBridge(
        resolver=resolver,
        secret=TrustedAuthoritySecret(b"k" * 32),
    )
    prepared = bridge.prepare_compact(
        turn_id="turn-1",
        user_envelope=_user_envelope(),
        evidence_view=_evidence_view(),
        actor_descriptor=actor.descriptor,
        router_policy={"mode": "compact_every_turn", "version": "c0"},
        run_epoch=1,
        idempotency_key="attempt-1",
    )
    return bridge, prepared


def test_offline_host_to_sse_to_exact_binding_has_no_network(monkeypatch) -> None:
    def reject_network(*_args, **_kwargs):
        raise AssertionError("network access attempted")

    monkeypatch.setattr(socket, "create_connection", reject_network)
    monkeypatch.setattr(socket.socket, "connect", reject_network)
    monkeypatch.setattr(urllib.request, "urlopen", reject_network)

    resolver = MutableTurnResolver(_open_turn())
    actor = OfflineClaudeActor()
    bridge, prepared = _prepare(resolver, actor)
    result = bridge.execute_compact(prepared=prepared, actor=actor, timeout=5.0)
    assert result.success
    assert result.data is not None
    assert result.discarded_reasoning_blocks == 1
    valid, errors = bridge.verify_candidate(prepared=prepared, candidate=result.data)
    assert valid, errors


def test_offline_capability_matrix_never_treats_untested_as_supported() -> None:
    profile = OfflineClaudeActor().capability_profile
    assert profile.evidence_kind is CapabilityEvidenceKind.OFFLINE_FIXTURE
    assert profile.strict_tool is CapabilityStatus.VERIFIED
    assert profile.supports("strict_tool")
    assert not profile.supports("json_output")
    assert not profile.supports("adaptive_thinking")
    with pytest.raises(ValueError, match="unknown Claude capability"):
        profile.supports("imaginary_capability")
    with pytest.raises(TypeError, match="strict_tool"):
        ClaudeCapabilityProfile(
            model_id="offline-fixture",
            evidence_kind=CapabilityEvidenceKind.OFFLINE_FIXTURE,
            json_output=CapabilityStatus.UNTESTED,
            strict_tool="verified",  # type: ignore[arg-type]
            adaptive_thinking=CapabilityStatus.UNTESTED,
            hidden_thinking_display=CapabilityStatus.UNTESTED,
            prompt_cache=CapabilityStatus.UNTESTED,
        )


def test_stale_turn_record_discards_late_provider_result() -> None:
    resolver = MutableTurnResolver(_open_turn())
    actor = OfflineClaudeActor()
    bridge, prepared = _prepare(resolver, actor)
    resolver.turn = replace(resolver.turn, record_version=2)
    result = bridge.execute_compact(prepared=prepared, actor=actor, timeout=5.0)
    assert not result.success
    assert result.error_code is ProviderErrorCode.LATE_RESULT


def test_actor_swap_after_prepare_fails_closed() -> None:
    resolver = MutableTurnResolver(_open_turn())
    actor = OfflineClaudeActor()
    bridge, prepared = _prepare(resolver, actor)
    result = bridge.execute_compact(prepared=prepared, actor=FakeActor(), timeout=5.0)
    assert not result.success
    assert result.error_code is ProviderErrorCode.CONFLICT


def test_provider_canary_never_enters_validated_candidate() -> None:
    resolver = MutableTurnResolver(_open_turn())
    actor = OfflineClaudeActor(leak_output=True)
    bridge, prepared = _prepare(resolver, actor)
    result = bridge.execute_compact(prepared=prepared, actor=actor, timeout=5.0)
    assert not result.success
    assert result.data is None
    assert result.error_code is ProviderErrorCode.OUTPUT_CANARY_LEAK


def test_prepare_rejects_user_text_not_in_open_turn() -> None:
    resolver = MutableTurnResolver(_open_turn())
    actor = OfflineClaudeActor()
    bridge = DeliberationHostBridge(
        resolver=resolver,
        secret=TrustedAuthoritySecret(b"k" * 32),
    )
    wrong = _user_envelope().model_copy(
        update={
            "parts": (
                MessagePart(
                    part_id="user-message-1",
                    kind="text",
                    exact_utf8="Different text",
                ),
            )
        }
    )
    with pytest.raises(ValueError, match="does not match"):
        bridge.prepare_compact(
            turn_id="turn-1",
            user_envelope=wrong,
            evidence_view=_evidence_view(),
            actor_descriptor=actor.descriptor,
            router_policy={"mode": "compact_every_turn"},
            run_epoch=1,
            idempotency_key="attempt-1",
        )


def test_prepared_request_swap_cannot_reuse_a_valid_host_commitment() -> None:
    resolver = MutableTurnResolver(_open_turn())
    actor = OfflineClaudeActor()
    bridge, prepared = _prepare(resolver, actor)
    replacement_user = prepared.actor_request.user_envelope.model_copy(
        update={
            "parts": (
                MessagePart(
                    part_id="user-message-1",
                    kind="text",
                    exact_utf8="A substituted message.",
                ),
            )
        }
    )
    swapped_request = prepared.actor_request.model_copy(
        update={"user_envelope": replacement_user}
    )
    swapped = replace(prepared, actor_request=swapped_request)
    result = bridge.execute_compact(prepared=swapped, actor=actor, timeout=5.0)
    assert not result.success
    assert result.error_code is ProviderErrorCode.CONFLICT

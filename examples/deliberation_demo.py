"""Offline Character Deliberation C0 host-bridge demonstration.

The example intentionally displays only the final reply. The private Interior
Scene remains process-local and is neither logged nor persisted.
"""

from erii.deliberation.claude_offline import OfflineClaudeActor
from erii.deliberation.core_validator import TrustedAuthoritySecret
from erii.deliberation.host_bridge import (
    DeliberationHostBridge,
    fingerprint_evidence_view,
    fingerprint_user_envelope,
)
from erii.deliberation.schemas import EvidenceViewV1, MessagePart, UserMessageEnvelope
from erii.models.turn import SourceTranscript, TurnMessage, TurnRecord, TurnRole, TurnStatus
from erii.models.turn_context import (
    TurnBlueprintReference,
    TurnContextBaseline,
    TurnPremiseReference,
)


class DemoTurnResolver:
    """Minimal host-owned resolver for one real open Turn snapshot."""

    def __init__(self, turn: TurnRecord) -> None:
        self._turn = turn

    def resolve_open_turn(self, turn_id: str) -> TurnRecord:
        if turn_id != self._turn.turn_id:
            raise KeyError("turn not found")
        return self._turn


def make_open_turn() -> TurnRecord:
    baseline = TurnContextBaseline.create(
        relationship_id="demo-relationship",
        turn_id="demo-turn",
        persona_id="demo-persona",
        blueprint=TurnBlueprintReference(
            blueprint_id="demo-blueprint",
            revision=1,
            source_sha256="a" * 64,
        ),
        manifest=None,
        approved_growth_refs=(),
        premise=TurnPremiseReference(
            premise_id="demo-premise",
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
        turn_id="demo-turn",
        relationship_id="demo-relationship",
        status=TurnStatus.OPEN,
        transcript=SourceTranscript(
            user_message=TurnMessage(
                message_id="demo-user-message",
                role=TurnRole.USER,
                content="Stay with me for a moment.",
            )
        ),
        context_baseline=baseline,
    )


def make_user_envelope() -> UserMessageEnvelope:
    provisional = UserMessageEnvelope(
        parts=(
            MessagePart(
                part_id="demo-user-message",
                kind="text",
                exact_utf8="Stay with me for a moment.",
            ),
        ),
        canonical_fingerprint="0" * 64,
    )
    return provisional.model_copy(
        update={"canonical_fingerprint": fingerprint_user_envelope(provisional)}
    )


def make_evidence_view() -> EvidenceViewV1:
    provisional = EvidenceViewV1(
        view_id="demo-evidence-view",
        relationship_id="demo-relationship",
        turn_id="demo-turn",
        items=(),
        allowed_claim_kinds=(),
        view_fingerprint="0" * 64,
    )
    return provisional.model_copy(
        update={"view_fingerprint": fingerprint_evidence_view(provisional)}
    )


def main() -> int:
    actor = OfflineClaudeActor()
    bridge = DeliberationHostBridge(
        resolver=DemoTurnResolver(make_open_turn()),
        secret=TrustedAuthoritySecret(b"d" * 32),
    )
    prepared = bridge.prepare_compact(
        turn_id="demo-turn",
        user_envelope=make_user_envelope(),
        evidence_view=make_evidence_view(),
        actor_descriptor=actor.descriptor,
        router_policy={"mode": "compact_every_turn", "version": "c0"},
        run_epoch=1,
        idempotency_key="demo-attempt-1",
    )
    result = bridge.execute_compact(prepared=prepared, actor=actor, timeout=5.0)
    if not result.success or result.data is None:
        print(f"C0 demo failed: {result.error_code}")
        return 1

    valid, errors = bridge.verify_candidate(prepared=prepared, candidate=result.data)
    if not valid:
        print(f"C0 binding verification failed: {errors}")
        return 1

    print("C0 offline host bridge: verified")
    print(f"discarded reasoning blocks: {result.discarded_reasoning_blocks}")
    for part in result.data.decision.reply_candidate.parts:
        print(f"reply[{part.part_id}]: {part.exact_utf8}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

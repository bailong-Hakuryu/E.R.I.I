"""Trusted, offline C0 host bridge over a real open ERII TurnRecord.

This bridge is deliberately not wired into ``ERIIEngine``.  It proves the
authority, isolation, parser and exact-binding contracts without changing the
kernel's runtime or persistence formats.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Mapping, Protocol

from erii.models.turn import TurnRecord, TurnStatus

from .contracts import ActorDescriptor, CharacterActor, ProviderErrorCode, ProviderResult
from .core_validator import (
    AuthorityState,
    CoreTrustedValidator,
    ResultBinding,
    TrustedAuthoritySecret,
    TrustedEnvelopeV2,
)
from .identifiers import validate_identifier
from .schemas import (
    CompactDecisionV1,
    CompactDeliberationRequestV1,
    EvidenceViewV1,
    UserMessageEnvelope,
)
from .security import run_security_scan
from .strict_codec import StrictCanonicalCodec


class TurnAuthorityResolver(Protocol):
    def resolve_open_turn(self, turn_id: str) -> TurnRecord:
        """Return the current durable Turn snapshot for one candidate run."""
        ...


@dataclass(frozen=True)
class HostRunCommitmentV1:
    commitment_version: str
    relationship_id: str
    turn_id: str
    persona_id: str
    source_revision: str
    turn_record_version: int
    context_baseline_fingerprint: str
    user_message_fingerprint: str
    evidence_view_fingerprint: str
    actor_descriptor_fingerprint: str
    router_policy_fingerprint: str
    run_epoch: int
    idempotency_key: str
    envelope_signature: str
    commitment_fingerprint: str
    hmac_signature: str

    def __post_init__(self) -> None:
        if self.commitment_version != "erii-deliberation-host-run/v1":
            raise ValueError("unsupported host-run commitment version")
        for name in (
            "relationship_id",
            "turn_id",
            "persona_id",
            "source_revision",
            "idempotency_key",
        ):
            validate_identifier(getattr(self, name), name)
        if type(self.turn_record_version) is not int or self.turn_record_version < 0:
            raise ValueError("turn_record_version must be a non-negative int")
        if type(self.run_epoch) is not int or self.run_epoch < 0:
            raise ValueError("run_epoch must be a non-negative int")
        for name in (
            "context_baseline_fingerprint",
            "user_message_fingerprint",
            "evidence_view_fingerprint",
            "actor_descriptor_fingerprint",
            "router_policy_fingerprint",
            "envelope_signature",
            "commitment_fingerprint",
            "hmac_signature",
        ):
            _require_fingerprint(getattr(self, name), name)


@dataclass(frozen=True)
class PreparedDeliberationRunV1:
    authority: AuthorityState
    envelope: TrustedEnvelopeV2
    actor_request: CompactDeliberationRequestV1 = field(repr=False)
    commitment: HostRunCommitmentV1


@dataclass(frozen=True)
class ValidatedDeliberationCandidateV1:
    decision: CompactDecisionV1
    result_binding: ResultBinding
    commitment_fingerprint: str


class DeliberationHostBridge:
    """Host-owned orchestrator for one compact C0 attempt."""

    def __init__(
        self,
        *,
        resolver: TurnAuthorityResolver,
        secret: TrustedAuthoritySecret,
    ) -> None:
        self._resolver = resolver
        self._secret = secret
        self._validator = CoreTrustedValidator(secret)

    def prepare_compact(
        self,
        *,
        turn_id: str,
        user_envelope: UserMessageEnvelope,
        evidence_view: EvidenceViewV1,
        actor_descriptor: ActorDescriptor,
        router_policy: Mapping[str, Any],
        run_epoch: int,
        idempotency_key: str,
    ) -> PreparedDeliberationRunV1:
        turn = self._resolver.resolve_open_turn(turn_id)
        baseline = self._require_open_authority(turn)
        validate_identifier(idempotency_key, "idempotency_key")
        if type(run_epoch) is not int or run_epoch < 0:
            raise ValueError("run_epoch must be a non-negative int")

        if len(user_envelope.parts) != 1:
            raise ValueError("C0 host bridge requires one exact user message part")
        user_part = user_envelope.parts[0]
        source_message = turn.transcript.user_message
        if user_part.part_id != source_message.message_id or user_part.exact_utf8 != source_message.content:
            raise ValueError("user envelope does not match the open Turn transcript")
        if evidence_view.relationship_id != turn.relationship_id or evidence_view.turn_id != turn.turn_id:
            raise ValueError("evidence view is outside the open Turn scope")

        user_fingerprint = fingerprint_user_envelope(user_envelope)
        evidence_fingerprint = fingerprint_evidence_view(evidence_view)
        if user_envelope.canonical_fingerprint != user_fingerprint:
            raise ValueError("user envelope fingerprint is invalid")
        if evidence_view.view_fingerprint != evidence_fingerprint:
            raise ValueError("evidence view fingerprint is invalid")

        actor_request = CompactDeliberationRequestV1(
            user_envelope=user_envelope,
            evidence_view=evidence_view,
            relationship_id=turn.relationship_id,
            turn_id=turn.turn_id,
        )
        authority = AuthorityState(
            current_epoch=run_epoch,
            turn_status=turn.status,
            active_relationship_id=turn.relationship_id,
            active_turn_id=turn.turn_id,
            active_persona_id=baseline.persona_id,
        )
        envelope = self._validator.create_envelope(
            relationship_id=turn.relationship_id,
            turn_id=turn.turn_id,
            persona_id=baseline.persona_id,
            evidence_view_fingerprint=evidence_fingerprint,
            user_message_fingerprint=user_fingerprint,
            run_epoch=run_epoch,
            expected_turn_state=turn.status,
        )

        actor_fp = StrictCanonicalCodec.fingerprint(
            asdict(actor_descriptor),
            domain="erii-deliberation-actor-descriptor/v1",
        )
        router_fp = StrictCanonicalCodec.fingerprint(
            dict(router_policy),
            domain="erii-deliberation-router-policy/v1",
        )
        identity = {
            "commitment_version": "erii-deliberation-host-run/v1",
            "relationship_id": turn.relationship_id,
            "turn_id": turn.turn_id,
            "persona_id": baseline.persona_id,
            "source_revision": turn.source_revision,
            "turn_record_version": turn.record_version,
            "context_baseline_fingerprint": baseline.baseline_fingerprint,
            "user_message_fingerprint": user_fingerprint,
            "evidence_view_fingerprint": evidence_fingerprint,
            "actor_descriptor_fingerprint": actor_fp,
            "router_policy_fingerprint": router_fp,
            "run_epoch": run_epoch,
            "idempotency_key": idempotency_key,
            "envelope_signature": envelope.hmac_signature,
        }
        commitment_fp = StrictCanonicalCodec.fingerprint(
            identity,
            domain="erii-deliberation-host-run/v1",
        )
        signature = self._secret.sign(f"erii-deliberation-host-run/v1\x00{commitment_fp}")
        commitment = HostRunCommitmentV1(
            **identity,
            commitment_fingerprint=commitment_fp,
            hmac_signature=signature,
        )
        return PreparedDeliberationRunV1(authority, envelope, actor_request, commitment)

    def execute_compact(
        self,
        *,
        prepared: PreparedDeliberationRunV1,
        actor: CharacterActor,
        timeout: float,
    ) -> ProviderResult[ValidatedDeliberationCandidateV1]:
        try:
            self._require_actor_descriptor(prepared, actor.descriptor)
        except ValueError:
            return _failure(ProviderErrorCode.CONFLICT)
        if not self._verify_prepared(prepared):
            return _failure(ProviderErrorCode.CONFLICT)
        current_before_call = self._resolver.resolve_open_turn(prepared.commitment.turn_id)
        if not self._matches_current_turn(prepared, current_before_call):
            return _failure(ProviderErrorCode.LATE_RESULT)

        try:
            provider_result = actor.compact(prepared.actor_request, timeout=timeout)
        except Exception:
            return _failure(ProviderErrorCode.UNAVAILABLE)
        if not isinstance(provider_result, ProviderResult):
            return _failure(ProviderErrorCode.OUTPUT_SCHEMA_INVALID)
        if not provider_result.success:
            code = provider_result.error_code
            if code is None:
                return _failure(ProviderErrorCode.OUTPUT_SCHEMA_INVALID)
            return ProviderResult(
                success=False,
                error_code=code,
                error_message=code.value,
                usage=provider_result.usage,
                discarded_reasoning_blocks=provider_result.discarded_reasoning_blocks,
                canary_hit=provider_result.canary_hit,
            )
        decision = provider_result.data
        if not isinstance(decision, CompactDecisionV1):
            return _failure(ProviderErrorCode.OUTPUT_SCHEMA_INVALID)

        current = self._resolver.resolve_open_turn(prepared.commitment.turn_id)
        if not self._matches_current_turn(prepared, current):
            return _failure(ProviderErrorCode.LATE_RESULT)
        scan = run_security_scan(decision, prepared.actor_request.evidence_view)
        if scan.canary_leaked:
            return _failure(ProviderErrorCode.OUTPUT_CANARY_LEAK, canary_hit=True)
        if scan.invalid_evidence_refs:
            return _failure(ProviderErrorCode.OUTPUT_EVIDENCE_INVALID)

        binding = self._validator.create_result_binding(
            prepared.envelope,
            decision,
            decision.reply_candidate,
            prepared.authority,
        )
        validated = ValidatedDeliberationCandidateV1(
            decision=decision,
            result_binding=binding,
            commitment_fingerprint=prepared.commitment.commitment_fingerprint,
        )
        valid, _errors = self.verify_candidate(prepared=prepared, candidate=validated)
        if not valid:
            return _failure(ProviderErrorCode.CONFLICT)
        return ProviderResult(
            success=True,
            data=validated,
            usage=provider_result.usage,
            discarded_reasoning_blocks=provider_result.discarded_reasoning_blocks,
        )

    def verify_candidate(
        self,
        *,
        prepared: PreparedDeliberationRunV1,
        candidate: ValidatedDeliberationCandidateV1,
    ) -> tuple[bool, tuple[str, ...]]:
        """Re-resolve authority and verify every artifact before completion."""
        errors: list[str] = []
        if not self._verify_prepared(prepared):
            errors.append("prepared run commitment is invalid")
        if candidate.commitment_fingerprint != prepared.commitment.commitment_fingerprint:
            errors.append("candidate is bound to another prepared run")
        current = self._resolver.resolve_open_turn(prepared.commitment.turn_id)
        if not self._matches_current_turn(prepared, current):
            errors.append("source Turn authority is stale")
        binding_valid, binding_errors = self._validator.verify_result_binding(
            candidate.result_binding,
            prepared.envelope,
            candidate.decision,
            candidate.decision.reply_candidate,
            prepared.authority,
        )
        if not binding_valid:
            errors.extend(binding_errors)
        return not errors, tuple(errors)

    def _require_open_authority(self, turn: TurnRecord):
        if not isinstance(turn, TurnRecord) or turn.status is not TurnStatus.OPEN:
            raise ValueError("character deliberation requires a real open TurnRecord")
        if turn.context_baseline is None:
            raise ValueError("open Turn is missing its frozen context baseline")
        baseline = turn.context_baseline
        if (
            baseline.relationship_id != turn.relationship_id
            or baseline.turn_id != turn.turn_id
        ):
            raise ValueError("Turn baseline scope mismatch")
        return baseline

    def _verify_prepared(self, prepared: PreparedDeliberationRunV1) -> bool:
        commitment = prepared.commitment
        request = prepared.actor_request
        if (
            request.relationship_id != commitment.relationship_id
            or request.turn_id != commitment.turn_id
            or request.evidence_view.relationship_id != commitment.relationship_id
            or request.evidence_view.turn_id != commitment.turn_id
            or fingerprint_user_envelope(request.user_envelope)
            != commitment.user_message_fingerprint
            or request.user_envelope.canonical_fingerprint
            != commitment.user_message_fingerprint
            or fingerprint_evidence_view(request.evidence_view)
            != commitment.evidence_view_fingerprint
            or request.evidence_view.view_fingerprint
            != commitment.evidence_view_fingerprint
            or prepared.envelope.relationship_id != commitment.relationship_id
            or prepared.envelope.turn_id != commitment.turn_id
            or prepared.envelope.persona_id != commitment.persona_id
            or prepared.envelope.run_epoch != commitment.run_epoch
            or prepared.envelope.user_message_fingerprint
            != commitment.user_message_fingerprint
            or prepared.envelope.evidence_view_fingerprint
            != commitment.evidence_view_fingerprint
            or prepared.envelope.hmac_signature != commitment.envelope_signature
        ):
            return False
        identity = {
            key: value
            for key, value in asdict(commitment).items()
            if key not in {"commitment_fingerprint", "hmac_signature"}
        }
        actual = StrictCanonicalCodec.fingerprint(
            identity,
            domain="erii-deliberation-host-run/v1",
        )
        if actual != commitment.commitment_fingerprint:
            return False
        if not self._secret.verify(
            f"erii-deliberation-host-run/v1\x00{actual}",
            commitment.hmac_signature,
        ):
            return False
        return self._validator.verify_envelope(prepared.envelope, prepared.authority)[0]

    def _matches_current_turn(
        self,
        prepared: PreparedDeliberationRunV1,
        turn: TurnRecord,
    ) -> bool:
        commitment = prepared.commitment
        return bool(
            turn.status is TurnStatus.OPEN
            and turn.turn_id == commitment.turn_id
            and turn.relationship_id == commitment.relationship_id
            and turn.record_version == commitment.turn_record_version
            and turn.source_revision == commitment.source_revision
            and turn.context_baseline is not None
            and turn.context_baseline.baseline_fingerprint
            == commitment.context_baseline_fingerprint
        )

    @staticmethod
    def _require_actor_descriptor(
        prepared: PreparedDeliberationRunV1,
        descriptor: ActorDescriptor,
    ) -> None:
        actual = StrictCanonicalCodec.fingerprint(
            asdict(descriptor),
            domain="erii-deliberation-actor-descriptor/v1",
        )
        if actual != prepared.commitment.actor_descriptor_fingerprint:
            raise ValueError("actor descriptor does not match prepared run")


def fingerprint_user_envelope(value: UserMessageEnvelope) -> str:
    payload = value.model_dump(mode="json", exclude={"canonical_fingerprint"})
    return StrictCanonicalCodec.fingerprint(
        payload,
        domain="erii-deliberation-user-envelope/v1",
    )


def fingerprint_evidence_view(value: EvidenceViewV1) -> str:
    payload = value.model_dump(mode="json", exclude={"view_fingerprint"})
    return StrictCanonicalCodec.fingerprint(
        payload,
        domain="erii-deliberation-evidence-view/v1",
    )


def _failure(
    code: ProviderErrorCode,
    *,
    canary_hit: bool = False,
) -> ProviderResult[Any]:
    return ProviderResult(
        success=False,
        error_code=code,
        error_message=code.value,
        canary_hit=canary_hit,
    )


def _require_fingerprint(value: str, field_name: str) -> None:
    if type(value) is not str or len(value) != 64:
        raise ValueError(f"{field_name} must be a lowercase SHA-256 fingerprint")
    if any(character not in "0123456789abcdef" for character in value):
        raise ValueError(f"{field_name} must be a lowercase SHA-256 fingerprint")


__all__ = [
    "TurnAuthorityResolver",
    "HostRunCommitmentV1",
    "PreparedDeliberationRunV1",
    "ValidatedDeliberationCandidateV1",
    "DeliberationHostBridge",
    "fingerprint_user_envelope",
    "fingerprint_evidence_view",
]

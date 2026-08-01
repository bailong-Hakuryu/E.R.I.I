"""Deterministic adjudication of untrusted relationship candidates."""

from dataclasses import replace
import hashlib
import json
import re
from typing import Dict, Iterable, List, Mapping, Optional, Sequence, Tuple
import uuid

from erii.models.adjudication import (
    AdjudicationBatchResult,
    AdjudicationRecord,
    CandidateConflictError,
    DecisionOutcome,
    DecisionReceipt,
    EvidenceCitation,
    EvidenceReference,
    GrowthTriggerKind,
    PersonaGrowthConflictError,
    PersonaGrowthDecision,
    PersonaGrowthIntentCandidate,
    PersonaGrowthProposal,
    PersonaGrowthStatus,
    RelationshipCandidateBatch,
    RelationshipEventCandidate,
    RelationshipPolicySpec,
    RelationshipSignalType,
    SignalStrength,
    SourceMessage,
    SourceRole,
    SourceTurn,
)
from erii.models.relationship import (
    MAX_AUTOMATIC_STATE_DELTA,
    RelationshipEvent,
    RelationshipEventType,
    RelationshipProfile,
    utc_now,
)
from erii.models.temporal import (
    OpenLoopResolution,
    OpenLoopResolutionKind,
    OpenLoopSpec,
    PromiseConditionConfirmation,
    PromiseResolution,
    PromiseResolutionKind,
    PromiseResponsibleParty,
    PromiseSpec,
    TemporalPayload,
)
from erii.storage.base import BaseStorage


RULE_VERSION = "relationship-adjudication-v1"
MIN_EXTRACTION_CONFIDENCE = 0.5
MIN_STATE_CONFIDENCE = 0.7
MIN_REFLECTION_CONFIDENCE = 0.8
MIN_PIVOTAL_CONFIDENCE = 0.9
MIN_PROMISE_EXTRACTION_CONFIDENCE = 0.8
MIN_PROMISE_INTERPRETATION_CONFIDENCE = 0.8
MIN_TEMPORAL_EXTRACTION_CONFIDENCE = 0.8
MIN_TEMPORAL_INTERPRETATION_CONFIDENCE = 0.8

_STRENGTH_MULTIPLIERS = {
    SignalStrength.WEAK: 0.5,
    SignalStrength.MODERATE: 1.0,
    SignalStrength.STRONG: 1.5,
}

_BASE_STATE_DELTAS: Mapping[RelationshipSignalType, Mapping[str, float]] = {
    RelationshipSignalType.NEUTRAL: {},
    RelationshipSignalType.GRATITUDE: {"familiarity": 0.01, "trust": 0.01},
    RelationshipSignalType.DISCLOSURE: {"familiarity": 0.02, "intimacy": 0.02},
    RelationshipSignalType.RELIABILITY: {"trust": 0.03, "safety": 0.01},
    RelationshipSignalType.BOUNDARY_RESPECTED: {"trust": 0.02, "safety": 0.03},
    RelationshipSignalType.BOUNDARY_VIOLATION: {
        "trust": -0.04,
        "safety": -0.04,
        "conflict_tension": 0.04,
    },
    RelationshipSignalType.CONFLICT: {
        "trust": -0.02,
        "safety": -0.02,
        "conflict_tension": 0.05,
    },
    RelationshipSignalType.REPAIR: {
        "trust": 0.03,
        "safety": 0.03,
        "conflict_tension": -0.05,
    },
    RelationshipSignalType.SHARED_EXPERIENCE: {
        "familiarity": 0.03,
        "intimacy": 0.02,
    },
    RelationshipSignalType.REMEMBRANCE: {"familiarity": 0.01, "intimacy": 0.01},
    RelationshipSignalType.COMMITMENT: {"trust": 0.02, "safety": 0.01},
    RelationshipSignalType.DISAPPOINTMENT: {
        "safety": -0.01,
        "conflict_tension": 0.02,
    },
    RelationshipSignalType.SUPPORT: {"trust": 0.02, "safety": 0.02},
}

_SIGNAL_EVENT_TYPES = {
    RelationshipSignalType.NEUTRAL: set(RelationshipEventType),
    RelationshipSignalType.GRATITUDE: {
        RelationshipEventType.OBSERVATION,
        RelationshipEventType.SHARED_EXPERIENCE,
    },
    RelationshipSignalType.DISCLOSURE: {
        RelationshipEventType.OBSERVATION,
        RelationshipEventType.SHARED_EXPERIENCE,
    },
    RelationshipSignalType.RELIABILITY: {
        RelationshipEventType.OBSERVATION,
        RelationshipEventType.PROMISE,
    },
    RelationshipSignalType.BOUNDARY_RESPECTED: {
        RelationshipEventType.OBSERVATION,
        RelationshipEventType.REPAIR,
    },
    RelationshipSignalType.BOUNDARY_VIOLATION: {
        RelationshipEventType.OBSERVATION,
        RelationshipEventType.CONFLICT,
    },
    RelationshipSignalType.CONFLICT: {RelationshipEventType.CONFLICT},
    RelationshipSignalType.REPAIR: {RelationshipEventType.REPAIR},
    RelationshipSignalType.SHARED_EXPERIENCE: {RelationshipEventType.SHARED_EXPERIENCE},
    RelationshipSignalType.REMEMBRANCE: {
        RelationshipEventType.OBSERVATION,
        RelationshipEventType.REFLECTION,
        RelationshipEventType.SHARED_EXPERIENCE,
    },
    RelationshipSignalType.COMMITMENT: {RelationshipEventType.PROMISE},
    RelationshipSignalType.DISAPPOINTMENT: {
        RelationshipEventType.OBSERVATION,
        RelationshipEventType.CONFLICT,
        RelationshipEventType.REFLECTION,
    },
    RelationshipSignalType.SUPPORT: {
        RelationshipEventType.OBSERVATION,
        RelationshipEventType.REPAIR,
        RelationshipEventType.SHARED_EXPERIENCE,
    },
}


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _canonical_hash(value: object) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return _sha256_text(payload)


def canonical_temporal_payload(value: Optional[object]) -> Optional[object]:
    """Returns one JSON-compatible temporal payload for stable identities.

    The helper accepts both the untrusted Pydantic candidate values and the
    frozen durable values used by relationship events.  Keeping this
    normalization beside the occurrence hash gives imports and adjudication
    one canonical identity rule without coupling them to a concrete payload
    class.
    """
    if value is None:
        return None
    if hasattr(value, "to_dict"):
        value = value.to_dict()
    elif hasattr(value, "model_dump"):
        value = value.model_dump(mode="json")
    try:
        return json.loads(
            json.dumps(
                value,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
        )
    except (TypeError, ValueError) as exc:
        raise ValueError("temporal payload must be JSON-compatible") from exc


def _normalized_summary(value: str) -> str:
    return re.sub(r"\s+", " ", value.strip().casefold())


def _unique_events(events: Iterable[RelationshipEvent]) -> List[RelationshipEvent]:
    by_id: Dict[str, RelationshipEvent] = {}
    for event in events:
        existing = by_id.get(event.event_id)
        if existing is not None and not existing.same_payload_as(event):
            raise CandidateConflictError(
                f"event_id {event.event_id!r} has conflicting persisted payloads"
            )
        by_id[event.event_id] = existing or event
    return sorted(by_id.values(), key=lambda item: (item.recorded_at, item.event_id))


def relationship_occurrence_fingerprint(
    relationship_id: str,
    event_type: str,
    summary: str,
    occurred_at: Optional[str],
    occurrence_key: Optional[str] = None,
    temporal_payload: Optional[object] = None,
) -> str:
    """Builds the portable semantic identity used for conservative occurrence dedup."""
    if occurrence_key is not None:
        occurrence = {"explicit_key": occurrence_key}
    else:
        occurrence = {
            "event_type": event_type,
            "occurred_at": occurred_at,
            "summary": _normalized_summary(summary),
        }
    canonical_temporal = canonical_temporal_payload(temporal_payload)
    if canonical_temporal is not None:
        occurrence["temporal_payload"] = canonical_temporal
    return _canonical_hash({"relationship_id": relationship_id, "occurrence": occurrence})


def list_complete_relationship_events(
    storage: BaseStorage,
    relationship_id: str,
) -> List[RelationshipEvent]:
    """Returns direct and adjudicated events once in deterministic history order."""
    direct = storage.list_relationship_events(relationship_id)
    adjudications = storage.list_relationship_adjudications(relationship_id)
    return relationship_events_from_journals(direct, adjudications)


def relationship_events_from_journals(
    direct_events: Sequence[RelationshipEvent],
    adjudications: Sequence[AdjudicationRecord],
) -> List[RelationshipEvent]:
    """Builds the deterministic event view from two frozen journal prefixes."""
    return _unique_events(
        [
            *direct_events,
            *(
                event
                for record in adjudications
                for event in record.events
            ),
        ]
    )


def relationship_adjudication_baseline_fingerprint(
    direct_events: Sequence[RelationshipEvent],
    adjudications: Sequence[AdjudicationRecord],
) -> str:
    """Binds one replay baseline to both append-only journal prefixes."""
    return _canonical_hash(
        {
            "direct_events": [event.to_dict() for event in direct_events],
            "adjudications": [
                record.to_dict() for record in adjudications
            ],
        }
    )


class RelationshipAdjudicator:
    """Deep module that verifies, decides, and durably records candidate outcomes."""

    def __init__(self, storage: BaseStorage) -> None:
        self._storage = storage

    def adjudicate(
        self,
        profile: RelationshipProfile,
        source_turn: SourceTurn,
        candidates: RelationshipCandidateBatch,
        *,
        baseline_direct_events: Optional[
            Sequence[RelationshipEvent]
        ] = None,
        baseline_adjudications: Optional[
            Sequence[AdjudicationRecord]
        ] = None,
    ) -> AdjudicationBatchResult:
        """Adjudicates one bounded candidate batch with candidate-level atomicity."""
        batch_fingerprint = self._batch_fingerprint(source_turn, candidates)
        lock = self._storage.lock_manager.lock(
            "__relationship_adjudication__", profile.relationship_id
        )
        with self._storage.relationship_processing_guard(
            profile.relationship_id
        ), lock:
            existing_records = self._storage.list_relationship_adjudications(
                profile.relationship_id
            )
            for record in existing_records:
                receipt = record.receipt
                same_processing_run = (
                    receipt.source_turn_id == source_turn.turn_id
                    and receipt.source_revision == source_turn.revision
                    and receipt.processing_mode == source_turn.processing_mode
                    and receipt.reprocessing_id == source_turn.reprocessing_id
                )
                if same_processing_run and receipt.batch_fingerprint != batch_fingerprint:
                    raise CandidateConflictError(
                        "a source processing run cannot add, remove, or change candidates"
                    )
            expected_decision_ids = {
                self._decision_id(profile, source_turn, candidate)
                for candidate in candidates.candidates
            }
            current_records = {
                record.receipt.decision_id: record
                for record in existing_records
                if record.receipt.decision_id in expected_decision_ids
            }
            if (
                baseline_direct_events is None
                and baseline_adjudications is None
            ):
                baseline_direct_events = (
                    self._storage.list_relationship_events(
                        profile.relationship_id
                    )
                )
                baseline_adjudications = tuple(
                    record
                    for record in existing_records
                    if record.receipt.decision_id
                    not in expected_decision_ids
                )
            elif (
                baseline_direct_events is None
                or baseline_adjudications is None
            ):
                raise ValueError(
                    "both relationship adjudication baseline journals are required"
                )

            canonical, resolution_order = self._reconstruct_batch_records(
                profile,
                source_turn,
                candidates,
                baseline_direct_events=baseline_direct_events,
                baseline_adjudications=baseline_adjudications,
                timestamp_hints=current_records,
                reusable_records=current_records,
            )
            canonical_by_id = {
                record.receipt.decision_id: record
                for record in canonical.records
            }
            stored_by_id: Dict[str, AdjudicationRecord] = {}
            for decision_id in resolution_order:
                expected = canonical_by_id[decision_id]
                stored = self._storage.commit_relationship_adjudication(
                    expected
                )
                if stored.to_dict() != expected.to_dict():
                    raise CandidateConflictError(
                        "persisted candidate decision differs from canonical replay"
                    )
                stored_by_id[decision_id] = stored

            return AdjudicationBatchResult(
                records=[
                    stored_by_id[record.receipt.decision_id]
                    for record in canonical.records
                ]
            )

    def propose_persona_growth(
        self,
        profile: RelationshipProfile,
        intent: PersonaGrowthIntentCandidate,
    ) -> PersonaGrowthProposal:
        """Persists a pending proposal after an independent, history-based review."""
        with self._storage.relationship_processing_guard(profile.relationship_id):
            events = {
                event.event_id: event
                for event in list_complete_relationship_events(
                    self._storage,
                    profile.relationship_id,
                )
            }
            missing = [
                event_id for event_id in intent.supporting_event_ids if event_id not in events
            ]
            if missing:
                raise ValueError("persona growth references unknown relationship events")

            records = self._storage.list_relationship_adjudications(profile.relationship_id)
            receipt_by_event = {
                event_id: record.receipt
                for record in records
                for event_id in record.receipt.event_ids
            }
            supporting_events = [events[event_id] for event_id in intent.supporting_event_ids]
            if not all(self._event_has_reflection(event) for event in supporting_events):
                raise ValueError("persona growth requires accepted historical reflections")

            policy = self._policy_for(profile)
            if intent.trigger_kind == GrowthTriggerKind.ACCUMULATION:
                if len(supporting_events) < 2:
                    raise ValueError("accumulation growth requires at least two accepted events")
                supporting_receipts = [
                    receipt_by_event.get(event.event_id) for event in supporting_events
                ]
                if any(receipt is None for receipt in supporting_receipts):
                    raise ValueError("accumulation growth requires adjudicated supporting events")
                source_turn_ids = {
                    receipt.source_turn_id for receipt in supporting_receipts if receipt is not None
                }
                if len(source_turn_ids) < 2:
                    raise ValueError("accumulation growth requires independent source turns")
            elif not any(
                receipt_by_event.get(event.event_id) is not None
                and self._event_is_pivotal(
                    event,
                    receipt_by_event[event.event_id],
                    policy,
                )
                for event in supporting_events
            ):
                raise ValueError("pivotal growth requires a rule-confirmed pivotal event")

            proposal_id = str(
                uuid.uuid5(
                    uuid.NAMESPACE_URL,
                    f"erii:{profile.relationship_id}:growth:{intent.review_id}:{intent.intent_key}",
                )
            )
            proposal = PersonaGrowthProposal(
                proposal_id=proposal_id,
                relationship_id=profile.relationship_id,
                revision=1,
                intent_key=intent.intent_key,
                review_id=intent.review_id,
                statement=intent.statement,
                rationale=intent.rationale,
                proposed_changes=intent.proposed_changes,
                supporting_event_ids=intent.supporting_event_ids,
                trigger_kind=intent.trigger_kind,
            )
            existing = self._proposal_by_id(profile.relationship_id, proposal_id)
            if existing is not None:
                if self._proposal_intent_content(existing) != self._proposal_intent_content(
                    proposal
                ):
                    raise PersonaGrowthConflictError(
                        "the same inner-review intent already produced different proposal content"
                    )
                return existing
            return self._storage.save_persona_growth_proposal(proposal)

    def decide_persona_growth(
        self,
        profile: RelationshipProfile,
        proposal_id: str,
        revision: int,
        actor_id: str,
        decision: PersonaGrowthDecision,
        reason: Optional[str] = None,
    ) -> PersonaGrowthProposal:
        """Records an exact-revision, out-of-band host safety decision."""
        clean_proposal_id = proposal_id.strip() if isinstance(proposal_id, str) else ""
        clean_actor = actor_id.strip() if isinstance(actor_id, str) else ""
        if not clean_proposal_id or not clean_actor:
            raise ValueError("proposal_id and actor_id must be non-empty strings")
        if len(clean_actor) > 256:
            raise ValueError("actor_id must not exceed 256 characters")
        if reason is not None and len(reason) > 4000:
            raise ValueError("persona growth decision reason must not exceed 4000 characters")
        if isinstance(decision, str):
            decision = PersonaGrowthDecision(decision)

        with self._storage.relationship_processing_guard(profile.relationship_id):
            current = self._proposal_by_id(profile.relationship_id, clean_proposal_id)
            if current is None:
                raise LookupError("persona growth proposal was not found")
            if current.revision != revision:
                raise PersonaGrowthConflictError("proposal revision changed before the decision")

            if decision in (PersonaGrowthDecision.APPROVE, PersonaGrowthDecision.REJECT):
                if current.status != PersonaGrowthStatus.PENDING:
                    raise PersonaGrowthConflictError("only a pending proposal can be decided")
                new_status = (
                    PersonaGrowthStatus.APPROVED
                    if decision == PersonaGrowthDecision.APPROVE
                    else PersonaGrowthStatus.REJECTED
                )
            else:
                if current.status != PersonaGrowthStatus.APPROVED:
                    raise PersonaGrowthConflictError("only an approved proposal can be revoked")
                new_status = PersonaGrowthStatus.REVOKED

            updated = replace(
                current,
                status=new_status,
                decided_by=clean_actor,
                decided_at=utc_now(),
                decision_reason=reason.strip()
                if isinstance(reason, str) and reason.strip()
                else None,
            )
            return self._storage.save_persona_growth_proposal(
                updated,
                expected_status=current.status,
            )

    def _reconstruct_batch_records(
        self,
        profile: RelationshipProfile,
        source_turn: SourceTurn,
        candidates: RelationshipCandidateBatch,
        *,
        baseline_direct_events: Sequence[RelationshipEvent],
        baseline_adjudications: Sequence[AdjudicationRecord],
        timestamp_hints: Optional[
            Mapping[str, AdjudicationRecord]
        ] = None,
        reusable_records: Optional[
            Mapping[str, AdjudicationRecord]
        ] = None,
    ) -> Tuple[AdjudicationBatchResult, Tuple[str, ...]]:
        """Purely replays one batch against an immutable history baseline."""
        direct_events = tuple(baseline_direct_events)
        base_records = tuple(baseline_adjudications)
        for event in direct_events:
            if event.relationship_id != profile.relationship_id:
                raise ValueError(
                    "relationship adjudication baseline event crosses "
                    "relationship boundaries"
                )
        base_decision_ids = set()
        for record in base_records:
            if record.receipt.relationship_id != profile.relationship_id:
                raise ValueError(
                    "relationship adjudication baseline decision crosses "
                    "relationship boundaries"
                )
            decision_id = record.receipt.decision_id
            if decision_id in base_decision_ids:
                raise ValueError(
                    "relationship adjudication baseline repeats a decision"
                )
            base_decision_ids.add(decision_id)

        policy = self._policy_for(profile)
        batch_fingerprint = self._batch_fingerprint(
            source_turn,
            candidates,
        )
        expected_decision_ids = {
            self._decision_id(profile, source_turn, candidate)
            for candidate in candidates.candidates
        }
        if base_decision_ids & expected_decision_ids:
            raise ValueError(
                "relationship adjudication baseline contains the current batch"
            )

        events = _unique_events(
            [
                *direct_events,
                *(
                    event
                    for record in base_records
                    for event in record.events
                ),
            ]
        )
        events_by_id = {event.event_id: event for event in events}
        occurrence_events = self._occurrence_index(base_records)
        hints = dict(timestamp_hints or {})
        reusable = dict(reusable_records or {})
        resolved: Dict[str, AdjudicationRecord] = {}
        pending = {
            candidate.candidate_key: candidate
            for candidate in candidates.candidates
        }
        input_keys = set(pending)
        resolution_order: List[str] = []

        def accept_resolution(
            candidate_key: str,
            candidate: RelationshipEventCandidate,
            record: AdjudicationRecord,
        ) -> None:
            decision_id = self._decision_id(
                profile,
                source_turn,
                candidate,
            )
            canonical = self._apply_timestamp_hint(
                record,
                hints.get(decision_id),
            )
            resolved[candidate_key] = canonical
            resolution_order.append(decision_id)
            for event in canonical.events:
                events_by_id[event.event_id] = event
            if canonical.events:
                occurrence_events.setdefault(
                    canonical.receipt.occurrence_fingerprint,
                    canonical.events[0],
                )
            del pending[candidate_key]

        while pending:
            made_progress = False
            for candidate_key, candidate in list(pending.items()):
                decision_id = self._decision_id(
                    profile,
                    source_turn,
                    candidate,
                )
                existing = reusable.get(decision_id)
                if existing is not None:
                    fingerprint = self._candidate_fingerprint(
                        source_turn,
                        candidate,
                    )
                    if (
                        existing.receipt.candidate_fingerprint
                        != fingerprint
                    ):
                        raise CandidateConflictError(
                            "source turn, revision, and candidate_key were "
                            "reused with different content"
                        )
                    accept_resolution(
                        candidate_key,
                        candidate,
                        existing,
                    )
                    made_progress = True
                    continue
                unknown_dependencies = [
                    dependency
                    for dependency in candidate.depends_on
                    if dependency not in input_keys
                ]
                if unknown_dependencies:
                    record = self._reject_without_evidence(
                        profile,
                        source_turn,
                        candidate,
                        policy,
                        batch_fingerprint,
                        ["unknown_candidate_dependency"],
                    )
                elif not all(
                    dependency in resolved
                    for dependency in candidate.depends_on
                ):
                    continue
                elif any(
                    resolved[dependency].receipt.outcome
                    not in (
                        DecisionOutcome.ACCEPTED,
                        DecisionOutcome.CORROBORATED,
                    )
                    for dependency in candidate.depends_on
                ):
                    record = self._reject_without_evidence(
                        profile,
                        source_turn,
                        candidate,
                        policy,
                        batch_fingerprint,
                        ["candidate_dependency_not_accepted"],
                    )
                else:
                    record = self._adjudicate_candidate(
                        profile=profile,
                        source_turn=source_turn,
                        candidate=candidate,
                        policy=policy,
                        batch_fingerprint=batch_fingerprint,
                        records_by_decision={},
                        events_by_id=events_by_id,
                        occurrence_events=occurrence_events,
                    )
                accept_resolution(candidate_key, candidate, record)
                made_progress = True

            if made_progress:
                continue

            # What remains is a dependency cycle. Preserve one minimal,
            # deterministic rejection per frozen candidate.
            for candidate_key, candidate in list(pending.items()):
                record = self._reject_without_evidence(
                    profile,
                    source_turn,
                    candidate,
                    policy,
                    batch_fingerprint,
                    ["candidate_dependency_cycle"],
                )
                accept_resolution(candidate_key, candidate, record)

        return (
            AdjudicationBatchResult(
                records=[
                    resolved[candidate.candidate_key]
                    for candidate in candidates.candidates
                ]
            ),
            tuple(resolution_order),
        )

    @staticmethod
    def _apply_timestamp_hint(
        record: AdjudicationRecord,
        hint: Optional[AdjudicationRecord],
    ) -> AdjudicationRecord:
        """Copies only nondeterministic durable timestamps from a stored hint."""
        if hint is None:
            return record
        events = tuple(
            replace(
                event,
                recorded_at=(
                    hint.events[index].recorded_at
                    if index < len(hint.events)
                    else hint.receipt.created_at
                ),
            )
            for index, event in enumerate(record.events)
        )
        return replace(
            record,
            receipt=replace(
                record.receipt,
                created_at=hint.receipt.created_at,
                event_ids=tuple(event.event_id for event in events),
            ),
            events=events,
        )

    def _adjudicate_candidate(
        self,
        *,
        profile: RelationshipProfile,
        source_turn: SourceTurn,
        candidate: RelationshipEventCandidate,
        policy: RelationshipPolicySpec,
        batch_fingerprint: str,
        records_by_decision: Mapping[str, AdjudicationRecord],
        events_by_id: Mapping[str, RelationshipEvent],
        occurrence_events: Mapping[str, RelationshipEvent],
    ) -> AdjudicationRecord:
        decision_id = self._decision_id(profile, source_turn, candidate)
        fingerprint = self._candidate_fingerprint(source_turn, candidate)
        existing = records_by_decision.get(decision_id)
        if existing is not None:
            if existing.receipt.candidate_fingerprint != fingerprint:
                raise CandidateConflictError(
                    "source turn, revision, and candidate_key were reused with different content"
                )
            return existing

        evidence, evidence_error = self._verify_evidence(
            profile.relationship_id,
            source_turn,
            candidate.evidence,
        )
        if evidence_error is not None:
            return self._receipt_record(
                profile,
                source_turn,
                candidate,
                policy,
                fingerprint,
                batch_fingerprint,
                DecisionOutcome.REJECTED,
                [evidence_error],
            )
        if candidate.signal.extraction_confidence < MIN_EXTRACTION_CONFIDENCE:
            return self._receipt_record(
                profile,
                source_turn,
                candidate,
                policy,
                fingerprint,
                batch_fingerprint,
                DecisionOutcome.IGNORED,
                ["low_extraction_confidence"],
            )

        temporal_payload: Optional[TemporalPayload] = (
            candidate.temporal_payload.to_durable()
            if candidate.temporal_payload is not None
            else None
        )
        if isinstance(temporal_payload, PromiseSpec):
            if (
                candidate.signal.extraction_confidence
                < MIN_PROMISE_EXTRACTION_CONFIDENCE
            ):
                return self._receipt_record(
                    profile,
                    source_turn,
                    candidate,
                    policy,
                    fingerprint,
                    batch_fingerprint,
                    DecisionOutcome.IGNORED,
                    ["low_promise_extraction_confidence"],
                )
            if (
                candidate.signal.interpretation_confidence
                < MIN_PROMISE_INTERPRETATION_CONFIDENCE
            ):
                return self._receipt_record(
                    profile,
                    source_turn,
                    candidate,
                    policy,
                    fingerprint,
                    batch_fingerprint,
                    DecisionOutcome.IGNORED,
                    ["low_promise_interpretation_confidence"],
                )
            evidenced_roles = {item.role for item in evidence}
            required_roles = {
                PromiseResponsibleParty.AGENT: SourceRole.AGENT,
                PromiseResponsibleParty.USER: SourceRole.USER,
            }
            missing_parties = [
                party.value
                for party in temporal_payload.responsible_parties
                if required_roles[party] not in evidenced_roles
            ]
            if missing_parties:
                return self._receipt_record(
                    profile,
                    source_turn,
                    candidate,
                    policy,
                    fingerprint,
                    batch_fingerprint,
                    DecisionOutcome.REJECTED,
                    [
                        "promise_responsible_party_not_evidenced:"
                        + ",".join(missing_parties)
                    ],
                )
        elif temporal_payload is not None:
            if (
                candidate.signal.extraction_confidence
                < MIN_TEMPORAL_EXTRACTION_CONFIDENCE
            ):
                return self._receipt_record(
                    profile,
                    source_turn,
                    candidate,
                    policy,
                    fingerprint,
                    batch_fingerprint,
                    DecisionOutcome.IGNORED,
                    ["low_temporal_extraction_confidence"],
                )
            if (
                candidate.signal.interpretation_confidence
                < MIN_TEMPORAL_INTERPRETATION_CONFIDENCE
            ):
                return self._receipt_record(
                    profile,
                    source_turn,
                    candidate,
                    policy,
                    fingerprint,
                    batch_fingerprint,
                    DecisionOutcome.IGNORED,
                    ["low_temporal_interpretation_confidence"],
                )
        if temporal_payload is not None:
            lifecycle_reasons = []
            if candidate.persona_reflection is not None:
                lifecycle_reasons.append(
                    "temporal_lifecycle_cannot_include_persona_reflection"
                )
            if candidate.growth_trigger != GrowthTriggerKind.NONE:
                lifecycle_reasons.append("temporal_lifecycle_cannot_trigger_growth")
            if lifecycle_reasons:
                return self._receipt_record(
                    profile,
                    source_turn,
                    candidate,
                    policy,
                    fingerprint,
                    batch_fingerprint,
                    DecisionOutcome.REJECTED,
                    lifecycle_reasons,
                )
        if candidate.event_type not in _SIGNAL_EVENT_TYPES[candidate.signal.signal_type]:
            return self._receipt_record(
                profile,
                source_turn,
                candidate,
                policy,
                fingerprint,
                batch_fingerprint,
                DecisionOutcome.REJECTED,
                ["signal_event_type_mismatch"],
            )

        temporal_error, temporal_references = self._validate_temporal_targets(
            profile,
            temporal_payload,
            events_by_id,
        )
        if temporal_error is not None:
            return self._receipt_record(
                profile,
                source_turn,
                candidate,
                policy,
                fingerprint,
                batch_fingerprint,
                DecisionOutcome.REJECTED,
                [temporal_error],
            )

        effective_occurred_at = self._effective_occurred_at(candidate, evidence)
        occurrence_fingerprint = self._occurrence_fingerprint(
            profile,
            candidate,
            effective_occurred_at,
            temporal_payload,
        )
        duplicate = occurrence_events.get(occurrence_fingerprint)
        if duplicate is None and temporal_payload is None:
            duplicate = next(
                (
                    event
                    for event in events_by_id.values()
                    if event.event_type == candidate.event_type
                    and (
                        event.occurred_at == effective_occurred_at
                        or event.occurred_at is None
                        or effective_occurred_at is None
                    )
                    and _normalized_summary(event.content) == _normalized_summary(candidate.summary)
                ),
                None,
            )
        if duplicate is not None:
            return self._receipt_record(
                profile,
                source_turn,
                candidate,
                policy,
                fingerprint,
                batch_fingerprint,
                DecisionOutcome.CORROBORATED,
                ["existing_occurrence_corroborated"],
                evidence=evidence,
                related_event_id=duplicate.event_id,
                occurrence_fingerprint=occurrence_fingerprint,
            )

        terminal_error = self._validate_temporal_terminal_transition(
            temporal_payload,
            events_by_id,
        )
        if terminal_error is not None:
            return self._receipt_record(
                profile,
                source_turn,
                candidate,
                policy,
                fingerprint,
                batch_fingerprint,
                DecisionOutcome.REJECTED,
                [terminal_error],
                occurrence_fingerprint=occurrence_fingerprint,
            )

        reasons: List[str] = []
        valid_references = [
            event_id for event_id in candidate.references if event_id in events_by_id
        ]
        if len(valid_references) != len(candidate.references):
            reasons.append("unresolved_references_removed")
        for event_id in temporal_references:
            if event_id not in valid_references:
                valid_references.append(event_id)

        state_delta: Mapping[str, float] = {}
        has_interaction_evidence = any(
            item.role in (SourceRole.USER, SourceRole.AGENT) for item in evidence
        )
        if not has_interaction_evidence:
            reasons.append("non_interaction_evidence_not_applied")
        elif temporal_payload is not None:
            # Temporal lifecycle never mutates relationship state by itself.
            pass
        elif candidate.signal.interpretation_confidence >= MIN_STATE_CONFIDENCE:
            state_delta = self._state_delta(candidate, policy)
        else:
            reasons.append("relationship_interpretation_not_applied")

        reflection: Optional[str] = None
        if candidate.persona_reflection is not None:
            if (
                has_interaction_evidence
                and candidate.signal.interpretation_confidence >= MIN_REFLECTION_CONFIDENCE
            ):
                reflection = candidate.persona_reflection
            else:
                reasons.append("persona_reflection_not_persisted")

        pivotal_eligible = self._pivotal_eligible(candidate, policy, reflection)
        if candidate.growth_trigger == GrowthTriggerKind.PIVOTAL and not pivotal_eligible:
            reasons.append("pivotal_trigger_not_confirmed")

        event_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"{decision_id}:event"))
        event = RelationshipEvent(
            event_id=event_id,
            relationship_id=profile.relationship_id,
            event_type=candidate.event_type,
            content=candidate.summary,
            temporal_payload=temporal_payload,
            state_delta=state_delta,
            occurred_at=effective_occurred_at,
            metadata={
                "adjudication": {
                    "decision_id": decision_id,
                    "occurrence_fingerprint": occurrence_fingerprint,
                    "occurrence_key": candidate.occurrence_key,
                    "signal_type": candidate.signal.signal_type.value,
                    "signal_strength": candidate.signal.strength.value,
                    "references": valid_references,
                    "persona_reflection": reflection,
                    "growth_trigger": candidate.growth_trigger.value,
                    "pivotal_eligible": pivotal_eligible,
                }
            },
        )
        return self._receipt_record(
            profile,
            source_turn,
            candidate,
            policy,
            fingerprint,
            batch_fingerprint,
            DecisionOutcome.ACCEPTED,
            reasons or ["accepted"],
            evidence=evidence,
            events=[event],
            occurrence_fingerprint=occurrence_fingerprint,
            pivotal_eligible=pivotal_eligible,
        )

    def _reject_without_evidence(
        self,
        profile: RelationshipProfile,
        source_turn: SourceTurn,
        candidate: RelationshipEventCandidate,
        policy: RelationshipPolicySpec,
        batch_fingerprint: str,
        reasons: Sequence[str],
    ) -> AdjudicationRecord:
        return self._receipt_record(
            profile,
            source_turn,
            candidate,
            policy,
            self._candidate_fingerprint(source_turn, candidate),
            batch_fingerprint,
            DecisionOutcome.REJECTED,
            reasons,
        )

    def _receipt_record(
        self,
        profile: RelationshipProfile,
        source_turn: SourceTurn,
        candidate: RelationshipEventCandidate,
        policy: RelationshipPolicySpec,
        fingerprint: str,
        batch_fingerprint: str,
        outcome: DecisionOutcome,
        reasons: Sequence[str],
        *,
        evidence: Sequence[EvidenceReference] = (),
        events: Sequence[RelationshipEvent] = (),
        related_event_id: Optional[str] = None,
        occurrence_fingerprint: Optional[str] = None,
        pivotal_eligible: bool = False,
    ) -> AdjudicationRecord:
        retained_evidence = (
            tuple(evidence)
            if outcome in (DecisionOutcome.ACCEPTED, DecisionOutcome.CORROBORATED)
            else ()
        )
        receipt = DecisionReceipt(
            decision_id=self._decision_id(profile, source_turn, candidate),
            relationship_id=profile.relationship_id,
            source_turn_id=source_turn.turn_id,
            source_revision=source_turn.revision,
            candidate_key=candidate.candidate_key,
            candidate_fingerprint=fingerprint,
            batch_fingerprint=batch_fingerprint,
            occurrence_fingerprint=(
                occurrence_fingerprint or self._occurrence_fingerprint(profile, candidate)
            ),
            outcome=outcome,
            reason_codes=reasons,
            extraction_confidence=candidate.signal.extraction_confidence,
            interpretation_confidence=candidate.signal.interpretation_confidence,
            extractor_version=source_turn.extractor_version,
            contract_version=source_turn.contract_version,
            rule_version=RULE_VERSION,
            policy_version=policy.version,
            processing_mode=source_turn.processing_mode,
            reprocessing_id=source_turn.reprocessing_id,
            evidence=retained_evidence,
            event_ids=[event.event_id for event in events],
            related_event_id=related_event_id,
            pivotal_eligible=pivotal_eligible,
        )
        return AdjudicationRecord(receipt=receipt, events=events)

    @classmethod
    def _reconstruct_accepted_record(
        cls,
        profile: RelationshipProfile,
        source_turn: SourceTurn,
        candidate: RelationshipEventCandidate,
        *,
        batch_fingerprint: str,
        evidence: Sequence[EvidenceReference],
        prior_events: Sequence[RelationshipEvent],
        receipt_created_at: str,
        event_recorded_at: str,
    ) -> AdjudicationRecord:
        """Rebuilds the only canonical accepted record for import validation."""
        if not evidence:
            raise ValueError("accepted adjudication requires verified evidence")
        if candidate.signal.extraction_confidence < MIN_EXTRACTION_CONFIDENCE:
            raise ValueError("low-confidence candidate cannot be accepted")

        temporal_payload: Optional[TemporalPayload] = (
            candidate.temporal_payload.to_durable()
            if candidate.temporal_payload is not None
            else None
        )
        if isinstance(temporal_payload, PromiseSpec):
            if (
                candidate.signal.extraction_confidence
                < MIN_PROMISE_EXTRACTION_CONFIDENCE
                or candidate.signal.interpretation_confidence
                < MIN_PROMISE_INTERPRETATION_CONFIDENCE
            ):
                raise ValueError("low-confidence Promise candidate cannot be accepted")
            evidenced_roles = {item.role for item in evidence}
            required_roles = {
                PromiseResponsibleParty.AGENT: SourceRole.AGENT,
                PromiseResponsibleParty.USER: SourceRole.USER,
            }
            if any(
                required_roles[party] not in evidenced_roles
                for party in temporal_payload.responsible_parties
            ):
                raise ValueError(
                    "Promise candidate cannot be accepted without responsible-party evidence"
                )
        elif temporal_payload is not None and (
            candidate.signal.extraction_confidence
            < MIN_TEMPORAL_EXTRACTION_CONFIDENCE
            or candidate.signal.interpretation_confidence
            < MIN_TEMPORAL_INTERPRETATION_CONFIDENCE
        ):
            raise ValueError("low-confidence temporal candidate cannot be accepted")
        if temporal_payload is not None and (
            candidate.persona_reflection is not None
            or candidate.growth_trigger != GrowthTriggerKind.NONE
        ):
            raise ValueError(
                "temporal lifecycle candidate cannot contain persona side effects"
            )
        if candidate.event_type not in _SIGNAL_EVENT_TYPES[
            candidate.signal.signal_type
        ]:
            raise ValueError("candidate signal cannot produce this event type")

        prior_by_id = {event.event_id: event for event in prior_events}
        temporal_error, temporal_references = (
            cls._validate_temporal_event_targets(
                profile,
                temporal_payload,
                prior_by_id,
            )
        )
        if temporal_error is not None:
            raise ValueError(
                f"accepted temporal candidate has invalid target: {temporal_error}"
            )
        terminal_error = cls._validate_temporal_terminal_transition(
            temporal_payload,
            prior_by_id,
        )
        if terminal_error is not None:
            raise ValueError(
                f"accepted temporal candidate has invalid transition: {terminal_error}"
            )
        if (
            isinstance(temporal_payload, OpenLoopSpec)
            and temporal_payload.origin_memory_node_id is not None
            and any(
                isinstance(event.temporal_payload, OpenLoopSpec)
                and event.temporal_payload.origin_memory_node_id
                == temporal_payload.origin_memory_node_id
                for event in prior_events
            )
        ):
            raise ValueError(
                "accepted Open Loop candidate repeats an already formalized origin"
            )

        effective_occurred_at = cls._effective_occurred_at(candidate, evidence)
        occurrence_fingerprint = cls._occurrence_fingerprint(
            profile,
            candidate,
            effective_occurred_at,
            temporal_payload,
        )
        duplicate = next(
            (
                event
                for event in prior_events
                if (
                    event.metadata.get("adjudication", {}).get(
                        "occurrence_fingerprint"
                    )
                    == occurrence_fingerprint
                )
                or (
                    temporal_payload is None
                    and event.event_type == candidate.event_type
                    and (
                        event.occurred_at == effective_occurred_at
                        or event.occurred_at is None
                        or effective_occurred_at is None
                    )
                    and _normalized_summary(event.content)
                    == _normalized_summary(candidate.summary)
                )
            ),
            None,
        )
        if duplicate is not None:
            raise ValueError(
                "duplicate candidate cannot create a new accepted relationship event"
            )

        reasons: List[str] = []
        valid_references = [
            event_id
            for event_id in candidate.references
            if event_id in prior_by_id
        ]
        if len(valid_references) != len(candidate.references):
            reasons.append("unresolved_references_removed")
        for event_id in temporal_references:
            if event_id not in valid_references:
                valid_references.append(event_id)

        policy = cls._policy_for(profile)
        state_delta: Mapping[str, float] = {}
        has_interaction_evidence = any(
            item.role in (SourceRole.USER, SourceRole.AGENT) for item in evidence
        )
        if not has_interaction_evidence:
            reasons.append("non_interaction_evidence_not_applied")
        elif temporal_payload is not None:
            pass
        elif candidate.signal.interpretation_confidence >= MIN_STATE_CONFIDENCE:
            state_delta = cls._state_delta(candidate, policy)
        else:
            reasons.append("relationship_interpretation_not_applied")

        reflection: Optional[str] = None
        if candidate.persona_reflection is not None:
            if (
                has_interaction_evidence
                and candidate.signal.interpretation_confidence
                >= MIN_REFLECTION_CONFIDENCE
            ):
                reflection = candidate.persona_reflection
            else:
                reasons.append("persona_reflection_not_persisted")
        pivotal_eligible = cls._pivotal_eligible(
            candidate,
            policy,
            reflection,
        )
        if (
            candidate.growth_trigger == GrowthTriggerKind.PIVOTAL
            and not pivotal_eligible
        ):
            reasons.append("pivotal_trigger_not_confirmed")

        decision_id = cls._decision_id(profile, source_turn, candidate)
        event = RelationshipEvent(
            event_id=str(
                uuid.uuid5(uuid.NAMESPACE_URL, f"{decision_id}:event")
            ),
            relationship_id=profile.relationship_id,
            event_type=candidate.event_type,
            content=candidate.summary,
            temporal_payload=temporal_payload,
            state_delta=state_delta,
            occurred_at=effective_occurred_at,
            recorded_at=event_recorded_at,
            metadata={
                "adjudication": {
                    "decision_id": decision_id,
                    "occurrence_fingerprint": occurrence_fingerprint,
                    "occurrence_key": candidate.occurrence_key,
                    "signal_type": candidate.signal.signal_type.value,
                    "signal_strength": candidate.signal.strength.value,
                    "references": valid_references,
                    "persona_reflection": reflection,
                    "growth_trigger": candidate.growth_trigger.value,
                    "pivotal_eligible": pivotal_eligible,
                }
            },
        )
        receipt = DecisionReceipt(
            decision_id=decision_id,
            relationship_id=profile.relationship_id,
            source_turn_id=source_turn.turn_id,
            source_revision=source_turn.revision,
            candidate_key=candidate.candidate_key,
            candidate_fingerprint=cls._candidate_fingerprint(
                source_turn,
                candidate,
            ),
            batch_fingerprint=batch_fingerprint,
            occurrence_fingerprint=occurrence_fingerprint,
            outcome=DecisionOutcome.ACCEPTED,
            reason_codes=reasons or ["accepted"],
            extraction_confidence=candidate.signal.extraction_confidence,
            interpretation_confidence=candidate.signal.interpretation_confidence,
            extractor_version=source_turn.extractor_version,
            contract_version=source_turn.contract_version,
            rule_version=RULE_VERSION,
            policy_version=policy.version,
            processing_mode=source_turn.processing_mode,
            reprocessing_id=source_turn.reprocessing_id,
            evidence=evidence,
            event_ids=(event.event_id,),
            pivotal_eligible=pivotal_eligible,
            created_at=receipt_created_at,
        )
        return AdjudicationRecord(receipt=receipt, events=(event,))

    @staticmethod
    def _verify_evidence(
        relationship_id: str,
        source_turn: SourceTurn,
        citations: Sequence[EvidenceCitation],
    ) -> Tuple[Tuple[EvidenceReference, ...], Optional[str]]:
        sources = {
            (message.source_id, message.revision): message for message in source_turn.messages
        }
        verified: List[EvidenceReference] = []
        for citation in citations:
            source = sources.get((citation.source_id, citation.source_revision))
            if source is None:
                return (), "evidence_source_not_found"
            span = RelationshipAdjudicator._resolve_span(source, citation)
            if span is None:
                return (), "evidence_quote_mismatch"
            start, end = span
            message_hash = _sha256_text(source.content)
            evidence_id = str(
                uuid.uuid5(
                    uuid.NAMESPACE_URL,
                    (
                        f"erii:{relationship_id}:evidence:{source.source_id}:"
                        f"{source.revision}:{message_hash}:{start}:{end}"
                    ),
                )
            )
            verified.append(
                EvidenceReference(
                    evidence_id=evidence_id,
                    source_id=source.source_id,
                    source_revision=source.revision,
                    role=source.role,
                    quote=citation.quote,
                    message_sha256=message_hash,
                    start=start,
                    end=end,
                    occurred_at=source.occurred_at,
                )
            )
        return tuple(verified), None

    @staticmethod
    def _resolve_span(
        source: SourceMessage,
        citation: EvidenceCitation,
    ) -> Optional[Tuple[int, int]]:
        if citation.start is not None and citation.end is not None:
            if citation.end > len(source.content):
                return None
            if source.content[citation.start : citation.end] != citation.quote:
                return None
            return citation.start, citation.end
        start = source.content.find(citation.quote)
        if start < 0:
            return None
        return start, start + len(citation.quote)

    @staticmethod
    def _policy_for(profile: RelationshipProfile) -> RelationshipPolicySpec:
        raw = profile.blueprint.compiled.get("relationship_policy")
        if raw is None:
            return RelationshipPolicySpec()
        return RelationshipPolicySpec.model_validate(raw)

    @staticmethod
    def _state_delta(
        candidate: RelationshipEventCandidate,
        policy: RelationshipPolicySpec,
    ) -> Mapping[str, float]:
        base = _BASE_STATE_DELTAS[candidate.signal.signal_type]
        strength = _STRENGTH_MULTIPLIERS[candidate.signal.strength]
        modifier = policy.signal_modifiers.get(candidate.signal.signal_type, 1.0)
        result: Dict[str, float] = {}
        for dimension, raw_delta in base.items():
            delta = raw_delta * strength * modifier
            delta = min(MAX_AUTOMATIC_STATE_DELTA, max(-MAX_AUTOMATIC_STATE_DELTA, delta))
            if delta:
                result[dimension] = round(delta, 6)
        return result

    @staticmethod
    def _pivotal_eligible(
        candidate: RelationshipEventCandidate,
        policy: RelationshipPolicySpec,
        reflection: Optional[str],
    ) -> bool:
        return (
            candidate.growth_trigger == GrowthTriggerKind.PIVOTAL
            and candidate.signal.signal_type in policy.pivotal_signals
            and candidate.signal.strength == SignalStrength.STRONG
            and candidate.signal.interpretation_confidence >= MIN_PIVOTAL_CONFIDENCE
            and reflection is not None
        )

    def _event_has_reflection(self, event: RelationshipEvent) -> bool:
        """Prefers formal reflection history, with read-only legacy fallback."""
        try:
            records = self._storage.list_persona_reflection_records(
                event.relationship_id
            )
        except (AttributeError, NotImplementedError):
            records = ()
        if any(record.event_id == event.event_id for record in records):
            return True
        adjudication = event.metadata.get("adjudication", {})
        return bool(adjudication.get("persona_reflection"))

    @staticmethod
    def _event_is_pivotal(
        event: RelationshipEvent,
        receipt: DecisionReceipt,
        policy: RelationshipPolicySpec,
    ) -> bool:
        """Re-evaluates modern neutral candidates after reflection exists."""
        if receipt.pivotal_eligible:
            return True
        adjudication = event.metadata.get("adjudication", {})
        try:
            signal_type = RelationshipSignalType(
                adjudication.get("signal_type")
            )
            signal_strength = SignalStrength(
                adjudication.get("signal_strength")
            )
        except (TypeError, ValueError):
            return False
        return (
            signal_type in policy.pivotal_signals
            and signal_strength == SignalStrength.STRONG
            and receipt.interpretation_confidence >= MIN_PIVOTAL_CONFIDENCE
        )

    @staticmethod
    def _decision_id(
        profile: RelationshipProfile,
        source_turn: SourceTurn,
        candidate: RelationshipEventCandidate,
    ) -> str:
        processing_identity = (
            f"{source_turn.processing_mode.value}:{source_turn.reprocessing_id or ''}"
        )
        return str(
            uuid.uuid5(
                uuid.NAMESPACE_URL,
                (
                    f"erii:{profile.relationship_id}:decision:{source_turn.turn_id}:"
                    f"{source_turn.revision}:{processing_identity}:{candidate.candidate_key}"
                ),
            )
        )

    @staticmethod
    def _candidate_fingerprint(
        source_turn: SourceTurn,
        candidate: RelationshipEventCandidate,
    ) -> str:
        source_hashes = {
            f"{message.source_id}:{message.revision}": {
                "content_sha256": _sha256_text(message.content),
                "role": message.role.value,
                "occurred_at": message.occurred_at,
            }
            for message in source_turn.messages
        }
        return _canonical_hash(
            {
                "candidate": candidate.model_dump(mode="json"),
                "source_hashes": source_hashes,
            }
        )

    @classmethod
    def _batch_fingerprint(
        cls,
        source_turn: SourceTurn,
        candidates: RelationshipCandidateBatch,
    ) -> str:
        return _canonical_hash(
            sorted(
                cls._candidate_fingerprint(source_turn, candidate)
                for candidate in candidates.candidates
            )
        )

    def _validate_temporal_targets(
        self,
        profile: RelationshipProfile,
        payload: Optional[TemporalPayload],
        events_by_id: Mapping[str, RelationshipEvent],
    ) -> Tuple[Optional[str], Tuple[str, ...]]:
        """Validates typed target references before an event can enter history."""

        if isinstance(payload, OpenLoopSpec) and payload.origin_memory_node_id is not None:
            origin = next(
                (
                    node
                    for node in self._storage.load_nodes(profile.agent_id, profile.user_id)
                    if node.node_id == payload.origin_memory_node_id
                ),
                None,
            )
            if origin is None:
                return "open_loop_origin_memory_not_found", ()
            if not origin.is_unresolved or not origin.is_latest:
                return "open_loop_origin_memory_not_active", ()
            if any(
                isinstance(event.temporal_payload, OpenLoopSpec)
                and event.temporal_payload.origin_memory_node_id
                == payload.origin_memory_node_id
                for event in events_by_id.values()
            ):
                return "open_loop_origin_already_formalized", ()
            return None, ()

        return self._validate_temporal_event_targets(
            profile,
            payload,
            events_by_id,
        )

    @staticmethod
    def _validate_temporal_event_targets(
        profile: RelationshipProfile,
        payload: Optional[TemporalPayload],
        events_by_id: Mapping[str, RelationshipEvent],
    ) -> Tuple[Optional[str], Tuple[str, ...]]:
        """Validates temporal references using relationship events only."""

        def target_event(
            event_id: str,
            expected_type: RelationshipEventType,
            expected_payload_type: type,
            label: str,
        ) -> Tuple[Optional[RelationshipEvent], Optional[str]]:
            event = events_by_id.get(event_id)
            if event is None:
                return None, f"{label}_target_not_found"
            if event.relationship_id != profile.relationship_id:
                return None, f"{label}_target_relationship_mismatch"
            if event.event_type != expected_type or not isinstance(
                event.temporal_payload,
                expected_payload_type,
            ):
                return None, f"{label}_target_not_structured"
            return event, None

        if isinstance(payload, PromiseConditionConfirmation):
            target, error = target_event(
                payload.promise_event_id,
                RelationshipEventType.PROMISE,
                PromiseSpec,
                "promise_condition",
            )
            if error is not None:
                return error, ()
            promise = target.temporal_payload
            if promise.activation_condition is None:
                return "promise_has_no_activation_condition", ()
            if promise.activation_condition.condition_id != payload.condition_id:
                return "promise_condition_id_mismatch", ()
            return None, (payload.promise_event_id,)

        if isinstance(payload, PromiseResolution):
            _, error = target_event(
                payload.promise_event_id,
                RelationshipEventType.PROMISE,
                PromiseSpec,
                "promise_resolution",
            )
            if error is not None:
                return error, ()
            references = [payload.promise_event_id]
            if payload.superseding_promise_event_id is not None:
                _, error = target_event(
                    payload.superseding_promise_event_id,
                    RelationshipEventType.PROMISE,
                    PromiseSpec,
                    "superseding_promise",
                )
                if error is not None:
                    return error, ()
                references.append(payload.superseding_promise_event_id)
            return None, tuple(references)

        if isinstance(payload, OpenLoopResolution):
            _, error = target_event(
                payload.open_loop_event_id,
                RelationshipEventType.OPEN_LOOP,
                OpenLoopSpec,
                "open_loop_resolution",
            )
            if error is not None:
                return error, ()
            references = [payload.open_loop_event_id]
            if payload.superseding_open_loop_event_id is not None:
                _, error = target_event(
                    payload.superseding_open_loop_event_id,
                    RelationshipEventType.OPEN_LOOP,
                    OpenLoopSpec,
                    "superseding_open_loop",
                )
                if error is not None:
                    return error, ()
                references.append(payload.superseding_open_loop_event_id)
            return None, tuple(references)

        return None, ()

    @staticmethod
    def _promise_condition_confirmation_for(
        promise_event_id: str,
        condition_id: str,
        events_by_id: Mapping[str, RelationshipEvent],
    ) -> Optional[PromiseConditionConfirmation]:
        return next(
            (
                event.temporal_payload
                for event in events_by_id.values()
                if isinstance(event.temporal_payload, PromiseConditionConfirmation)
                and event.temporal_payload.promise_event_id == promise_event_id
                and event.temporal_payload.condition_id == condition_id
            ),
            None,
        )

    @classmethod
    def _validate_temporal_terminal_transition(
        cls,
        payload: Optional[TemporalPayload],
        events_by_id: Mapping[str, RelationshipEvent],
    ) -> Optional[str]:
        """Rejects conflicting terminal decisions and supersession cycles."""
        if isinstance(payload, PromiseConditionConfirmation):
            if cls._promise_resolution_for(payload.promise_event_id, events_by_id):
                return "promise_already_resolved"
            if cls._promise_condition_confirmation_for(
                payload.promise_event_id,
                payload.condition_id,
                events_by_id,
            ):
                return "promise_condition_already_confirmed"
            return None
        if isinstance(payload, PromiseResolution):
            if cls._promise_resolution_for(payload.promise_event_id, events_by_id):
                return "promise_already_resolved"
            if (
                payload.resolution_kind == PromiseResolutionKind.SUPERSEDED
                and cls._supersession_creates_cycle(
                    payload.promise_event_id,
                    payload.superseding_promise_event_id,
                    events_by_id,
                    PromiseResolution,
                )
            ):
                return "promise_supersession_cycle"
            return None
        if isinstance(payload, OpenLoopResolution):
            if cls._open_loop_resolution_for(payload.open_loop_event_id, events_by_id):
                return "open_loop_already_resolved"
            if (
                payload.resolution_kind == OpenLoopResolutionKind.SUPERSEDED
                and cls._supersession_creates_cycle(
                    payload.open_loop_event_id,
                    payload.superseding_open_loop_event_id,
                    events_by_id,
                    OpenLoopResolution,
                )
            ):
                return "open_loop_supersession_cycle"
        return None

    @staticmethod
    def _promise_resolution_for(
        promise_event_id: str,
        events_by_id: Mapping[str, RelationshipEvent],
    ) -> Optional[PromiseResolution]:
        return next(
            (
                event.temporal_payload
                for event in events_by_id.values()
                if isinstance(event.temporal_payload, PromiseResolution)
                and event.temporal_payload.promise_event_id == promise_event_id
            ),
            None,
        )

    @staticmethod
    def _open_loop_resolution_for(
        open_loop_event_id: str,
        events_by_id: Mapping[str, RelationshipEvent],
    ) -> Optional[OpenLoopResolution]:
        return next(
            (
                event.temporal_payload
                for event in events_by_id.values()
                if isinstance(event.temporal_payload, OpenLoopResolution)
                and event.temporal_payload.open_loop_event_id == open_loop_event_id
            ),
            None,
        )

    @staticmethod
    def _supersession_creates_cycle(
        target_event_id: str,
        successor_event_id: Optional[str],
        events_by_id: Mapping[str, RelationshipEvent],
        resolution_type: type,
    ) -> bool:
        if successor_event_id is None:
            return False
        edges: Dict[str, str] = {}
        for event in events_by_id.values():
            resolution = event.temporal_payload
            if not isinstance(resolution, resolution_type):
                continue
            if isinstance(resolution, PromiseResolution):
                source_id = resolution.promise_event_id
                next_id = resolution.superseding_promise_event_id
                is_superseded = resolution.resolution_kind == PromiseResolutionKind.SUPERSEDED
            else:
                source_id = resolution.open_loop_event_id
                next_id = resolution.superseding_open_loop_event_id
                is_superseded = (
                    resolution.resolution_kind == OpenLoopResolutionKind.SUPERSEDED
                )
            if is_superseded and next_id is not None:
                edges[source_id] = next_id

        current = successor_event_id
        visited = set()
        while current is not None:
            if current == target_event_id or current in visited:
                return True
            visited.add(current)
            current = edges.get(current)
        return False

    @staticmethod
    def _occurrence_fingerprint(
        profile: RelationshipProfile,
        candidate: RelationshipEventCandidate,
        effective_occurred_at: Optional[str] = None,
        temporal_payload: Optional[TemporalPayload] = None,
    ) -> str:
        fingerprint_payload: Optional[object] = temporal_payload
        if fingerprint_payload is None and candidate.temporal_payload is not None:
            fingerprint_payload = candidate.temporal_payload
        return relationship_occurrence_fingerprint(
            relationship_id=profile.relationship_id,
            event_type=candidate.event_type.value,
            summary=candidate.summary,
            occurred_at=candidate.occurred_at or effective_occurred_at,
            occurrence_key=candidate.occurrence_key,
            temporal_payload=fingerprint_payload,
        )

    @staticmethod
    def _effective_occurred_at(
        candidate: RelationshipEventCandidate,
        evidence: Sequence[EvidenceReference],
    ) -> Optional[str]:
        if candidate.occurred_at is not None:
            return candidate.occurred_at
        timestamps = {item.occurred_at for item in evidence if item.occurred_at is not None}
        return next(iter(timestamps)) if len(timestamps) == 1 else None

    @staticmethod
    def _occurrence_index(
        records: Sequence[AdjudicationRecord],
    ) -> Dict[str, RelationshipEvent]:
        result: Dict[str, RelationshipEvent] = {}
        for record in records:
            if record.events:
                result.setdefault(record.receipt.occurrence_fingerprint, record.events[0])
        return result

    def _proposal_by_id(
        self,
        relationship_id: str,
        proposal_id: str,
    ) -> Optional[PersonaGrowthProposal]:
        return next(
            (
                proposal
                for proposal in self._storage.list_persona_growth_proposals(relationship_id)
                if proposal.proposal_id == proposal_id
            ),
            None,
        )

    @staticmethod
    def _proposal_intent_content(proposal: PersonaGrowthProposal) -> Mapping[str, object]:
        data = proposal.to_dict()
        for key in (
            "status",
            "created_at",
            "decided_by",
            "decided_at",
            "decision_reason",
        ):
            data.pop(key, None)
        return data

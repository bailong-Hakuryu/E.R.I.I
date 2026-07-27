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
from erii.storage.base import BaseStorage


RULE_VERSION = "relationship-adjudication-v1"
MIN_EXTRACTION_CONFIDENCE = 0.5
MIN_STATE_CONFIDENCE = 0.7
MIN_REFLECTION_CONFIDENCE = 0.8
MIN_PIVOTAL_CONFIDENCE = 0.9

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
    return _canonical_hash({"relationship_id": relationship_id, "occurrence": occurrence})


def list_complete_relationship_events(
    storage: BaseStorage,
    relationship_id: str,
) -> List[RelationshipEvent]:
    """Returns direct and adjudicated events once in deterministic history order."""
    direct = storage.list_relationship_events(relationship_id)
    adjudicated = [
        event
        for record in storage.list_relationship_adjudications(relationship_id)
        for event in record.events
    ]
    return _unique_events([*direct, *adjudicated])


class RelationshipAdjudicator:
    """Deep module that verifies, decides, and durably records candidate outcomes."""

    def __init__(self, storage: BaseStorage) -> None:
        self._storage = storage

    def adjudicate(
        self,
        profile: RelationshipProfile,
        source_turn: SourceTurn,
        candidates: RelationshipCandidateBatch,
    ) -> AdjudicationBatchResult:
        """Adjudicates one bounded candidate batch with candidate-level atomicity."""
        policy = self._policy_for(profile)
        batch_fingerprint = self._batch_fingerprint(source_turn, candidates)
        lock = self._storage.lock_manager.lock(
            "__relationship_adjudication__", profile.relationship_id
        )
        with lock:
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
            records_by_decision = {
                record.receipt.decision_id: record for record in existing_records
            }
            existing_events = list_complete_relationship_events(
                self._storage,
                profile.relationship_id,
            )
            events_by_id = {event.event_id: event for event in existing_events}
            occurrence_events = self._occurrence_index(existing_records)

            resolved: Dict[str, AdjudicationRecord] = {}
            pending = {candidate.candidate_key: candidate for candidate in candidates.candidates}
            input_keys = set(pending)

            while pending:
                made_progress = False
                for candidate_key, candidate in list(pending.items()):
                    decision_id = self._decision_id(profile, source_turn, candidate)
                    existing = records_by_decision.get(decision_id)
                    if existing is not None:
                        fingerprint = self._candidate_fingerprint(source_turn, candidate)
                        if existing.receipt.candidate_fingerprint != fingerprint:
                            raise CandidateConflictError(
                                "source turn, revision, and candidate_key were reused "
                                "with different content"
                            )
                        record = existing
                    else:
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
                        elif not all(dependency in resolved for dependency in candidate.depends_on):
                            continue
                        elif any(
                            resolved[dependency].receipt.outcome
                            not in (DecisionOutcome.ACCEPTED, DecisionOutcome.CORROBORATED)
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
                                records_by_decision=records_by_decision,
                                events_by_id=events_by_id,
                                occurrence_events=occurrence_events,
                            )

                    stored = self._storage.commit_relationship_adjudication(record)
                    resolved[candidate_key] = stored
                    records_by_decision[stored.receipt.decision_id] = stored
                    for event in stored.events:
                        events_by_id[event.event_id] = event
                    if stored.events:
                        occurrence_events.setdefault(
                            stored.receipt.occurrence_fingerprint,
                            stored.events[0],
                        )
                    del pending[candidate_key]
                    made_progress = True

                if made_progress:
                    continue

                # What remains is a dependency cycle. Each candidate gets its own
                # minimal receipt so one malformed graph does not discard others.
                for candidate_key, candidate in list(pending.items()):
                    record = self._reject_without_evidence(
                        profile,
                        source_turn,
                        candidate,
                        policy,
                        batch_fingerprint,
                        ["candidate_dependency_cycle"],
                    )
                    stored = self._storage.commit_relationship_adjudication(record)
                    resolved[candidate_key] = stored
                    del pending[candidate_key]

            return AdjudicationBatchResult(
                records=[resolved[candidate.candidate_key] for candidate in candidates.candidates]
            )

    def propose_persona_growth(
        self,
        profile: RelationshipProfile,
        intent: PersonaGrowthIntentCandidate,
    ) -> PersonaGrowthProposal:
        """Persists a pending proposal after an independent, history-based review."""
        with self._storage.lock_manager.lock(
            "__persona_growth__",
            profile.relationship_id,
        ):
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
                and receipt_by_event[event.event_id].pivotal_eligible
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

        with self._storage.lock_manager.lock(
            "__persona_growth__",
            profile.relationship_id,
        ):
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

        effective_occurred_at = self._effective_occurred_at(candidate, evidence)
        occurrence_fingerprint = self._occurrence_fingerprint(
            profile,
            candidate,
            effective_occurred_at,
        )
        duplicate = occurrence_events.get(occurrence_fingerprint)
        if duplicate is None:
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

        reasons: List[str] = []
        valid_references = [
            event_id for event_id in candidate.references if event_id in events_by_id
        ]
        if len(valid_references) != len(candidate.references):
            reasons.append("unresolved_references_removed")

        state_delta: Mapping[str, float] = {}
        has_interaction_evidence = any(
            item.role in (SourceRole.USER, SourceRole.AGENT) for item in evidence
        )
        if not has_interaction_evidence:
            reasons.append("non_interaction_evidence_not_applied")
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

    @staticmethod
    def _event_has_reflection(event: RelationshipEvent) -> bool:
        adjudication = event.metadata.get("adjudication", {})
        return bool(adjudication.get("persona_reflection"))

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

    @staticmethod
    def _occurrence_fingerprint(
        profile: RelationshipProfile,
        candidate: RelationshipEventCandidate,
        effective_occurred_at: Optional[str] = None,
    ) -> str:
        return relationship_occurrence_fingerprint(
            relationship_id=profile.relationship_id,
            event_type=candidate.event_type.value,
            summary=candidate.summary,
            occurred_at=candidate.occurred_at or effective_occurred_at,
            occurrence_key=candidate.occurrence_key,
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

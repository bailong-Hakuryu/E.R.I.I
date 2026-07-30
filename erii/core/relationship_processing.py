"""Synchronous Source Turn relationship processing orchestration."""

import hashlib
import json
from typing import List, Optional, Tuple
import uuid

from erii._version import __version__
from erii.core.adjudication import (
    RULE_VERSION,
    RelationshipAdjudicator,
    relationship_adjudication_baseline_fingerprint,
    relationship_events_from_journals,
    list_complete_relationship_events,
)
from erii.models.adjudication import (
    AdjudicationRecord,
    DecisionOutcome,
    PersonaGrowthStatus,
    RelationshipCandidateBatch,
    SourceMessage,
    SourceProcessingMode,
    SourceRole,
    SourceTurn,
)
from erii.models.consolidation import (
    ApprovedGrowthReference,
    PersonaReflectionContentDecision,
    PersonaReflectionDecisionRecord,
    PersonaReflectionInterpretationRequest,
    PersonaReflectionInterpreterV1,
    PersonaReflectionRecord,
    PersonaReflectionRecordKind,
    ReflectionContextProvenance,
    ReflectionInterpreterDescriptor,
    RelationshipEventCandidatesDecision,
    RelationshipEventExtractionRequest,
    RelationshipEventExtractorV1,
    RelationshipNoEventDecision,
    RelationshipProcessingOutcome,
    RelationshipProcessingConflictError,
    RelationshipProcessingRun,
    RelationshipProcessingStatus,
    persona_reflection_decision_from_value,
    relationship_extraction_decision_from_value,
)
from erii.models.provenance import ExtractorDescriptor
from erii.models.relationship import RelationshipEvent, RelationshipProfile, utc_now
from erii.models.turn import (
    SourceProcessingChannel,
    TurnRecord,
    TurnStatus,
)
from erii.storage.base import BaseStorage


def _fingerprint(value: object) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


class RelationshipProcessingError(Exception):
    """Base class for synchronous relationship-processing failures."""


class RelationshipProcessingCapabilityError(RelationshipProcessingError):
    """A required host capability or durable storage seam is unavailable."""


class RelationshipProcessingSubmissionError(RelationshipProcessingError, ValueError):
    """A source cannot enter the requested relationship-processing run."""


class RelationshipProcessingCoordinator:
    """Deep module owning extraction freeze, adjudication, and reflection."""

    def __init__(
        self,
        *,
        storage: BaseStorage,
        relationship_event_extractor: Optional[RelationshipEventExtractorV1],
        persona_reflection_interpreter: Optional[PersonaReflectionInterpreterV1],
    ) -> None:
        self.storage = storage
        self.relationship_event_extractor = relationship_event_extractor
        self.persona_reflection_interpreter = persona_reflection_interpreter
        self.adjudicator = RelationshipAdjudicator(storage)
        self.extractor_descriptor = self._validate_extractor(
            relationship_event_extractor
        )
        self.interpreter_descriptor = self._validate_interpreter(
            persona_reflection_interpreter
        )

    @property
    def storage_available(self) -> bool:
        return all(
            getattr(type(self.storage), name, None)
            is not getattr(BaseStorage, name)
            for name in (
                "create_relationship_processing_run",
                "get_relationship_processing_run",
                "list_relationship_processing_runs",
                "update_relationship_processing_run",
                "commit_persona_reflection_decision",
                "get_persona_reflection_decision",
                "list_persona_reflection_decisions",
                "get_persona_reflection_record",
                "list_persona_reflection_records",
            )
        )

    @property
    def available(self) -> bool:
        return (
            self.relationship_event_extractor is not None
            and self.extractor_descriptor is not None
            and self.storage_available
        )

    @staticmethod
    def _validate_extractor(
        extractor: Optional[RelationshipEventExtractorV1],
    ) -> Optional[ExtractorDescriptor]:
        if extractor is None:
            return None
        descriptor = getattr(extractor, "descriptor", None)
        if not isinstance(descriptor, ExtractorDescriptor):
            raise RelationshipProcessingCapabilityError(
                "relationship_event_extractor must expose an ExtractorDescriptor"
            )
        if descriptor.erii_version is not None or descriptor.processed_at is not None:
            raise RelationshipProcessingCapabilityError(
                "host extractor descriptor cannot contain kernel processing metadata"
            )
        return descriptor

    @staticmethod
    def _validate_interpreter(
        interpreter: Optional[PersonaReflectionInterpreterV1],
    ) -> Optional[ReflectionInterpreterDescriptor]:
        if interpreter is None:
            return None
        descriptor = getattr(interpreter, "descriptor", None)
        if not isinstance(descriptor, ReflectionInterpreterDescriptor):
            raise RelationshipProcessingCapabilityError(
                "persona_reflection_interpreter must expose "
                "a ReflectionInterpreterDescriptor"
            )
        return descriptor

    def ensure_available(self) -> None:
        if not self.available:
            raise RelationshipProcessingCapabilityError(
                "relationship processing requires an extractor and durable storage support"
            )

    def ensure_storage_available(self) -> None:
        if not self.storage_available:
            raise RelationshipProcessingCapabilityError(
                "relationship processing requires durable storage support"
            )

    @staticmethod
    def processing_id(
        profile: RelationshipProfile,
        turn: TurnRecord,
        *,
        processing_mode: SourceProcessingMode,
        reprocessing_id: Optional[str],
    ) -> str:
        identity = f"{processing_mode.value}:{reprocessing_id or ''}"
        return str(
            uuid.uuid5(
                uuid.NAMESPACE_URL,
                (
                    f"erii:{profile.relationship_id}:relationship-processing:"
                    f"{turn.turn_id}:{turn.source_revision}:{identity}"
                ),
            )
        )

    def process(
        self,
        profile: RelationshipProfile,
        turn: TurnRecord,
        *,
        processing_mode: SourceProcessingMode = SourceProcessingMode.NORMAL,
        reprocessing_id: Optional[str] = None,
    ) -> RelationshipProcessingRun:
        """Processes one sealed turn synchronously and resumes durable partial work."""
        if not isinstance(processing_mode, SourceProcessingMode):
            processing_mode = SourceProcessingMode(processing_mode)
        self._validate_submission(
            profile,
            turn,
            processing_mode=processing_mode,
            reprocessing_id=reprocessing_id,
        )
        processing_id = self.processing_id(
            profile,
            turn,
            processing_mode=processing_mode,
            reprocessing_id=reprocessing_id,
        )
        self.ensure_storage_available()

        with self.storage.relationship_processing_guard(profile.relationship_id):
            run = self.storage.get_relationship_processing_run(
                profile.relationship_id,
                processing_id,
            )
            if run is None:
                self.ensure_available()
                run = self._extract_and_freeze(
                    profile,
                    turn,
                    processing_id=processing_id,
                    processing_mode=processing_mode,
                    reprocessing_id=reprocessing_id,
                )
            self._validate_existing_run(
                run,
                profile,
                turn,
                processing_mode=processing_mode,
                reprocessing_id=reprocessing_id,
            )

            if isinstance(run.frozen_decision, RelationshipNoEventDecision):
                return run
            if run.status == RelationshipProcessingStatus.COMPLETED:
                return run

            if not run.decision_ids:
                run = self._adjudicate(profile, turn, run)
                if run.status == RelationshipProcessingStatus.FAILED:
                    return run
                if not run.decision_ids:
                    # A competing adapter instance won the CAS but has not yet
                    # published adjudication results. Leave the run resumable.
                    return run

            if run.status in (
                RelationshipProcessingStatus.EXTRACTED,
                RelationshipProcessingStatus.ADJUDICATED,
                RelationshipProcessingStatus.FAILED,
            ):
                run = self._advance_after_adjudication(run)

            if run.status in (
                RelationshipProcessingStatus.REFLECTION_PENDING,
                RelationshipProcessingStatus.PARTIAL_FAILED,
            ):
                return self._reflect_accepted_events(profile, turn, run)
            return run

    def get(
        self,
        relationship_id: str,
        processing_id: str,
    ) -> RelationshipProcessingRun:
        """Loads one run without permitting cross-relationship lookup."""
        if not self.query_available:
            raise RelationshipProcessingCapabilityError(
                "storage adapter does not expose relationship processing queries"
            )
        run = self.storage.get_relationship_processing_run(
            relationship_id,
            processing_id,
        )
        if run is None:
            raise LookupError("relationship processing run was not found")
        return run

    def list(self, relationship_id: str) -> List[RelationshipProcessingRun]:
        """Lists durable runs for exactly one relationship."""
        if not self.query_available:
            raise RelationshipProcessingCapabilityError(
                "storage adapter does not expose relationship processing queries"
            )
        return self.storage.list_relationship_processing_runs(relationship_id)

    def get_reflection(
        self,
        relationship_id: str,
        reflection_id: str,
    ) -> PersonaReflectionRecord:
        """Loads one formal reflection record inside a relationship scope."""
        if not self.query_available:
            raise RelationshipProcessingCapabilityError(
                "storage adapter does not expose persona reflection queries"
            )
        record = self.storage.get_persona_reflection_record(
            relationship_id,
            reflection_id,
        )
        if record is None:
            raise LookupError("persona reflection was not found")
        return record

    def list_reflections(
        self,
        relationship_id: str,
    ) -> List[PersonaReflectionRecord]:
        """Lists append-only formal reflection history."""
        if not self.query_available:
            raise RelationshipProcessingCapabilityError(
                "storage adapter does not expose persona reflection queries"
            )
        return self.storage.list_persona_reflection_records(relationship_id)

    def list_reflection_decisions(
        self,
        relationship_id: str,
    ) -> List[PersonaReflectionDecisionRecord]:
        """Lists reflection and explicit no-reflection decisions."""
        if not self.query_available:
            raise RelationshipProcessingCapabilityError(
                "storage adapter does not expose persona reflection queries"
            )
        return self.storage.list_persona_reflection_decisions(relationship_id)

    def append_reflection_interpretation(
        self,
        profile: RelationshipProfile,
        turn: TurnRecord,
        *,
        target_reflection_id: str,
        interpretation_id: str,
        record_kind: PersonaReflectionRecordKind,
    ) -> PersonaReflectionDecisionRecord:
        """Serializes and appends one explicit correction/reinterpretation."""
        with self.storage.relationship_processing_guard(profile.relationship_id):
            return self._append_reflection_interpretation_locked(
                profile,
                turn,
                target_reflection_id=target_reflection_id,
                interpretation_id=interpretation_id,
                record_kind=record_kind,
            )

    def _append_reflection_interpretation_locked(
        self,
        profile: RelationshipProfile,
        turn: TurnRecord,
        *,
        target_reflection_id: str,
        interpretation_id: str,
        record_kind: PersonaReflectionRecordKind,
    ) -> PersonaReflectionDecisionRecord:
        """Appends a correction/reinterpretation without mutating prior records."""
        if record_kind not in (
            PersonaReflectionRecordKind.CORRECTION,
            PersonaReflectionRecordKind.REINTERPRETATION,
        ):
            raise ValueError(
                "explicit reflection interpretation must be a correction or reinterpretation"
            )
        interpreter = self.persona_reflection_interpreter
        descriptor = self.interpreter_descriptor
        if interpreter is None or descriptor is None:
            raise RelationshipProcessingCapabilityError(
                "persona reflection interpreter is not configured"
            )
        if not isinstance(interpretation_id, str) or not interpretation_id.strip():
            raise ValueError("interpretation_id must be a non-empty stable host ID")
        target = self.get_reflection(
            profile.relationship_id,
            target_reflection_id,
        )
        provenance = target.context_provenance
        if (
            provenance.source_turn_id is None
            or provenance.source_revision is None
            or turn.turn_id != provenance.source_turn_id
            or turn.source_revision != provenance.source_revision
        ):
            raise RelationshipProcessingSubmissionError(
                "target reflection has no matching modern Source Turn provenance"
            )
        clean_interpretation_id = interpretation_id.strip()
        decision_id = self._explicit_interpretation_decision_id(
            profile.relationship_id,
            target_reflection_id,
            clean_interpretation_id,
            record_kind,
        )
        existing = self.storage.get_persona_reflection_decision(
            profile.relationship_id,
            decision_id,
        )
        if existing is not None:
            if (
                existing.relationship_id != profile.relationship_id
                or existing.event_id != target.event_id
                or existing.source_turn_id != turn.turn_id
                or existing.source_revision != turn.source_revision
                or existing.record_kind != record_kind
                or existing.target_reflection_id != target_reflection_id
                or existing.interpretation_id != clean_interpretation_id
            ):
                raise RelationshipProcessingConflictError(
                    "stored reflection interpretation has a conflicting identity"
                )
            return existing

        event, adjudication = self._event_context(
            profile.relationship_id,
            target.event_id,
        )
        request, current_provenance = self._reflection_request(
            profile,
            turn,
            event,
            adjudication,
            record_kind=record_kind,
            target_reflection_id=target_reflection_id,
        )
        try:
            decision = persona_reflection_decision_from_value(
                interpreter.interpret(request)
            )
        except Exception as exc:
            raise RelationshipProcessingSubmissionError(
                "persona_reflection_interpretation_failed"
            ) from exc
        now = utc_now()
        reflection_record = None
        if isinstance(decision, PersonaReflectionContentDecision):
            reflection_record = PersonaReflectionRecord(
                reflection_id=str(
                    uuid.uuid5(
                        uuid.NAMESPACE_URL,
                        f"erii:{decision_id}:persona-reflection",
                    )
                ),
                relationship_id=profile.relationship_id,
                event_id=event.event_id,
                record_kind=record_kind,
                target_reflection_id=target_reflection_id,
                content=decision.content,
                emotional_direction=decision.emotional_direction,
                emotional_intensity=decision.emotional_intensity,
                core_meaning=decision.core_meaning,
                interpreter_descriptor=descriptor,
                context_provenance=current_provenance,
                recorded_at=now,
            )
        outcome = PersonaReflectionDecisionRecord(
            decision_id=decision_id,
            relationship_id=profile.relationship_id,
            event_id=event.event_id,
            source_turn_id=turn.turn_id,
            source_revision=turn.source_revision,
            interpreter_descriptor=descriptor,
            decision=decision,
            context_provenance=current_provenance,
            record_kind=record_kind,
            target_reflection_id=target_reflection_id,
            interpretation_id=clean_interpretation_id,
            reflection_record=reflection_record,
            recorded_at=now,
        )
        return self.storage.commit_persona_reflection_decision(outcome)

    def _extract_and_freeze(
        self,
        profile: RelationshipProfile,
        turn: TurnRecord,
        *,
        processing_id: str,
        processing_mode: SourceProcessingMode,
        reprocessing_id: Optional[str],
    ) -> RelationshipProcessingRun:
        extractor = self.relationship_event_extractor
        descriptor = self.extractor_descriptor
        if extractor is None or descriptor is None:
            raise RelationshipProcessingCapabilityError(
                "relationship event extractor is not configured"
            )
        baseline_direct_events = tuple(
            self.storage.list_relationship_events(
                profile.relationship_id
            )
        )
        baseline_adjudications = tuple(
            self.storage.list_relationship_adjudications(
                profile.relationship_id
            )
        )
        prior_events = tuple(
            relationship_events_from_journals(
                baseline_direct_events,
                baseline_adjudications,
            )[-32:]
        )
        request = RelationshipEventExtractionRequest(
            source_turn_id=turn.turn_id,
            source_revision=turn.source_revision,
            relationship_id=profile.relationship_id,
            agent_id=profile.agent_id,
            user_id=profile.user_id,
            transcript=turn.transcript,
            interaction_context=turn.interaction_context,
            prior_events=prior_events,
        )
        try:
            decision = relationship_extraction_decision_from_value(
                extractor.extract(request)
            )
        except Exception as exc:
            raise RelationshipProcessingSubmissionError(
                "relationship_extraction_failed: no durable decision was produced"
            ) from exc

        now = utc_now()
        terminal = isinstance(decision, RelationshipNoEventDecision)
        run = RelationshipProcessingRun(
            processing_id=processing_id,
            relationship_id=profile.relationship_id,
            source_turn_id=turn.turn_id,
            source_revision=turn.source_revision,
            processing_mode=processing_mode,
            reprocessing_id=reprocessing_id,
            status=(
                RelationshipProcessingStatus.COMPLETED
                if terminal
                else RelationshipProcessingStatus.EXTRACTED
            ),
            outcome=(
                RelationshipProcessingOutcome.NO_RELATIONSHIP_EVENT
                if terminal
                else RelationshipProcessingOutcome.PENDING
            ),
            extractor_descriptor=descriptor.for_processing(
                erii_version=__version__,
                processed_at=now,
            ),
            frozen_decision=decision,
            adjudication_base_direct_event_count=len(
                baseline_direct_events
            ),
            adjudication_base_decision_count=len(
                baseline_adjudications
            ),
            adjudication_base_fingerprint=(
                relationship_adjudication_baseline_fingerprint(
                    baseline_direct_events,
                    baseline_adjudications,
                )
            ),
            reflection_planned=(
                not terminal
                and self.persona_reflection_interpreter is not None
            ),
            rule_version=RULE_VERSION,
            contract_version="relationship-processing-v1",
            created_at=now,
            updated_at=now,
            completed_at=now if terminal else None,
        )
        try:
            return self.storage.create_relationship_processing_run(run)
        except RelationshipProcessingConflictError:
            existing = self.storage.get_relationship_processing_run(
                profile.relationship_id,
                processing_id,
            )
            if existing is None:
                raise
            return existing

    @staticmethod
    def _validate_submission(
        profile: RelationshipProfile,
        turn: TurnRecord,
        *,
        processing_mode: SourceProcessingMode,
        reprocessing_id: Optional[str],
    ) -> None:
        if turn.relationship_id != profile.relationship_id:
            raise RelationshipProcessingSubmissionError(
                "invalid_source_turn: turn belongs to another relationship"
            )
        if turn.status != TurnStatus.COMPLETED:
            raise RelationshipProcessingSubmissionError(
                "invalid_source_turn: relationship processing requires a completed turn"
            )
        if turn.transcript.agent_message is None:
            raise RelationshipProcessingSubmissionError(
                "invalid_source_turn: completed turn has no visible agent reply"
            )
        if processing_mode == SourceProcessingMode.NORMAL:
            if reprocessing_id is not None:
                raise RelationshipProcessingSubmissionError(
                    "normal processing cannot contain reprocessing_id"
                )
            plan = turn.processing_plan
            if (
                plan is None
                or SourceProcessingChannel.RELATIONSHIP_ADJUDICATION
                not in plan.channels
            ):
                raise RelationshipProcessingSubmissionError(
                    "relationship channel was not accepted in the sealed Source Turn plan"
                )
        elif not reprocessing_id:
            raise RelationshipProcessingSubmissionError(
                "historical processing requires reprocessing_id"
            )

    @staticmethod
    def _validate_existing_run(
        run: RelationshipProcessingRun,
        profile: RelationshipProfile,
        turn: TurnRecord,
        *,
        processing_mode: SourceProcessingMode,
        reprocessing_id: Optional[str],
    ) -> None:
        if (
            run.relationship_id != profile.relationship_id
            or run.source_turn_id != turn.turn_id
            or run.source_revision != turn.source_revision
            or run.processing_mode != processing_mode
            or run.reprocessing_id != reprocessing_id
        ):
            raise RelationshipProcessingConflictError(
                "durable relationship processing identity does not match the source"
            )

    def _adjudicate(
        self,
        profile: RelationshipProfile,
        turn: TurnRecord,
        run: RelationshipProcessingRun,
    ) -> RelationshipProcessingRun:
        decision = run.frozen_decision
        if not isinstance(decision, RelationshipEventCandidatesDecision):
            return run
        try:
            direct_events = self.storage.list_relationship_events(
                profile.relationship_id
            )
            adjudications = (
                self.storage.list_relationship_adjudications(
                    profile.relationship_id
                )
            )
            if (
                len(direct_events)
                < run.adjudication_base_direct_event_count
                or len(adjudications)
                < run.adjudication_base_decision_count
            ):
                raise RelationshipProcessingConflictError(
                    "relationship adjudication baseline journal was truncated"
                )
            baseline_direct_events = tuple(
                direct_events[
                    : run.adjudication_base_direct_event_count
                ]
            )
            baseline_adjudications = tuple(
                adjudications[
                    : run.adjudication_base_decision_count
                ]
            )
            expected_baseline_fingerprint = (
                relationship_adjudication_baseline_fingerprint(
                    baseline_direct_events,
                    baseline_adjudications,
                )
            )
            if (
                run.adjudication_base_fingerprint
                != expected_baseline_fingerprint
            ):
                raise RelationshipProcessingConflictError(
                    "relationship adjudication baseline no longer matches "
                    "the frozen run"
                )
            result = self.adjudicator.adjudicate(
                profile,
                self._source_turn(turn, run),
                RelationshipCandidateBatch(
                    candidates=list(decision.candidates),
                ),
                baseline_direct_events=baseline_direct_events,
                baseline_adjudications=baseline_adjudications,
            )
        except Exception:
            failed = run.advance(
                status=RelationshipProcessingStatus.FAILED,
                outcome=RelationshipProcessingOutcome.FAILED,
                safe_failure_code="relationship_adjudication_failed",
                completed_at=utc_now(),
            )
            return self._cas_advance(run, failed)

        decision_ids = tuple(
            dict.fromkeys(
                (*run.decision_ids, *(record.receipt.decision_id for record in result.records))
            )
        )
        # CORROBORATED receipts point at older events. Only events created by an
        # ACCEPTED decision in this frozen run may receive a new reflection.
        event_ids = tuple(
            dict.fromkeys(
                (
                    *run.event_ids,
                    *(
                        event.event_id
                        for record in result.records
                        if record.receipt.outcome == DecisionOutcome.ACCEPTED
                        for event in record.events
                    ),
                )
            )
        )
        adjudicated = run.advance(
            status=RelationshipProcessingStatus.ADJUDICATED,
            outcome=RelationshipProcessingOutcome.PENDING,
            decision_ids=decision_ids,
            event_ids=event_ids,
            reflection_failure_event_ids=(),
            safe_failure_code=None,
            completed_at=None,
        )
        return self._cas_advance(run, adjudicated)

    def _advance_after_adjudication(
        self,
        run: RelationshipProcessingRun,
    ) -> RelationshipProcessingRun:
        if not run.event_ids:
            completed = run.advance(
                status=RelationshipProcessingStatus.COMPLETED,
                outcome=RelationshipProcessingOutcome.NO_ACCEPTED_EVENTS,
                reflection_failure_event_ids=(),
                safe_failure_code=None,
                completed_at=utc_now(),
            )
            return self._cas_advance(run, completed)
        if not run.reflection_planned:
            completed = run.advance(
                status=RelationshipProcessingStatus.COMPLETED,
                outcome=RelationshipProcessingOutcome.EVENTS_ACCEPTED,
                reflection_failure_event_ids=(),
                safe_failure_code=None,
                completed_at=utc_now(),
            )
            return self._cas_advance(run, completed)
        pending = run.advance(
            status=RelationshipProcessingStatus.REFLECTION_PENDING,
            outcome=RelationshipProcessingOutcome.PENDING,
            reflection_failure_event_ids=(),
            safe_failure_code=None,
            completed_at=None,
        )
        return self._cas_advance(run, pending)

    def _reflect_accepted_events(
        self,
        profile: RelationshipProfile,
        turn: TurnRecord,
        run: RelationshipProcessingRun,
    ) -> RelationshipProcessingRun:
        interpreter = self.persona_reflection_interpreter
        descriptor = self.interpreter_descriptor
        if interpreter is None or descriptor is None:
            raise RelationshipProcessingCapabilityError(
                "persona reflection interpreter is required to resume this run"
            )

        for event_id in run.event_ids:
            decision_id = self._reflection_decision_id(
                run,
                event_id,
                PersonaReflectionRecordKind.REFLECTION,
                None,
            )
            existing = self.storage.get_persona_reflection_decision(
                profile.relationship_id,
                decision_id,
            )
            if existing is not None:
                run = self._record_reflection_outcome(run, event_id, existing)
                if existing.decision_id not in run.reflection_outcome_ids:
                    return run
                continue
            try:
                event, adjudication = self._event_context(
                    profile.relationship_id,
                    event_id,
                )
                request, provenance = self._reflection_request(
                    profile,
                    turn,
                    event,
                    adjudication,
                    record_kind=PersonaReflectionRecordKind.REFLECTION,
                    target_reflection_id=None,
                )
                decision = persona_reflection_decision_from_value(
                    interpreter.interpret(request)
                )
                outcome = self._make_reflection_outcome(
                    run=run,
                    event=event,
                    decision_id=decision_id,
                    decision=decision,
                    provenance=provenance,
                    record_kind=PersonaReflectionRecordKind.REFLECTION,
                    target_reflection_id=None,
                )
                stored = self.storage.commit_persona_reflection_decision(outcome)
                run = self._record_reflection_outcome(run, event_id, stored)
                if stored.decision_id not in run.reflection_outcome_ids:
                    return run
            except Exception:
                failure_ids = tuple(
                    dict.fromkeys((*run.reflection_failure_event_ids, event_id))
                )
                failed = run.advance(
                    status=RelationshipProcessingStatus.PARTIAL_FAILED,
                    outcome=RelationshipProcessingOutcome.PARTIAL_FAILED,
                    reflection_failure_event_ids=failure_ids,
                    safe_failure_code="persona_reflection_failed",
                    completed_at=utc_now(),
                )
                return self._cas_advance(run, failed)

        completed = run.advance(
            status=RelationshipProcessingStatus.COMPLETED,
            outcome=RelationshipProcessingOutcome.EVENTS_ACCEPTED,
            reflection_failure_event_ids=(),
            safe_failure_code=None,
            completed_at=utc_now(),
        )
        return self._cas_advance(run, completed)

    def _record_reflection_outcome(
        self,
        run: RelationshipProcessingRun,
        event_id: str,
        outcome: PersonaReflectionDecisionRecord,
    ) -> RelationshipProcessingRun:
        if outcome.relationship_id != run.relationship_id or outcome.event_id != event_id:
            raise RelationshipProcessingConflictError(
                "reflection decision belongs to another processing input"
            )
        outcome_ids = tuple(
            dict.fromkeys((*run.reflection_outcome_ids, outcome.decision_id))
        )
        failure_ids = tuple(
            item for item in run.reflection_failure_event_ids if item != event_id
        )
        if (
            outcome_ids == run.reflection_outcome_ids
            and failure_ids == run.reflection_failure_event_ids
            and run.status == RelationshipProcessingStatus.REFLECTION_PENDING
        ):
            return run
        pending = run.advance(
            status=RelationshipProcessingStatus.REFLECTION_PENDING,
            outcome=RelationshipProcessingOutcome.PENDING,
            reflection_outcome_ids=outcome_ids,
            reflection_failure_event_ids=failure_ids,
            safe_failure_code=None,
            completed_at=None,
        )
        return self._cas_advance(run, pending)

    @staticmethod
    def _source_turn(
        turn: TurnRecord,
        run: RelationshipProcessingRun,
    ) -> SourceTurn:
        messages = [
            SourceMessage(
                source_id=turn.transcript.user_message.message_id,
                revision=turn.source_revision,
                role=SourceRole.USER,
                content=turn.transcript.user_message.content,
                occurred_at=turn.transcript.user_message.recorded_at,
            )
        ]
        agent_message = turn.transcript.agent_message
        if agent_message is not None:
            messages.append(
                SourceMessage(
                    source_id=agent_message.message_id,
                    revision=turn.source_revision,
                    role=SourceRole.AGENT,
                    content=agent_message.content,
                    occurred_at=agent_message.recorded_at,
                )
            )
        return SourceTurn(
            turn_id=turn.turn_id,
            revision=turn.source_revision,
            messages=messages,
            extractor_version=run.extractor_descriptor.extractor_version,
            contract_version=run.contract_version,
            processing_mode=run.processing_mode,
            reprocessing_id=run.reprocessing_id,
        )

    def _event_context(
        self,
        relationship_id: str,
        event_id: str,
    ) -> Tuple[RelationshipEvent, AdjudicationRecord]:
        for adjudication in self.storage.list_relationship_adjudications(
            relationship_id
        ):
            for event in adjudication.events:
                if event.event_id == event_id:
                    return event, adjudication
        raise RelationshipProcessingConflictError(
            "accepted processing event has no durable adjudication"
        )

    def _reflection_request(
        self,
        profile: RelationshipProfile,
        turn: TurnRecord,
        event: RelationshipEvent,
        adjudication: AdjudicationRecord,
        *,
        record_kind: PersonaReflectionRecordKind,
        target_reflection_id: Optional[str],
    ) -> Tuple[
        PersonaReflectionInterpretationRequest,
        ReflectionContextProvenance,
    ]:
        manifest = (
            self.storage.get_persona_manifest(profile.manifest_id)
            if profile.manifest_id is not None
            else None
        )
        approved_growth = tuple(
            item
            for item in self.storage.list_persona_growth_proposals(
                profile.relationship_id
            )
            if item.status == PersonaGrowthStatus.APPROVED
        )[-32:]
        all_events = list_complete_relationship_events(
            self.storage,
            profile.relationship_id,
        )
        prior_events = tuple(
            item for item in all_events if item.event_id != event.event_id
        )[-32:]
        prior_reflections = tuple(
            self.storage.list_persona_reflection_records(profile.relationship_id)[
                -32:
            ]
        )
        request = PersonaReflectionInterpretationRequest(
            relationship_id=profile.relationship_id,
            agent_id=profile.agent_id,
            user_id=profile.user_id,
            source_turn_id=turn.turn_id,
            source_revision=turn.source_revision,
            event=event,
            evidence=tuple(adjudication.receipt.evidence),
            blueprint=profile.blueprint,
            baseline=profile.baseline,
            manifest=manifest,
            approved_growth=approved_growth,
            prior_events=prior_events,
            prior_reflections=prior_reflections,
            record_kind=record_kind,
            target_reflection_id=target_reflection_id,
        )
        provenance = ReflectionContextProvenance(
            relationship_event_id=event.event_id,
            source_turn_id=turn.turn_id,
            source_revision=turn.source_revision,
            decision_id=adjudication.receipt.decision_id,
            evidence_ids=tuple(
                item.evidence_id for item in adjudication.receipt.evidence
            ),
            blueprint_id=profile.blueprint.blueprint_id,
            blueprint_sha256=profile.blueprint.source_sha256,
            blueprint_revision=profile.blueprint.revision,
            manifest_id=manifest.manifest_id if manifest is not None else None,
            manifest_revision=(
                manifest.approved_revision if manifest is not None else None
            ),
            manifest_fingerprint=(
                manifest.content_fingerprint if manifest is not None else None
            ),
            baseline_fingerprint=_fingerprint(profile.baseline.to_dict()),
            approved_growth=tuple(
                ApprovedGrowthReference(
                    proposal_id=item.proposal_id,
                    revision=item.revision,
                    content_fingerprint=_fingerprint(item.to_dict()),
                    approved_at=item.decided_at,
                )
                for item in approved_growth
            ),
            prior_event_ids=tuple(item.event_id for item in prior_events),
            prior_reflection_ids=tuple(
                item.reflection_id for item in prior_reflections
            ),
        )
        return request, provenance

    def _make_reflection_outcome(
        self,
        *,
        run: RelationshipProcessingRun,
        event: RelationshipEvent,
        decision_id: str,
        decision: object,
        provenance: ReflectionContextProvenance,
        record_kind: PersonaReflectionRecordKind,
        target_reflection_id: Optional[str],
    ) -> PersonaReflectionDecisionRecord:
        descriptor = self.interpreter_descriptor
        if descriptor is None:
            raise RelationshipProcessingCapabilityError(
                "persona reflection interpreter is not configured"
            )
        parsed = persona_reflection_decision_from_value(decision)
        now = utc_now()
        reflection_record = None
        if isinstance(parsed, PersonaReflectionContentDecision):
            reflection_record = PersonaReflectionRecord(
                reflection_id=str(
                    uuid.uuid5(
                        uuid.NAMESPACE_URL,
                        f"erii:{decision_id}:persona-reflection",
                    )
                ),
                relationship_id=run.relationship_id,
                event_id=event.event_id,
                record_kind=record_kind,
                target_reflection_id=target_reflection_id,
                content=parsed.content,
                emotional_direction=parsed.emotional_direction,
                emotional_intensity=parsed.emotional_intensity,
                core_meaning=parsed.core_meaning,
                interpreter_descriptor=descriptor,
                context_provenance=provenance,
                recorded_at=now,
            )
        return PersonaReflectionDecisionRecord(
            decision_id=decision_id,
            relationship_id=run.relationship_id,
            event_id=event.event_id,
            source_turn_id=run.source_turn_id,
            source_revision=run.source_revision,
            record_kind=record_kind,
            target_reflection_id=target_reflection_id,
            interpreter_descriptor=descriptor,
            decision=parsed,
            context_provenance=provenance,
            reflection_record=reflection_record,
            recorded_at=now,
        )

    @staticmethod
    def _reflection_decision_id(
        run: RelationshipProcessingRun,
        event_id: str,
        record_kind: PersonaReflectionRecordKind,
        target_reflection_id: Optional[str],
    ) -> str:
        return str(
            uuid.uuid5(
                uuid.NAMESPACE_URL,
                (
                    f"erii:{run.relationship_id}:reflection-decision:"
                    f"{run.processing_id}:{event_id}:{record_kind.value}:"
                    f"{target_reflection_id or ''}"
                ),
            )
        )

    @staticmethod
    def _explicit_interpretation_decision_id(
        relationship_id: str,
        target_reflection_id: str,
        interpretation_id: str,
        record_kind: PersonaReflectionRecordKind,
    ) -> str:
        """Builds the stable ID for one explicit correction/reinterpretation."""
        return str(
            uuid.uuid5(
                uuid.NAMESPACE_URL,
                (
                    f"erii:{relationship_id}:reflection-decision:"
                    f"{record_kind.value}:{target_reflection_id}:"
                    f"{interpretation_id}"
                ),
            )
        )

    def _cas_advance(
        self,
        previous: RelationshipProcessingRun,
        advanced: RelationshipProcessingRun,
    ) -> RelationshipProcessingRun:
        try:
            return self.storage.update_relationship_processing_run(
                advanced,
                expected_record_version=previous.record_version,
            )
        except RelationshipProcessingConflictError:
            current = self.storage.get_relationship_processing_run(
                previous.relationship_id,
                previous.processing_id,
            )
            if current is None:
                raise
            return current

    @property
    def query_available(self) -> bool:
        """Whether the adapter can expose durable relationship processing state."""
        return all(
            getattr(type(self.storage), name, None) is not getattr(BaseStorage, name)
            for name in (
                "get_relationship_processing_run",
                "list_relationship_processing_runs",
                "get_persona_reflection_decision",
                "list_persona_reflection_decisions",
                "get_persona_reflection_record",
                "list_persona_reflection_records",
            )
        )

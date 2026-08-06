"""Deterministic projection of the append-only relationship-consequence journal."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
import hashlib
from typing import Any, Dict, List, Optional, Tuple
import uuid

from erii.models.adjudication import (
    AdjudicationRecord,
    DecisionOutcome,
    SourceRole,
)
from erii.models.consequence import (
    ConsequenceConflictError,
    NarrativeTensionConflictError,
    NarrativeTensionLink,
    NarrativeTensionOutcome,
    NarrativeTensionProjection,
    RelationshipConsequence,
    RelationshipConsequenceKind,
)
from erii.models.continuity_review import ContinuityReviewKind
from erii.models.relationship import RelationshipEvent, utc_now
from erii.models.turn import (
    ContinuityVerdict,
    DeliveryDisposition,
    TurnRecord,
    TurnStatus,
)
from erii.storage.base import BaseStorage


ConsequenceValue = RelationshipConsequence | Mapping[str, Any]
TensionLinkValue = NarrativeTensionLink | Mapping[str, Any]


class NarrativeTensionProjector:
    """Rebuilds current tension outcomes without storage, clocks, or inference."""

    _TERMINAL_OUTCOMES = frozenset(
        {
            NarrativeTensionOutcome.MUTUALLY_RECONCILED,
            NarrativeTensionOutcome.BOUNDARY_STABILIZED,
            NarrativeTensionOutcome.RELATIONSHIP_ENDED,
            NarrativeTensionOutcome.SUPERSEDED,
        }
    )

    @classmethod
    def project(
        cls,
        consequences: Sequence[ConsequenceValue],
        links: Sequence[TensionLinkValue] = (),
    ) -> Tuple[NarrativeTensionProjection, ...]:
        """Projects one result per consequence in deterministic source order.

        Consequences and links are independent append-only journals.  Exact
        replay of the same stable identity is idempotent; reuse with different
        content is a conflict.  A link must resolve the same relationship,
        tension, and consequence simultaneously.
        """
        normalized_consequences = cls._normalize_consequences(consequences)
        normalized_links = cls._normalize_links(links)

        ordered_consequences = sorted(
            normalized_consequences,
            key=lambda item: (
                item.recorded_at,
                item.consequence_id,
                item.relationship_id,
            ),
        )
        unique_consequences: List[RelationshipConsequence] = []
        consequences_by_key: Dict[
            tuple[str, str], RelationshipConsequence
        ] = {}
        consequences_by_id: Dict[str, set[str]] = {}
        tensions_by_key: Dict[tuple[str, str], RelationshipConsequence] = {}

        for consequence in ordered_consequences:
            consequence_key = (
                consequence.relationship_id,
                consequence.consequence_id,
            )
            duplicate = consequences_by_key.get(consequence_key)
            if duplicate is not None:
                if not duplicate.same_payload_as(consequence):
                    raise ConsequenceConflictError(
                        f"consequence_id {consequence.consequence_id!r} "
                        "has conflicting journal payloads"
                    )
                continue

            tension_key = (consequence.relationship_id, consequence.tension_id)
            prior_tension = tensions_by_key.get(tension_key)
            if prior_tension is not None:
                raise NarrativeTensionConflictError(
                    f"tension_id {consequence.tension_id!r} is already rooted "
                    "in another consequence"
                )

            consequences_by_key[consequence_key] = consequence
            consequences_by_id.setdefault(consequence.consequence_id, set()).add(
                consequence.relationship_id
            )
            tensions_by_key[tension_key] = consequence
            unique_consequences.append(consequence)

        ordered_links = sorted(
            normalized_links,
            key=lambda item: (
                item.recorded_at,
                item.link_id,
                item.relationship_id,
            ),
        )
        unique_links: List[NarrativeTensionLink] = []
        links_by_key: Dict[tuple[str, str], NarrativeTensionLink] = {}
        for link in ordered_links:
            link_key = (link.relationship_id, link.link_id)
            duplicate = links_by_key.get(link_key)
            if duplicate is not None:
                if not duplicate.same_payload_as(link):
                    raise NarrativeTensionConflictError(
                        f"link_id {link.link_id!r} has conflicting journal payloads"
                    )
                continue
            links_by_key[link_key] = link
            unique_links.append(link)

        outcomes: Dict[tuple[str, str], NarrativeTensionOutcome] = {
            (item.relationship_id, item.consequence_id):
            NarrativeTensionOutcome.UNADDRESSED
            for item in unique_consequences
        }
        summaries: Dict[tuple[str, str], str] = {
            (item.relationship_id, item.consequence_id): item.summary
            for item in unique_consequences
        }
        link_ids: Dict[tuple[str, str], List[str]] = {
            (item.relationship_id, item.consequence_id): []
            for item in unique_consequences
        }

        for link in unique_links:
            consequence_key = (link.relationship_id, link.consequence_id)
            consequence = consequences_by_key.get(consequence_key)
            if consequence is None:
                other_relationships = consequences_by_id.get(link.consequence_id, set())
                if other_relationships:
                    raise NarrativeTensionConflictError(
                        "narrative tension link references a consequence in "
                        "another relationship"
                    )
                raise NarrativeTensionConflictError(
                    "narrative tension link references a missing consequence"
                )
            if consequence.tension_id != link.tension_id:
                raise NarrativeTensionConflictError(
                    "narrative tension link does not match its consequence tension"
                )

            current_outcome = outcomes[consequence_key]
            if (
                current_outcome in cls._TERMINAL_OUTCOMES
                and link.outcome != NarrativeTensionOutcome.SUPERSEDED
            ):
                raise NarrativeTensionConflictError(
                    "a terminal narrative tension cannot be silently reopened"
                )

            outcomes[consequence_key] = link.outcome
            summaries[consequence_key] = link.summary
            link_ids[consequence_key].append(link.link_id)

        return tuple(
            NarrativeTensionProjection(
                relationship_id=consequence.relationship_id,
                tension_id=consequence.tension_id,
                consequence_id=consequence.consequence_id,
                source_turn_id=consequence.source_turn_id,
                source_revision=consequence.source_revision,
                source_decision_id=consequence.source_decision_id,
                source_event_id=consequence.source_event_id,
                source_message_id=consequence.source_message_id,
                effects=consequence.effects,
                outcome=outcomes[
                    (consequence.relationship_id, consequence.consequence_id)
                ],
                summary=summaries[
                    (consequence.relationship_id, consequence.consequence_id)
                ],
                link_ids=tuple(
                    link_ids[
                        (consequence.relationship_id, consequence.consequence_id)
                    ]
                ),
            )
            for consequence in unique_consequences
        )

    @staticmethod
    def _normalize_consequences(
        values: Sequence[ConsequenceValue],
    ) -> Tuple[RelationshipConsequence, ...]:
        if isinstance(values, (str, bytes)) or not isinstance(values, Sequence):
            raise ValueError("consequences must be a sequence")
        normalized: List[RelationshipConsequence] = []
        for index, value in enumerate(values):
            if isinstance(value, RelationshipConsequence):
                normalized.append(value)
            elif isinstance(value, Mapping):
                normalized.append(RelationshipConsequence.from_dict(value))
            else:
                raise ValueError(
                    f"consequences[{index}] must be a RelationshipConsequence"
                )
        return tuple(normalized)

    @staticmethod
    def _normalize_links(
        values: Sequence[TensionLinkValue],
    ) -> Tuple[NarrativeTensionLink, ...]:
        if isinstance(values, (str, bytes)) or not isinstance(values, Sequence):
            raise ValueError("links must be a sequence")
        normalized: List[NarrativeTensionLink] = []
        for index, value in enumerate(values):
            if isinstance(value, NarrativeTensionLink):
                normalized.append(value)
            elif isinstance(value, Mapping):
                normalized.append(NarrativeTensionLink.from_dict(value))
            else:
                raise ValueError(
                    f"links[{index}] must be a NarrativeTensionLink"
                )
        return tuple(normalized)


class RelationshipConsequenceCoordinator:
    """Validates and commits the source-bound consequence journals.

    A consequence is intentionally stricter than an ordinary accepted
    relationship event.  Its Agent source must be the exact final message of
    a completed, reviewed, ``shown`` Turn whose continuity verdict supports
    delivery.  Later tension links must pass the same source gate and directly
    reference the initiating event.
    """

    _SUPPORTED_VERDICTS = frozenset(
        {
            ContinuityVerdict.ALIGNED,
            ContinuityVerdict.SUPPORTED_NEW_CHOICE,
        }
    )

    def __init__(self, storage: BaseStorage) -> None:
        self.storage = storage

    @staticmethod
    def consequence_id(
        relationship_id: str,
        source_decision_id: str,
        source_event_id: str,
    ) -> str:
        """Returns the stable identity for one initiating accepted event."""
        return str(
            uuid.uuid5(
                uuid.NAMESPACE_URL,
                (
                    f"erii:{relationship_id}:relationship-consequence:"
                    f"{source_decision_id}:{source_event_id}"
                ),
            )
        )

    @staticmethod
    def tension_id(relationship_id: str, consequence_id: str) -> str:
        """Returns the one-to-one stable tension identity for a consequence."""
        return str(
            uuid.uuid5(
                uuid.NAMESPACE_URL,
                f"erii:{relationship_id}:narrative-tension:{consequence_id}",
            )
        )

    @staticmethod
    def link_id(
        relationship_id: str,
        tension_id: str,
        source_decision_id: str,
        source_event_id: str,
    ) -> str:
        """Returns the stable identity for one later sourced tension link."""
        return str(
            uuid.uuid5(
                uuid.NAMESPACE_URL,
                (
                    f"erii:{relationship_id}:narrative-tension-link:"
                    f"{tension_id}:{source_decision_id}:{source_event_id}"
                ),
            )
        )

    def record_consequence(
        self,
        relationship_id: str,
        *,
        source_turn_id: str,
        source_decision_id: str,
        source_event_id: str,
        effects: Sequence[RelationshipConsequenceKind | str],
        summary: str,
        consequence_id: Optional[str] = None,
        tension_id: Optional[str] = None,
        recorded_at: Optional[str] = None,
    ) -> RelationshipConsequence:
        """Builds and appends one consequence from authoritative stored source."""
        with self.storage.relationship_processing_guard(relationship_id):
            turns, adjudications = self._source_journals(relationship_id)
            turn, _, _ = self._resolve_accepted_event_source(
                relationship_id,
                source_turn_id,
                None,
                source_decision_id,
                source_event_id,
                turns,
                adjudications,
            )
            agent_message = turn.transcript.agent_message
            if agent_message is None:  # Defensive; source validation is fail-closed.
                raise ValueError("relationship consequence requires an Agent message")
            stable_consequence_id = consequence_id or self.consequence_id(
                relationship_id,
                source_decision_id,
                source_event_id,
            )
            consequence = RelationshipConsequence(
                consequence_id=stable_consequence_id,
                relationship_id=relationship_id,
                tension_id=(
                    tension_id
                    or self.tension_id(relationship_id, stable_consequence_id)
                ),
                source_turn_id=source_turn_id,
                source_revision=turn.source_revision,
                source_decision_id=source_decision_id,
                source_event_id=source_event_id,
                source_message_id=agent_message.message_id,
                effects=effects,
                summary=summary,
                recorded_at=recorded_at or utc_now(),
            )
            return self._append_consequence_locked(
                relationship_id,
                consequence,
                turns,
                adjudications,
            )

    def append_consequence(
        self,
        relationship_id: str,
        consequence: RelationshipConsequence,
    ) -> RelationshipConsequence:
        """Validates and appends a caller-supplied immutable consequence."""
        with self.storage.relationship_processing_guard(relationship_id):
            turns, adjudications = self._source_journals(relationship_id)
            return self._append_consequence_locked(
                relationship_id,
                consequence,
                turns,
                adjudications,
            )

    def _append_consequence_locked(
        self,
        relationship_id: str,
        consequence: RelationshipConsequence,
        turns: Sequence[TurnRecord],
        adjudications: Sequence[AdjudicationRecord],
    ) -> RelationshipConsequence:
        existing_consequences = self.storage.list_relationship_consequences(
            relationship_id
        )
        links = self.storage.list_narrative_tension_links(relationship_id)
        self.validate_journal(
            relationship_id,
            (*existing_consequences, consequence),
            links,
            turns,
            adjudications,
        )
        stored = self.storage.append_relationship_consequence(consequence)
        if not stored.same_payload_as(consequence):
            raise ConsequenceConflictError(
                "persisted relationship consequence differs from requested payload"
            )
        return stored

    def record_tension_link(
        self,
        relationship_id: str,
        *,
        consequence_id: str,
        source_turn_id: str,
        source_decision_id: str,
        source_event_id: str,
        outcome: NarrativeTensionOutcome | str,
        summary: str,
        link_id: Optional[str] = None,
        recorded_at: Optional[str] = None,
    ) -> NarrativeTensionLink:
        """Builds and appends one later outcome linked to a consequence."""
        with self.storage.relationship_processing_guard(relationship_id):
            consequences = self.storage.list_relationship_consequences(
                relationship_id
            )
            matches = [
                item
                for item in consequences
                if item.consequence_id == consequence_id
            ]
            if len(matches) != 1:
                raise NarrativeTensionConflictError(
                    "narrative tension link requires exactly one initiating consequence"
                )
            consequence = matches[0]
            stable_link_id = link_id or self.link_id(
                relationship_id,
                consequence.tension_id,
                source_decision_id,
                source_event_id,
            )
            turns, adjudications = self._source_journals(relationship_id)
            source_turn = next(
                (item for item in turns if item.turn_id == source_turn_id),
                None,
            )
            if source_turn is None:
                raise ValueError("narrative tension source Turn is missing")
            link = NarrativeTensionLink(
                link_id=stable_link_id,
                relationship_id=relationship_id,
                tension_id=consequence.tension_id,
                consequence_id=consequence.consequence_id,
                source_turn_id=source_turn_id,
                source_revision=source_turn.source_revision,
                source_decision_id=source_decision_id,
                source_event_id=source_event_id,
                outcome=outcome,
                summary=summary,
                recorded_at=recorded_at or utc_now(),
            )
            return self._append_tension_link_locked(
                relationship_id,
                link,
                consequences,
                turns,
                adjudications,
            )

    def append_tension_link(
        self,
        relationship_id: str,
        link: NarrativeTensionLink,
    ) -> NarrativeTensionLink:
        """Validates and appends a caller-supplied immutable tension link."""
        with self.storage.relationship_processing_guard(relationship_id):
            consequences = self.storage.list_relationship_consequences(
                relationship_id
            )
            turns, adjudications = self._source_journals(relationship_id)
            return self._append_tension_link_locked(
                relationship_id,
                link,
                consequences,
                turns,
                adjudications,
            )

    def _append_tension_link_locked(
        self,
        relationship_id: str,
        link: NarrativeTensionLink,
        consequences: Sequence[RelationshipConsequence],
        turns: Sequence[TurnRecord],
        adjudications: Sequence[AdjudicationRecord],
    ) -> NarrativeTensionLink:
        existing_links = self.storage.list_narrative_tension_links(
            relationship_id
        )
        self.validate_journal(
            relationship_id,
            consequences,
            (*existing_links, link),
            turns,
            adjudications,
        )
        stored = self.storage.append_narrative_tension_link(link)
        if not stored.same_payload_as(link):
            raise NarrativeTensionConflictError(
                "persisted narrative tension link differs from requested payload"
            )
        return stored

    def list_consequences(
        self,
        relationship_id: str,
    ) -> Tuple[RelationshipConsequence, ...]:
        """Returns an immutable snapshot of one relationship's consequences."""
        return tuple(
            self.storage.list_relationship_consequences(relationship_id)
        )

    def list_links(
        self,
        relationship_id: str,
    ) -> Tuple[NarrativeTensionLink, ...]:
        """Returns an immutable snapshot of one relationship's tension links."""
        return tuple(self.storage.list_narrative_tension_links(relationship_id))

    def project(
        self,
        relationship_id: str,
    ) -> Tuple[NarrativeTensionProjection, ...]:
        """Projects a consistent relationship-scoped journal snapshot."""
        with self.storage.relationship_processing_guard(relationship_id):
            consequences = self.storage.list_relationship_consequences(
                relationship_id
            )
            links = self.storage.list_narrative_tension_links(relationship_id)
            return NarrativeTensionProjector.project(consequences, links)

    def _source_journals(
        self,
        relationship_id: str,
    ) -> Tuple[Sequence[TurnRecord], Sequence[AdjudicationRecord]]:
        return (
            self.storage.list_turn_records(relationship_id),
            self.storage.list_relationship_adjudications(relationship_id),
        )

    @classmethod
    def validate_journal(
        cls,
        relationship_id: str,
        consequences: Sequence[RelationshipConsequence],
        links: Sequence[NarrativeTensionLink],
        turns: Sequence[TurnRecord],
        adjudications: Sequence[AdjudicationRecord],
    ) -> Tuple[NarrativeTensionProjection, ...]:
        """Validates a complete portable causal graph and returns its projection."""
        if any(item.relationship_id != relationship_id for item in consequences):
            raise ConsequenceConflictError(
                "relationship consequence crosses relationship boundaries"
            )
        if any(item.relationship_id != relationship_id for item in links):
            raise NarrativeTensionConflictError(
                "narrative tension link crosses relationship boundaries"
            )

        projection = NarrativeTensionProjector.project(consequences, links)
        consequence_by_id = {
            item.consequence_id: item for item in consequences
        }
        for consequence in consequences:
            cls._resolve_accepted_event_source(
                relationship_id,
                consequence.source_turn_id,
                consequence.source_revision,
                consequence.source_decision_id,
                consequence.source_event_id,
                turns,
                adjudications,
                expected_agent_message_id=consequence.source_message_id,
            )

        for link in links:
            consequence = consequence_by_id.get(link.consequence_id)
            if consequence is None:
                raise NarrativeTensionConflictError(
                    "narrative tension link references a missing consequence"
                )
            _, record, event = cls._resolve_accepted_event_source(
                relationship_id,
                link.source_turn_id,
                link.source_revision,
                link.source_decision_id,
                link.source_event_id,
                turns,
                adjudications,
            )
            adjudication_metadata = event.metadata.get("adjudication")
            references = (
                adjudication_metadata.get("references", ())
                if isinstance(adjudication_metadata, Mapping)
                else ()
            )
            if (
                isinstance(references, (str, bytes))
                or not isinstance(references, Sequence)
                or consequence.source_event_id not in references
            ):
                raise NarrativeTensionConflictError(
                    "narrative tension source event must directly reference "
                    "the initiating event"
                )
            evidence_roles = {item.role for item in record.receipt.evidence}
            if link.outcome == NarrativeTensionOutcome.MUTUALLY_RECONCILED and not {
                SourceRole.USER,
                SourceRole.AGENT,
            }.issubset(evidence_roles):
                raise NarrativeTensionConflictError(
                    "mutual reconciliation requires both User and Agent evidence"
                )
            if (
                link.outcome == NarrativeTensionOutcome.BOUNDARY_STABILIZED
                and SourceRole.AGENT not in evidence_roles
            ):
                raise NarrativeTensionConflictError(
                    "boundary stabilization requires Agent evidence"
                )
        return projection

    @classmethod
    def _resolve_accepted_event_source(
        cls,
        relationship_id: str,
        source_turn_id: str,
        source_revision: Optional[str],
        source_decision_id: str,
        source_event_id: str,
        turns: Sequence[TurnRecord],
        adjudications: Sequence[AdjudicationRecord],
        *,
        expected_agent_message_id: Optional[str] = None,
    ) -> Tuple[TurnRecord, AdjudicationRecord, RelationshipEvent]:
        matching_turns = [
            item
            for item in turns
            if item.turn_id == source_turn_id
            and item.relationship_id == relationship_id
        ]
        if len(matching_turns) != 1:
            raise ValueError(
                "relationship consequence requires exactly one source Turn"
            )
        turn = matching_turns[0]
        if source_revision is not None and turn.source_revision != source_revision:
            raise ValueError("relationship consequence source revision does not match")
        if turn.status != TurnStatus.COMPLETED:
            raise ValueError("relationship consequence requires a completed source Turn")
        if turn.delivery_disposition != DeliveryDisposition.SHOWN:
            raise ValueError(
                "relationship consequence requires an exactly shown final reply"
            )
        review = turn.review_record
        if (
            review is None
            or review.kind != ContinuityReviewKind.REVIEWED
            or review.receipt is None
            or review.receipt.assessment.verdict not in cls._SUPPORTED_VERDICTS
        ):
            raise ValueError(
                "relationship consequence requires a supported continuity review"
            )
        agent_message = turn.transcript.agent_message
        if agent_message is None:
            raise ValueError("relationship consequence requires a final Agent message")
        if (
            expected_agent_message_id is not None
            and agent_message.message_id != expected_agent_message_id
        ):
            raise ValueError(
                "relationship consequence source message is not the final Agent message"
            )
        binding = review.receipt.review_binding
        message_digest = hashlib.sha256(
            agent_message.content.encode("utf-8")
        ).hexdigest()
        if (
            binding.relationship_id != relationship_id
            or binding.turn_id != source_turn_id
            or binding.reply_sha256 != message_digest
            or binding.reply_length != len(agent_message.content)
        ):
            raise ValueError(
                "relationship consequence continuity review is not bound to "
                "the exact final Agent message"
            )

        matching_records = [
            item
            for item in adjudications
            if item.receipt.decision_id == source_decision_id
            and item.receipt.relationship_id == relationship_id
        ]
        if len(matching_records) != 1:
            raise ValueError(
                "relationship consequence requires exactly one source decision"
            )
        record = matching_records[0]
        receipt = record.receipt
        if (
            receipt.source_turn_id != source_turn_id
            or receipt.source_revision != turn.source_revision
        ):
            raise ValueError(
                "relationship consequence decision does not match its source Turn"
            )
        if receipt.outcome != DecisionOutcome.ACCEPTED:
            raise ValueError(
                "relationship consequence requires an accepted source decision"
            )
        matching_events = [
            event for event in record.events if event.event_id == source_event_id
        ]
        if (
            len(matching_events) != 1
            or source_event_id not in receipt.event_ids
            or matching_events[0].relationship_id != relationship_id
        ):
            raise ValueError(
                "relationship consequence source event does not belong to its decision"
            )
        exact_agent_evidence = any(
            item.source_id == agent_message.message_id
            and item.source_revision == turn.source_revision
            and item.role == SourceRole.AGENT
            and item.quote == agent_message.content
            and item.start == 0
            and item.end == len(agent_message.content)
            and item.message_sha256 == message_digest
            and item.occurred_at == agent_message.recorded_at
            for item in receipt.evidence
        )
        if not exact_agent_evidence:
            raise ValueError(
                "relationship consequence requires exact final Agent message evidence"
            )
        return turn, record, matching_events[0]


def project_narrative_tensions(
    consequences: Sequence[ConsequenceValue],
    links: Sequence[TensionLinkValue] = (),
) -> Tuple[NarrativeTensionProjection, ...]:
    """Functional entry point for :class:`NarrativeTensionProjector`."""
    return NarrativeTensionProjector.project(consequences, links)


__all__ = [
    "ConsequenceConflictError",
    "NarrativeTensionConflictError",
    "NarrativeTensionProjector",
    "RelationshipConsequenceCoordinator",
    "project_narrative_tensions",
]

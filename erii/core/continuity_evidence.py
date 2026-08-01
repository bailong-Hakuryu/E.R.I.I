"""Kernel resolution for typed continuity evidence references."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
import hashlib
import json
from typing import Tuple, Union

from erii.models.adjudication import PersonaGrowthStatus
from erii.models.archival import (
    ArchivalArtifactKind,
    ArchivalNotFoundError,
    ArchivalStatus,
    archival_artifact_fingerprint,
)
from erii.models.consolidation import (
    PersonaReflectionRecordKind,
    ReflectionProvenanceState,
)
from erii.models.continuity_evidence import (
    PERSONA_EVIDENCE_KINDS,
    RELATIONSHIP_EVIDENCE_KINDS,
    ContinuityEvidenceKind,
    ContinuityEvidenceRef,
)
from erii.models.persona import PersonaApplicability, PersonaManifest, PersonaScope
from erii.models.relationship import RelationshipPremiseMode
from erii.models.provenance import ArtifactProvenanceState
from erii.models.relationship import RelationshipProfile
from erii.models.turn import ContextSignalSource, TurnNotFoundError, TurnStatus
from erii.models.turn_context import TurnContextBaseline
from erii.models.turn_context import (
    persona_growth_content_fingerprint,
    premise_content_fingerprint,
)
from erii.models.voice_trace import VoiceActivationTrace
from erii.storage.base import BaseStorage


ContinuityEvidenceRefValue = Union[
    ContinuityEvidenceRef,
    Mapping[str, object],
]


def _canonical_hash(value: object) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


class ContinuityEvidenceResolver:
    """Resolves every supported locator through one relationship-scoped seam."""

    def __init__(self, storage: BaseStorage) -> None:
        self._storage = storage

    def resolve_persona_refs(
        self,
        profile: RelationshipProfile,
        baseline: TurnContextBaseline,
        values: Sequence[ContinuityEvidenceRefValue],
    ) -> Tuple[ContinuityEvidenceRef, ...]:
        """Returns verified Persona authorities in caller-supplied order."""
        return self._resolve(
            profile,
            baseline,
            values,
            allowed_kinds=PERSONA_EVIDENCE_KINDS,
            field_name="persona_context_refs",
        )

    def resolve_relationship_refs(
        self,
        profile: RelationshipProfile,
        baseline: TurnContextBaseline,
        values: Sequence[ContinuityEvidenceRefValue],
    ) -> Tuple[ContinuityEvidenceRef, ...]:
        """Returns verified relationship authorities in caller-supplied order."""
        return self._resolve(
            profile,
            baseline,
            values,
            allowed_kinds=RELATIONSHIP_EVIDENCE_KINDS,
            field_name="relationship_context_refs",
        )

    def validate_binding(
        self,
        profile: RelationshipProfile,
        baseline: TurnContextBaseline,
        *,
        persona_refs: Sequence[ContinuityEvidenceRefValue],
        relationship_refs: Sequence[ContinuityEvidenceRefValue],
        voice_activation_traces: Sequence[VoiceActivationTrace] = (),
    ) -> None:
        """Re-resolves a portable binding before reviewed Turn completion."""
        resolved_persona = self.resolve_persona_refs(
            profile,
            baseline,
            persona_refs,
        )
        resolved_relationship = self.resolve_relationship_refs(
            profile,
            baseline,
            relationship_refs,
        )
        self._validate_voice_trace_sources(
            profile,
            baseline,
            (*resolved_persona, *resolved_relationship),
            voice_activation_traces,
        )

    def _validate_voice_trace_sources(
        self,
        profile: RelationshipProfile,
        baseline: TurnContextBaseline,
        evidence_refs: Sequence[ContinuityEvidenceRef],
        values: Sequence[VoiceActivationTrace],
    ) -> None:
        traces = tuple(
            item
            if isinstance(item, VoiceActivationTrace)
            else VoiceActivationTrace.from_dict(item)
            for item in values
        )
        if not traces:
            return
        try:
            turn = self._storage.get_turn_record(
                profile.relationship_id,
                baseline.turn_id,
            )
        except TurnNotFoundError as exc:
            raise ValueError("voice activation Trace parent Turn is dangling") from exc
        if turn.relationship_id != profile.relationship_id:
            raise ValueError("voice activation Trace parent belongs to another relationship")

        refs_by_id = {item.ref_id: item for item in evidence_refs}
        allowed_ref_ids = set(refs_by_id)
        manifest = self._active_manifest(profile, baseline)
        patterns_by_id = {
            item.pattern_id: item
            for item in manifest.candidate.contextual_voice_patterns
        }
        derived_signal = None
        for trace in traces:
            pattern_ref = refs_by_id.get(trace.pattern_ref_id)
            if (
                pattern_ref is None
                or pattern_ref.kind
                != ContinuityEvidenceKind.CONTEXTUAL_VOICE_PATTERN
            ):
                raise ValueError("voice activation Trace pattern evidence is dangling")
            pattern = patterns_by_id.get(str(pattern_ref.locator["pattern_id"]))
            if pattern is None or trace.pattern_scope != pattern.scope:
                raise ValueError("voice activation Trace pattern no longer matches")
            if len(trace.condition_matches) != len(pattern.conditions):
                raise ValueError("voice activation Trace omits pattern conditions")
            for match, condition in zip(
                trace.condition_matches,
                pattern.conditions,
            ):
                if (
                    match.condition_id != condition.condition_id
                    or match.signal_source != condition.signal_source
                    or match.signal_type != condition.condition_type.value
                    or match.matched_value not in condition.values
                ):
                    raise ValueError(
                        "voice activation Trace does not match its Manifest condition"
                    )
                if match.signal_source == ContextSignalSource.HOST_OBSERVED:
                    self._validate_host_observed_trace_match(
                        turn.interaction_context,
                        match,
                        allowed_ref_ids,
                    )
                elif match.signal_source == ContextSignalSource.CORE_DERIVED:
                    if derived_signal is None:
                        derived_signal = self._rebuild_relationship_safety_signal(
                            profile,
                            baseline,
                        )
                    self._validate_core_derived_trace_match(
                        derived_signal,
                        match,
                        allowed_ref_ids,
                        baseline,
                    )
                else:
                    self._validate_evaluator_inferred_trace_match(match)

    @staticmethod
    def _validate_host_observed_trace_match(
        signals,
        match,
        allowed_ref_ids: set[str],
    ) -> None:
        candidates = [
            item
            for item in signals
            if item.signal_id == match.signal_id
            and item.source == ContextSignalSource.HOST_OBSERVED
        ]
        if len(candidates) != 1:
            raise ValueError("host-observed voice Trace signal is dangling")
        signal = candidates[0]
        expected_refs = tuple(
            sorted(set(signal.evidence_refs).intersection(allowed_ref_ids))
        )
        if (
            signal.signal_type.casefold() != match.signal_type.casefold()
            or signal.value.casefold() != match.matched_value.casefold()
            or match.producer_version
            != (signal.producer_version or "host-observation-admission/v1")
            or match.evidence_ref_ids != expected_refs
            or match.source_context["observation_fingerprint"]
            != _canonical_hash(signal.to_dict())
        ):
            raise ValueError("host-observed voice Trace does not match its parent Turn")

    def _rebuild_relationship_safety_signal(
        self,
        profile: RelationshipProfile,
        baseline: TurnContextBaseline,
    ):
        from erii.core.continuity import RelationshipSafetySignalProjector
        from erii.core.relationship import RelationshipProjector
        from erii.core.turn_context import resolve_turn_context_history

        events = resolve_turn_context_history(
            self._storage,
            profile,
            baseline,
        )
        snapshot = RelationshipProjector.project(profile, events)
        return RelationshipSafetySignalProjector.project(
            snapshot,
            source_turn_id=baseline.turn_id,
            history_prefix_fingerprint=baseline.history_prefix_fingerprint,
        )

    @staticmethod
    def _validate_core_derived_trace_match(
        signal,
        match,
        allowed_ref_ids: set[str],
        baseline: TurnContextBaseline,
    ) -> None:
        expected_refs = tuple(
            sorted(set(signal.evidence_refs).intersection(allowed_ref_ids))
        )
        expected_context = getattr(signal, "_trace_context", None)
        if (
            signal.signal_id != match.signal_id
            or signal.signal_type != match.signal_type
            or signal.value != match.matched_value
            or signal.producer_version != match.producer_version
            or match.evidence_ref_ids != expected_refs
            or not isinstance(expected_context, Mapping)
            or dict(match.source_context) != dict(expected_context)
            or match.source_context["history_prefix_fingerprint"]
            != baseline.history_prefix_fingerprint
        ):
            raise ValueError(
                "core-derived voice Trace cannot be reproduced from frozen history"
            )

    @staticmethod
    def _validate_evaluator_inferred_trace_match(match) -> None:
        from erii.models.continuity import InteractionContextEvaluatorDescriptor

        descriptor = InteractionContextEvaluatorDescriptor.from_dict(
            match.source_context["evaluator_descriptor"]
        )
        if (
            match.signal_type != "emotion"
            or match.producer_version != descriptor.public_version
        ):
            raise ValueError(
                "evaluator-inferred voice Trace has inconsistent producer metadata"
            )

    def _resolve(
        self,
        profile: RelationshipProfile,
        baseline: TurnContextBaseline,
        values: Sequence[ContinuityEvidenceRefValue],
        *,
        allowed_kinds: frozenset[ContinuityEvidenceKind],
        field_name: str,
    ) -> Tuple[ContinuityEvidenceRef, ...]:
        if isinstance(values, (str, bytes)) or not isinstance(values, Sequence):
            raise ValueError(f"{field_name} must be a sequence of typed references")
        self._validate_review_scope(profile, baseline)
        refs = tuple(
            value
            if isinstance(value, ContinuityEvidenceRef)
            else ContinuityEvidenceRef.from_dict(value)
            for value in values
        )
        ref_ids = tuple(item.ref_id for item in refs)
        if len(ref_ids) != len(set(ref_ids)):
            raise ValueError(f"{field_name} must not contain duplicate references")
        for ref in refs:
            if ref.kind not in allowed_kinds:
                raise ValueError(
                    f"{ref.kind.value} is not valid in {field_name}"
                )
            self._resolve_one(profile, baseline, ref)
        return refs

    @staticmethod
    def _validate_review_scope(
        profile: RelationshipProfile,
        baseline: TurnContextBaseline,
    ) -> None:
        if (
            baseline.relationship_id != profile.relationship_id
            or baseline.persona_id != profile.persona_id
        ):
            raise ValueError(
                "continuity evidence baseline belongs to another Persona Instance"
            )

    def _resolve_one(
        self,
        profile: RelationshipProfile,
        baseline: TurnContextBaseline,
        ref: ContinuityEvidenceRef,
    ) -> None:
        kind = ref.kind
        locator = ref.locator
        if kind == ContinuityEvidenceKind.CHARACTER_BLUEPRINT:
            self._resolve_blueprint(profile, baseline, locator)
            return
        if kind in {
            ContinuityEvidenceKind.PERSONA_CLAIM,
            ContinuityEvidenceKind.FORMATIVE_EXPERIENCE,
            ContinuityEvidenceKind.MEANING_CAPSULE,
            ContinuityEvidenceKind.CONTEXTUAL_VOICE_PATTERN,
        }:
            self._resolve_manifest_item(profile, baseline, kind, locator)
            return
        if kind == ContinuityEvidenceKind.CHARACTER_SOURCE_SPAN:
            self._resolve_character_source_span(profile, baseline, locator)
            return
        if kind == ContinuityEvidenceKind.APPROVED_PERSONA_GROWTH:
            self._resolve_growth(profile, baseline, locator)
            return
        self._require_relationship_locator(profile, locator)
        if kind == ContinuityEvidenceKind.RELATIONSHIP_PREMISE:
            if (
                locator["premise_id"] != profile.premise.premise_id
                or locator["premise_id"] != baseline.premise.premise_id
                or locator["content_fingerprint"]
                != baseline.premise.content_fingerprint
                or locator["content_fingerprint"]
                != premise_content_fingerprint(profile.premise)
            ):
                raise ValueError("relationship premise evidence is dangling")
            return
        if kind == ContinuityEvidenceKind.PREMISE_EXPERIENCE:
            self._resolve_premise_experience(profile, baseline, locator)
            return
        if kind == ContinuityEvidenceKind.SOURCE_TURN:
            self._resolve_source_turn(profile, baseline, locator)
            return
        if kind == ContinuityEvidenceKind.RELATIONSHIP_EVENT:
            self._resolve_relationship_event(profile, baseline, locator)
            return
        if kind == ContinuityEvidenceKind.PERSONA_REFLECTION_RECORD:
            self._resolve_reflection(profile, baseline, locator)
            return
        if kind == ContinuityEvidenceKind.MEMORY_NODE:
            self._resolve_memory_node(profile, baseline, locator)
            return
        raise ValueError(f"unsupported continuity evidence kind: {kind.value}")

    @staticmethod
    def _resolve_blueprint(
        profile: RelationshipProfile,
        baseline: TurnContextBaseline,
        locator: Mapping[str, object],
    ) -> None:
        blueprint = profile.blueprint
        expected = (
            blueprint.blueprint_id,
            blueprint.revision,
            blueprint.source_sha256,
        )
        supplied = (
            locator["blueprint_id"],
            locator["revision"],
            locator["source_sha256"],
        )
        frozen = (
            baseline.blueprint.blueprint_id,
            baseline.blueprint.revision,
            baseline.blueprint.source_sha256,
        )
        if supplied != expected or supplied != frozen:
            raise ValueError("Character Blueprint evidence is dangling")

    def _active_manifest(
        self,
        profile: RelationshipProfile,
        baseline: TurnContextBaseline,
    ) -> PersonaManifest:
        if baseline.manifest is None or profile.manifest_id != baseline.manifest.manifest_id:
            raise ValueError("continuity evidence requires the frozen active Manifest")
        manifest = self._storage.get_persona_manifest(baseline.manifest.manifest_id)
        if (
            manifest is None
            or manifest.manifest_id != profile.manifest_id
            or manifest.blueprint_id != profile.blueprint.blueprint_id
            or manifest.blueprint_revision != profile.blueprint.revision
            or manifest.content_fingerprint != baseline.manifest.content_fingerprint
        ):
            raise ValueError("continuity evidence Manifest is dangling")
        return manifest

    def _resolve_character_source_span(
        self,
        profile: RelationshipProfile,
        baseline: TurnContextBaseline,
        locator: Mapping[str, object],
    ) -> None:
        self._resolve_blueprint(
            profile,
            baseline,
            {
                "blueprint_id": locator["blueprint_id"],
                "revision": locator["revision"],
                "source_sha256": locator["source_sha256"],
            },
        )
        manifest = self._active_manifest(profile, baseline)
        matches = [
            span
            for span in manifest.candidate.source_spans
            if span.start == locator["start"]
            and span.end == locator["end"]
            and hashlib.sha256(span.quote.encode("utf-8")).hexdigest()
            == locator["quote_sha256"]
        ]
        if len(matches) != 1:
            raise ValueError("Character source-span evidence is dangling or ambiguous")
        span = matches[0]
        if profile.blueprint.source_text[span.start : span.end] != span.quote:
            raise ValueError("Character source-span evidence no longer matches the Blueprint")

    def _resolve_manifest_item(
        self,
        profile: RelationshipProfile,
        baseline: TurnContextBaseline,
        kind: ContinuityEvidenceKind,
        locator: Mapping[str, object],
    ) -> None:
        manifest = self._active_manifest(profile, baseline)
        if (
            locator["manifest_id"] != manifest.manifest_id
            or locator["content_fingerprint"] != manifest.content_fingerprint
        ):
            raise ValueError("Persona evidence belongs to another Manifest")
        collection_name, identifier_name = {
            ContinuityEvidenceKind.PERSONA_CLAIM: ("claims", "claim_id"),
            ContinuityEvidenceKind.FORMATIVE_EXPERIENCE: (
                "formative_experiences",
                "experience_id",
            ),
            ContinuityEvidenceKind.MEANING_CAPSULE: (
                "meaning_capsules",
                "capsule_id",
            ),
            ContinuityEvidenceKind.CONTEXTUAL_VOICE_PATTERN: (
                "contextual_voice_patterns",
                "pattern_id",
            ),
        }[kind]
        identifier = locator[identifier_name]
        matches = [
            item
            for item in getattr(manifest.candidate, collection_name)
            if getattr(item, identifier_name) == identifier
        ]
        if len(matches) != 1:
            raise ValueError(f"{kind.value} evidence is dangling or ambiguous")
        item = matches[0]
        if (
            kind == ContinuityEvidenceKind.PERSONA_CLAIM
            and item.applicability != PersonaApplicability.APPLICABLE
        ):
            raise ValueError("inapplicable Persona Claim cannot authorize continuity")
        if (
            getattr(item, "scope", None) == PersonaScope.CANONICAL_RELATIONSHIP
            and profile.premise.mode
            != RelationshipPremiseMode.CANONICAL_CONTINUATION
        ):
            raise ValueError(
                "canonical-relationship Persona evidence is unavailable in this premise"
            )
        if (
            kind == ContinuityEvidenceKind.CONTEXTUAL_VOICE_PATTERN
            and item.scope == PersonaScope.CANONICAL_RELATIONSHIP
            and profile.premise.premise_id
            not in item.canonical_premise_template_ids
        ):
            raise ValueError(
                "Contextual Voice Pattern does not belong to the bound premise"
            )

    def _resolve_growth(
        self,
        profile: RelationshipProfile,
        baseline: TurnContextBaseline,
        locator: Mapping[str, object],
    ) -> None:
        self._require_relationship_locator(profile, locator)
        key = (locator["proposal_id"], locator["revision"])
        if key not in {
            (item.proposal_id, item.revision)
            for item in baseline.approved_growth_refs
        }:
            raise ValueError("Persona Growth evidence was not approved at Turn Opening")
        proposal = self._storage.get_persona_growth_proposal(
            profile.relationship_id,
            str(locator["proposal_id"]),
        )
        if (
            proposal is None
            or proposal.relationship_id != profile.relationship_id
            or proposal.revision != locator["revision"]
            or proposal.status != PersonaGrowthStatus.APPROVED
            or persona_growth_content_fingerprint(proposal)
            != locator["content_fingerprint"]
        ):
            raise ValueError("approved Persona Growth evidence is dangling")

    @staticmethod
    def _require_relationship_locator(
        profile: RelationshipProfile,
        locator: Mapping[str, object],
    ) -> None:
        if locator.get("relationship_id") != profile.relationship_id:
            raise ValueError("continuity evidence belongs to another relationship")

    @staticmethod
    def _resolve_premise_experience(
        profile: RelationshipProfile,
        baseline: TurnContextBaseline,
        locator: Mapping[str, object],
    ) -> None:
        if (
            locator["premise_id"] != profile.premise.premise_id
            or locator["premise_id"] != baseline.premise.premise_id
            or locator["content_fingerprint"]
            != baseline.premise.content_fingerprint
            or locator["content_fingerprint"]
            != premise_content_fingerprint(profile.premise)
            or not any(
                item.experience_id == locator["experience_id"]
                for item in profile.premise.experiences
            )
        ):
            raise ValueError("Premise Experience evidence is dangling")

    def _resolve_source_turn(
        self,
        profile: RelationshipProfile,
        baseline: TurnContextBaseline,
        locator: Mapping[str, object],
    ) -> None:
        if locator["turn_id"] == baseline.turn_id:
            raise ValueError("the open Turn cannot cite itself as Source Turn evidence")
        try:
            turn = self._storage.get_turn_record(
                profile.relationship_id,
                str(locator["turn_id"]),
            )
        except TurnNotFoundError as exc:
            raise ValueError("Source Turn evidence is dangling") from exc
        if (
            turn.relationship_id != profile.relationship_id
            or turn.status != TurnStatus.COMPLETED
            or turn.source_revision != locator["source_revision"]
        ):
            raise ValueError("Source Turn evidence is not an exact completed revision")

    def _direct_event_prefix(
        self,
        profile: RelationshipProfile,
        baseline: TurnContextBaseline,
    ):
        events = self._storage.list_relationship_events(profile.relationship_id)
        if len(events) < baseline.direct_event_count:
            raise ValueError("the frozen relationship-event prefix is no longer available")
        return tuple(events[: baseline.direct_event_count])

    def _resolve_relationship_event(
        self,
        profile: RelationshipProfile,
        baseline: TurnContextBaseline,
        locator: Mapping[str, object],
    ) -> None:
        if not any(
            item.event_id == locator["event_id"]
            and item.relationship_id == profile.relationship_id
            for item in self._direct_event_prefix(profile, baseline)
        ):
            raise ValueError(
                "Relationship Event evidence is dangling or was not visible at Turn Opening"
            )

    def _resolve_reflection(
        self,
        profile: RelationshipProfile,
        baseline: TurnContextBaseline,
        locator: Mapping[str, object],
    ) -> None:
        record = self._storage.get_persona_reflection_record(
            profile.relationship_id,
            str(locator["reflection_id"]),
        )
        event_ids = {item.event_id for item in self._direct_event_prefix(profile, baseline)}
        if (
            record is None
            or record.relationship_id != profile.relationship_id
            or record.record_kind == PersonaReflectionRecordKind.LEGACY
            or record.context_provenance.provenance_state
            != ReflectionProvenanceState.COMPLETE
            or record.event_id not in event_ids
            or record.content_fingerprint != locator["content_fingerprint"]
        ):
            raise ValueError(
                "Persona Reflection evidence is dangling, legacy, or outside the frozen history"
            )

    def _resolve_memory_node(
        self,
        profile: RelationshipProfile,
        baseline: TurnContextBaseline,
        locator: Mapping[str, object],
    ) -> None:
        matches = [
            item
            for item in self._storage.load_nodes(profile.agent_id, profile.user_id)
            if item.node_id == locator["node_id"]
        ]
        if len(matches) != 1:
            raise ValueError("MemoryNode evidence is dangling or ambiguous")
        node = matches[0]
        if (
            node.relationship_id != profile.relationship_id
            or node.provenance_state != ArtifactProvenanceState.COMPLETE
            or archival_artifact_fingerprint(node) != locator["artifact_fingerprint"]
        ):
            raise ValueError("MemoryNode evidence lacks exact relationship provenance")
        self._resolve_source_turn(
            profile,
            baseline,
            {
                "relationship_id": profile.relationship_id,
                "turn_id": node.source_turn_id,
                "source_revision": self._source_revision(
                    profile.relationship_id,
                    node.source_turn_id,
                ),
            },
        )
        try:
            archival = self._storage.get_archival_record(
                profile.relationship_id,
                node.source_archival_id,
            )
        except (ArchivalNotFoundError, KeyError, LookupError) as exc:
            raise ValueError("MemoryNode evidence archival is dangling") from exc
        receipt = archival.receipt
        artifact_fingerprint = archival_artifact_fingerprint(node)
        if (
            receipt.status != ArchivalStatus.COMPLETED
            or receipt.relationship_id != profile.relationship_id
            or receipt.source_turn_id != node.source_turn_id
            or not any(
                item.kind == ArchivalArtifactKind.MEMORY_NODE
                and item.artifact_id == node.node_id
                and item.artifact_fingerprint == artifact_fingerprint
                for item in receipt.artifacts
            )
        ):
            raise ValueError("MemoryNode evidence is not bound to its completed archival")

    def _source_revision(self, relationship_id: str, turn_id: str) -> str:
        try:
            return self._storage.get_turn_record(
                relationship_id,
                turn_id,
            ).source_revision
        except TurnNotFoundError as exc:
            raise ValueError("MemoryNode Source Turn is dangling") from exc


__all__ = ["ContinuityEvidenceRefValue", "ContinuityEvidenceResolver"]

"""Portable, non-replayable audit traces for contextual voice matches."""

from __future__ import annotations

from dataclasses import dataclass
import re
from types import MappingProxyType
from typing import Any, Dict, Mapping, Sequence, Tuple

from erii.models._wire_codec import canonical_wire_sha256
from erii.models.persona import PersonaScope
from erii.models.turn import ContextSignalSource


VOICE_ACTIVATION_TRACE_VERSION = "voice-activation-trace/v1"
_HEX_64 = re.compile(r"^[0-9a-f]{64}$")
_VERSION_PART = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/@+-]{0,255}$")


def _text(value: object, field_name: str, *, maximum: int = 256) -> str:
    if (
        not isinstance(value, str)
        or not value
        or value != value.strip()
        or len(value) > maximum
    ):
        raise ValueError(f"{field_name} must be a bounded canonical string")
    return value


def _digest(value: object, field_name: str) -> str:
    if not isinstance(value, str) or _HEX_64.fullmatch(value) is None:
        raise ValueError(f"{field_name} must be a lowercase SHA-256 digest")
    return value


def _exact_mapping(
    value: object,
    fields: frozenset[str],
    object_name: str,
) -> Mapping[str, object]:
    if not isinstance(value, Mapping) or set(value) != fields:
        raise ValueError(f"{object_name} contains unknown or missing fields")
    return value


def _normalize_source_context(
    source: ContextSignalSource,
    value: Mapping[str, object],
) -> Mapping[str, object]:
    if source == ContextSignalSource.HOST_OBSERVED:
        data = _exact_mapping(
            value,
            frozenset({"kind", "observation_fingerprint"}),
            "host-observed Trace context",
        )
        if data["kind"] != source.value:
            raise ValueError("Trace context kind does not match its signal source")
        return MappingProxyType(
            {
                "kind": source.value,
                "observation_fingerprint": _digest(
                    data["observation_fingerprint"],
                    "observation_fingerprint",
                ),
            }
        )
    if source == ContextSignalSource.CORE_DERIVED:
        data = _exact_mapping(
            value,
            frozenset(
                {
                    "kind",
                    "producer_input_fingerprint",
                    "history_prefix_fingerprint",
                    "relationship_projection_version",
                }
            ),
            "core-derived Trace context",
        )
        if data["kind"] != source.value:
            raise ValueError("Trace context kind does not match its signal source")
        projection_version = _text(
            data["relationship_projection_version"],
            "relationship_projection_version",
        )
        if _VERSION_PART.fullmatch(projection_version) is None:
            raise ValueError("relationship_projection_version is not a safe version")
        return MappingProxyType(
            {
                "kind": source.value,
                "producer_input_fingerprint": _digest(
                    data["producer_input_fingerprint"],
                    "producer_input_fingerprint",
                ),
                "history_prefix_fingerprint": _digest(
                    data["history_prefix_fingerprint"],
                    "history_prefix_fingerprint",
                ),
                "relationship_projection_version": projection_version,
            }
        )
    data = _exact_mapping(
        value,
        frozenset(
            {
                "kind",
                "candidate_key",
                "producer_input_fingerprint",
                "evaluator_descriptor",
            }
        ),
        "evaluator-inferred Trace context",
    )
    if data["kind"] != source.value:
        raise ValueError("Trace context kind does not match its signal source")
    descriptor = _exact_mapping(
        data["evaluator_descriptor"],
        frozenset(
            {"evaluator_id", "evaluator_version", "evaluation_schema_version"}
        ),
        "Trace evaluator descriptor",
    )
    normalized_descriptor = {}
    for field_name in (
        "evaluator_id",
        "evaluator_version",
        "evaluation_schema_version",
    ):
        part = _text(descriptor[field_name], field_name, maximum=128)
        if _VERSION_PART.fullmatch(part) is None:
            raise ValueError(f"{field_name} is not a safe version identifier")
        normalized_descriptor[field_name] = part
    return MappingProxyType(
        {
            "kind": source.value,
            "candidate_key": _text(data["candidate_key"], "candidate_key"),
            "producer_input_fingerprint": _digest(
                data["producer_input_fingerprint"],
                "producer_input_fingerprint",
            ),
            "evaluator_descriptor": MappingProxyType(normalized_descriptor),
        }
    )


@dataclass(frozen=True)
class VoiceConditionMatchTrace:
    """One bounded condition-to-signal match used by a final voice finding."""

    condition_id: str
    signal_source: ContextSignalSource
    signal_id: str
    signal_type: str
    matched_value: str
    producer_version: str
    evidence_ref_ids: Tuple[str, ...]
    source_context: Mapping[str, object]

    _FIELDS = frozenset(
        {
            "condition_id",
            "signal_source",
            "signal_id",
            "signal_type",
            "matched_value",
            "producer_version",
            "evidence_ref_ids",
            "source_context",
        }
    )

    def __post_init__(self) -> None:
        for field_name in (
            "condition_id",
            "signal_id",
            "signal_type",
            "matched_value",
            "producer_version",
        ):
            object.__setattr__(
                self,
                field_name,
                _text(getattr(self, field_name), field_name),
            )
        source = self.signal_source
        if not isinstance(source, ContextSignalSource):
            source = ContextSignalSource(source)
            object.__setattr__(self, "signal_source", source)
        if isinstance(self.evidence_ref_ids, (str, bytes)) or not isinstance(
            self.evidence_ref_ids,
            Sequence,
        ):
            raise ValueError("evidence_ref_ids must be a sequence")
        evidence = tuple(
            _digest(item, "evidence_ref_id") for item in self.evidence_ref_ids
        )
        if len(evidence) != len(set(evidence)) or evidence != tuple(sorted(evidence)):
            raise ValueError("evidence_ref_ids must be unique and sorted")
        object.__setattr__(self, "evidence_ref_ids", evidence)
        object.__setattr__(
            self,
            "source_context",
            _normalize_source_context(source, self.source_context),
        )

    def to_dict(self) -> Dict[str, Any]:
        context = dict(self.source_context)
        descriptor = context.get("evaluator_descriptor")
        if isinstance(descriptor, Mapping):
            context["evaluator_descriptor"] = dict(descriptor)
        return {
            "condition_id": self.condition_id,
            "signal_source": self.signal_source.value,
            "signal_id": self.signal_id,
            "signal_type": self.signal_type,
            "matched_value": self.matched_value,
            "producer_version": self.producer_version,
            "evidence_ref_ids": list(self.evidence_ref_ids),
            "source_context": context,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "VoiceConditionMatchTrace":
        _exact_mapping(data, cls._FIELDS, "VoiceConditionMatchTrace")
        if not isinstance(data["evidence_ref_ids"], list):
            raise ValueError("evidence_ref_ids must be an array")
        if not isinstance(data["source_context"], Mapping):
            raise ValueError("source_context must be an object")
        return cls(
            condition_id=data["condition_id"],
            signal_source=ContextSignalSource(data["signal_source"]),
            signal_id=data["signal_id"],
            signal_type=data["signal_type"],
            matched_value=data["matched_value"],
            producer_version=data["producer_version"],
            evidence_ref_ids=tuple(data["evidence_ref_ids"]),
            source_context=data["source_context"],
        )


def voice_activation_trace_fingerprint(
    *,
    activation_id: str,
    relationship_id: str,
    turn_id: str,
    persona_id: str,
    manifest_id: str,
    context_baseline_fingerprint: str,
    pattern_ref_id: str,
    pattern_scope: PersonaScope,
    matcher_version: str,
    matcher_input_fingerprint: str,
    condition_matches: Sequence[VoiceConditionMatchTrace],
) -> str:
    return canonical_wire_sha256(
        wire_type="VoiceActivationTrace",
        wire_version=VOICE_ACTIVATION_TRACE_VERSION,
        identity_payload={
            "activation_id": activation_id,
            "relationship_id": relationship_id,
            "turn_id": turn_id,
            "persona_id": persona_id,
            "manifest_id": manifest_id,
            "context_baseline_fingerprint": context_baseline_fingerprint,
            "pattern_ref_id": pattern_ref_id,
            "pattern_scope": pattern_scope.value,
            "matcher_version": matcher_version,
            "matcher_input_fingerprint": matcher_input_fingerprint,
            "condition_matches": [item.to_dict() for item in condition_matches],
        },
    )


@dataclass(frozen=True)
class VoiceActivationTrace:
    """Durable explanation that deliberately carries no runtime authority."""

    activation_id: str
    relationship_id: str
    turn_id: str
    persona_id: str
    manifest_id: str
    context_baseline_fingerprint: str
    pattern_ref_id: str
    pattern_scope: PersonaScope
    matcher_version: str
    matcher_input_fingerprint: str
    condition_matches: Tuple[VoiceConditionMatchTrace, ...]
    trace_fingerprint: str
    trace_version: str = VOICE_ACTIVATION_TRACE_VERSION

    _FIELDS = frozenset(
        {
            "trace_version",
            "activation_id",
            "relationship_id",
            "turn_id",
            "persona_id",
            "manifest_id",
            "context_baseline_fingerprint",
            "pattern_ref_id",
            "pattern_scope",
            "matcher_version",
            "matcher_input_fingerprint",
            "condition_matches",
            "trace_fingerprint",
        }
    )

    def __post_init__(self) -> None:
        if self.trace_version != VOICE_ACTIVATION_TRACE_VERSION:
            raise ValueError("unsupported VoiceActivationTrace version")
        for field_name in (
            "activation_id",
            "relationship_id",
            "turn_id",
            "persona_id",
            "manifest_id",
            "matcher_version",
        ):
            object.__setattr__(
                self,
                field_name,
                _text(getattr(self, field_name), field_name),
            )
        for field_name in (
            "context_baseline_fingerprint",
            "pattern_ref_id",
            "matcher_input_fingerprint",
            "trace_fingerprint",
        ):
            object.__setattr__(
                self,
                field_name,
                _digest(getattr(self, field_name), field_name),
            )
        scope = self.pattern_scope
        if not isinstance(scope, PersonaScope):
            scope = PersonaScope(scope)
            object.__setattr__(self, "pattern_scope", scope)
        matches = tuple(
            item
            if isinstance(item, VoiceConditionMatchTrace)
            else VoiceConditionMatchTrace.from_dict(item)
            for item in self.condition_matches
        )
        if not matches:
            raise ValueError("VoiceActivationTrace requires condition matches")
        condition_ids = tuple(item.condition_id for item in matches)
        if len(condition_ids) != len(set(condition_ids)):
            raise ValueError("VoiceActivationTrace condition IDs must be unique")
        object.__setattr__(self, "condition_matches", matches)
        expected = voice_activation_trace_fingerprint(
            activation_id=self.activation_id,
            relationship_id=self.relationship_id,
            turn_id=self.turn_id,
            persona_id=self.persona_id,
            manifest_id=self.manifest_id,
            context_baseline_fingerprint=self.context_baseline_fingerprint,
            pattern_ref_id=self.pattern_ref_id,
            pattern_scope=scope,
            matcher_version=self.matcher_version,
            matcher_input_fingerprint=self.matcher_input_fingerprint,
            condition_matches=matches,
        )
        if self.trace_fingerprint != expected:
            raise ValueError("trace_fingerprint does not match VoiceActivationTrace")

    @classmethod
    def create(
        cls,
        *,
        activation_id: str,
        relationship_id: str,
        turn_id: str,
        persona_id: str,
        manifest_id: str,
        context_baseline_fingerprint: str,
        pattern_ref_id: str,
        pattern_scope: PersonaScope,
        matcher_version: str,
        matcher_input_fingerprint: str,
        condition_matches: Sequence[VoiceConditionMatchTrace],
    ) -> "VoiceActivationTrace":
        matches = tuple(condition_matches)
        scope = (
            pattern_scope
            if isinstance(pattern_scope, PersonaScope)
            else PersonaScope(pattern_scope)
        )
        return cls(
            activation_id=activation_id,
            relationship_id=relationship_id,
            turn_id=turn_id,
            persona_id=persona_id,
            manifest_id=manifest_id,
            context_baseline_fingerprint=context_baseline_fingerprint,
            pattern_ref_id=pattern_ref_id,
            pattern_scope=scope,
            matcher_version=matcher_version,
            matcher_input_fingerprint=matcher_input_fingerprint,
            condition_matches=matches,
            trace_fingerprint=voice_activation_trace_fingerprint(
                activation_id=activation_id,
                relationship_id=relationship_id,
                turn_id=turn_id,
                persona_id=persona_id,
                manifest_id=manifest_id,
                context_baseline_fingerprint=context_baseline_fingerprint,
                pattern_ref_id=pattern_ref_id,
                pattern_scope=scope,
                matcher_version=matcher_version,
                matcher_input_fingerprint=matcher_input_fingerprint,
                condition_matches=matches,
            ),
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "trace_version": self.trace_version,
            "activation_id": self.activation_id,
            "relationship_id": self.relationship_id,
            "turn_id": self.turn_id,
            "persona_id": self.persona_id,
            "manifest_id": self.manifest_id,
            "context_baseline_fingerprint": self.context_baseline_fingerprint,
            "pattern_ref_id": self.pattern_ref_id,
            "pattern_scope": self.pattern_scope.value,
            "matcher_version": self.matcher_version,
            "matcher_input_fingerprint": self.matcher_input_fingerprint,
            "condition_matches": [item.to_dict() for item in self.condition_matches],
            "trace_fingerprint": self.trace_fingerprint,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "VoiceActivationTrace":
        _exact_mapping(data, cls._FIELDS, "VoiceActivationTrace")
        if not isinstance(data["condition_matches"], list):
            raise ValueError("condition_matches must be an array")
        return cls(
            trace_version=data["trace_version"],
            activation_id=data["activation_id"],
            relationship_id=data["relationship_id"],
            turn_id=data["turn_id"],
            persona_id=data["persona_id"],
            manifest_id=data["manifest_id"],
            context_baseline_fingerprint=data["context_baseline_fingerprint"],
            pattern_ref_id=data["pattern_ref_id"],
            pattern_scope=PersonaScope(data["pattern_scope"]),
            matcher_version=data["matcher_version"],
            matcher_input_fingerprint=data["matcher_input_fingerprint"],
            condition_matches=tuple(
                VoiceConditionMatchTrace.from_dict(item)
                for item in data["condition_matches"]
            ),
            trace_fingerprint=data["trace_fingerprint"],
        )


__all__ = [
    "VOICE_ACTIVATION_TRACE_VERSION",
    "VoiceActivationTrace",
    "VoiceConditionMatchTrace",
    "voice_activation_trace_fingerprint",
]

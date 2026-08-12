"""Typed, content-minimizing contracts for the removable CD-1 Shadow harness."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from erii.deliberation.core_validator import (
    ResultBinding,
    TrustedAuthoritySecret,
)
from erii.deliberation.host_bridge import (
    fingerprint_evidence_view,
    fingerprint_user_envelope,
)
from erii.deliberation.identifiers import validate_identifier
from erii.deliberation.schemas import (
    CompactDecisionV1,
    DeliberationPlanV1,
    EvidenceViewV1,
    ReplyRealizationV1,
    UserMessageEnvelope,
    VisibleReplyEnvelopeV1,
)
from erii.deliberation.strict_codec import StrictCanonicalCodec
from erii.models.turn import TurnRecord, TurnStatus

from .errors import ShadowFailureCode


ConfigurationLabel = Literal["D0", "D1", "D2", "D3", "D4"]
RouteTaken = Literal["direct", "compact", "staged", "equal_compute_direct"]
ComparisonTarget = Literal["D1", "D2", "D3"]


def _require_sha256(value: object, field_name: str) -> str:
    if type(value) is not str or len(value) != 64:
        raise ValueError(f"{field_name} must be a lowercase SHA-256 fingerprint")
    if any(character not in "0123456789abcdef" for character in value):
        raise ValueError(f"{field_name} must be a lowercase SHA-256 fingerprint")
    return value


def _require_non_negative_int(value: object, field_name: str) -> int:
    if type(value) is not int or value < 0:
        raise ValueError(f"{field_name} must be a non-negative integer")
    return value


@dataclass(frozen=True)
class ScenarioIdentityV1:
    """Exact scenario, relationship, and frozen-input identity."""

    scenario_id: str
    agent_id: str
    user_id: str
    relationship_id: str
    turn_ordinal: int
    baseline_fingerprint: str
    user_message_fingerprint: str
    evidence_view_fingerprint: str
    schema_version: Literal["erii-shadow-scenario-identity/v1"] = (
        "erii-shadow-scenario-identity/v1"
    )

    def __post_init__(self) -> None:
        for name in ("scenario_id", "agent_id", "user_id", "relationship_id"):
            validate_identifier(getattr(self, name), name)
        _require_non_negative_int(self.turn_ordinal, "turn_ordinal")
        for name in (
            "baseline_fingerprint",
            "user_message_fingerprint",
            "evidence_view_fingerprint",
        ):
            _require_sha256(getattr(self, name), name)


@dataclass(frozen=True)
class RunConfigurationV1:
    """Exact D0-D4 fixture and routing descriptor."""

    config_label: ConfigurationLabel
    provider_kind: str
    model_id: str
    adapter_version: str
    router_policy: str | None
    temperature: float | None
    max_tokens: int
    seed: int
    capability_fingerprint: str
    call_budget: int = 1
    comparison_target: ComparisonTarget | None = None
    schema_version: Literal["erii-shadow-run-configuration/v1"] = (
        "erii-shadow-run-configuration/v1"
    )

    def __post_init__(self) -> None:
        if self.config_label not in {"D0", "D1", "D2", "D3", "D4"}:
            raise ValueError("config_label must be one of D0-D4")
        for name in ("provider_kind", "model_id", "adapter_version"):
            validate_identifier(getattr(self, name), name)
        if self.router_policy is not None:
            validate_identifier(self.router_policy, "router_policy")
        if self.temperature is not None and (
            type(self.temperature) is not float or not 0.0 <= self.temperature <= 2.0
        ):
            raise ValueError("temperature must be a float in [0.0, 2.0]")
        if type(self.max_tokens) is not int or self.max_tokens <= 0:
            raise ValueError("max_tokens must be a positive integer")
        if type(self.seed) is not int:
            raise ValueError("seed must be an integer")
        _require_sha256(self.capability_fingerprint, "capability_fingerprint")
        if type(self.call_budget) is not int or self.call_budget <= 0:
            raise ValueError("call_budget must be a positive integer")
        if self.config_label == "D4":
            if self.comparison_target not in {"D1", "D2", "D3"}:
                raise ValueError("D4 requires a D1-D3 comparison_target")
        elif self.comparison_target is not None:
            raise ValueError("comparison_target is only valid for D4")


@dataclass(frozen=True)
class ShadowEvaluationInputV1:
    """One host-validated, exact-input-bound offline Shadow run."""

    scenario: ScenarioIdentityV1
    config: RunConfigurationV1
    sample_index: int
    frozen_turn: TurnRecord = field(repr=False)
    user_envelope: UserMessageEnvelope = field(repr=False)
    evidence_view: EvidenceViewV1 = field(repr=False)
    schema_version: Literal["erii-shadow-evaluation-input/v1"] = (
        "erii-shadow-evaluation-input/v1"
    )

    def __post_init__(self) -> None:
        _require_non_negative_int(self.sample_index, "sample_index")
        turn = self.frozen_turn
        baseline = turn.context_baseline
        if turn.status is not TurnStatus.OPEN or baseline is None:
            raise ValueError("shadow evaluation requires an open Turn with a baseline")
        if turn.relationship_id != self.scenario.relationship_id:
            raise ValueError("frozen_turn relationship_id does not match scenario")
        if baseline.relationship_id != turn.relationship_id:
            raise ValueError("frozen baseline relationship_id does not match turn")
        if baseline.turn_id != turn.turn_id:
            raise ValueError("frozen baseline turn_id does not match turn")
        if baseline.persona_id != self.scenario.agent_id:
            raise ValueError("frozen_turn persona_id does not match scenario agent_id")
        if baseline.baseline_fingerprint != self.scenario.baseline_fingerprint:
            raise ValueError("baseline fingerprint does not match scenario")
        if self.evidence_view.relationship_id != self.scenario.relationship_id:
            raise ValueError("evidence_view relationship_id does not match scenario")
        if self.evidence_view.turn_id != turn.turn_id:
            raise ValueError("evidence_view turn_id does not match frozen_turn")
        user_fingerprint = fingerprint_user_envelope(self.user_envelope)
        if self.user_envelope.canonical_fingerprint != user_fingerprint:
            raise ValueError("user envelope self-fingerprint is invalid")
        if self.scenario.user_message_fingerprint != user_fingerprint:
            raise ValueError("user message fingerprint does not match scenario")
        evidence_fingerprint = fingerprint_evidence_view(self.evidence_view)
        if self.evidence_view.view_fingerprint != evidence_fingerprint:
            raise ValueError("evidence view self-fingerprint is invalid")
        if self.scenario.evidence_view_fingerprint != evidence_fingerprint:
            raise ValueError("evidence view fingerprint does not match scenario")
        if len(self.user_envelope.parts) != 1:
            raise ValueError("CD-1 fixture requires one exact user message part")
        user_part = self.user_envelope.parts[0]
        source_message = turn.transcript.user_message
        if (
            user_part.part_id != source_message.message_id
            or user_part.exact_utf8 != source_message.content
        ):
            raise ValueError("user envelope does not match the frozen Turn transcript")


@dataclass(frozen=True)
class ShadowRunBindingV1:
    """Content-free signature over one exact Shadow input, route, and reply."""

    relationship_id: str
    turn_id: str
    scenario_id: str
    config_label: ConfigurationLabel
    sample_index: int
    route_taken: RouteTaken
    input_fingerprint: str
    plan_fingerprint: str | None
    reply_fingerprint: str
    result_fingerprint: str
    hmac_signature: str = field(repr=False)
    binding_version: Literal["erii-shadow-run-binding/v1"] = (
        "erii-shadow-run-binding/v1"
    )

    def __post_init__(self) -> None:
        for name in ("relationship_id", "turn_id", "scenario_id"):
            validate_identifier(getattr(self, name), name)
        _require_non_negative_int(self.sample_index, "sample_index")
        for name in (
            "input_fingerprint",
            "reply_fingerprint",
            "result_fingerprint",
            "hmac_signature",
        ):
            _require_sha256(getattr(self, name), name)
        if self.plan_fingerprint is not None:
            _require_sha256(self.plan_fingerprint, "plan_fingerprint")
        if self.config_label not in {"D0", "D1", "D2", "D3", "D4"}:
            raise ValueError("config_label must be one of D0-D4")
        if self.route_taken not in {
            "direct",
            "compact",
            "staged",
            "equal_compute_direct",
        }:
            raise ValueError("unsupported Shadow route")

    @staticmethod
    def compute_message(
        *,
        relationship_id: str,
        turn_id: str,
        scenario_id: str,
        config_label: ConfigurationLabel,
        sample_index: int,
        route_taken: RouteTaken,
        input_fingerprint: str,
        plan_fingerprint: str | None,
        reply_fingerprint: str,
        result_fingerprint: str,
    ) -> str:
        payload = {
            "binding_version": "erii-shadow-run-binding/v1",
            "relationship_id": relationship_id,
            "turn_id": turn_id,
            "scenario_id": scenario_id,
            "config_label": config_label,
            "sample_index": sample_index,
            "route_taken": route_taken,
            "input_fingerprint": input_fingerprint,
            "plan_fingerprint": plan_fingerprint,
            "reply_fingerprint": reply_fingerprint,
            "result_fingerprint": result_fingerprint,
        }
        return StrictCanonicalCodec.serialize(payload)

    def verify_with_secret(self, secret: TrustedAuthoritySecret) -> bool:
        message = self.compute_message(
            relationship_id=self.relationship_id,
            turn_id=self.turn_id,
            scenario_id=self.scenario_id,
            config_label=self.config_label,
            sample_index=self.sample_index,
            route_taken=self.route_taken,
            input_fingerprint=self.input_fingerprint,
            plan_fingerprint=self.plan_fingerprint,
            reply_fingerprint=self.reply_fingerprint,
            result_fingerprint=self.result_fingerprint,
        )
        return secret.verify(message, self.hmac_signature)


@dataclass(frozen=True)
class ShadowEvaluationOutputV1:
    """One immutable operator result; generated content is hidden from repr."""

    scenario_id: str
    config_label: ConfigurationLabel
    sample_index: int
    transport_completed: bool
    schema_valid: bool
    scope_and_binding_valid: bool
    route_taken: RouteTaken | None = None
    expected_semantic_axes_match: bool | None = None
    human_judgment: Literal["not_run", "pending", "recorded"] = "not_run"
    decision: CompactDecisionV1 | None = field(default=None, repr=False)
    plan: DeliberationPlanV1 | None = field(default=None, repr=False)
    realization: ReplyRealizationV1 | None = field(default=None, repr=False)
    reply_envelope: VisibleReplyEnvelopeV1 | None = field(default=None, repr=False)
    core_result_binding: ResultBinding | None = field(default=None, repr=False)
    shadow_binding: ShadowRunBindingV1 | None = field(default=None, repr=False)
    attempt_count: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cost_units: int = 0
    latency_ms: int = 0
    fallback_used: bool = False
    escalation_occurred: bool = False
    failure_code: ShadowFailureCode | None = None
    failure_stage: str | None = None
    schema_version: Literal["erii-shadow-evaluation-output/v1"] = (
        "erii-shadow-evaluation-output/v1"
    )

    def __post_init__(self) -> None:
        validate_identifier(self.scenario_id, "scenario_id")
        if self.config_label not in {"D0", "D1", "D2", "D3", "D4"}:
            raise ValueError("config_label must be one of D0-D4")
        for name in (
            "sample_index",
            "attempt_count",
            "input_tokens",
            "output_tokens",
            "cost_units",
            "latency_ms",
        ):
            _require_non_negative_int(getattr(self, name), name)
        if self.schema_valid and not self.transport_completed:
            raise ValueError("schema_valid requires completed transport")
        if self.scope_and_binding_valid and not self.schema_valid:
            raise ValueError("scope_and_binding_valid requires valid schema")
        if self.transport_completed and self.route_taken is None:
            raise ValueError("completed transport requires route_taken")
        if self.scope_and_binding_valid and self.shadow_binding is None:
            raise ValueError("a valid Shadow result requires an exact Shadow binding")
        if self.scope_and_binding_valid and self.reply_envelope is None:
            raise ValueError("a valid Shadow result requires a visible reply")
        if self.shadow_binding is not None:
            if (
                self.shadow_binding.scenario_id != self.scenario_id
                or self.shadow_binding.config_label != self.config_label
                or self.shadow_binding.sample_index != self.sample_index
                or self.shadow_binding.route_taken != self.route_taken
            ):
                raise ValueError("Shadow binding does not match output identity")
        if self.plan is None and self.realization is not None:
            raise ValueError("realization requires a staged plan")
        if self.plan is not None:
            if self.realization is None:
                raise ValueError("staged plan requires a realization")
            if self.plan.plan_fingerprint != self.realization.plan_fingerprint:
                raise ValueError("staged plan and realization fingerprints differ")
        if self.human_judgment != "recorded" and self.expected_semantic_axes_match is not None:
            raise ValueError("semantic match cannot be asserted without recorded judgment")
        if self.failure_stage is not None:
            validate_identifier(self.failure_stage, "failure_stage")


@dataclass(frozen=True)
class BlindedJudgeInputV1:
    """Configuration-blind human evaluation artifact."""

    case_id: str
    candidate_id: str
    agent_blueprint_excerpt: str = field(repr=False)
    relationship_stage_summary: str = field(repr=False)
    user_message_parts: tuple[str, ...] = field(repr=False)
    reply_parts: tuple[str, ...] = field(repr=False)
    schema_version: Literal["erii-shadow-blinded-judge-input/v1"] = (
        "erii-shadow-blinded-judge-input/v1"
    )

    def __post_init__(self) -> None:
        validate_identifier(self.case_id, "case_id")
        validate_identifier(self.candidate_id, "candidate_id")
        if not self.agent_blueprint_excerpt:
            raise ValueError("agent_blueprint_excerpt must not be empty")
        if not self.relationship_stage_summary:
            raise ValueError("relationship_stage_summary must not be empty")
        if not self.user_message_parts or not all(self.user_message_parts):
            raise ValueError("user_message_parts must contain exact non-empty parts")
        if not self.reply_parts or not all(self.reply_parts):
            raise ValueError("reply_parts must contain exact non-empty parts")


__all__ = [
    "ConfigurationLabel",
    "RouteTaken",
    "ComparisonTarget",
    "ScenarioIdentityV1",
    "RunConfigurationV1",
    "ShadowEvaluationInputV1",
    "ShadowRunBindingV1",
    "ShadowEvaluationOutputV1",
    "BlindedJudgeInputV1",
]

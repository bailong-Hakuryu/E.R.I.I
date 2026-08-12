"""Execute exact-input-bound, process-local CD-1 Shadow evaluations."""

from __future__ import annotations

from dataclasses import asdict
from time import perf_counter_ns

from erii.deliberation.core_validator import (
    AuthorityState,
    CoreTrustedValidator,
    TrustedAuthoritySecret,
)
from erii.deliberation.schemas import (
    CompactDecisionV1,
    RouterSignal,
)
from erii.deliberation.security import run_security_scan
from erii.deliberation.strict_codec import StrictCanonicalCodec

from .contracts import (
    RouteTaken,
    ShadowEvaluationInputV1,
    ShadowEvaluationOutputV1,
    ShadowRunBindingV1,
)
from .errors import ShadowFailureCode
from .fake_actors import DeterministicShadowActor, ShadowActorExecution


class ShadowEvaluationRunner:
    """Run and revalidate one removable offline Shadow configuration."""

    def __init__(
        self,
        secret: TrustedAuthoritySecret,
        *,
        actor: DeterministicShadowActor | None = None,
    ) -> None:
        self._secret = secret
        self._validator = CoreTrustedValidator(secret)
        self._actor = actor or DeterministicShadowActor()

    def run_single(
        self,
        shadow_input: ShadowEvaluationInputV1,
    ) -> ShadowEvaluationOutputV1:
        """Execute one scenario/config/sample without delivery or persistence."""
        started_ns = perf_counter_ns()
        try:
            execution = self._actor.execute(shadow_input)
        except Exception:
            return self._failure(
                shadow_input,
                started_ns=started_ns,
                code=ShadowFailureCode.TRANSPORT_PROVIDER_ERROR,
                stage="transport",
            )

        route = self._validated_route(shadow_input, execution)
        if not execution.success or route is None:
            return self._failure(
                shadow_input,
                started_ns=started_ns,
                code=ShadowFailureCode.CONFIGURATION_INVALID,
                stage="transport",
                execution=execution,
                route=execution.route_taken,
            )

        validation_decision = self._validation_decision(execution)
        if not self._execution_shape_is_valid(shadow_input, execution, validation_decision):
            return self._failure(
                shadow_input,
                started_ns=started_ns,
                code=ShadowFailureCode.SCHEMA_INVALID_STRUCTURE,
                stage="schema",
                execution=execution,
                route=route,
                transport_completed=True,
            )

        if validation_decision is not None:
            scan = run_security_scan(validation_decision, shadow_input.evidence_view)
            if not scan.passed:
                code = ShadowFailureCode.BINDING_MISMATCH
                if scan.canary_leaked:
                    code = ShadowFailureCode.CANARY_LEAK_DETECTED
                elif scan.invalid_evidence_refs:
                    code = ShadowFailureCode.EVIDENCE_SCOPE_VIOLATION
                return self._failure(
                    shadow_input,
                    started_ns=started_ns,
                    code=code,
                    stage="scope-binding",
                    execution=execution,
                    route=route,
                    transport_completed=True,
                    schema_valid=True,
                )

        try:
            core_binding = self._create_core_binding(shadow_input, validation_decision)
            shadow_binding = self._create_shadow_binding(
                shadow_input,
                execution,
                route=route,
                core_result_fingerprint=(
                    core_binding.result_fingerprint if core_binding is not None else None
                ),
            )
        except Exception:
            return self._failure(
                shadow_input,
                started_ns=started_ns,
                code=ShadowFailureCode.BINDING_MISMATCH,
                stage="scope-binding",
                execution=execution,
                route=route,
                transport_completed=True,
                schema_valid=True,
            )

        usage = execution.usage
        output = ShadowEvaluationOutputV1(
            scenario_id=shadow_input.scenario.scenario_id,
            config_label=shadow_input.config.config_label,
            sample_index=shadow_input.sample_index,
            route_taken=route,
            transport_completed=True,
            schema_valid=True,
            scope_and_binding_valid=True,
            decision=execution.compact_decision,
            plan=execution.plan,
            realization=execution.realization,
            reply_envelope=execution.reply_envelope,
            core_result_binding=core_binding,
            shadow_binding=shadow_binding,
            attempt_count=execution.attempt_count,
            input_tokens=usage.input_tokens if usage is not None else 0,
            output_tokens=usage.output_tokens if usage is not None else 0,
            cost_units=(
                usage.input_tokens + usage.output_tokens if usage is not None else 0
            ),
            latency_ms=self._elapsed_ms(started_ns),
            escalation_occurred=execution.escalation_occurred,
        )
        valid, _errors = self.verify_output(shadow_input, output)
        if not valid:
            return self._failure(
                shadow_input,
                started_ns=started_ns,
                code=ShadowFailureCode.BINDING_MISMATCH,
                stage="scope-binding",
                execution=execution,
                route=route,
                transport_completed=True,
                schema_valid=True,
            )
        return output

    def verify_output(
        self,
        shadow_input: ShadowEvaluationInputV1,
        output: ShadowEvaluationOutputV1,
    ) -> tuple[bool, tuple[str, ...]]:
        """Recompute all bindings for one returned Shadow output."""
        errors: list[str] = []
        if (
            output.scenario_id != shadow_input.scenario.scenario_id
            or output.config_label != shadow_input.config.config_label
            or output.sample_index != shadow_input.sample_index
        ):
            errors.append("output identity does not match Shadow input")
        if not output.scope_and_binding_valid:
            errors.append("output is not marked scope-and-binding valid")
        if output.reply_envelope is None or output.shadow_binding is None:
            errors.append("output is missing exact Shadow artifacts")
            return False, tuple(errors)
        if output.decision is not None and output.reply_envelope != output.decision.reply_candidate:
            errors.append("reply envelope does not match compact decision")
        if output.realization is not None and output.reply_envelope != output.realization.reply_candidate:
            errors.append("reply envelope does not match staged realization")

        execution = ShadowActorExecution(
            success=True,
            route_taken=output.route_taken or "direct",
            reply_envelope=output.reply_envelope,
            compact_decision=output.decision,
            plan=output.plan,
            realization=output.realization,
            attempt_count=output.attempt_count,
            escalation_occurred=output.escalation_occurred,
        )
        validation_decision = self._validation_decision(execution)
        expected_core_binding = None
        if validation_decision is not None:
            try:
                expected_core_binding = self._create_core_binding(
                    shadow_input,
                    validation_decision,
                )
            except Exception:
                errors.append("core result binding cannot be recomputed")
            if expected_core_binding != output.core_result_binding:
                errors.append("core result binding does not match exact output")
        elif output.core_result_binding is not None:
            errors.append("direct output must not carry a core result binding")

        try:
            expected_shadow_binding = self._create_shadow_binding(
                shadow_input,
                execution,
                route=execution.route_taken,
                core_result_fingerprint=(
                    expected_core_binding.result_fingerprint
                    if expected_core_binding is not None
                    else None
                ),
            )
        except Exception:
            errors.append("Shadow binding cannot be recomputed")
        else:
            if expected_shadow_binding != output.shadow_binding:
                errors.append("Shadow binding does not match exact output")
            if not output.shadow_binding.verify_with_secret(self._secret):
                errors.append("Shadow binding HMAC is invalid")
        return not errors, tuple(errors)

    def _create_core_binding(
        self,
        shadow_input: ShadowEvaluationInputV1,
        decision: CompactDecisionV1 | None,
    ):
        if decision is None:
            return None
        turn = shadow_input.frozen_turn
        baseline = turn.context_baseline
        if baseline is None:
            raise ValueError("Shadow input lost its frozen baseline")
        authority = AuthorityState(
            current_epoch=shadow_input.sample_index,
            turn_status=turn.status,
            active_relationship_id=turn.relationship_id,
            active_turn_id=turn.turn_id,
            active_persona_id=baseline.persona_id,
        )
        envelope = self._validator.create_envelope(
            relationship_id=turn.relationship_id,
            turn_id=turn.turn_id,
            persona_id=baseline.persona_id,
            evidence_view_fingerprint=shadow_input.scenario.evidence_view_fingerprint,
            user_message_fingerprint=shadow_input.scenario.user_message_fingerprint,
            run_epoch=shadow_input.sample_index,
            expected_turn_state=turn.status,
        )
        binding = self._validator.create_result_binding(
            envelope,
            decision,
            decision.reply_candidate,
            authority,
        )
        valid, _errors = self._validator.verify_result_binding(
            binding,
            envelope,
            decision,
            decision.reply_candidate,
            authority,
        )
        if not valid:
            raise ValueError("Core result binding verification failed")
        return binding

    def _create_shadow_binding(
        self,
        shadow_input: ShadowEvaluationInputV1,
        execution: ShadowActorExecution,
        *,
        route: RouteTaken,
        core_result_fingerprint: str | None,
    ) -> ShadowRunBindingV1:
        if execution.reply_envelope is None:
            raise ValueError("Shadow execution has no reply")
        input_fingerprint = self._input_fingerprint(shadow_input)
        reply_fingerprint = StrictCanonicalCodec.fingerprint(
            execution.reply_envelope.model_dump(mode="json"),
            domain="erii-shadow-visible-reply/v1",
        )
        plan_fingerprint = (
            execution.plan.plan_fingerprint if execution.plan is not None else None
        )
        result_payload = {
            "input_fingerprint": input_fingerprint,
            "route_taken": route,
            "compact_decision": (
                execution.compact_decision.model_dump(mode="json")
                if execution.compact_decision is not None
                else None
            ),
            "plan": (
                execution.plan.model_dump(mode="json")
                if execution.plan is not None
                else None
            ),
            "realization": (
                execution.realization.model_dump(mode="json")
                if execution.realization is not None
                else None
            ),
            "reply_fingerprint": reply_fingerprint,
            "core_result_fingerprint": core_result_fingerprint,
        }
        result_fingerprint = StrictCanonicalCodec.fingerprint(
            result_payload,
            domain="erii-shadow-result/v1",
        )
        message = ShadowRunBindingV1.compute_message(
            relationship_id=shadow_input.scenario.relationship_id,
            turn_id=shadow_input.frozen_turn.turn_id,
            scenario_id=shadow_input.scenario.scenario_id,
            config_label=shadow_input.config.config_label,
            sample_index=shadow_input.sample_index,
            route_taken=route,
            input_fingerprint=input_fingerprint,
            plan_fingerprint=plan_fingerprint,
            reply_fingerprint=reply_fingerprint,
            result_fingerprint=result_fingerprint,
        )
        return ShadowRunBindingV1(
            relationship_id=shadow_input.scenario.relationship_id,
            turn_id=shadow_input.frozen_turn.turn_id,
            scenario_id=shadow_input.scenario.scenario_id,
            config_label=shadow_input.config.config_label,
            sample_index=shadow_input.sample_index,
            route_taken=route,
            input_fingerprint=input_fingerprint,
            plan_fingerprint=plan_fingerprint,
            reply_fingerprint=reply_fingerprint,
            result_fingerprint=result_fingerprint,
            hmac_signature=self._secret.sign(message),
        )

    @staticmethod
    def _input_fingerprint(shadow_input: ShadowEvaluationInputV1) -> str:
        turn = shadow_input.frozen_turn
        baseline = turn.context_baseline
        if baseline is None:
            raise ValueError("Shadow input has no frozen baseline")
        payload = {
            "scenario": asdict(shadow_input.scenario),
            "config": asdict(shadow_input.config),
            "sample_index": shadow_input.sample_index,
            "turn_id": turn.turn_id,
            "relationship_id": turn.relationship_id,
            "source_revision": turn.source_revision,
            "record_version": turn.record_version,
            "baseline_fingerprint": baseline.baseline_fingerprint,
            "user_message_fingerprint": shadow_input.user_envelope.canonical_fingerprint,
            "evidence_view_fingerprint": shadow_input.evidence_view.view_fingerprint,
        }
        return StrictCanonicalCodec.fingerprint(
            payload,
            domain="erii-shadow-input/v1",
        )

    @staticmethod
    def _validation_decision(
        execution: ShadowActorExecution,
    ) -> CompactDecisionV1 | None:
        if execution.compact_decision is not None:
            return execution.compact_decision
        if execution.plan is not None and execution.realization is not None:
            return CompactDecisionV1(
                result_kind=execution.plan.frame.result_kind,
                frame=execution.plan.frame,
                interior_scene=execution.plan.interior_scene,
                reply_candidate=execution.realization.reply_candidate,
                router_signal=RouterSignal.NONE,
            )
        return None

    @staticmethod
    def _validated_route(
        shadow_input: ShadowEvaluationInputV1,
        execution: ShadowActorExecution,
    ) -> RouteTaken | None:
        allowed: dict[str, set[str]] = {
            "D0": {"direct"},
            "D1": {"compact"},
            "D2": {"staged"},
            "D3": {"compact", "staged"},
            "D4": {"equal_compute_direct"},
        }
        route = execution.route_taken
        if route not in allowed[shadow_input.config.config_label]:
            return None
        return route

    @staticmethod
    def _execution_shape_is_valid(
        shadow_input: ShadowEvaluationInputV1,
        execution: ShadowActorExecution,
        validation_decision: CompactDecisionV1 | None,
    ) -> bool:
        if execution.reply_envelope is None:
            return False
        if execution.attempt_count <= 0:
            return False
        if execution.attempt_count > shadow_input.config.call_budget:
            return False
        route = execution.route_taken
        if route in {"direct", "equal_compute_direct"}:
            return bool(
                validation_decision is None
                and execution.plan is None
                and execution.realization is None
            )
        if route == "compact":
            return bool(
                execution.compact_decision is not None
                and execution.plan is None
                and execution.realization is None
                and execution.compact_decision.reply_candidate
                == execution.reply_envelope
            )
        if route == "staged":
            return bool(
                execution.compact_decision is None
                and execution.plan is not None
                and execution.realization is not None
                and execution.plan.plan_fingerprint
                == execution.realization.plan_fingerprint
                and execution.realization.reply_candidate == execution.reply_envelope
            )
        return False

    def _failure(
        self,
        shadow_input: ShadowEvaluationInputV1,
        *,
        started_ns: int,
        code: ShadowFailureCode,
        stage: str,
        execution: ShadowActorExecution | None = None,
        route: RouteTaken | None = None,
        transport_completed: bool = False,
        schema_valid: bool = False,
    ) -> ShadowEvaluationOutputV1:
        usage = execution.usage if execution is not None else None
        return ShadowEvaluationOutputV1(
            scenario_id=shadow_input.scenario.scenario_id,
            config_label=shadow_input.config.config_label,
            sample_index=shadow_input.sample_index,
            route_taken=route,
            transport_completed=transport_completed,
            schema_valid=schema_valid,
            scope_and_binding_valid=False,
            attempt_count=execution.attempt_count if execution is not None else 0,
            input_tokens=usage.input_tokens if usage is not None else 0,
            output_tokens=usage.output_tokens if usage is not None else 0,
            cost_units=(
                usage.input_tokens + usage.output_tokens if usage is not None else 0
            ),
            latency_ms=self._elapsed_ms(started_ns),
            escalation_occurred=(
                execution.escalation_occurred if execution is not None else False
            ),
            failure_code=code,
            failure_stage=stage,
        )

    @staticmethod
    def _elapsed_ms(started_ns: int) -> int:
        return max(0, (perf_counter_ns() - started_ns) // 1_000_000)


__all__ = ["ShadowEvaluationRunner"]

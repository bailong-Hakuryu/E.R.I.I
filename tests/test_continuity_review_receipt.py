"""Continuity audit receipts bind one review to one delivered reply."""

from dataclasses import replace
import hashlib
import json
import unittest

from erii.core.continuity import ContinuityEvaluationCoordinator
from erii.core.continuity_review import build_continuity_review_receipt
from erii.models.continuity import (
    CONTINUITY_EVALUATION_RESULT_VERSION,
    CONTINUITY_REVIEW_BINDING_VERSION,
    ContinuityAxis,
    ContinuityEvaluationRequest,
    ContinuityEvaluationResult,
    ContinuityEvaluatorDescriptor,
    ContinuityFinding,
    ContinuityReviewBinding,
)
from erii.models.continuity_evidence import (
    CONTINUITY_EVIDENCE_REF_VERSION,
    ContinuityEvidenceKind,
    ContinuityEvidenceRef,
)
from erii.models.continuity_review import ContinuityReviewReceipt
from erii.models.persona import PersonaScope
from erii.models.turn import ContextSignalSource, DeliveryDisposition
from erii.models.voice_trace import VoiceActivationTrace, VoiceConditionMatchTrace


class _AlignedEvaluator:
    descriptor = ContinuityEvaluatorDescriptor(
        evaluator_id="test-continuity",
        evaluator_version="1.0",
    )

    def evaluate(self, request):
        return {
            "kind": "findings",
            "findings": [
                {
                    "finding_id": f"finding-{axis.value}",
                    "axis": axis.value,
                    "assessment": "aligned",
                    "severity": "info",
                    "reason_code": "aligned",
                    "reply_start": 0,
                    "reply_end": len(request.proposed_reply),
                    "reply_quote": request.proposed_reply,
                    "supporting_basis_refs": [
                        request.persona_context_refs[0].ref_id
                    ],
                }
                for axis in ContinuityAxis
            ],
        }


def _reviewed_reply():
    reply = "绘梨衣把写着晚安的手写板递给Sakura。"
    request = ContinuityEvaluationRequest(
        turn_id="turn-1",
        relationship_id="relationship-1",
        persona_id="persona-1",
        user_message="该休息了。",
        proposed_reply=reply,
        persona_manifest_id="manifest-1",
        context_baseline_fingerprint="0" * 64,
        persona_context_refs=(
            ContinuityEvidenceRef.create(
                ContinuityEvidenceKind.PERSONA_CLAIM,
                {
                    "manifest_id": "manifest-1",
                    "content_fingerprint": "1" * 64,
                    "claim_id": "gentle",
                },
            ),
        ),
    )
    result = ContinuityEvaluationCoordinator.evaluate(
        request,
        _AlignedEvaluator(),
    )
    return reply, request, result


def _reviewed_reply_with_voice_trace():
    reply, request, result = _reviewed_reply()
    activation_id = "activation-1"
    pattern_ref = ContinuityEvidenceRef.create(
        ContinuityEvidenceKind.CONTEXTUAL_VOICE_PATTERN,
        {
            "manifest_id": request.persona_manifest_id,
            "content_fingerprint": "1" * 64,
            "pattern_id": "pattern-1",
        },
    )
    condition_match = VoiceConditionMatchTrace(
        condition_id="condition-1",
        signal_source=ContextSignalSource.HOST_OBSERVED,
        signal_id="signal-1",
        signal_type="activity",
        matched_value="gaming",
        producer_version="host-observation-admission/v1",
        evidence_ref_ids=(),
        source_context={
            "kind": "host_observed",
            "observation_fingerprint": "b" * 64,
        },
    )
    trace = VoiceActivationTrace.create(
        activation_id=activation_id,
        relationship_id=request.relationship_id,
        turn_id=request.turn_id,
        persona_id=request.persona_id,
        manifest_id=request.persona_manifest_id,
        context_baseline_fingerprint=request.context_baseline_fingerprint,
        pattern_ref_id=pattern_ref.ref_id,
        pattern_scope=PersonaScope.RELATIONSHIP_TENDENCY,
        matcher_version="voice-pattern-matcher-v1",
        matcher_input_fingerprint="a" * 64,
        condition_matches=(condition_match,),
    )
    voice_finding = next(
        item for item in result.findings if item.axis == ContinuityAxis.VOICE_STYLE
    )
    voice_finding_data = voice_finding.model_dump(mode="python")
    voice_finding_data.update(
        {
            "assessment": "supported",
            "reason_code": "supported_contextual_voice",
            "supporting_basis_refs": (pattern_ref.ref_id,),
            "voice_activation_refs": (activation_id,),
        }
    )
    reviewed_findings = tuple(
        ContinuityFinding.model_validate(voice_finding_data)
        if item.axis == ContinuityAxis.VOICE_STYLE
        else item
        for item in result.findings
    )
    binding = replace(
        result.review_binding,
        persona_context_refs=(
            *result.review_binding.persona_context_refs,
            pattern_ref,
        ),
        voice_pattern_activation_ids=(activation_id,),
    )
    return reply, request, type(result)(
        assessment=result.assessment,
        findings=reviewed_findings,
        evaluator_descriptor=result.evaluator_descriptor,
        aggregation_policy_version=result.aggregation_policy_version,
        review_binding=binding,
        style_revision_advised=result.style_revision_advised,
        voice_activation_traces=(trace,),
    )


class ContinuityReviewReceiptTests(unittest.TestCase):
    def test_temporary_evaluation_result_has_a_strict_versioned_round_trip(self):
        _reply, _request, result = _reviewed_reply()

        payload = json.loads(json.dumps(result.to_dict(), ensure_ascii=False))
        restored = ContinuityEvaluationResult.from_dict(payload)

        self.assertEqual(restored, result)
        self.assertEqual(
            payload["result_version"],
            CONTINUITY_EVALUATION_RESULT_VERSION,
        )
        self.assertEqual(
            payload["review_binding"]["review_binding_version"],
            CONTINUITY_REVIEW_BINDING_VERSION,
        )

    def test_result_and_binding_wire_reject_unknown_missing_or_future_schema(self):
        _reply, _request, result = _reviewed_reply()

        result_mutations = (
            ("missing version", lambda value: value.pop("result_version")),
            (
                "future version",
                lambda value: value.update(
                    {"result_version": "continuity-evaluation-result/v999"}
                ),
            ),
            (
                "unknown field",
                lambda value: value.update({"future_result_authority": True}),
            ),
        )
        for name, mutate in result_mutations:
            with self.subTest(object="result", case=name):
                payload = result.to_dict()
                mutate(payload)
                with self.assertRaises(ValueError):
                    ContinuityEvaluationResult.from_dict(payload)

        binding_mutations = (
            (
                "missing version",
                lambda value: value.pop("review_binding_version"),
            ),
            (
                "future version",
                lambda value: value.update(
                    {"review_binding_version": "continuity-review-binding/v999"}
                ),
            ),
            (
                "unknown field",
                lambda value: value.update({"future_binding_authority": True}),
            ),
        )
        for name, mutate in binding_mutations:
            with self.subTest(object="binding", case=name):
                payload = result.review_binding.to_dict()
                mutate(payload)
                with self.assertRaises(ValueError):
                    ContinuityReviewBinding.from_dict(payload)

    def test_result_and_binding_wire_require_json_arrays(self):
        _reply, _request, result = _reviewed_reply()

        result_arrays = ("findings", "voice_activation_traces")
        for field_name in result_arrays:
            with self.subTest(object="result", field=field_name):
                payload = result.to_dict()
                payload[field_name] = tuple(payload[field_name])
                with self.assertRaisesRegex(ValueError, "must be an array"):
                    ContinuityEvaluationResult.from_dict(payload)

        binding_arrays = (
            "persona_context_refs",
            "relationship_context_refs",
            "voice_pattern_activation_ids",
        )
        for field_name in binding_arrays:
            with self.subTest(object="binding", field=field_name):
                payload = result.review_binding.to_dict()
                payload[field_name] = tuple(payload[field_name])
                with self.assertRaisesRegex(ValueError, "must be an array"):
                    ContinuityReviewBinding.from_dict(payload)

        payload = result.to_dict()
        payload["findings"][0]["supporting_basis_refs"] = tuple(
            payload["findings"][0]["supporting_basis_refs"]
        )
        with self.assertRaisesRegex(ValueError, "must be an array"):
            ContinuityEvaluationResult.from_dict(payload)

        _reply, _request, activated_result = _reviewed_reply_with_voice_trace()
        payload = activated_result.to_dict()
        payload["voice_activation_traces"][0]["condition_matches"] = tuple(
            payload["voice_activation_traces"][0]["condition_matches"]
        )
        with self.assertRaisesRegex(ValueError, "must be an array"):
            ContinuityEvaluationResult.from_dict(payload)

        payload = activated_result.to_dict()
        payload["voice_activation_traces"][0]["condition_matches"][0][
            "evidence_ref_ids"
        ] = tuple(
            payload["voice_activation_traces"][0]["condition_matches"][0][
                "evidence_ref_ids"
            ]
        )
        with self.assertRaisesRegex(ValueError, "must be an array"):
            ContinuityEvaluationResult.from_dict(payload)

    def test_result_and_binding_wire_do_not_coerce_scalars(self):
        _reply, _request, result = _reviewed_reply()
        mutations = (
            (
                "string finding offset",
                lambda value: value["findings"][0].update({"reply_start": "0"}),
            ),
            (
                "boolean finding offset",
                lambda value: value["findings"][0].update({"reply_start": False}),
            ),
            (
                "string binding length",
                lambda value: value["review_binding"].update(
                    {"user_message_length": str(result.review_binding.user_message_length)}
                ),
            ),
            (
                "boolean binding length",
                lambda value: value["review_binding"].update(
                    {"reply_length": True}
                ),
            ),
            (
                "integer boolean",
                lambda value: value.update({"style_revision_advised": 0}),
            ),
        )
        for name, mutate in mutations:
            with self.subTest(case=name):
                payload = result.to_dict()
                mutate(payload)
                with self.assertRaises(ValueError):
                    ContinuityEvaluationResult.from_dict(payload)

    def test_result_wire_rejects_nested_unknown_fields_and_tampering(self):
        _reply, _request, result = _reviewed_reply()
        mutations = (
            (
                "assessment unknown field",
                lambda value: value["assessment"].update(
                    {"future_assessment_authority": True}
                ),
            ),
            (
                "verdict tamper",
                lambda value: value["assessment"].update(
                    {"verdict": "supported_new_choice"}
                ),
            ),
            (
                "style advisory tamper",
                lambda value: value.update({"style_revision_advised": True}),
            ),
            (
                "activation binding tamper",
                lambda value: value["review_binding"].update(
                    {"voice_pattern_activation_ids": ["activation-forged"]}
                ),
            ),
            (
                "evidence ref identity tamper",
                lambda value: value["review_binding"]["persona_context_refs"][
                    0
                ].update({"ref_id": "f" * 64}),
            ),
        )
        for name, mutate in mutations:
            with self.subTest(case=name):
                payload = result.to_dict()
                mutate(payload)
                with self.assertRaises(ValueError):
                    ContinuityEvaluationResult.from_dict(payload)

    def test_receipt_round_trip_binds_identity_reply_and_review_versions(self):
        reply, request, result = _reviewed_reply()

        receipt = build_continuity_review_receipt(
            result,
            reply,
            DeliveryDisposition.SHOWN,
        )
        restored = ContinuityReviewReceipt.from_dict(receipt.to_dict())

        self.assertEqual(restored, receipt)
        self.assertEqual(receipt.relationship_id, request.relationship_id)
        self.assertEqual(receipt.turn_id, request.turn_id)
        self.assertEqual(
            receipt.review_binding.persona_manifest_id,
            request.persona_manifest_id,
        )
        self.assertEqual(receipt.reply_length, len(reply))
        self.assertEqual(len(receipt.reply_sha256), 64)
        self.assertEqual(len(receipt.findings), 5)
        self.assertEqual(
            receipt.assessment.evaluator_version,
            "test-continuity@1.0/1+continuity-aggregation-v1",
        )
        serialized = json.dumps(receipt.to_dict(), ensure_ascii=False)
        self.assertNotIn("proposed_reply", serialized)
        self.assertNotIn("system_prompt", serialized)
        self.assertNotIn("model_reasoning", serialized)

    def test_voice_trace_round_trips_without_persisting_runtime_activation(self):
        reply, _request, result = _reviewed_reply_with_voice_trace()

        result_payload = json.loads(
            json.dumps(result.to_dict(), ensure_ascii=False)
        )
        restored_result = ContinuityEvaluationResult.from_dict(result_payload)
        receipt = build_continuity_review_receipt(
            restored_result,
            reply,
            DeliveryDisposition.SHOWN,
        )
        receipt_payload = json.loads(
            json.dumps(receipt.to_dict(), ensure_ascii=False)
        )
        restored_receipt = ContinuityReviewReceipt.from_dict(receipt_payload)

        self.assertEqual(restored_result, result)
        self.assertEqual(restored_receipt, receipt)
        self.assertEqual(len(receipt.voice_activation_traces), 1)
        trace = receipt.voice_activation_traces[0]
        self.assertEqual(trace.activation_id, "activation-1")
        self.assertEqual(
            trace.pattern_ref_id,
            result.review_binding.persona_context_refs[1].ref_id,
        )
        self.assertEqual(
            next(
                item
                for item in receipt.findings
                if item.axis == ContinuityAxis.VOICE_STYLE
            ).voice_activation_refs,
            (trace.activation_id,),
        )
        self.assertNotIn("voice_pattern_activations", result_payload)
        self.assertNotIn("voice_pattern_activations", receipt_payload)
        self.assertNotIn("runtime_attestation", json.dumps(receipt_payload))

    def test_receipt_rejects_review_of_draft_a_attached_to_reply_b(self):
        reply, _request, result = _reviewed_reply()

        with self.assertRaisesRegex(ValueError, "different delivered reply"):
            build_continuity_review_receipt(
                result,
                reply + " 明天见。",
                DeliveryDisposition.SHOWN,
            )

    def test_gate_override_keeps_the_same_evaluated_reply(self):
        reply, _request, result = _reviewed_reply()
        persona_ref_id = result.review_binding.persona_context_refs[0].ref_id
        raw_finding = result.findings[0].model_dump(mode="python")
        raw_finding.update(
            {
                "assessment": "review",
                "severity": "warning",
                "reason_code": "value_tension",
                "supporting_basis_refs": (),
                "conflicting_source_refs": (persona_ref_id,),
            }
        )
        review_finding = ContinuityFinding.model_validate(raw_finding)
        review_result = type(result)(
            assessment=replace(result.assessment, verdict="review_required"),
            findings=(review_finding, *result.findings[1:]),
            evaluator_descriptor=result.evaluator_descriptor,
            aggregation_policy_version=result.aggregation_policy_version,
            review_binding=result.review_binding,
            style_revision_advised=result.style_revision_advised,
            voice_activation_traces=result.voice_activation_traces,
        )

        receipt = build_continuity_review_receipt(
            review_result,
            reply,
            DeliveryDisposition.OVERRIDDEN,
        )

        self.assertEqual(
            receipt.delivery_disposition,
            DeliveryDisposition.OVERRIDDEN,
        )
        with self.assertRaisesRegex(ValueError, "different delivered reply"):
            build_continuity_review_receipt(
                review_result,
                reply + " 被替换的内容",
                DeliveryDisposition.OVERRIDDEN,
            )

    def test_result_rejects_a_finding_outside_the_evaluated_context(self):
        _reply, _request, result = _reviewed_reply()
        finding = result.findings[0].model_copy(
            update={"supporting_basis_refs": ("f" * 64,)}
        )

        with self.assertRaisesRegex(ValueError, "outside its review binding"):
            type(result)(
                assessment=result.assessment,
                findings=(finding, *result.findings[1:]),
                evaluator_descriptor=result.evaluator_descriptor,
                aggregation_policy_version=result.aggregation_policy_version,
                review_binding=result.review_binding,
                style_revision_advised=result.style_revision_advised,
                voice_activation_traces=result.voice_activation_traces,
            )

    def test_result_recomputes_the_declared_aggregate_verdict(self):
        _reply, _request, result = _reviewed_reply()
        persona_ref_id = result.review_binding.persona_context_refs[0].ref_id
        raw_finding = result.findings[0].model_dump(mode="python")
        raw_finding.update(
            {
                "assessment": "unsupported",
                "severity": "warning",
                "reason_code": "unsupported_identity_change",
                "supporting_basis_refs": (),
                "conflicting_source_refs": (persona_ref_id,),
            }
        )
        unsupported = ContinuityFinding.model_validate(raw_finding)
        with self.assertRaisesRegex(ValueError, "conflicts with its findings"):
            type(result)(
                assessment=result.assessment,
                findings=(unsupported, *result.findings[1:]),
                evaluator_descriptor=result.evaluator_descriptor,
                aggregation_policy_version=result.aggregation_policy_version,
                review_binding=result.review_binding,
                style_revision_advised=result.style_revision_advised,
                voice_activation_traces=result.voice_activation_traces,
            )

    def test_persisted_receipt_recomputes_verdict_and_style_advisory(self):
        reply, _request, result = _reviewed_reply()
        receipt = build_continuity_review_receipt(
            result,
            reply,
            DeliveryDisposition.SHOWN,
        ).to_dict()
        persona_ref_id = receipt["review_binding"]["persona_context_refs"][0][
            "ref_id"
        ]
        unsupported = dict(receipt["findings"][0])
        unsupported.update(
            {
                "assessment": "unsupported",
                "severity": "warning",
                "reason_code": "unsupported_identity_change",
                "supporting_basis_refs": [],
                "conflicting_source_refs": [persona_ref_id],
            }
        )
        receipt["findings"][0] = unsupported

        with self.assertRaisesRegex(ValueError, "conflicts with its findings"):
            ContinuityReviewReceipt.from_dict(receipt)

        receipt = build_continuity_review_receipt(
            result,
            reply,
            DeliveryDisposition.SHOWN,
        ).to_dict()
        receipt["style_revision_advised"] = True
        with self.assertRaisesRegex(ValueError, "style advisory conflicts"):
            ContinuityReviewReceipt.from_dict(receipt)

    def test_binding_hashes_exact_visible_text_without_trimming(self):
        user_message = "  \n用户🙂还在这里\t"
        reply = "\n  角色🙂也还在这里  \n"
        request = ContinuityEvaluationRequest(
            turn_id="turn-whitespace",
            relationship_id="relationship-whitespace",
            persona_id="persona-whitespace",
            user_message=user_message,
            proposed_reply=reply,
            persona_manifest_id="manifest-whitespace",
            context_baseline_fingerprint="1" * 64,
            persona_context_refs=(
                ContinuityEvidenceRef.create(
                    ContinuityEvidenceKind.PERSONA_CLAIM,
                    {
                        "manifest_id": "manifest-whitespace",
                        "content_fingerprint": "2" * 64,
                        "claim_id": "gentle",
                    },
                ),
            ),
        )

        result = ContinuityEvaluationCoordinator.evaluate(
            request,
            _AlignedEvaluator(),
        )
        binding = result.review_binding

        self.assertEqual(request.user_message, user_message)
        self.assertEqual(request.proposed_reply, reply)
        self.assertEqual(result.findings[0].reply_quote, reply)
        self.assertEqual(
            binding.user_message_sha256,
            hashlib.sha256(user_message.encode("utf-8")).hexdigest(),
        )
        self.assertEqual(
            binding.reply_sha256,
            hashlib.sha256(reply.encode("utf-8")).hexdigest(),
        )
        self.assertEqual(binding.user_message_length, len(user_message))
        self.assertEqual(binding.reply_length, len(reply))
        binding.verify_user_message(user_message)
        with self.assertRaisesRegex(ValueError, "different User message"):
            binding.verify_user_message(user_message.strip())
        build_continuity_review_receipt(
            result,
            reply,
            DeliveryDisposition.SHOWN,
        )
        with self.assertRaisesRegex(ValueError, "different delivered reply"):
            build_continuity_review_receipt(
                result,
                reply.strip(),
                DeliveryDisposition.SHOWN,
            )

    def test_binding_covers_the_opening_message_without_retaining_its_text(self):
        _reply, request, result = _reviewed_reply()
        other_request = replace(request, user_message="这是另一条用户消息。")
        other_binding = ContinuityReviewBinding.from_request(other_request)

        self.assertNotEqual(
            result.review_binding.user_message_sha256,
            other_binding.user_message_sha256,
        )
        serialized = json.dumps(result.review_binding.to_dict(), ensure_ascii=False)
        self.assertNotIn(request.user_message, serialized)
        self.assertNotIn(other_request.user_message, serialized)

    def test_persistent_evidence_refs_are_strict_typed_canonical_objects(self):
        _reply, request, _result = _reviewed_reply()

        with self.assertRaisesRegex(ValueError, "must be a sequence"):
            replace(
                request,
                persona_context_refs="abc",
            )
        with self.assertRaisesRegex(ValueError, "unknown or missing fields"):
            replace(
                request,
                persona_context_refs=({"kind": "persona_claim"},),
            )
        with self.assertRaisesRegex(ValueError, "one scope"):
            replace(
                request,
                relationship_context_refs=(request.persona_context_refs[0],),
            )

        payload = request.persona_context_refs[0].to_dict()
        payload["ref_id"] = "f" * 64
        with self.assertRaisesRegex(ValueError, "does not match"):
            ContinuityEvidenceRef.from_dict(payload)

        first = ContinuityEvidenceRef.create(
            ContinuityEvidenceKind.CHARACTER_BLUEPRINT,
            {
                "blueprint_id": "blueprint-1",
                "revision": 1,
                "source_sha256": "a" * 64,
            },
        )
        second = ContinuityEvidenceRef.create(
            ContinuityEvidenceKind.CHARACTER_BLUEPRINT,
            {
                "source_sha256": "a" * 64,
                "revision": 1,
                "blueprint_id": "blueprint-1",
            },
        )
        self.assertEqual(first.ref_id, second.ref_id)
        self.assertEqual(first.ref_version, CONTINUITY_EVIDENCE_REF_VERSION)

    def test_receipt_rejects_scalar_values_for_sequence_fields(self):
        reply, _request, result = _reviewed_reply()
        receipt = build_continuity_review_receipt(
            result,
            reply,
            DeliveryDisposition.SHOWN,
        ).to_dict()
        receipt["voice_activation_traces"] = ""

        with self.assertRaisesRegex(ValueError, "must be an array"):
            ContinuityReviewReceipt.from_dict(receipt)

    def test_receipt_wire_requires_json_arrays_and_strict_nested_values(self):
        reply, _request, result = _reviewed_reply_with_voice_trace()
        original = build_continuity_review_receipt(
            result,
            reply,
            DeliveryDisposition.SHOWN,
        ).to_dict()
        mutations = (
            (
                "tuple findings",
                lambda value: value.update({"findings": tuple(value["findings"])}),
            ),
            (
                "tuple voice traces",
                lambda value: value.update(
                    {
                        "voice_activation_traces": tuple(
                            value["voice_activation_traces"]
                        )
                    }
                ),
            ),
            (
                "tuple condition matches",
                lambda value: value["voice_activation_traces"][0].update(
                    {
                        "condition_matches": tuple(
                            value["voice_activation_traces"][0][
                                "condition_matches"
                            ]
                        )
                    }
                ),
            ),
            (
                "trace fingerprint tamper",
                lambda value: value["voice_activation_traces"][0].update(
                    {"trace_fingerprint": "f" * 64}
                ),
            ),
            (
                "string finding offset",
                lambda value: value["findings"][0].update({"reply_start": "0"}),
            ),
            (
                "numeric assessment status",
                lambda value: value["assessment"].update({"status": 1}),
            ),
            (
                "unknown nested finding field",
                lambda value: value["findings"][0].update({"future": True}),
            ),
        )
        for name, mutate in mutations:
            with self.subTest(case=name):
                payload = json.loads(json.dumps(original, ensure_ascii=False))
                mutate(payload)
                with self.assertRaises(ValueError):
                    ContinuityReviewReceipt.from_dict(payload)

    def test_receipt_rejects_unknown_future_versions(self):
        reply, _request, result = _reviewed_reply()
        receipt = build_continuity_review_receipt(
            result,
            reply,
            DeliveryDisposition.SHOWN,
        ).to_dict()
        receipt["receipt_version"] = "continuity-review-receipt/v999"

        with self.assertRaisesRegex(ValueError, "unsupported"):
            ContinuityReviewReceipt.from_dict(receipt)


if __name__ == "__main__":
    unittest.main()

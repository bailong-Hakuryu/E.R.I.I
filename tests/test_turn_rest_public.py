"""REST contracts for the canonical Turn Recording lifecycle."""

import importlib
import tempfile
import unittest

from fastapi.testclient import TestClient

from erii import ERIIEngine, FileStorage, MemoryPack
from erii.models.continuity import ContinuityAxis, ContinuityEvaluatorDescriptor
from erii.models.continuity_evidence import (
    ContinuityEvidenceKind,
    ContinuityEvidenceRef,
)
server_module = importlib.import_module("erii.server.app")
TEST_API_KEY = "test-turn-rest-" + ("x" * 32)


class _AlignedContinuityEvaluator:
    descriptor = ContinuityEvaluatorDescriptor(
        evaluator_id="tests.rest-continuity-evaluator",
        evaluator_version="1",
        evaluation_schema_version="1",
    )

    def evaluate(self, request):
        return {
            "kind": "findings",
            "findings": [
                {
                    "finding_id": f"rest-{axis.value}",
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
                    "conflicting_source_refs": [],
                }
                for axis in ContinuityAxis
            ],
        }


class _ContextualVoiceContinuityEvaluator:
    descriptor = ContinuityEvaluatorDescriptor(
        evaluator_id="tests.rest-contextual-voice-evaluator",
        evaluator_version="1",
        evaluation_schema_version="1",
    )

    def evaluate(self, request):
        pattern_ref = next(
            item
            for item in request.persona_context_refs
            if item.kind == ContinuityEvidenceKind.CONTEXTUAL_VOICE_PATTERN
        )
        activation = request.voice_pattern_activations[0]
        findings = []
        for axis in ContinuityAxis:
            contextual_voice = axis == ContinuityAxis.VOICE_STYLE
            findings.append(
                {
                    "finding_id": f"rest-contextual-{axis.value}",
                    "axis": axis.value,
                    "assessment": "supported" if contextual_voice else "aligned",
                    "severity": "info",
                    "reason_code": (
                        "supported_contextual_voice"
                        if contextual_voice
                        else "aligned"
                    ),
                    "reply_start": 0,
                    "reply_end": len(request.proposed_reply),
                    "reply_quote": request.proposed_reply,
                    "supporting_basis_refs": [
                        pattern_ref.ref_id
                        if contextual_voice
                        else request.persona_context_refs[0].ref_id
                    ],
                    "conflicting_source_refs": [],
                    "voice_activation_refs": (
                        [activation.activation_id] if contextual_voice else []
                    ),
                }
            )
        return {"kind": "findings", "findings": findings}


def _persona_candidate():
    return {
        "schema_version": "0.4.0a7",
        "compiler_version": "tests.rest-persona-compiler/1",
        "source_spans": [
            {
                "span_id": "span-gentle",
                "start": 0,
                "end": 15,
                "quote": "A quiet charact",
            }
        ],
        "claims": [
            {
                "claim_id": "voice-gentle",
                "kind": "voice",
                "statement": "The character speaks with quiet care.",
                "activation_tier": "situational",
                "basis": "explicit",
                "scope": "character",
                "source_span_ids": ["span-gentle"],
            }
        ],
        "contextual_voice_patterns": [
            {
                "pattern_id": "gentle-gaming",
                "description": "A concise register used while playing a game.",
                "scope": "character",
                "basis": "explicit",
                "source_span_ids": ["span-gentle"],
                "conditions": [
                    {
                        "condition_id": "while-gaming",
                        "condition_type": "activity",
                        "values": ["gaming"],
                    }
                ],
                "required_claim_ids": ["voice-gentle"],
            }
        ],
    }


class TurnRestPublicTests(unittest.TestCase):
    def setUp(self):
        self.engine = ERIIEngine(
            storage_driver=FileStorage(tempfile.mkdtemp()),
            continuity_evaluator=_AlignedContinuityEvaluator(),
        )
        self.engine.initialize_relationship(
            "agent_erii",
            "user_one",
            "A quiet character who values honest companionship.",
        )
        proposal = self.engine.propose_persona_compilation(
            "agent_erii",
            "user_one",
            _persona_candidate(),
        )
        self.engine.decide_persona_compilation(
            "agent_erii",
            "user_one",
            proposal.proposal_id,
            proposal.revision,
            "tests",
            "approve",
        )
        manifest = self.engine.get_persona_manifest("agent_erii", "user_one")
        if manifest is None:
            raise AssertionError("test requires an approved Persona Manifest")
        self.persona_ref = ContinuityEvidenceRef.create(
            ContinuityEvidenceKind.PERSONA_CLAIM,
            {
                "manifest_id": manifest.manifest_id,
                "content_fingerprint": manifest.content_fingerprint,
                "claim_id": "voice-gentle",
            },
        ).to_dict()
        self.voice_pattern_ref = ContinuityEvidenceRef.create(
            ContinuityEvidenceKind.CONTEXTUAL_VOICE_PATTERN,
            {
                "manifest_id": manifest.manifest_id,
                "content_fingerprint": manifest.content_fingerprint,
                "pattern_id": "gentle-gaming",
            },
        ).to_dict()
        server_module._engine = self.engine
        server_module.configure_server_access(TEST_API_KEY)
        self.client = TestClient(
            server_module.app,
            headers={"X-API-Key": TEST_API_KEY},
        )

    def tearDown(self):
        self.client.close()
        server_module._engine = None
        server_module.configure_server_access(None)
        self.engine.close()

    def test_open_complete_and_read_turn_without_echoing_transcript_in_receipt(self):
        opened = self.client.post(
            "/api/v1/turns/open",
            json={
                "agent_id": "agent_erii",
                "user_id": "user_one",
                "turn_id": "turn-rest",
                "user_message": "Can we keep this memory?",
            },
        )

        self.assertEqual(opened.status_code, 201)
        self.assertEqual(opened.json()["turn"]["status"], "open")

        attempt = self.client.post(
            "/api/v1/turns/turn-rest/reply-attempts",
            json={
                "agent_id": "agent_erii",
                "user_id": "user_one",
                "attempt_number": 1,
                "stage": "generation",
                "capability_descriptor": "test-provider/model-v1",
                "failure_classification": "temporary_provider_error",
            },
        )
        self.assertEqual(attempt.status_code, 201)
        self.assertNotIn("draft", attempt.json()["attempt"])

        completed = self.client.post(
            "/api/v1/turns/turn-rest/complete",
            json={
                "agent_id": "agent_erii",
                "user_id": "user_one",
                "agent_message": "Yes. We will keep the whole moment.",
                "processing_channels": [],
                "delivery_disposition": "shown_unreviewed",
                "delivery_exception": {
                    "exception_record_version": "delivery-exception-record/v1",
                    "disposition": "shown_unreviewed",
                    "actor_kind": "host_policy",
                    "actor_id": "tests.turn-rest/v1",
                    "reason_code": "availability_fallback",
                    "decided_at": "2026-08-01T00:00:00+00:00",
                    "reply_attempt_number": 1,
                },
            },
        )

        self.assertEqual(completed.status_code, 200)
        receipt = completed.json()["receipt"]
        self.assertEqual(receipt["source_turn_id"], "turn-rest")
        self.assertNotIn("transcript", receipt)

        restored = self.client.get(
            "/api/v1/turns/turn-rest",
            params={"agent_id": "agent_erii", "user_id": "user_one"},
        )
        self.assertEqual(restored.status_code, 200)
        self.assertEqual(
            restored.json()["turn"]["transcript"]["agent_message"]["content"],
            "Yes. We will keep the whole moment.",
        )
        self.assertEqual(
            restored.json()["turn"]["review_record"]["kind"],
            "not_evaluated",
        )
        listed = self.client.get(
            "/api/v1/turns",
            params={
                "agent_id": "agent_erii",
                "user_id": "user_one",
                "status": "completed",
            },
        )
        self.assertEqual(listed.status_code, 200)
        self.assertEqual(
            [turn["turn_id"] for turn in listed.json()["turns"]],
            ["turn-rest"],
        )

    def test_reviewed_reply_round_trips_through_rest_before_completion(self):
        opened = self.client.post(
            "/api/v1/turns/open",
            json={
                "agent_id": "agent_erii",
                "user_id": "user_one",
                "turn_id": "turn-rest-reviewed",
                "user_message": "Are you still here?",
            },
        )
        self.assertEqual(opened.status_code, 201)

        evaluated = self.client.post(
            "/api/v1/turns/turn-rest-reviewed/continuity/evaluate",
            json={
                "agent_id": "agent_erii",
                "user_id": "user_one",
                "proposed_reply": "I am still here.",
                "persona_context_refs": [self.persona_ref],
                "relationship_context_refs": [],
            },
        )

        self.assertEqual(evaluated.status_code, 200)
        result = evaluated.json()["result"]
        self.assertEqual(
            result["result_version"],
            "continuity-evaluation-result/v1",
        )
        completed = self.client.post(
            "/api/v1/turns/turn-rest-reviewed/complete",
            json={
                "agent_id": "agent_erii",
                "user_id": "user_one",
                "agent_message": "I am still here.",
                "continuity_result": result,
                "delivery_disposition": "shown",
                "processing_channels": [],
            },
        )

        self.assertEqual(completed.status_code, 200)
        restored = self.client.get(
            "/api/v1/turns/turn-rest-reviewed",
            params={"agent_id": "agent_erii", "user_id": "user_one"},
        )
        self.assertEqual(restored.status_code, 200)
        self.assertEqual(restored.json()["turn"]["review_record"]["kind"], "reviewed")
        self.assertEqual(
            restored.json()["turn"]["delivery_disposition"],
            "shown",
        )

    def test_tampered_rest_review_result_cannot_complete_the_turn(self):
        self.client.post(
            "/api/v1/turns/open",
            json={
                "agent_id": "agent_erii",
                "user_id": "user_one",
                "turn_id": "turn-rest-tampered",
                "user_message": "Are you still here?",
            },
        )
        evaluated = self.client.post(
            "/api/v1/turns/turn-rest-tampered/continuity/evaluate",
            json={
                "agent_id": "agent_erii",
                "user_id": "user_one",
                "proposed_reply": "I am still here.",
                "persona_context_refs": [self.persona_ref],
            },
        )
        result = evaluated.json()["result"]
        result["review_binding"]["turn_id"] = "another-turn"

        completed = self.client.post(
            "/api/v1/turns/turn-rest-tampered/complete",
            json={
                "agent_id": "agent_erii",
                "user_id": "user_one",
                "agent_message": "I am still here.",
                "continuity_result": result,
                "delivery_disposition": "shown",
            },
        )

        self.assertEqual(completed.status_code, 422)
        restored = self.client.get(
            "/api/v1/turns/turn-rest-tampered",
            params={"agent_id": "agent_erii", "user_id": "user_one"},
        )
        self.assertEqual(restored.json()["turn"]["status"], "open")
        self.assertIsNone(
            restored.json()["turn"]["transcript"]["agent_message"]
        )

    def test_contextual_voice_trace_round_trips_without_runtime_activation(self):
        self.engine.continuity_evaluator = _ContextualVoiceContinuityEvaluator()
        opened = self.client.post(
            "/api/v1/turns/open",
            json={
                "agent_id": "agent_erii",
                "user_id": "user_one",
                "turn_id": "turn-rest-voice-trace",
                "user_message": "One more game?",
                "interaction_context": [
                    {
                        "signal_id": "activity-gaming",
                        "source": "host_observed",
                        "signal_type": "activity",
                        "value": "gaming",
                    }
                ],
            },
        )
        self.assertEqual(opened.status_code, 201)

        evaluated = self.client.post(
            "/api/v1/turns/turn-rest-voice-trace/continuity/evaluate",
            json={
                "agent_id": "agent_erii",
                "user_id": "user_one",
                "proposed_reply": "Then one more round.",
                "persona_context_refs": [
                    self.persona_ref,
                    self.voice_pattern_ref,
                ],
            },
        )
        self.assertEqual(evaluated.status_code, 200)
        result = evaluated.json()["result"]
        self.assertNotIn("voice_pattern_activations", result)
        self.assertEqual(len(result["voice_activation_traces"]), 1)
        trace = result["voice_activation_traces"][0]
        self.assertEqual(trace["pattern_ref_id"], self.voice_pattern_ref["ref_id"])

        completed = self.client.post(
            "/api/v1/turns/turn-rest-voice-trace/complete",
            json={
                "agent_id": "agent_erii",
                "user_id": "user_one",
                "agent_message": "Then one more round.",
                "continuity_result": result,
                "delivery_disposition": "shown",
                "processing_channels": [],
            },
        )
        self.assertEqual(completed.status_code, 200)
        restored = self.client.get(
            "/api/v1/turns/turn-rest-voice-trace",
            params={"agent_id": "agent_erii", "user_id": "user_one"},
        ).json()["turn"]
        receipt = restored["review_record"]["receipt"]
        self.assertEqual(receipt["voice_activation_traces"], [trace])
        self.assertNotIn("voice_pattern_activations", receipt)

        portable = MemoryPack.from_json(
            self.engine.export_memory("agent_erii", "user_one").to_json()
        )
        with ERIIEngine(storage_driver=FileStorage(tempfile.mkdtemp())) as target:
            target.import_memory(portable)
            imported = target.get_turn(
                "agent_erii",
                "user_one",
                "turn-rest-voice-trace",
            )
            imported_receipt = imported.review_record.receipt
            self.assertEqual(
                [item.to_dict() for item in imported_receipt.voice_activation_traces],
                [trace],
            )


if __name__ == "__main__":
    unittest.main()

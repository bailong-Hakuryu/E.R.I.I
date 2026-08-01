"""Strict model contracts for portable, non-replayable voice traces."""

import copy
import json
import unittest

from erii.models.continuity_evidence import (
    ContinuityEvidenceKind,
    ContinuityEvidenceRef,
)
from erii.models.persona import PersonaScope
from erii.models.turn import ContextSignalSource
from erii.models.voice_trace import (
    VOICE_ACTIVATION_TRACE_VERSION,
    VoiceActivationTrace,
    VoiceConditionMatchTrace,
)


_HOST_EVIDENCE_ID = "1" * 64
_CORE_EVIDENCE_ID = "2" * 64
_EVALUATOR_EVIDENCE_ID = "3" * 64


def _pattern_ref_id() -> str:
    return ContinuityEvidenceRef.create(
        ContinuityEvidenceKind.CONTEXTUAL_VOICE_PATTERN,
        {
            "manifest_id": "manifest-1",
            "content_fingerprint": "4" * 64,
            "pattern_id": "voice-playful",
        },
    ).ref_id


def _match(source: ContextSignalSource) -> VoiceConditionMatchTrace:
    common = {
        "signal_source": source,
        "signal_id": f"signal-{source.value}",
        "producer_version": "producer-v1",
    }
    if source == ContextSignalSource.HOST_OBSERVED:
        return VoiceConditionMatchTrace(
            condition_id="condition-activity",
            signal_type="activity",
            matched_value="gaming",
            evidence_ref_ids=(_HOST_EVIDENCE_ID,),
            source_context={
                "kind": source.value,
                "observation_fingerprint": "5" * 64,
            },
            **common,
        )
    if source == ContextSignalSource.CORE_DERIVED:
        return VoiceConditionMatchTrace(
            condition_id="condition-safety",
            signal_type="relationship_safety",
            matched_value="high",
            evidence_ref_ids=(_CORE_EVIDENCE_ID,),
            source_context={
                "kind": source.value,
                "producer_input_fingerprint": "6" * 64,
                "history_prefix_fingerprint": "7" * 64,
                "relationship_projection_version": "relationship-history-v1",
            },
            **common,
        )
    return VoiceConditionMatchTrace(
        condition_id="condition-emotion",
        signal_type="emotion",
        matched_value="excited",
        evidence_ref_ids=(_EVALUATOR_EVIDENCE_ID,),
        source_context={
            "kind": source.value,
            "candidate_key": "emotion-primary",
            "producer_input_fingerprint": "8" * 64,
            "evaluator_descriptor": {
                "evaluator_id": "emotion-evaluator",
                "evaluator_version": "1.2.0",
                "evaluation_schema_version": "1",
            },
        },
        **common,
    )


def _trace(*matches: VoiceConditionMatchTrace) -> VoiceActivationTrace:
    selected = matches or (_match(ContextSignalSource.HOST_OBSERVED),)
    return VoiceActivationTrace.create(
        activation_id="activation-1",
        relationship_id="relationship-1",
        turn_id="turn-1",
        persona_id="persona-1",
        manifest_id="manifest-1",
        context_baseline_fingerprint="9" * 64,
        pattern_ref_id=_pattern_ref_id(),
        pattern_scope=PersonaScope.RELATIONSHIP_TENDENCY,
        matcher_version="voice-pattern-matcher-v1",
        matcher_input_fingerprint="a" * 64,
        condition_matches=selected,
    )


class VoiceActivationTraceModelTests(unittest.TestCase):
    def test_strict_round_trip_preserves_all_source_context_branches(self):
        original = _trace(
            _match(ContextSignalSource.HOST_OBSERVED),
            _match(ContextSignalSource.CORE_DERIVED),
            _match(ContextSignalSource.EVALUATOR_INFERRED),
        )

        payload = json.loads(json.dumps(original.to_dict()))
        restored = VoiceActivationTrace.from_dict(payload)

        self.assertEqual(restored, original)
        self.assertEqual(payload["trace_version"], VOICE_ACTIVATION_TRACE_VERSION)
        self.assertIsInstance(payload["condition_matches"], list)
        self.assertEqual(
            [item.signal_source for item in restored.condition_matches],
            [
                ContextSignalSource.HOST_OBSERVED,
                ContextSignalSource.CORE_DERIVED,
                ContextSignalSource.EVALUATOR_INFERRED,
            ],
        )
        self.assertEqual(
            restored.condition_matches[0].source_context[
                "observation_fingerprint"
            ],
            "5" * 64,
        )
        self.assertEqual(
            restored.condition_matches[1].source_context[
                "history_prefix_fingerprint"
            ],
            "7" * 64,
        )
        self.assertEqual(
            restored.condition_matches[2].source_context[
                "evaluator_descriptor"
            ]["evaluator_id"],
            "emotion-evaluator",
        )

    def test_trace_wire_rejects_unknown_missing_and_future_version(self):
        original = _trace().to_dict()
        mutations = {
            "unknown field": lambda value: value.update({"runtime_authority": True}),
            "missing field": lambda value: value.pop("matcher_version"),
            "future version": lambda value: value.update(
                {"trace_version": "voice-activation-trace/v999"}
            ),
        }

        for name, mutate in mutations.items():
            with self.subTest(case=name):
                payload = copy.deepcopy(original)
                mutate(payload)
                with self.assertRaises(ValueError):
                    VoiceActivationTrace.from_dict(payload)

    def test_condition_match_wire_rejects_unknown_and_missing_fields(self):
        original = _match(ContextSignalSource.HOST_OBSERVED).to_dict()
        mutations = {
            "unknown field": lambda value: value.update({"reasoning": "hidden"}),
            "missing field": lambda value: value.pop("matched_value"),
        }

        for name, mutate in mutations.items():
            with self.subTest(case=name):
                payload = copy.deepcopy(original)
                mutate(payload)
                with self.assertRaises(ValueError):
                    VoiceConditionMatchTrace.from_dict(payload)

    def test_each_source_context_requires_its_exact_branch_shape(self):
        cases = (
            ContextSignalSource.HOST_OBSERVED,
            ContextSignalSource.CORE_DERIVED,
            ContextSignalSource.EVALUATOR_INFERRED,
        )
        for source in cases:
            with self.subTest(source=source.value, mutation="unknown"):
                payload = _match(source).to_dict()
                payload["source_context"]["unknown"] = "forbidden"
                with self.assertRaises(ValueError):
                    VoiceConditionMatchTrace.from_dict(payload)

            with self.subTest(source=source.value, mutation="missing"):
                payload = _match(source).to_dict()
                removable = next(
                    key for key in payload["source_context"] if key != "kind"
                )
                payload["source_context"].pop(removable)
                with self.assertRaises(ValueError):
                    VoiceConditionMatchTrace.from_dict(payload)

            with self.subTest(source=source.value, mutation="wrong kind"):
                payload = _match(source).to_dict()
                payload["source_context"]["kind"] = (
                    ContextSignalSource.CORE_DERIVED.value
                    if source != ContextSignalSource.CORE_DERIVED
                    else ContextSignalSource.HOST_OBSERVED.value
                )
                with self.assertRaises(ValueError):
                    VoiceConditionMatchTrace.from_dict(payload)

    def test_wire_requires_json_arrays(self):
        trace_payload = _trace().to_dict()
        trace_payload["condition_matches"] = tuple(
            trace_payload["condition_matches"]
        )
        with self.assertRaisesRegex(ValueError, "must be an array"):
            VoiceActivationTrace.from_dict(trace_payload)

        match_payload = _match(ContextSignalSource.HOST_OBSERVED).to_dict()
        match_payload["evidence_ref_ids"] = tuple(
            match_payload["evidence_ref_ids"]
        )
        with self.assertRaisesRegex(ValueError, "must be an array"):
            VoiceConditionMatchTrace.from_dict(match_payload)

    def test_trace_rejects_fingerprint_or_identity_tampering(self):
        original = _trace().to_dict()
        fingerprint = original["trace_fingerprint"]
        mutations = {
            "fingerprint": lambda value: value.update(
                {
                    "trace_fingerprint": (
                        ("0" if fingerprint[0] != "0" else "1")
                        + fingerprint[1:]
                    )
                }
            ),
            "bound identity": lambda value: value.update(
                {"relationship_id": "relationship-other"}
            ),
            "condition value": lambda value: value["condition_matches"][0].update(
                {"matched_value": "sleeping"}
            ),
        }

        for name, mutate in mutations.items():
            with self.subTest(case=name):
                payload = copy.deepcopy(original)
                mutate(payload)
                with self.assertRaisesRegex(ValueError, "trace_fingerprint"):
                    VoiceActivationTrace.from_dict(payload)

    def test_evidence_refs_must_be_unique_and_sorted(self):
        base = _match(ContextSignalSource.HOST_OBSERVED).to_dict()
        cases = {
            "duplicate": [_HOST_EVIDENCE_ID, _HOST_EVIDENCE_ID],
            "unsorted": [_CORE_EVIDENCE_ID, _HOST_EVIDENCE_ID],
        }

        for name, evidence_refs in cases.items():
            with self.subTest(case=name):
                payload = copy.deepcopy(base)
                payload["evidence_ref_ids"] = evidence_refs
                with self.assertRaisesRegex(ValueError, "unique and sorted"):
                    VoiceConditionMatchTrace.from_dict(payload)

    def test_trace_rejects_duplicate_condition_ids(self):
        first = _match(ContextSignalSource.HOST_OBSERVED)
        duplicate = VoiceConditionMatchTrace(
            condition_id=first.condition_id,
            signal_source=ContextSignalSource.CORE_DERIVED,
            signal_id="signal-other",
            signal_type="relationship_safety",
            matched_value="moderate",
            producer_version="relationship-safety-v1",
            evidence_ref_ids=(_CORE_EVIDENCE_ID,),
            source_context={
                "kind": ContextSignalSource.CORE_DERIVED.value,
                "producer_input_fingerprint": "b" * 64,
                "history_prefix_fingerprint": "c" * 64,
                "relationship_projection_version": "relationship-history-v1",
            },
        )

        with self.assertRaisesRegex(ValueError, "condition IDs must be unique"):
            _trace(first, duplicate)

    def test_trace_exposes_no_runtime_activation_conversion_interface(self):
        trace = _trace()

        public_conversion_methods = {
            name
            for name in dir(type(trace))
            if not name.startswith("_")
            and "activation" in name.casefold()
            and callable(getattr(type(trace), name))
        }
        self.assertEqual(public_conversion_methods, set())
        self.assertNotIn("runtime_attestation", trace.to_dict())
        self.assertIsInstance(
            VoiceActivationTrace.from_dict(trace.to_dict()),
            VoiceActivationTrace,
        )


if __name__ == "__main__":
    unittest.main()

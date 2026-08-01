"""Strict portable contracts for continuity evidence references."""

import json
import unittest

from erii.models.continuity_evidence import (
    CONTINUITY_EVIDENCE_REF_VERSION,
    ContinuityEvidenceKind,
    ContinuityEvidenceRef,
)


def _locators():
    manifest = {"manifest_id": "manifest-1", "content_fingerprint": "a" * 64}
    relationship = {"relationship_id": "relationship-1"}
    return {
        ContinuityEvidenceKind.CHARACTER_BLUEPRINT: {
            "blueprint_id": "blueprint-1",
            "revision": 1,
            "source_sha256": "b" * 64,
        },
        ContinuityEvidenceKind.CHARACTER_SOURCE_SPAN: {
            "blueprint_id": "blueprint-1",
            "revision": 1,
            "source_sha256": "b" * 64,
            "start": 0,
            "end": 12,
            "quote_sha256": "c" * 64,
        },
        ContinuityEvidenceKind.PERSONA_CLAIM: {
            **manifest,
            "claim_id": "claim-1",
        },
        ContinuityEvidenceKind.FORMATIVE_EXPERIENCE: {
            **manifest,
            "experience_id": "experience-1",
        },
        ContinuityEvidenceKind.MEANING_CAPSULE: {
            **manifest,
            "capsule_id": "capsule-1",
        },
        ContinuityEvidenceKind.CONTEXTUAL_VOICE_PATTERN: {
            **manifest,
            "pattern_id": "pattern-1",
        },
        ContinuityEvidenceKind.APPROVED_PERSONA_GROWTH: {
            **relationship,
            "proposal_id": "growth-1",
            "revision": 2,
            "content_fingerprint": "d" * 64,
        },
        ContinuityEvidenceKind.RELATIONSHIP_PREMISE: {
            **relationship,
            "premise_id": "premise-1",
            "content_fingerprint": "e" * 64,
        },
        ContinuityEvidenceKind.PREMISE_EXPERIENCE: {
            **relationship,
            "premise_id": "premise-1",
            "content_fingerprint": "e" * 64,
            "experience_id": "premise-experience-1",
        },
        ContinuityEvidenceKind.SOURCE_TURN: {
            **relationship,
            "turn_id": "turn-1",
            "source_revision": "1",
        },
        ContinuityEvidenceKind.RELATIONSHIP_EVENT: {
            **relationship,
            "event_id": "event-1",
        },
        ContinuityEvidenceKind.PERSONA_REFLECTION_RECORD: {
            **relationship,
            "reflection_id": "reflection-1",
            "content_fingerprint": "f" * 64,
        },
        ContinuityEvidenceKind.MEMORY_NODE: {
            **relationship,
            "node_id": "memory-1",
            "artifact_fingerprint": "1" * 64,
        },
    }


class ContinuityEvidenceRefTests(unittest.TestCase):
    def test_every_v1_kind_has_a_strict_json_round_trip(self):
        self.assertEqual(set(_locators()), set(ContinuityEvidenceKind))
        for kind, locator in _locators().items():
            with self.subTest(kind=kind.value):
                reference = ContinuityEvidenceRef.create(kind, locator)
                payload = json.loads(json.dumps(reference.to_dict()))
                self.assertEqual(
                    ContinuityEvidenceRef.from_dict(payload),
                    reference,
                )
                self.assertEqual(
                    payload["ref_version"],
                    CONTINUITY_EVIDENCE_REF_VERSION,
                )
                self.assertEqual(len(reference.ref_id), 64)

    def test_identity_is_canonical_and_binds_locator_content(self):
        locator = _locators()[ContinuityEvidenceKind.PERSONA_CLAIM]
        reordered = {
            "claim_id": locator["claim_id"],
            "content_fingerprint": locator["content_fingerprint"],
            "manifest_id": locator["manifest_id"],
        }
        first = ContinuityEvidenceRef.create(
            ContinuityEvidenceKind.PERSONA_CLAIM,
            locator,
        )
        second = ContinuityEvidenceRef.create(
            ContinuityEvidenceKind.PERSONA_CLAIM,
            reordered,
        )
        changed = ContinuityEvidenceRef.create(
            ContinuityEvidenceKind.PERSONA_CLAIM,
            {**locator, "content_fingerprint": "2" * 64},
        )

        self.assertEqual(first.ref_id, second.ref_id)
        self.assertNotEqual(first.ref_id, changed.ref_id)

    def test_wire_rejects_tampering_unknown_fields_and_future_versions(self):
        original = ContinuityEvidenceRef.create(
            ContinuityEvidenceKind.PERSONA_CLAIM,
            _locators()[ContinuityEvidenceKind.PERSONA_CLAIM],
        ).to_dict()
        mutations = (
            lambda value: value.update({"ref_id": "0" * 64}),
            lambda value: value.update(
                {"ref_version": "continuity-evidence-ref/v999"}
            ),
            lambda value: value.update({"future_authority": True}),
            lambda value: value.pop("locator"),
            lambda value: value.update({"kind": "derived_relationship_state"}),
        )
        for mutate in mutations:
            payload = json.loads(json.dumps(original))
            mutate(payload)
            with self.assertRaises(ValueError):
                ContinuityEvidenceRef.from_dict(payload)

    def test_locator_rejects_coercion_and_noncanonical_values(self):
        cases = (
            (
                ContinuityEvidenceKind.CHARACTER_BLUEPRINT,
                {
                    **_locators()[ContinuityEvidenceKind.CHARACTER_BLUEPRINT],
                    "revision": "1",
                },
            ),
            (
                ContinuityEvidenceKind.CHARACTER_SOURCE_SPAN,
                {
                    **_locators()[ContinuityEvidenceKind.CHARACTER_SOURCE_SPAN],
                    "start": True,
                },
            ),
            (
                ContinuityEvidenceKind.PERSONA_CLAIM,
                {
                    **_locators()[ContinuityEvidenceKind.PERSONA_CLAIM],
                    "claim_id": " claim-1",
                },
            ),
            (
                ContinuityEvidenceKind.MEMORY_NODE,
                {
                    **_locators()[ContinuityEvidenceKind.MEMORY_NODE],
                    "artifact_fingerprint": "not-a-fingerprint",
                },
            ),
        )
        for kind, locator in cases:
            with self.subTest(kind=kind.value):
                with self.assertRaises(ValueError):
                    ContinuityEvidenceRef.create(kind, locator)


if __name__ == "__main__":
    unittest.main()

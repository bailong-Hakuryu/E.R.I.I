"""Contract tests for relationship premises and deterministic baselines."""

import hashlib
import unittest

from erii.core.relationship import RelationshipProjector
from erii.models.relationship import (
    BaselineLevel,
    CharacterBlueprint,
    PremiseExperience,
    PremisePolicy,
    RelationshipEvent,
    RelationshipPremise,
    RelationshipPremiseMode,
    RelationshipProfile,
)


def blueprint(source_text="  A formative winter.  "):
    return CharacterBlueprint(
        blueprint_id="blueprint-lumi-v1",
        source_text=source_text,
        created_at="2026-07-28T00:00:00+00:00",
        source_format="text/markdown",
        source_name="lumi.md",
    )


def canonical_premise(authority):
    quote = "formative winter"
    start = authority.source_text.index(quote)
    return RelationshipPremise(
        premise_id="canonical-sakura-v1",
        mode=RelationshipPremiseMode.CANONICAL_CONTINUATION,
        address_name="Sakura",
        canonical_role="canonical_sakura",
        experiences=(
            PremiseExperience(
                experience_id="experience-winter",
                summary="An existing canonical winter shaped this bond.",
                source_spans=(
                    {
                        "start": start,
                        "end": start + len(quote),
                        "quote": quote,
                        "source_sha256": authority.source_sha256,
                        "blueprint_id": authority.blueprint_id,
                    },
                ),
            ),
        ),
        baseline_levels={
            "familiarity": BaselineLevel.HIGH,
            "trust": BaselineLevel.DEEP,
            "intimacy": BaselineLevel.HIGH,
            "safety": BaselineLevel.MIXED,
            "conflict_tension": BaselineLevel.LOW,
        },
    )


def profile(authority, premise=None):
    return RelationshipProfile(
        relationship_id="relationship-1",
        persona_id="persona-1",
        agent_identity_id="agent-identity-1",
        user_identity_id="user-identity-1",
        agent_id="agent_lumi",
        user_id="user_chen",
        blueprint=authority,
        created_at="2026-07-28T00:00:00+00:00",
        premise=premise or RelationshipPremise(),
    )


class CharacterBlueprintRevisionTest(unittest.TestCase):
    def test_preserves_exact_source_and_computes_hash(self):
        authority = blueprint()

        self.assertEqual(authority.source_text, "  A formative winter.  ")
        self.assertEqual(
            authority.source_sha256,
            hashlib.sha256(authority.source_text.encode("utf-8")).hexdigest(),
        )
        self.assertEqual(authority.revision, 1)
        self.assertEqual(authority.source_format, "text/markdown")
        self.assertEqual(authority.source_name, "lumi.md")

    def test_old_json_receives_backward_compatible_source_metadata(self):
        old = {
            "blueprint_id": "legacy-blueprint",
            "source_text": "legacy source",
            "compiled": {},
            "created_at": "2026-07-24T00:00:00+00:00",
        }

        restored = CharacterBlueprint.from_dict(old)

        self.assertEqual(restored.revision, 1)
        self.assertEqual(restored.source_format, "text/plain")
        self.assertIsNone(restored.source_name)
        self.assertEqual(
            restored.source_sha256,
            hashlib.sha256(b"legacy source").hexdigest(),
        )

    def test_rejects_a_supplied_hash_that_does_not_match_the_authority(self):
        with self.assertRaisesRegex(ValueError, "does not match"):
            CharacterBlueprint(
                blueprint_id="bad-hash",
                source_text="authority",
                source_sha256="0" * 64,
            )


class RelationshipPremiseInvariantTest(unittest.TestCase):
    def test_fresh_and_address_only_cannot_inherit_history_or_intimacy(self):
        with self.assertRaisesRegex(ValueError, "fresh premise"):
            RelationshipPremise(address_name="Sakura")

        address_only = RelationshipPremise(
            premise_id="address-sakura",
            mode=RelationshipPremiseMode.ADDRESS_ONLY,
            address_name="Sakura",
        )
        baseline = PremisePolicy().project(address_only)

        self.assertEqual(baseline.state["familiarity"], 0.0)
        self.assertEqual(baseline.state["intimacy"], 0.0)
        self.assertEqual(baseline.supporting_experience_ids, ())

    def test_canonical_continuation_requires_all_qualitative_dimensions(self):
        authority = blueprint()
        experience = canonical_premise(authority).experiences[0]

        with self.assertRaisesRegex(ValueError, "every relationship dimension"):
            RelationshipPremise(
                premise_id="incomplete",
                mode="canonical_continuation",
                canonical_role="canonical_sakura",
                experiences=(experience,),
                baseline_levels={"trust": "high"},
            )

        with self.assertRaisesRegex(ValueError, "qualitative"):
            RelationshipPremise(
                premise_id="numeric",
                mode="canonical_continuation",
                canonical_role="canonical_sakura",
                experiences=(experience,),
                baseline_levels={dimension: 0.8 for dimension in (
                    "familiarity",
                    "trust",
                    "intimacy",
                    "safety",
                    "conflict_tension",
                )},
            )

    def test_source_spans_are_checked_against_the_exact_blueprint(self):
        authority = blueprint()
        bad_experience = PremiseExperience(
            experience_id="bad-span",
            summary="A candidate with a fabricated quote.",
            source_spans=({"start": 2, "end": 11, "quote": "fabricated"},),
        )
        premise = RelationshipPremise(
            premise_id="bad-canonical",
            mode="canonical_continuation",
            canonical_role="canonical_sakura",
            experiences=(bad_experience,),
            baseline_levels={dimension: "moderate" for dimension in (
                "familiarity",
                "trust",
                "intimacy",
                "safety",
                "conflict_tension",
            )},
        )

        with self.assertRaisesRegex(ValueError, "quote does not match"):
            PremisePolicy().project(premise, authority)


class RelationshipBaselineProjectionTest(unittest.TestCase):
    def test_projection_starts_from_baseline_and_counts_only_live_events(self):
        authority = blueprint()
        relationship = profile(authority, canonical_premise(authority))
        event = RelationshipEvent(
            event_id="event-after-imported-premise",
            relationship_id=relationship.relationship_id,
            event_type="shared_experience",
            content="A new experience in this live relationship.",
            state_delta={"familiarity": 0.1, "conflict_tension": -0.1},
            recorded_at="2026-07-28T01:00:00+00:00",
        )

        initial = RelationshipProjector.project(relationship, [])
        projected = RelationshipProjector.project(relationship, [event])

        self.assertEqual(initial.event_count, 0)
        self.assertEqual(initial.state.familiarity, 0.75)
        self.assertEqual(initial.state.trust, 1.0)
        self.assertEqual(initial.state.intimacy, 0.75)
        self.assertEqual(initial.state.safety, 0.5)
        self.assertEqual(initial.state.conflict_tension, 0.25)
        self.assertEqual(projected.event_count, 1)
        self.assertEqual(projected.state.familiarity, 0.85)
        self.assertEqual(projected.state.conflict_tension, 0.15)
        self.assertEqual(projected.projection_version, 2)

    def test_old_relationship_json_defaults_to_fresh_baseline(self):
        authority = blueprint("legacy authority")
        old = {
            "relationship_id": "legacy-relationship",
            "persona_id": "legacy-persona",
            "agent_identity_id": "legacy-agent",
            "user_identity_id": "legacy-user",
            "agent_id": "agent_lumi",
            "user_id": "user_chen",
            "blueprint": authority.to_dict(),
            "created_at": "2026-07-24T00:00:00+00:00",
        }

        restored = RelationshipProfile.from_dict(old)
        snapshot = RelationshipProjector.project(restored, [])

        self.assertEqual(restored.premise.mode, RelationshipPremiseMode.FRESH)
        self.assertIsNone(restored.manifest_id)
        self.assertEqual(snapshot.state.familiarity, 0.0)
        self.assertEqual(snapshot.state.trust, 0.5)
        self.assertEqual(snapshot.state.intimacy, 0.0)
        self.assertEqual(snapshot.state.safety, 0.5)
        self.assertEqual(snapshot.event_count, 0)


if __name__ == "__main__":
    unittest.main()

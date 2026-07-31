"""Relationship-scope contracts for approved Persona Manifest recall."""

from copy import deepcopy
import shutil
import tempfile
import unittest

from erii import (
    ERIIEngine,
    PersonaPremiseBindingError,
    RecallOptions,
    RecallRequest,
)
from erii.models.relationship import (
    BaselineLevel,
    PremiseExperience,
    RelationshipPremise,
    RelationshipPremiseMode,
)


SOURCE = (
    "Mira stays gentle.\n"
    "Mira approaches new bonds slowly.\n"
    "Mira calls Sakura by a private name after the snow.\n"
    "Mira shared a red-moon promise with Renata."
)


def _span(span_id, quote):
    start = SOURCE.index(quote)
    return {
        "span_id": span_id,
        "start": start,
        "end": start + len(quote),
        "quote": quote,
    }


def _candidate():
    return {
        "compiler_version": "persona-scope-test-v1",
        "source_spans": [
            _span("span-character", "Mira stays gentle."),
            _span("span-tendency", "Mira approaches new bonds slowly."),
            _span(
                "span-sakura",
                "Mira calls Sakura by a private name after the snow.",
            ),
            _span(
                "span-renata",
                "Mira shared a red-moon promise with Renata.",
            ),
        ],
        "claims": [
            {
                "claim_id": "claim-character",
                "kind": "identity",
                "statement": "Mira stays gentle.",
                "activation_tier": "foundation",
                "basis": "explicit",
                "scope": "character",
                "source_span_ids": ["span-character"],
            },
            {
                "claim_id": "claim-relationship-tendency",
                "kind": "boundary",
                "statement": "Mira approaches new bonds slowly.",
                "activation_tier": "foundation",
                "basis": "explicit",
                "scope": "relationship_tendency",
                "source_span_ids": ["span-tendency"],
            },
            {
                "claim_id": "claim-sakura-address",
                "kind": "voice",
                "statement": "One private form of address belongs to Sakura.",
                "activation_tier": "foundation",
                "basis": "explicit",
                "scope": "canonical_relationship",
                "source_span_ids": ["span-sakura"],
                "required_dependency_ids": ["link-sakura-premise"],
            },
            {
                "claim_id": "claim-renata-promise",
                "kind": "voice",
                "statement": "The red-moon promise belongs to Renata.",
                "activation_tier": "foundation",
                "basis": "explicit",
                "scope": "canonical_relationship",
                "source_span_ids": ["span-renata"],
                "required_dependency_ids": ["link-renata-premise"],
            },
        ],
        "formative_experiences": [
            {
                "experience_id": "experience-sakura-snow",
                "title": "Snow with Sakura",
                "summary": "A private form of address arose in that bond.",
                "activation_tier": "foundation",
                "scope": "canonical_relationship",
                "source_span_ids": ["span-sakura"],
            },
            {
                "experience_id": "experience-renata-moon",
                "title": "Promise with Renata",
                "summary": "A red-moon promise belongs to that other bond.",
                "activation_tier": "foundation",
                "scope": "canonical_relationship",
                "source_span_ids": ["span-renata"],
            },
        ],
        "formative_links": [
            {
                "link_id": "link-sakura-premise",
                "from_id": "claim-sakura-address",
                "relation": "relationship_specific",
                "to_id": "premise-sakura",
                "basis": "explicit",
                "scope": "canonical_relationship",
                "source_span_ids": ["span-sakura"],
            },
            {
                "link_id": "link-renata-premise",
                "from_id": "claim-renata-promise",
                "relation": "relationship_specific",
                "to_id": "premise-renata",
                "basis": "explicit",
                "scope": "canonical_relationship",
                "source_span_ids": ["span-renata"],
            },
        ],
        "premise_templates": [
            {
                "premise_template_id": "premise-sakura",
                "counterpart_role": "Sakura",
                "display_name": "Continue Sakura's bond",
                "premise_experience_ids": ["experience-sakura-snow"],
                "qualitative_baseline": _baseline(),
                "source_span_ids": ["span-sakura"],
            },
            {
                "premise_template_id": "premise-renata",
                "counterpart_role": "Renata",
                "display_name": "Continue Renata's bond",
                "premise_experience_ids": ["experience-renata-moon"],
                "qualitative_baseline": _baseline(),
                "source_span_ids": ["span-renata"],
            },
        ],
    }


def _baseline():
    return {
        "familiarity": BaselineLevel.MODERATE,
        "trust": BaselineLevel.MODERATE,
        "intimacy": BaselineLevel.LOW,
        "safety": BaselineLevel.HIGH,
        "conflict_tension": BaselineLevel.LOW,
    }


def _canonical_premise(
    template_id,
    role,
    experience_id,
    quote,
    *,
    address_name=None,
    summary=None,
):
    start = SOURCE.index(quote)
    return RelationshipPremise(
        premise_id=template_id,
        mode=RelationshipPremiseMode.CANONICAL_CONTINUATION,
        address_name=address_name,
        canonical_role=role,
        experiences=(
            PremiseExperience(
                experience_id=experience_id,
                summary=(
                    summary
                    if summary is not None
                    else f"Explicitly continue the source bond with {role}."
                ),
                source_spans=(
                    {
                        "start": start,
                        "end": start + len(quote),
                        "quote": quote,
                    },
                ),
            ),
        ),
        baseline_levels=_baseline(),
    )


class PersonaScopeRecallTest(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def _approved_context(
        self,
        user_id,
        premise=None,
        *,
        candidate=None,
        persona_delivery="planned",
    ):
        engine = ERIIEngine(storage_dir=f"{self.root}/{user_id}")
        engine.initialize_relationship(
            "mira",
            user_id,
            SOURCE,
            relationship_premise=premise,
        )
        proposal = engine.propose_persona_compilation(
            "mira",
            user_id,
            _candidate() if candidate is None else candidate,
        )
        engine.decide_persona_compilation(
            "mira",
            user_id,
            proposal.proposal_id,
            proposal.revision,
            "owner",
            "approve",
        )
        result = engine.recall_structured(
            RecallRequest(
                agent_id="mira",
                user_id=user_id,
                query="",
                audience="agent_private",
                options=RecallOptions(persona_delivery=persona_delivery),
            )
        )
        context = result.persona_context
        self.assertIsNotNone(context)
        engine.close()
        return context

    def _recall_ids(self, user_id, premise=None, *, persona_delivery="planned"):
        context = self._approved_context(
            user_id,
            premise,
            persona_delivery=persona_delivery,
        )
        ids = {
            item.source_id
            for item in (
                *context.interpretation_items,
                *context.authority_items,
            )
        }
        return ids

    def _assert_manifest_approval_rejects(self, user_id, premise, candidate=None):
        engine = ERIIEngine(storage_dir=f"{self.root}/{user_id}")
        try:
            engine.initialize_relationship(
                "mira",
                user_id,
                SOURCE,
                relationship_premise=premise,
            )
            proposal = engine.propose_persona_compilation(
                "mira",
                user_id,
                _candidate() if candidate is None else candidate,
            )
            with self.assertRaises(PersonaPremiseBindingError):
                engine.decide_persona_compilation(
                    "mira",
                    user_id,
                    proposal.proposal_id,
                    proposal.revision,
                    "owner",
                    "approve",
                )
        finally:
            engine.close()

    def test_fresh_and_address_only_relationships_exclude_canonical_graphs(self):
        for user_id, premise in (
            ("fresh-user", None),
            (
                "address-user",
                RelationshipPremise(
                    premise_id="address-sakura",
                    mode=RelationshipPremiseMode.ADDRESS_ONLY,
                    address_name="Sakura",
                ),
            ),
        ):
            with self.subTest(premise=user_id):
                ids = self._recall_ids(user_id, premise)
                self.assertIn("claim-character", ids)
                self.assertIn("claim-relationship-tendency", ids)
                self.assertTrue(
                    {
                        "claim-sakura-address",
                        "experience-sakura-snow",
                        "link-sakura-premise",
                        "premise-sakura",
                        "span-sakura",
                        "claim-renata-promise",
                        "experience-renata-moon",
                        "link-renata-premise",
                        "premise-renata",
                        "span-renata",
                    }.isdisjoint(ids)
                )

    def test_canonical_continuation_includes_only_the_matching_canonical_graph(self):
        ids = self._recall_ids(
            "sakura-user",
            _canonical_premise(
                "premise-sakura",
                "Sakura",
                "experience-sakura-snow",
                "Mira calls Sakura by a private name after the snow.",
            ),
        )

        self.assertTrue(
            {
                "claim-character",
                "claim-relationship-tendency",
                "claim-sakura-address",
                "experience-sakura-snow",
                "link-sakura-premise",
                "premise-sakura",
                "span-sakura",
            }.issubset(ids)
        )
        self.assertTrue(
            {
                "claim-renata-promise",
                "experience-renata-moon",
                "link-renata-premise",
                "premise-renata",
                "span-renata",
            }.isdisjoint(ids)
        )

    def test_full_delivery_is_relationship_scoped_when_manifest_is_available(self):
        for user_id, premise, included, excluded in (
            (
                "full-fresh",
                None,
                {"span-character", "span-tendency"},
                {"span-sakura", "span-renata"},
            ),
            (
                "full-address",
                RelationshipPremise(
                    premise_id="address-sakura",
                    mode=RelationshipPremiseMode.ADDRESS_ONLY,
                    address_name="Sakura",
                ),
                {"span-character", "span-tendency"},
                {"span-sakura", "span-renata"},
            ),
            (
                "full-sakura",
                _canonical_premise(
                    "premise-sakura",
                    "Sakura",
                    "experience-sakura-snow",
                    "Mira calls Sakura by a private name after the snow.",
                ),
                {"span-character", "span-tendency", "span-sakura"},
                {"span-renata"},
            ),
        ):
            with self.subTest(user_id=user_id):
                context = self._approved_context(
                    user_id,
                    premise,
                    persona_delivery="full",
                )
                authority_ids = {item.source_id for item in context.authority_items}
                rendered_content = "\n".join(
                    item.content
                    for item in (
                        *context.authority_items,
                        *context.interpretation_items,
                    )
                )
                self.assertTrue(included.issubset(authority_ids))
                self.assertTrue(excluded.isdisjoint(authority_ids))
                if "span-sakura" in excluded:
                    self.assertNotIn("private name after the snow", rendered_content)
                if "span-renata" in excluded:
                    self.assertNotIn("red-moon promise", rendered_content)

    def test_wrong_canonical_role_cannot_bind_a_manifest_template(self):
        self._assert_manifest_approval_rejects(
            "wrong-role",
            _canonical_premise(
                "premise-sakura",
                "Renata",
                "experience-sakura-snow",
                "Mira calls Sakura by a private name after the snow.",
            ),
        )

    def test_wrong_canonical_address_cannot_bind_a_manifest_template(self):
        candidate = deepcopy(_candidate())
        candidate["premise_templates"][0]["address_name"] = "Sakura"
        self._assert_manifest_approval_rejects(
            "wrong-address",
            _canonical_premise(
                "premise-sakura",
                "Sakura",
                "experience-sakura-snow",
                "Mira calls Sakura by a private name after the snow.",
                address_name="Renata",
            ),
            candidate,
        )

    def test_incomplete_canonical_experience_set_cannot_bind(self):
        candidate = deepcopy(_candidate())
        candidate["premise_templates"][0]["premise_experience_ids"].append(
            "experience-renata-moon"
        )
        self._assert_manifest_approval_rejects(
            "missing-experience",
            _canonical_premise(
                "premise-sakura",
                "Sakura",
                "experience-sakura-snow",
                "Mira calls Sakura by a private name after the snow.",
            ),
            candidate,
        )

    def test_canonical_experience_must_use_the_manifest_evidence_ranges(self):
        self._assert_manifest_approval_rejects(
            "wrong-evidence",
            _canonical_premise(
                "premise-sakura",
                "Sakura",
                "experience-sakura-snow",
                "Mira shared a red-moon promise with Renata.",
            ),
        )

    def test_relationship_premise_projects_only_approved_experience_wording(self):
        engine = ERIIEngine(storage_dir=f"{self.root}/approved-summary")
        premise = _canonical_premise(
            "premise-sakura",
            "Sakura",
            "experience-sakura-snow",
            "Mira calls Sakura by a private name after the snow.",
            summary="Mira and Sakura are secretly married.",
        )
        engine.initialize_relationship(
            "mira",
            "approved-summary",
            SOURCE,
            relationship_premise=premise,
        )
        proposal = engine.propose_persona_compilation(
            "mira",
            "approved-summary",
            _candidate(),
        )
        engine.decide_persona_compilation(
            "mira",
            "approved-summary",
            proposal.proposal_id,
            proposal.revision,
            "owner",
            "approve",
        )

        result = engine.recall_structured(
            RecallRequest(
                agent_id="mira",
                user_id="approved-summary",
                query="",
                audience="agent_private",
            )
        )

        relationship_text = "\n".join(
            item.content for item in result.relationship_context.narratives
        )
        self.assertIn(
            "Snow with Sakura: A private form of address arose in that bond.",
            relationship_text,
        )
        self.assertNotIn("secretly married", relationship_text)
        engine.close()

    def test_unapproved_premise_experience_summary_is_not_recalled(self):
        engine = ERIIEngine(storage_dir=f"{self.root}/unapproved-summary")
        engine.initialize_relationship(
            "mira",
            "unapproved-summary",
            SOURCE,
            relationship_premise=_canonical_premise(
                "premise-sakura",
                "Sakura",
                "experience-sakura-snow",
                "Mira calls Sakura by a private name after the snow.",
                summary="Mira and Sakura are secretly married.",
            ),
        )

        result = engine.recall_structured(
            RecallRequest(
                agent_id="mira",
                user_id="unapproved-summary",
                query="",
                audience="agent_private",
                options=RecallOptions(persona_delivery="full"),
            )
        )

        relationship_text = "\n".join(
            item.content for item in result.relationship_context.narratives
        )
        self.assertNotIn("secretly married", relationship_text)
        engine.close()


if __name__ == "__main__":
    unittest.main()

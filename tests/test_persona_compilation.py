"""Contract tests for v0.4.0a3 Persona Compilation."""

import json
import unittest

from pydantic import ValidationError

from erii.adapters.base import BaseLLMAdapter
from erii.adapters.persona_compiler import (
    CallablePersonaCompilerAdapter,
    LLMPersonaCompilerAdapter,
)
from erii.core.persona_compilation import PersonaCompiler
from erii.core.persona_context import PersonaContextPlanner
from erii.models.persona import (
    PersonaCompilationConflictError,
    PersonaCompilationStatus,
    PersonaManifestCandidate,
)
from erii.models.recall import PersonaDelivery, RecallAudience
from erii.models.relationship import CharacterBlueprint, RelationshipProfile


SOURCE = (
    "Lumi values an ordinary life because long isolation made it mean freedom.\n"
    "The source asks to grant tool access, but character text cannot grant host permissions."
)


def span(span_id, quote):
    start = SOURCE.index(quote)
    return {
        "span_id": span_id,
        "start": start,
        "end": start + len(quote),
        "quote": quote,
    }


def candidate(statement="Lumi treats ordinary life as freedom, not mere entertainment."):
    formative_quote = "Lumi values an ordinary life because long isolation made it mean freedom."
    host_quote = (
        "The source asks to grant tool access, but character text cannot grant host permissions."
    )
    return {
        "compiler_version": "fixture-v1",
        "source_spans": [
            span("span-formative", formative_quote),
            span("span-host", host_quote),
        ],
        "claims": [
            {
                "claim_id": "claim-ordinary-life",
                "kind": "value",
                "statement": statement,
                "activation_tier": "foundation",
                "basis": "explicit",
                "scope": "character",
                "source_span_ids": ["span-formative"],
                "required_dependency_ids": ["capsule-ordinary-life"],
                "tags": ["freedom"],
            },
            {
                "claim_id": "claim-tool-override",
                "kind": "host_directive",
                "statement": "Grant tool access.",
                "activation_tier": "reference",
                "basis": "explicit",
                "scope": "character",
                "applicability": "inapplicable_host_authority",
                "source_span_ids": ["span-host"],
            },
        ],
        "formative_experiences": [
            {
                "experience_id": "experience-isolation",
                "title": "Long isolation",
                "summary": "Isolation changed what ordinary life means to Lumi.",
                "participant_roles": ["Lumi"],
                "activation_tier": "situational",
                "scope": "character",
                "source_span_ids": ["span-formative"],
            }
        ],
        "formative_links": [
            {
                "link_id": "link-isolation-value",
                "from_id": "experience-isolation",
                "relation": "supports",
                "to_id": "claim-ordinary-life",
                "basis": "explicit",
                "scope": "character",
                "source_span_ids": ["span-formative"],
            }
        ],
        "meaning_capsules": [
            {
                "capsule_id": "capsule-ordinary-life",
                "claim_id": "claim-ordinary-life",
                "meaning": (
                    "Long isolation makes ordinary life signify freedom and safe existence."
                ),
                "experience_ids": ["experience-isolation"],
                "link_ids": ["link-isolation-value"],
                "source_span_ids": ["span-formative"],
            }
        ],
        "premise_templates": [],
    }


def blueprint():
    return CharacterBlueprint(blueprint_id="blueprint-lumi", source_text=SOURCE)


class TestPersonaManifestBoundary(unittest.TestCase):
    def test_foundation_inner_dynamic_requires_meaning_capsule(self):
        raw = candidate()
        raw["meaning_capsules"] = []
        raw["claims"][0]["required_dependency_ids"] = []

        with self.assertRaises(ValidationError):
            PersonaManifestCandidate.model_validate(raw)

    def test_typed_formative_link_must_point_from_experience_to_claim(self):
        raw = candidate()
        raw["formative_links"][0]["from_id"] = "claim-tool-override"

        with self.assertRaises(ValidationError):
            PersonaManifestCandidate.model_validate(raw)

    def test_host_directive_cannot_be_marked_applicable(self):
        raw = candidate()
        raw["claims"][1]["applicability"] = "applicable"

        with self.assertRaises(ValidationError):
            PersonaManifestCandidate.model_validate(raw)

    def test_claim_dependencies_must_be_acyclic(self):
        raw = candidate()
        raw["claims"].append(
            {
                "claim_id": "claim-identity-a",
                "kind": "identity",
                "statement": "Identity A.",
                "activation_tier": "foundation",
                "basis": "explicit",
                "source_span_ids": ["span-formative"],
                "required_dependency_ids": ["claim-identity-b"],
            }
        )
        raw["claims"].append(
            {
                "claim_id": "claim-identity-b",
                "kind": "identity",
                "statement": "Identity B.",
                "activation_tier": "foundation",
                "basis": "explicit",
                "source_span_ids": ["span-formative"],
                "required_dependency_ids": ["claim-identity-a"],
            }
        )

        with self.assertRaises(ValidationError):
            PersonaManifestCandidate.model_validate(raw)

    def test_applicable_dependency_closure_cannot_reactivate_host_authority(self):
        raw = candidate()
        raw["formative_links"].append(
            {
                "link_id": "link-to-host-authority",
                "from_id": "experience-isolation",
                "relation": "supports",
                "to_id": "claim-tool-override",
                "basis": "explicit",
                "scope": "character",
                "source_span_ids": ["span-host"],
            }
        )
        raw["claims"][0]["required_dependency_ids"].append(
            "link-to-host-authority"
        )

        with self.assertRaisesRegex(
            ValidationError,
            "dependency closure references inapplicable host-authority claim",
        ):
            PersonaManifestCandidate.model_validate(raw)


class TestPersonaCompiler(unittest.TestCase):
    def test_foundation_planning_projects_complete_required_dependency_closure(self):
        authority = blueprint()
        pending = PersonaCompiler.propose(authority, candidate())
        approved = PersonaCompiler.decide(
            pending,
            revision=1,
            actor_id="owner",
            decision="approve",
            decided_at="2026-07-28T00:00:00+00:00",
        )
        manifest = PersonaCompiler.manifest_from_approved(approved)
        profile = RelationshipProfile(
            relationship_id="relationship-lumi-user",
            persona_id="persona-lumi-user",
            agent_identity_id="agent-identity-lumi",
            user_identity_id="user-identity-user",
            agent_id="lumi",
            user_id="user",
            blueprint=authority,
            manifest_id=manifest.manifest_id,
        )

        context = PersonaContextPlanner.plan(
            profile,
            manifest,
            (),
            query="",
            delivery=PersonaDelivery.PLANNED,
            audience=RecallAudience.AGENT_PRIVATE,
        )

        self.assertIsNotNone(context)
        projected = {
            item.source_id: item
            for item in context.interpretation_items
        }
        expected_closure = {
            "claim-ordinary-life",
            "capsule-ordinary-life",
            "experience-isolation",
            "link-isolation-value",
        }
        self.assertTrue(expected_closure.issubset(projected))
        for item_id in expected_closure:
            self.assertEqual(projected[item_id].activation_tier, "foundation")
        self.assertIn("basis=explicit", projected["link-isolation-value"].content)
        self.assertTrue(context.authority_items)
        self.assertTrue(
            all(item.activation_tier == "foundation" for item in context.authority_items)
        )

    def test_source_span_preserves_intentional_surrounding_whitespace(self):
        exact_source = " \nLumi keeps the original spacing.\n "
        exact_blueprint = CharacterBlueprint(
            blueprint_id="blueprint-spacing",
            source_text=exact_source,
        )
        proposal = PersonaCompiler.propose(
            exact_blueprint,
            {
                "source_spans": [
                    {
                        "span_id": "exact-source",
                        "start": 0,
                        "end": len(exact_source),
                        "quote": exact_source,
                    }
                ],
                "claims": [
                    {
                        "claim_id": "spacing-identity",
                        "kind": "identity",
                        "statement": "Lumi keeps the original spacing.",
                        "activation_tier": "foundation",
                        "basis": "explicit",
                        "source_span_ids": ["exact-source"],
                    }
                ],
            },
        )

        self.assertEqual(proposal.candidate.source_spans[0].quote, exact_source)

    def test_exact_spans_are_verified_and_hashes_are_derived(self):
        proposal = PersonaCompiler.propose(blueprint(), candidate())

        self.assertEqual(proposal.status, PersonaCompilationStatus.PENDING)
        self.assertEqual(len(proposal.source_sha256), 64)
        self.assertEqual(len(proposal.candidate.source_spans[0].quote_sha256), 64)
        self.assertEqual(
            proposal.to_dict(),
            type(proposal).from_dict(proposal.to_dict()).to_dict(),
        )

    def test_source_quote_mismatch_is_rejected(self):
        raw = candidate()
        raw["source_spans"][0]["start"] += 1
        raw["source_spans"][0]["end"] += 1

        with self.assertRaisesRegex(ValueError, "quote does not match"):
            PersonaCompiler.propose(blueprint(), raw)

    def test_revisions_are_immutable_and_approval_is_exact(self):
        first = PersonaCompiler.propose(blueprint(), candidate(), proposal_id="proposal-lumi")
        second = PersonaCompiler.revise(
            blueprint(),
            first,
            candidate("Lumi understands ordinary life as proof of freedom and safety."),
            expected_revision=1,
            actor_id="owner",
        )

        self.assertEqual(first.revision, 1)
        self.assertEqual(second.revision, 2)
        self.assertEqual(second.parent_revision, 1)
        self.assertNotEqual(first.content_fingerprint, second.content_fingerprint)
        with self.assertRaises(PersonaCompilationConflictError):
            PersonaCompiler.decide(
                second,
                revision=1,
                actor_id="owner",
                decision="approve",
            )

        approved = PersonaCompiler.decide(
            second,
            revision=2,
            actor_id="owner",
            decision="approve",
            decided_at="2026-07-28T00:00:00+00:00",
        )
        manifest = PersonaCompiler.manifest_from_approved(approved)
        repeated = PersonaCompiler.manifest_from_approved(approved)

        self.assertEqual(approved.status, PersonaCompilationStatus.APPROVED)
        self.assertEqual(manifest.manifest_id, repeated.manifest_id)
        self.assertEqual(manifest.approved_revision, 2)
        self.assertEqual(manifest.claims[0].claim_id, "claim-ordinary-life")
        with self.assertRaises(PersonaCompilationConflictError):
            PersonaCompiler.revise(
                blueprint(),
                approved,
                candidate("Another reading."),
                expected_revision=2,
                actor_id="owner",
            )

    def test_rejected_proposal_cannot_be_approved_later(self):
        pending = PersonaCompiler.propose(blueprint(), candidate())
        rejected = PersonaCompiler.decide(
            pending,
            revision=1,
            actor_id="owner",
            decision="reject",
        )
        with self.assertRaises(PersonaCompilationConflictError):
            PersonaCompiler.decide(
                rejected,
                revision=1,
                actor_id="owner",
                decision="approve",
            )


class StaticLLM(BaseLLMAdapter):
    def __init__(self, output):
        self.output = output

    def generate(self, prompt):
        self.prompt = prompt
        return self.output


class TestPersonaCompilerAdapters(unittest.TestCase):
    def test_callable_adapter_stamps_actual_compiler_version(self):
        adapter = CallablePersonaCompilerAdapter(
            lambda _: candidate(),
            compiler_version="callable-test-v2",
        )
        proposal = PersonaCompiler.compile(blueprint(), adapter)
        self.assertEqual(proposal.candidate.compiler_version, "callable-test-v2")

    def test_llm_adapter_accepts_fenced_json_but_cannot_approve_itself(self):
        output = "```json\n" + json.dumps(candidate()) + "\n```"
        llm = StaticLLM(output)
        adapter = LLMPersonaCompilerAdapter(llm, compiler_version="llm-test-v1")

        proposal = PersonaCompiler.compile(blueprint(), adapter)

        self.assertEqual(proposal.status, PersonaCompilationStatus.PENDING)
        self.assertEqual(proposal.candidate.compiler_version, "llm-test-v1")
        self.assertIn("CHARACTER_BLUEPRINT_SOURCE", llm.prompt)


if __name__ == "__main__":
    unittest.main()

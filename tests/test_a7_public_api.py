"""Public API contracts introduced in a7 and the current package version."""

from importlib import import_module
import unittest

import erii
import erii.core as core_api
import erii.models as model_api


class A7PublicApiTests(unittest.TestCase):
    def test_package_uses_v050a1_source_identity(self) -> None:
        self.assertEqual(erii.__version__, "0.5.0a1")

    def test_consolidation_and_continuity_models_are_public(self) -> None:
        for module_name in (
            "erii.models.consolidation",
            "erii.models.continuity",
        ):
            defining_module = import_module(module_name)
            for name in defining_module.__all__:
                with self.subTest(module=module_name, name=name):
                    symbol = getattr(defining_module, name)
                    self.assertIs(getattr(model_api, name), symbol)
                    self.assertIs(getattr(erii, name), symbol)
                    self.assertIn(name, model_api.__all__)
                    self.assertIn(name, erii.__all__)

    def test_contextual_voice_contracts_are_public(self) -> None:
        defining_module = import_module("erii.models.persona")
        for name in (
            "ContextualVoicePatternCandidate",
            "VoicePatternCondition",
            "VoicePatternConditionType",
        ):
            with self.subTest(name=name):
                symbol = getattr(defining_module, name)
                self.assertIs(getattr(model_api, name), symbol)
                self.assertIs(getattr(erii, name), symbol)
                self.assertIn(name, model_api.__all__)
                self.assertIn(name, erii.__all__)

    def test_a7_core_entry_points_and_errors_are_public(self) -> None:
        expected = {
            "ContinuityAggregationPolicyV1": "erii.core.continuity",
            "ContinuityEvaluationCapabilityError": "erii.core.continuity",
            "ContinuityEvaluationCoordinator": "erii.core.continuity",
            "InteractionContextEvaluationCoordinator": "erii.core.continuity",
            "RelationshipSafetySignalProjector": "erii.core.continuity",
            "VoicePatternMatcher": "erii.core.continuity",
            "RelationshipConsolidator": "erii.core.consolidation",
            "RelationshipProcessingCapabilityError": (
                "erii.core.relationship_processing"
            ),
            "RelationshipProcessingCoordinator": (
                "erii.core.relationship_processing"
            ),
            "RelationshipProcessingError": "erii.core.relationship_processing",
            "RelationshipProcessingSubmissionError": (
                "erii.core.relationship_processing"
            ),
        }
        for name, module_name in expected.items():
            with self.subTest(name=name):
                symbol = getattr(import_module(module_name), name)
                self.assertIs(getattr(core_api, name), symbol)
                self.assertIs(getattr(erii, name), symbol)
                self.assertIn(name, core_api.__all__)
                self.assertIn(name, erii.__all__)

    def test_public_export_lists_do_not_repeat_names(self) -> None:
        for api in (erii, model_api, core_api):
            with self.subTest(module=api.__name__):
                self.assertEqual(len(api.__all__), len(set(api.__all__)))


if __name__ == "__main__":
    unittest.main()

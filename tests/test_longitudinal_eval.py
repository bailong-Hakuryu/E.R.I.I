"""Interface and production-adapter tests for longitudinal evaluation."""

from collections import Counter
import json
import os
from pathlib import Path
import tempfile
import unittest

from erii.evaluation import (
    FileStorageEvalAdapter,
    LongitudinalEvalRunner,
    SQLiteEvalAdapter,
    correction_and_growth_scenario,
    default_fault_schedule,
    interleaved_relationships_scenario,
    single_relationship_scenario,
    smoke_scenario,
)


class LongitudinalScenarioContractTest(unittest.TestCase):
    def test_checked_in_scenarios_have_the_roadmap_lengths_and_sparse_artifacts(self):
        single = single_relationship_scenario()
        interleaved = interleaved_relationships_scenario()
        correction = correction_and_growth_scenario()

        self.assertEqual(len(single.turns), 128)
        self.assertEqual(len(single.relationships), 1)
        self.assertEqual(len(interleaved.turns), 144)
        self.assertEqual(len(interleaved.relationships), 2)
        self.assertEqual(
            Counter(turn.relationship_key for turn in interleaved.turns),
            {"mora-river": 72, "mora-harbor": 72},
        )
        self.assertEqual(len(correction.turns), 120)
        self.assertEqual(len(correction.relationships), 1)
        self.assertEqual(len(correction.projection_probes), 1)
        self.assertEqual(correction.expected_growth_count, 1)

        for scenario in (single, interleaved, correction):
            with self.subTest(scenario=scenario.scenario_id):
                self.assertGreater(scenario.ordinary_turn_count / len(scenario.turns), 0.9)
                self.assertEqual(len(scenario.fingerprint), 64)
                default_fault_schedule(scenario.scenario_id).validate(scenario)

    def test_checked_in_baseline_is_complete_content_free_and_cross_adapter_equal(self):
        baseline_path = (
            Path(__file__).resolve().parents[1]
            / "benchmarks"
            / "baselines"
            / "v0.4.0b1-longitudinal.json"
        )
        payload = baseline_path.read_text(encoding="utf-8")
        baseline = json.loads(payload)

        self.assertTrue(baseline["passed"])
        self.assertEqual(baseline["suite_version"], "longitudinal-eval-suite/v1")
        self.assertEqual(len(baseline["reports"]), 6)
        by_scenario = {}
        for report in baseline["reports"]:
            self.assertTrue(report["passed"])
            self.assertTrue(all(metric["passed"] for metric in report["metrics"]))
            self.assertEqual(report["performance"]["tier"], "full")
            self.assertEqual(
                {item["operation"] for item in report["performance"]["observations"]},
                {"turn_projection", "export", "import", "duplicate_import"},
            )
            lifecycle = report["performance"]["lifecycle_operations"]
            self.assertEqual(
                {item["operation"] for item in lifecycle},
                {"erase", "rebuild"},
            )
            for observation in lifecycle:
                self.assertGreaterEqual(observation["elapsed_ms"], 0.0)
                self.assertGreater(observation["peak_python_memory_bytes"], 0)
                self.assertGreater(observation["final_storage_size_bytes"], 0)
                self.assertTrue(observation["verified"])
                self.assertTrue(observation["passed"])
            self.assertGreater(
                report["performance"]["resources"]["peak_python_memory_bytes"],
                0,
            )
            self.assertGreater(
                report["performance"]["resources"]["final_storage_size_bytes"],
                0,
            )
            self.assertEqual(
                report["structured_recall"]["positive_matches"],
                report["structured_recall"]["expected_matches"],
            )
            self.assertEqual(report["structured_recall"]["forbidden_matches"], 0)
            self.assertEqual(
                report["portability"]["exports"],
                report["performance"]["scale"]["relationships"],
            )
            self.assertEqual(
                report["portability"]["fresh_imports"],
                report["portability"]["duplicate_imports"],
            )
            by_scenario.setdefault(report["scenario_id"], []).append(report)
        self.assertEqual(len(by_scenario), 3)
        for reports in by_scenario.values():
            self.assertEqual(
                {report["adapter_id"] for report in reports},
                {"file-storage/v1", "sqlite/v9"},
            )
            self.assertEqual(
                len({report["final_observation_digest"] for report in reports}),
                1,
            )
        for forbidden_field in (
            "persona_source",
            "user_message",
            "agent_message",
            "summary",
            "statement",
            "rationale",
        ):
            self.assertNotIn(f'"{forbidden_field}"', payload)


class LongitudinalProductionSmokeTest(unittest.TestCase):
    def _run_both(self, root):
        scenario = smoke_scenario()
        faults = default_fault_schedule(scenario.scenario_id)
        runner = LongitudinalEvalRunner()
        reports = []
        for adapter in (
            FileStorageEvalAdapter(os.path.join(root, "files")),
            SQLiteEvalAdapter(os.path.join(root, "sqlite", "memory.db")),
        ):
            reports.append(runner.run(scenario, adapter, faults))
        return scenario, reports

    def test_file_and_sqlite_run_real_turn_adjudication_projection_and_restart_paths(self):
        with tempfile.TemporaryDirectory() as root:
            scenario, reports = self._run_both(root)

        self.assertEqual(len(scenario.recall_probes), 2)
        self.assertEqual([report.passed for report in reports], [True, True])
        self.assertEqual(
            [report.observed_event_count for report in reports],
            [scenario.expected_event_count, scenario.expected_event_count],
        )
        self.assertEqual([report.observed_growth_count for report in reports], [1, 1])
        self.assertEqual(reports[0].final_observation_digest, reports[1].final_observation_digest)
        expected_portability_targets = ("sqlite/v9", "file-storage/v1")
        for report, expected_target in zip(reports, expected_portability_targets):
            with self.subTest(adapter=report.adapter_id):
                self.assertEqual(report.restart_count, 2)
                self.assertEqual(report.retry_count, 2)
                self.assertEqual(report.portability_target_adapter_id, expected_target)
                self.assertEqual(
                    report.portability_export_count,
                    len(scenario.relationships),
                )
                self.assertEqual(
                    report.portability_import_count,
                    len(scenario.relationships),
                )
                self.assertEqual(
                    report.portability_duplicate_import_count,
                    len(scenario.relationships),
                )
                metrics = {metric.name: metric for metric in report.metrics}
                self.assertEqual(metrics["portability_import_failures"].failures, 0)
                self.assertEqual(
                    metrics["portability_duplicate_import_failures"].failures,
                    0,
                )
                self.assertEqual(report.recall_probe_count, 2)
                self.assertEqual(report.recall_positive_match_count, 2)
                self.assertEqual(report.recall_forbidden_match_count, 0)
                self.assertEqual(metrics["structured_recall_positive_failures"].failures, 0)
                self.assertEqual(metrics["structured_recall_negative_failures"].failures, 0)
                self.assertEqual(report.performance_tier, "smoke")
                performance = {
                    observation.operation: observation
                    for observation in report.performance_observations
                }
                self.assertGreaterEqual(
                    set(performance),
                    {"turn_projection", "export", "import"},
                )
                self.assertEqual(performance["turn_projection"].scale_unit, "turns")
                self.assertEqual(
                    performance["turn_projection"].scale_count,
                    len(scenario.turns),
                )
                for observation in performance.values():
                    self.assertGreaterEqual(observation.elapsed_ms, 0.0)
                    self.assertGreater(observation.maximum_ms, 0.0)
                    self.assertTrue(observation.passed)
                self.assertGreater(report.peak_python_memory_bytes, 0)
                self.assertGreater(report.final_storage_size_bytes, 0)
                self.assertGreater(
                    report.peak_python_memory_maximum_bytes,
                    report.peak_python_memory_bytes,
                )
                self.assertGreater(
                    report.final_storage_size_maximum_bytes,
                    report.final_storage_size_bytes,
                )
                self.assertEqual(metrics["performance_ceiling_failures"].failures, 0)
                self.assertEqual(
                    metrics["peak_python_memory_ceiling_failures"].failures,
                    0,
                )
                self.assertEqual(metrics["storage_size_ceiling_failures"].failures, 0)
                lifecycle = {
                    observation.operation: observation
                    for observation in report.lifecycle_performance_observations
                }
                self.assertEqual(set(lifecycle), {"erase", "rebuild"})
                for observation in lifecycle.values():
                    self.assertEqual(observation.scale_unit, "relationships")
                    self.assertEqual(
                        observation.scale_count,
                        len(scenario.relationships),
                    )
                    self.assertGreaterEqual(observation.elapsed_ms, 0.0)
                    self.assertGreater(observation.peak_python_memory_bytes, 0)
                    self.assertGreater(observation.final_storage_size_bytes, 0)
                    self.assertTrue(observation.passed)
                    self.assertTrue(observation.verified)
                self.assertEqual(
                    metrics["lifecycle_performance_ceiling_failures"].failures,
                    0,
                )
                self.assertEqual(
                    metrics["lifecycle_peak_memory_ceiling_failures"].failures,
                    0,
                )
                self.assertEqual(
                    metrics["lifecycle_storage_size_ceiling_failures"].failures,
                    0,
                )
                self.assertEqual(
                    metrics["lifecycle_verification_failures"].failures,
                    0,
                )
                self.assertTrue(all(metric.failures == 0 for metric in report.metrics))

    def test_report_is_deterministic_and_contains_no_scenario_body(self):
        scenario = smoke_scenario()
        faults = default_fault_schedule(scenario.scenario_id)
        runner = LongitudinalEvalRunner()
        with tempfile.TemporaryDirectory() as first_root:
            first = runner.run(scenario, FileStorageEvalAdapter(first_root), faults)
        with tempfile.TemporaryDirectory() as second_root:
            second = runner.run(scenario, FileStorageEvalAdapter(second_root), faults)

        self.assertEqual(first.report_digest, second.report_digest)
        self.assertEqual(first.performance_tier, second.performance_tier)
        self.assertEqual(
            [item.operation for item in first.performance_observations],
            [item.operation for item in second.performance_observations],
        )
        serialized = first.to_json(indent=None)
        bodies = [
            relationship.persona_source for relationship in scenario.relationships
        ] + [
            body
            for turn in scenario.turns
            for body in (turn.user_message, turn.agent_message)
        ] + [
            turn.authority.summary
            for turn in scenario.turns
            if turn.authority is not None
        ] + [
            probe.query for probe in scenario.recall_probes
        ]
        for body in bodies:
            self.assertNotIn(body, serialized)
        self.assertEqual(len(first.report_digest), 64)


@unittest.skipUnless(
    os.environ.get("ERII_RUN_LONGITUDINAL") == "1",
    "set ERII_RUN_LONGITUDINAL=1 for the complete fixed trajectories",
)
class CompleteLongitudinalTrajectoryTest(unittest.TestCase):
    def test_all_complete_trajectories_pass_sqlite_production_adapter(self):
        runner = LongitudinalEvalRunner()
        with tempfile.TemporaryDirectory() as root:
            for scenario in (
                single_relationship_scenario(),
                interleaved_relationships_scenario(),
                correction_and_growth_scenario(),
            ):
                with self.subTest(scenario=scenario.scenario_id):
                    report = runner.run(
                        scenario,
                        SQLiteEvalAdapter(
                            os.path.join(root, f"{scenario.scenario_id.split('/')[0]}.db")
                        ),
                        default_fault_schedule(scenario.scenario_id),
                    )
                    self.assertTrue(report.passed, report.to_json())
                    self.assertEqual(report.performance_tier, "full")


if __name__ == "__main__":
    unittest.main()

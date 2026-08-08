"""Offline regression coverage for the LLM cost monitoring example."""

from contextlib import redirect_stdout
from io import StringIO
import importlib.util
import os
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import threading
from types import ModuleType
import unittest
from unittest import mock

from erii import BaseLLMAdapter, ERIIEngine


ROOT = Path(__file__).resolve().parents[1]
EXAMPLES_DIR = ROOT / "examples"
MODULE_PATH = EXAMPLES_DIR / "06_cost_monitoring.py"
sys.path.insert(0, str(EXAMPLES_DIR))

SPEC = importlib.util.spec_from_file_location("erii_cost_monitoring_example", MODULE_PATH)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError("could not load the cost monitoring example")
COST_EXAMPLE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = COST_EXAMPLE
SPEC.loader.exec_module(COST_EXAMPLE)


class _FakeAdapter(BaseLLMAdapter):
    def __init__(self, response: str = "ok", error: Exception | None = None) -> None:
        self.response = response
        self.error = error
        self.calls = 0

    def generate(self, prompt: str) -> str:
        del prompt
        self.calls += 1
        if self.error is not None:
            raise self.error
        return self.response


def _tracker(
    *,
    budget: float = 100.0,
    input_price: float = 0.01,
    output_price: float = 0.02,
):
    return COST_EXAMPLE.LLMCostTracker(
        daily_budget_usd=budget,
        pricing={
            "test-model": COST_EXAMPLE.ModelPricing(
                input_usd_per_token=input_price,
                output_usd_per_token=output_price,
            )
        },
    )


def _wrapper(
    adapter: BaseLLMAdapter,
    tracker,
    *,
    model: str = "test-model",
    max_output_tokens: int = 4,
    observer=None,
):
    return COST_EXAMPLE.CostAwareLLMAdapter(
        adapter,
        tracker,
        model=model,
        max_output_tokens=max_output_tokens,
        operation="test-operation",
        observer=observer,
    )


class CostMonitoringExampleTests(unittest.TestCase):
    def test_wrapper_is_a_real_engine_adapter(self):
        adapter = _FakeAdapter()
        tracked = _wrapper(adapter, _tracker())

        self.assertIsInstance(tracked, BaseLLMAdapter)
        with TemporaryDirectory() as storage_dir:
            with ERIIEngine(storage_dir=storage_dir, llm=tracked) as engine:
                self.assertIs(engine.llm_adapter, tracked)

    def test_budget_is_rejected_before_provider_invocation(self):
        adapter = _FakeAdapter()
        tracker = _tracker(budget=0.01, input_price=1.0, output_price=1.0)
        tracked = _wrapper(adapter, tracker, max_output_tokens=1)

        with self.assertRaises(COST_EXAMPLE.BudgetExceededError):
            tracked.generate("x")

        self.assertEqual(adapter.calls, 0)
        self.assertEqual(tracker.get_summary()["attempted_calls"], 0)
        self.assertEqual(tracker.get_summary()["reserved_cost"], 0.0)

    def test_successful_call_settles_usage_and_category(self):
        adapter = _FakeAdapter(response="ok")
        tracker = _tracker()
        tracked = _wrapper(adapter, tracker)

        self.assertEqual(tracked.generate("abc"), "ok")

        summary = tracker.get_summary()
        self.assertEqual(adapter.calls, 1)
        self.assertEqual(summary["successful_calls"], 1)
        self.assertEqual(summary["uncertain_attempts"], 0)
        self.assertEqual(summary["reserved_cost"], 0.0)
        self.assertAlmostEqual(summary["daily_cost"], 0.07)
        self.assertEqual(
            summary["tokens_by_operation"],
            {"test-operation": {"input": 3, "output": 2}},
        )

    def test_provider_exception_commits_maximum_as_uncertain(self):
        adapter = _FakeAdapter(error=RuntimeError("provider failed"))
        tracker = _tracker(budget=0.12)
        tracked = _wrapper(adapter, tracker)

        with self.assertRaisesRegex(RuntimeError, "provider failed"):
            tracked.generate("abc")

        summary = tracker.get_summary()
        self.assertEqual(adapter.calls, 1)
        self.assertEqual(summary["successful_calls"], 0)
        self.assertEqual(summary["uncertain_attempts"], 1)
        self.assertEqual(summary["attempted_calls"], 1)
        self.assertAlmostEqual(summary["daily_cost"], 0.11)
        self.assertAlmostEqual(summary["uncertain_cost"], 0.11)
        self.assertEqual(summary["reserved_cost"], 0.0)
        with self.assertRaises(COST_EXAMPLE.BudgetExceededError):
            tracked.generate("abc")
        self.assertEqual(adapter.calls, 1)

    def test_unknown_model_fails_closed_before_provider_invocation(self):
        adapter = _FakeAdapter()
        tracker = _tracker()
        tracked = _wrapper(adapter, tracker, model="unknown-model")

        with self.assertRaises(COST_EXAMPLE.UnknownModelPricingError):
            tracked.generate("abc")

        self.assertEqual(adapter.calls, 0)

    def test_reset_daily_clears_operation_categories(self):
        tracker = _tracker()
        _wrapper(_FakeAdapter(response="ok"), tracker).generate("abc")

        tracker.reset_daily()

        summary = tracker.get_summary()
        self.assertEqual(summary["daily_cost"], 0.0)
        self.assertEqual(summary["successful_calls"], 0)
        self.assertEqual(summary["uncertain_attempts"], 0)
        self.assertEqual(summary["costs_by_operation"], {})
        self.assertEqual(summary["tokens_by_operation"], {})

    def test_reset_rejects_an_inflight_reservation(self):
        tracker = _tracker()
        reservation = tracker.reserve_call(
            model="test-model",
            input_tokens=1,
            max_output_tokens=1,
            operation="test-operation",
        )

        with self.assertRaisesRegex(RuntimeError, "calls are reserved"):
            tracker.reset_daily()

        tracker.release_reservation(reservation)

    def test_concurrent_reservation_prevents_overspend(self):
        entered = threading.Event()
        release = threading.Event()

        class _BlockingAdapter(BaseLLMAdapter):
            def __init__(self) -> None:
                self.calls = 0

            def generate(self, prompt: str) -> str:
                del prompt
                self.calls += 1
                entered.set()
                if not release.wait(timeout=5):
                    raise RuntimeError("test timed out")
                return "x"

        adapter = _BlockingAdapter()
        tracker = _tracker(budget=1.0, input_price=0.0, output_price=1.0)
        tracked = _wrapper(adapter, tracker, max_output_tokens=1)
        failures: list[BaseException] = []

        def invoke_first() -> None:
            try:
                tracked.generate("")
            except BaseException as exc:  # pragma: no cover - assertion below reports it
                failures.append(exc)

        worker = threading.Thread(target=invoke_first)
        worker.start()
        self.assertTrue(entered.wait(timeout=5))
        try:
            with self.assertRaises(COST_EXAMPLE.BudgetExceededError):
                tracked.generate("")
        finally:
            release.set()
            worker.join(timeout=5)

        self.assertFalse(worker.is_alive())
        self.assertEqual(failures, [])
        self.assertEqual(adapter.calls, 1)
        self.assertEqual(tracker.get_summary()["successful_calls"], 1)

    def test_visible_provider_configuration_must_match_wrapper(self):
        adapter = _FakeAdapter()
        adapter.model = "provider-model"
        adapter.max_tokens = 8

        with self.assertRaisesRegex(ValueError, "base_adapter.model"):
            _wrapper(adapter, _tracker(), model="test-model", max_output_tokens=8)
        with self.assertRaisesRegex(ValueError, "base_adapter.max_tokens"):
            COST_EXAMPLE.CostAwareLLMAdapter(
                adapter,
                _tracker(),
                model="provider-model",
                max_output_tokens=4,
            )

        inferred = COST_EXAMPLE.CostAwareLLMAdapter(adapter, _tracker())
        self.assertEqual(inferred.model, "provider-model")
        self.assertEqual(inferred.max_output_tokens, 8)

    def test_other_reservations_reduce_record_and_summary_remaining_budget(self):
        tracker = _tracker(budget=10.0, input_price=0.0, output_price=1.0)
        first = tracker.reserve_call(
            model="test-model",
            input_tokens=0,
            max_output_tokens=3,
            operation="first",
        )
        second = tracker.reserve_call(
            model="test-model",
            input_tokens=0,
            max_output_tokens=3,
            operation="second",
        )

        record = tracker.settle_call(first, output_tokens=1)

        self.assertEqual(record.budget_remaining_usd, 6.0)
        self.assertEqual(tracker.get_summary()["budget_remaining"], 6.0)
        tracker.release_reservation(second)

    def test_pricing_and_budget_are_read_only_and_reservation_snapshots_price(self):
        source_pricing = {
            "test-model": COST_EXAMPLE.ModelPricing(
                input_usd_per_token=1.0,
                output_usd_per_token=2.0,
            )
        }
        tracker = COST_EXAMPLE.LLMCostTracker(100.0, source_pricing)
        reservation = tracker.reserve_call(
            model="test-model",
            input_tokens=1,
            max_output_tokens=2,
            operation="snapshot",
        )
        source_pricing["test-model"] = COST_EXAMPLE.ModelPricing(
            input_usd_per_token=50.0,
            output_usd_per_token=50.0,
        )

        record = tracker.settle_call(reservation, output_tokens=1)

        self.assertEqual(record.cost_usd, 3.0)
        with self.assertRaises(TypeError):
            tracker.pricing["test-model"] = source_pricing["test-model"]
        with self.assertRaises(AttributeError):
            tracker.daily_budget = 1.0

    def test_surrogate_output_is_accounted_without_leaking_reservation(self):
        tracker = _tracker()
        tracked = _wrapper(_FakeAdapter(response="\ud800"), tracker)

        self.assertEqual(tracked.generate("x"), "\ud800")

        summary = tracker.get_summary()
        self.assertEqual(summary["successful_calls"], 1)
        self.assertEqual(summary["reserved_cost"], 0.0)

    def test_broken_observer_is_best_effort_and_does_not_trigger_retry(self):
        adapter = _FakeAdapter(response="ok")
        tracker = _tracker()

        def broken_observer(record) -> None:
            del record
            raise RuntimeError("telemetry failed")

        tracked = _wrapper(adapter, tracker, observer=broken_observer)
        with self.assertLogs("erii_cost_monitoring_example", level="ERROR"):
            response = tracked.generate("abc")

        self.assertEqual(response, "ok")
        self.assertEqual(adapter.calls, 1)
        self.assertEqual(tracker.get_summary()["successful_calls"], 1)
        self.assertEqual(tracker.get_summary()["reserved_cost"], 0.0)

    def test_default_provider_is_offline_without_any_environment(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            provider = COST_EXAMPLE.build_demo_provider()

        self.assertFalse(provider.online)
        self.assertIsInstance(provider.adapter, COST_EXAMPLE.OfflineDemoAdapter)
        self.assertEqual(provider.adapter.model, COST_EXAMPLE.OFFLINE_MODEL)
        self.assertEqual(
            provider.adapter.max_tokens,
            COST_EXAMPLE.OFFLINE_MAX_OUTPUT_TOKENS,
        )

    def test_api_key_presence_alone_does_not_opt_in_to_online_mode(self):
        with mock.patch.dict(
            os.environ,
            {"OPENAI_API_KEY": "present-but-unused"},
            clear=True,
        ):
            provider = COST_EXAMPLE.build_demo_provider()

        self.assertFalse(provider.online)
        self.assertIsInstance(provider.adapter, COST_EXAMPLE.OfflineDemoAdapter)

    def test_online_mode_requires_explicit_prices_before_adapter_creation(self):
        with mock.patch.dict(
            os.environ,
            {COST_EXAMPLE.ONLINE_OPT_IN_ENV: "1"},
            clear=True,
        ):
            with self.assertRaisesRegex(ValueError, COST_EXAMPLE.ONLINE_INPUT_PRICE_ENV):
                COST_EXAMPLE.build_demo_provider()

    def test_main_runs_modern_archival_fully_offline(self):
        output = StringIO()
        with mock.patch.dict(os.environ, {}, clear=True):
            with mock.patch(
                "urllib.request.urlopen",
                side_effect=AssertionError("urllib network access attempted"),
            ):
                with mock.patch(
                    "socket.socket.connect",
                    side_effect=AssertionError("socket network access attempted"),
                ):
                    with redirect_stdout(output):
                        summary = COST_EXAMPLE.main()

        rendered = output.getvalue()
        self.assertIn("Mode: offline (no network)", rendered)
        self.assertIn("The user enjoys hiking in the mountains.", rendered)
        self.assertEqual(summary["successful_calls"], 1)
        self.assertGreater(summary["daily_cost"], 0.0)
        self.assertEqual(summary["reserved_cost"], 0.0)

    def test_prometheus_uses_isolated_registries_when_available(self):
        fake_prometheus = ModuleType("prometheus_client")

        class _Registry:
            def __init__(self) -> None:
                self.metrics = {}

        class _Metric:
            def __init__(self, name, *args, registry, **kwargs) -> None:
                del args, kwargs
                self.name = name
                self.registry = registry
                self.values = {}
                registry.metrics[name] = self

            def labels(self, **labels):
                metric = self
                key = tuple(sorted(labels.items()))

                class _BoundMetric:
                    def inc(self, value=1) -> None:
                        metric.values[key] = metric.values.get(key, 0) + value

                return _BoundMetric()

            def inc(self, value=1) -> None:
                self.values[()] = self.values.get((), 0) + value

            def set(self, value) -> None:
                self.values[()] = value

        fake_prometheus.CollectorRegistry = _Registry
        fake_prometheus.Counter = _Metric
        fake_prometheus.Gauge = _Metric
        output = StringIO()
        with mock.patch.dict(sys.modules, {"prometheus_client": fake_prometheus}):
            with redirect_stdout(output):
                first, observer = COST_EXAMPLE.create_prometheus_observer()
                observer(
                    COST_EXAMPLE.SettledCostRecord(
                        model="test-model",
                        operation="memory_extraction",
                        input_tokens=3,
                        output_tokens=2,
                        outcome=COST_EXAMPLE.CostOutcome.SUCCESSFUL,
                        cost_usd=0.25,
                        budget_remaining_usd=7.5,
                    )
                )
                second, _ = COST_EXAMPLE.create_prometheus_observer()
                COST_EXAMPLE.production_example()

        self.assertIsNotNone(first)
        self.assertIsNotNone(second)
        self.assertIsNot(first, second)
        self.assertIn("No HTTP server was started", output.getvalue())
        outcome_labels = (
            ("model", "test-model"),
            ("operation", "memory_extraction"),
            ("outcome", "successful"),
        )
        self.assertEqual(
            first.metrics["erii_llm_attempts_total"].values[outcome_labels],
            1,
        )
        self.assertEqual(
            first.metrics["erii_llm_estimated_cost_usd_total"].values[
                outcome_labels
            ],
            0.25,
        )
        self.assertEqual(
            first.metrics["erii_llm_estimated_tokens_total"].values[
                tuple(sorted(outcome_labels + (("direction", "input"),)))
            ],
            3,
        )
        self.assertEqual(
            first.metrics["erii_llm_estimated_tokens_total"].values[
                tuple(sorted(outcome_labels + (("direction", "output"),)))
            ],
            2,
        )
        self.assertEqual(
            first.metrics["erii_llm_budget_remaining_usd"].values[()],
            7.5,
        )


if __name__ == "__main__":
    unittest.main()

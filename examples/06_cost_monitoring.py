"""Example: conservative LLM cost monitoring and budget control.

The example is offline by default.  Set ``ERII_COST_DEMO_ONLINE=1`` to opt in
to a real OpenAI-compatible request.  Online mode also requires
``OPENAI_API_KEY`` plus explicit per-million-token prices; pricing is never
guessed for an unknown model.

The public ``BaseLLMAdapter`` contract returns text only, not provider usage
metadata.  Consequently this example reports conservative estimates rather
than billing-grade usage.  A production host should reconcile these records
with usage returned by its provider-specific client.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from enum import Enum
import json
import logging
import math
import os
from tempfile import TemporaryDirectory
import threading
import time
from types import MappingProxyType

from erii import BaseLLMAdapter, ERIIConfig, ERIIEngine
from erii.adapters.openai_adapter import OpenAIAdapter

if __package__:
    from ._shared import (
        CallableJSONMemoryExtractor,
        record_and_archive_visible_exchange,
    )
else:
    from _shared import (  # type: ignore[import-not-found]
        CallableJSONMemoryExtractor,
        record_and_archive_visible_exchange,
    )


ONLINE_OPT_IN_ENV = "ERII_COST_DEMO_ONLINE"
ONLINE_MODEL_ENV = "ERII_COST_DEMO_MODEL"
ONLINE_MAX_OUTPUT_ENV = "ERII_COST_DEMO_MAX_OUTPUT_TOKENS"
ONLINE_INPUT_PRICE_ENV = "ERII_COST_DEMO_INPUT_USD_PER_MILLION"
ONLINE_OUTPUT_PRICE_ENV = "ERII_COST_DEMO_OUTPUT_USD_PER_MILLION"

OFFLINE_MODEL = "offline-demo"
OFFLINE_MAX_OUTPUT_TOKENS = 256

logger = logging.getLogger(__name__)


class BudgetExceededError(RuntimeError):
    """Raised before an LLM request whose reservation would exceed budget."""


class UnknownModelPricingError(ValueError):
    """Raised before provider invocation when a model has no configured price."""


class CostOutcome(str, Enum):
    """Accounting disposition of one provider attempt."""

    SUCCESSFUL = "successful"
    UNCERTAIN = "uncertain"


@dataclass(frozen=True)
class ModelPricing:
    """Configured USD price for one token of a model."""

    input_usd_per_token: float
    output_usd_per_token: float

    def __post_init__(self) -> None:
        for name, value in (
            ("input_usd_per_token", self.input_usd_per_token),
            ("output_usd_per_token", self.output_usd_per_token),
        ):
            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(value)
                or value < 0
            ):
                raise ValueError(f"{name} must be a finite non-negative number")

    @classmethod
    def from_per_million(
        cls,
        *,
        input_usd: float,
        output_usd: float,
    ) -> ModelPricing:
        """Build pricing from the units normally published by providers."""
        if input_usd < 0 or output_usd < 0:
            raise ValueError("model prices cannot be negative")
        return cls(
            input_usd_per_token=input_usd / 1_000_000,
            output_usd_per_token=output_usd / 1_000_000,
        )


@dataclass(frozen=True)
class CostReservation:
    """One thread-safe maximum-cost reservation made before provider I/O."""

    reservation_id: int
    model: str
    operation: str
    input_tokens: int
    max_output_tokens: int
    input_usd_per_token: float
    output_usd_per_token: float
    maximum_cost_usd: float


@dataclass(frozen=True)
class SettledCostRecord:
    """Committed estimate for one successful or uncertain provider attempt."""

    model: str
    operation: str
    input_tokens: int
    output_tokens: int
    outcome: CostOutcome
    cost_usd: float
    budget_remaining_usd: float


class LLMCostTracker:
    """Tracks estimated daily LLM costs with atomic pre-call reservations."""

    def __init__(
        self,
        daily_budget_usd: float,
        pricing: Mapping[str, ModelPricing],
    ) -> None:
        if (
            isinstance(daily_budget_usd, bool)
            or not isinstance(daily_budget_usd, (int, float))
            or not math.isfinite(daily_budget_usd)
            or daily_budget_usd < 0
        ):
            raise ValueError("daily_budget_usd must be a finite non-negative number")
        if not pricing:
            raise ValueError("at least one model price must be configured")
        self._daily_budget = float(daily_budget_usd)
        self._pricing = MappingProxyType(dict(pricing))
        self.start_time = time.time()
        self._lock = threading.RLock()
        self._reservations: dict[int, CostReservation] = {}
        self._next_reservation_id = 1
        self.daily_cost = 0.0
        self.successful_cost = 0.0
        self.uncertain_cost = 0.0
        self.successful_calls = 0
        self.uncertain_attempts = 0
        self.day_start = self.start_time
        self.costs: dict[str, float] = {}
        self.token_usage: dict[str, dict[str, int]] = {}

    @property
    def daily_budget(self) -> float:
        """Read-only configured budget."""
        return self._daily_budget

    @property
    def pricing(self) -> Mapping[str, ModelPricing]:
        """Read-only model pricing copied at tracker construction."""
        return self._pricing

    @staticmethod
    def _validate_token_count(value: int, field_name: str) -> None:
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise ValueError(f"{field_name} must be a non-negative integer")

    def _pricing_for(self, model: str) -> ModelPricing:
        try:
            return self._pricing[model]
        except KeyError as exc:
            configured = ", ".join(sorted(self._pricing))
            raise UnknownModelPricingError(
                f"no pricing configured for model {model!r}; configured: {configured}"
            ) from exc

    def _calculate_cost(
        self,
        *,
        model: str,
        input_tokens: int,
        output_tokens: int,
    ) -> float:
        self._validate_token_count(input_tokens, "input_tokens")
        self._validate_token_count(output_tokens, "output_tokens")
        pricing = self._pricing_for(model)
        return (
            input_tokens * pricing.input_usd_per_token
            + output_tokens * pricing.output_usd_per_token
        )

    def reserve_call(
        self,
        *,
        model: str,
        input_tokens: int,
        max_output_tokens: int,
        operation: str,
    ) -> CostReservation:
        """Atomically reserve a call's conservative maximum cost."""
        if not operation:
            raise ValueError("operation cannot be empty")
        pricing = self._pricing_for(model)
        maximum_cost = self._calculate_cost(
            model=model,
            input_tokens=input_tokens,
            output_tokens=max_output_tokens,
        )
        with self._lock:
            outstanding = sum(
                item.maximum_cost_usd for item in self._reservations.values()
            )
            projected = self.daily_cost + outstanding + maximum_cost
            if projected > self.daily_budget:
                raise BudgetExceededError(
                    f"daily budget ${self.daily_budget:.2f} would be exceeded; "
                    f"committed=${self.daily_cost:.6f}, "
                    f"reserved=${outstanding:.6f}, "
                    f"new maximum=${maximum_cost:.6f}"
                )
            reservation = CostReservation(
                reservation_id=self._next_reservation_id,
                model=model,
                operation=operation,
                input_tokens=input_tokens,
                max_output_tokens=max_output_tokens,
                input_usd_per_token=pricing.input_usd_per_token,
                output_usd_per_token=pricing.output_usd_per_token,
                maximum_cost_usd=maximum_cost,
            )
            self._next_reservation_id += 1
            self._reservations[reservation.reservation_id] = reservation
            return reservation

    def _require_active_reservation(
        self,
        reservation: CostReservation,
    ) -> CostReservation:
        active = self._reservations.get(reservation.reservation_id)
        if active != reservation:
            raise ValueError("reservation is unknown, already settled, or already released")
        return active

    def release_reservation(self, reservation: CostReservation) -> None:
        """Cancel a reservation only when provider invocation has not begun."""
        with self._lock:
            self._require_active_reservation(reservation)
            del self._reservations[reservation.reservation_id]

    def _settle(
        self,
        reservation: CostReservation,
        *,
        output_tokens: int,
        outcome: CostOutcome,
    ) -> SettledCostRecord:
        self._validate_token_count(output_tokens, "output_tokens")
        billed_output_tokens = min(output_tokens, reservation.max_output_tokens)
        actual_cost = (
            reservation.input_tokens * reservation.input_usd_per_token
            + billed_output_tokens * reservation.output_usd_per_token
        )
        with self._lock:
            self._require_active_reservation(reservation)
            if actual_cost > reservation.maximum_cost_usd:
                raise RuntimeError("settled cost exceeded its pre-call reservation")

            del self._reservations[reservation.reservation_id]
            self.daily_cost += actual_cost
            if outcome is CostOutcome.SUCCESSFUL:
                self.successful_cost += actual_cost
                self.successful_calls += 1
            else:
                self.uncertain_cost += actual_cost
                self.uncertain_attempts += 1
            self.costs[reservation.operation] = (
                self.costs.get(reservation.operation, 0.0) + actual_cost
            )
            tokens = self.token_usage.setdefault(
                reservation.operation,
                {"input": 0, "output": 0},
            )
            tokens["input"] += reservation.input_tokens
            tokens["output"] += billed_output_tokens
            outstanding = sum(
                item.maximum_cost_usd for item in self._reservations.values()
            )
            return SettledCostRecord(
                model=reservation.model,
                operation=reservation.operation,
                input_tokens=reservation.input_tokens,
                output_tokens=billed_output_tokens,
                outcome=outcome,
                cost_usd=actual_cost,
                budget_remaining_usd=(
                    self._daily_budget - self.daily_cost - outstanding
                ),
            )

    def settle_call(
        self,
        reservation: CostReservation,
        *,
        output_tokens: int,
    ) -> SettledCostRecord:
        """Commit one successful provider response."""
        return self._settle(
            reservation,
            output_tokens=output_tokens,
            outcome=CostOutcome.SUCCESSFUL,
        )

    def settle_uncertain_attempt(
        self,
        reservation: CostReservation,
    ) -> SettledCostRecord:
        """Charge the maximum reservation after an invoked provider fails."""
        return self._settle(
            reservation,
            output_tokens=reservation.max_output_tokens,
            outcome=CostOutcome.UNCERTAIN,
        )

    def reservation_is_active(self, reservation: CostReservation) -> bool:
        """Return whether an exceptional path still needs conservative settlement."""
        with self._lock:
            return self._reservations.get(reservation.reservation_id) == reservation

    def reset_daily(self) -> None:
        """Clear all daily totals and categories when no call is in flight."""
        with self._lock:
            if self._reservations:
                raise RuntimeError("cannot reset daily totals while calls are reserved")
            self.daily_cost = 0.0
            self.successful_cost = 0.0
            self.uncertain_cost = 0.0
            self.successful_calls = 0
            self.uncertain_attempts = 0
            self.day_start = time.time()
            self.costs.clear()
            self.token_usage.clear()

    def get_summary(self) -> dict[str, object]:
        """Return a detached snapshot of committed and reserved estimates."""
        with self._lock:
            reserved_cost = sum(
                item.maximum_cost_usd for item in self._reservations.values()
            )
            return {
                "daily_budget": self.daily_budget,
                "daily_cost": self.daily_cost,
                "successful_cost": self.successful_cost,
                "uncertain_cost": self.uncertain_cost,
                "successful_calls": self.successful_calls,
                "uncertain_attempts": self.uncertain_attempts,
                "attempted_calls": self.successful_calls + self.uncertain_attempts,
                "budget_remaining": self.daily_budget - self.daily_cost - reserved_cost,
                "budget_used_pct": (
                    (self.daily_cost + reserved_cost) / self.daily_budget * 100
                    if self.daily_budget > 0
                    else 0.0
                ),
                "reserved_cost": reserved_cost,
                "costs_by_operation": dict(self.costs),
                "tokens_by_operation": {
                    operation: dict(tokens)
                    for operation, tokens in self.token_usage.items()
                },
                "uptime_hours": (time.time() - self.start_time) / 3600,
            }

    def print_summary(self) -> None:
        """Print a human-readable estimated-cost summary."""
        summary = self.get_summary()
        print("\n" + "=" * 60)
        print("ESTIMATED LLM COST SUMMARY")
        print("=" * 60)
        print(f"Daily Budget:     ${summary['daily_budget']:.2f}")
        print(f"Committed Cost:   ${summary['daily_cost']:.6f}")
        print(f"  Successful:     ${summary['successful_cost']:.6f}")
        print(f"  Uncertain:      ${summary['uncertain_cost']:.6f}")
        print(f"Reserved Cost:    ${summary['reserved_cost']:.6f}")
        print(f"Budget Remaining: ${summary['budget_remaining']:.6f}")
        print(f"Successful Calls: {summary['successful_calls']}")
        print(f"Uncertain Attempts: {summary['uncertain_attempts']}")
        print("\nCosts by Operation:")
        costs = summary["costs_by_operation"]
        tokens_by_operation = summary["tokens_by_operation"]
        assert isinstance(costs, dict)
        assert isinstance(tokens_by_operation, dict)
        for operation, cost in costs.items():
            tokens = tokens_by_operation[operation]
            print(
                f"  {operation:20s} ${cost:10.6f}  "
                f"({tokens['input']:,} in, {tokens['output']:,} out)"
            )
        print("=" * 60)


def conservative_token_estimate(text: str) -> int:
    """Return a tokenizer-independent, deliberately conservative estimate."""
    return len(text.encode("utf-8", errors="replace"))


class CostAwareLLMAdapter(BaseLLMAdapter):
    """Wrap a provider with fail-closed pricing and pre-call reservations.

    If the wrapped adapter exposes ``model`` or ``max_tokens``, those values
    must match this wrapper.  A custom adapter without those attributes must
    receive explicit wrapper values and guarantee that they describe the
    actual provider request and its enforced output-token cap.
    """

    def __init__(
        self,
        base_adapter: BaseLLMAdapter,
        cost_tracker: LLMCostTracker,
        *,
        model: str | None = None,
        max_output_tokens: int | None = None,
        operation: str = "memory_extraction",
        observer: Callable[[SettledCostRecord], None] | None = None,
    ) -> None:
        if not isinstance(base_adapter, BaseLLMAdapter):
            raise TypeError("base_adapter must implement BaseLLMAdapter")
        visible_model = getattr(base_adapter, "model", None)
        visible_max_tokens = getattr(base_adapter, "max_tokens", None)
        resolved_model = model if model is not None else visible_model
        resolved_max_tokens = (
            max_output_tokens
            if max_output_tokens is not None
            else visible_max_tokens
        )
        if not isinstance(resolved_model, str) or not resolved_model:
            raise ValueError("model cannot be empty")
        if (
            isinstance(resolved_max_tokens, bool)
            or not isinstance(resolved_max_tokens, int)
            or resolved_max_tokens <= 0
        ):
            raise ValueError("max_output_tokens must be a positive integer")
        if visible_model is not None and visible_model != resolved_model:
            raise ValueError(
                "wrapper model must match base_adapter.model "
                f"({resolved_model!r} != {visible_model!r})"
            )
        if (
            visible_max_tokens is not None
            and visible_max_tokens != resolved_max_tokens
        ):
            raise ValueError(
                "wrapper max_output_tokens must match base_adapter.max_tokens "
                f"({resolved_max_tokens!r} != {visible_max_tokens!r})"
            )
        if not operation:
            raise ValueError("operation cannot be empty")
        self.base_adapter = base_adapter
        self.cost_tracker = cost_tracker
        self.model = resolved_model
        self.max_output_tokens = resolved_max_tokens
        self.operation = operation
        self.observer = observer

    def generate(self, prompt: str) -> str:
        """Reserve budget, invoke the provider, then settle estimated usage."""
        input_tokens = conservative_token_estimate(prompt)
        reservation = self.cost_tracker.reserve_call(
            model=self.model,
            input_tokens=input_tokens,
            max_output_tokens=self.max_output_tokens,
            operation=self.operation,
        )
        record: SettledCostRecord | None = None
        try:
            response = self.base_adapter.generate(prompt)
            if not isinstance(response, str):
                raise TypeError("BaseLLMAdapter.generate() must return str")
            record = self.cost_tracker.settle_call(
                reservation,
                output_tokens=conservative_token_estimate(response),
            )
        except BaseException:
            if self.cost_tracker.reservation_is_active(reservation):
                record = self.cost_tracker.settle_uncertain_attempt(reservation)
            if record is not None:
                self._notify_observer(record)
            raise
        self._notify_observer(record)
        return response

    def _notify_observer(self, record: SettledCostRecord) -> None:
        """Report telemetry best-effort without converting success into a retry."""
        if self.observer is None:
            return
        try:
            self.observer(record)
        except Exception:
            logger.exception(
                "cost observer failed after %s provider accounting",
                record.outcome.value,
            )


class OfflineDemoAdapter(BaseLLMAdapter):
    """Deterministic, network-free provider used unless online mode is explicit."""

    model = OFFLINE_MODEL
    max_tokens = OFFLINE_MAX_OUTPUT_TOKENS

    def generate(self, prompt: str) -> str:
        """Return one valid memory extraction payload without external I/O."""
        del prompt
        return json.dumps(
            {
                "timeline_entry": "The user shared an interest in mountain hiking.",
                "impressions": [
                    {
                        "type": "preference",
                        "content": "The user enjoys hiking in the mountains.",
                        "base_importance": 0.8,
                        "emotional_score": 0.2,
                        "tags": ["hiking", "mountains"],
                    }
                ],
            }
        )


@dataclass(frozen=True)
class DemoProvider:
    """Resolved provider plus the explicit accounting configuration."""

    adapter: BaseLLMAdapter
    model: str
    max_output_tokens: int
    pricing: ModelPricing
    online: bool


def _read_opt_in(environ: Mapping[str, str]) -> bool:
    raw = environ.get(ONLINE_OPT_IN_ENV, "").strip().casefold()
    if raw in {"", "0", "false", "no", "off"}:
        return False
    if raw in {"1", "true", "yes", "on"}:
        return True
    raise ValueError(
        f"{ONLINE_OPT_IN_ENV} must be one of 1/true/yes/on or 0/false/no/off"
    )


def _read_non_negative_float(environ: Mapping[str, str], name: str) -> float:
    raw = environ.get(name)
    if raw is None or not raw.strip():
        raise ValueError(f"online mode requires {name}")
    try:
        value = float(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be a number") from exc
    if not math.isfinite(value) or value < 0:
        raise ValueError(f"{name} must be a finite non-negative number")
    return value


def _read_positive_int(
    environ: Mapping[str, str],
    name: str,
    default: int,
) -> int:
    raw = environ.get(name)
    if raw is None or not raw.strip():
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer") from exc
    if value <= 0:
        raise ValueError(f"{name} must be positive")
    return value


def build_demo_provider() -> DemoProvider:
    """Resolve offline mode by default and online mode only after opt-in."""
    resolved_environ = os.environ
    if not _read_opt_in(resolved_environ):
        return DemoProvider(
            adapter=OfflineDemoAdapter(),
            model=OFFLINE_MODEL,
            max_output_tokens=OFFLINE_MAX_OUTPUT_TOKENS,
            pricing=ModelPricing.from_per_million(
                input_usd=0.10,
                output_usd=0.40,
            ),
            online=False,
        )

    model = resolved_environ.get(ONLINE_MODEL_ENV, "gpt-4o-mini").strip()
    if not model:
        raise ValueError(f"{ONLINE_MODEL_ENV} cannot be empty")
    max_output_tokens = _read_positive_int(
        resolved_environ,
        ONLINE_MAX_OUTPUT_ENV,
        1000,
    )
    pricing = ModelPricing.from_per_million(
        input_usd=_read_non_negative_float(
            resolved_environ,
            ONLINE_INPUT_PRICE_ENV,
        ),
        output_usd=_read_non_negative_float(
            resolved_environ,
            ONLINE_OUTPUT_PRICE_ENV,
        ),
    )
    return DemoProvider(
        adapter=OpenAIAdapter(model=model, max_tokens=max_output_tokens),
        model=model,
        max_output_tokens=max_output_tokens,
        pricing=pricing,
        online=True,
    )


def create_prometheus_observer() -> tuple[object, Callable[[SettledCostRecord], None]]:
    """Create metrics in an isolated registry without starting an HTTP server."""
    from prometheus_client import CollectorRegistry, Counter, Gauge

    registry = CollectorRegistry()
    calls = Counter(
        "erii_llm_attempts_total",
        "Successful and uncertain LLM attempts observed by this host",
        ("operation", "model", "outcome"),
        registry=registry,
    )
    cost = Counter(
        "erii_llm_estimated_cost_usd_total",
        "Estimated LLM cost in USD observed by this host",
        ("operation", "model", "outcome"),
        registry=registry,
    )
    tokens = Counter(
        "erii_llm_estimated_tokens_total",
        "Estimated LLM tokens observed by this host",
        ("operation", "model", "outcome", "direction"),
        registry=registry,
    )
    remaining = Gauge(
        "erii_llm_budget_remaining_usd",
        "Estimated remaining daily LLM budget in USD",
        registry=registry,
    )

    def observe(record: SettledCostRecord) -> None:
        labels = {
            "operation": record.operation,
            "model": record.model,
            "outcome": record.outcome.value,
        }
        calls.labels(**labels).inc()
        cost.labels(**labels).inc(record.cost_usd)
        tokens.labels(**labels, direction="input").inc(record.input_tokens)
        tokens.labels(**labels, direction="output").inc(record.output_tokens)
        remaining.set(record.budget_remaining_usd)

    return registry, observe


def production_example() -> object | None:
    """Build isolated metrics and state the host-owned exposure step truthfully."""
    try:
        registry, _observer = create_prometheus_observer()
    except ImportError:
        print("Prometheus integration unavailable: install prometheus-client.")
        return None
    print("Prometheus metrics created in an isolated CollectorRegistry.")
    print("No HTTP server was started by this example.")
    print(
        "A long-running host may explicitly call "
        "start_http_server(9090, registry=registry)."
    )
    return registry


def main() -> dict[str, object]:
    """Run one modern record/archive/recall cycle and return its cost summary."""
    provider = build_demo_provider()
    mode = "online (real provider request)" if provider.online else "offline (no network)"
    print("Cost-Aware E.R.I.I. Demo")
    print("=" * 60)
    print(f"Mode: {mode}")
    print(f"Model accounting key: {provider.model}")

    tracker = LLMCostTracker(
        daily_budget_usd=10.0,
        pricing={provider.model: provider.pricing},
    )
    tracked_adapter = CostAwareLLMAdapter(
        provider.adapter,
        tracker,
        model=provider.model,
        max_output_tokens=provider.max_output_tokens,
        operation="memory_extraction",
    )
    extractor = CallableJSONMemoryExtractor(tracked_adapter.generate)

    with TemporaryDirectory(prefix="erii-cost-demo-") as storage_dir:
        config = ERIIConfig(storage_dir=storage_dir, async_archival=False)
        with ERIIEngine(
            config=config,
            llm=tracked_adapter,
            memory_extractor=extractor,
        ) as engine:
            agent_id = "assistant"
            user_id = "user123"
            engine.initialize_relationship(
                agent_id,
                user_id,
                "A thoughtful companion who remembers shared experiences.",
            )
            record_and_archive_visible_exchange(
                engine,
                agent_id=agent_id,
                user_id=user_id,
                user_message="I love hiking in the mountains.",
                agent_message="I'll remember that you enjoy hiking.",
                turn_id="cost-demo-hiking-turn",
                actor_id="examples.cost-monitoring-host",
            )
            context = engine.recall(
                agent_id=agent_id,
                user_id=user_id,
                query="outdoor activities",
            )
            print("\n--- Recalled Context ---")
            print(context)

    tracker.print_summary()
    return tracker.get_summary()


if __name__ == "__main__":
    print(__doc__)
    main()
    print("\n" + "=" * 60)
    print("Optional Prometheus Integration")
    print("=" * 60)
    production_example()

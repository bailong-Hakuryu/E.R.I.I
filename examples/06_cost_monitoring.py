"""Example: LLM Cost Monitoring and Budget Control.

Demonstrates how to track and limit LLM API costs when using E.R.I.I. with
OpenAI or other providers.

This is especially important for:
- Production deployments
- Multi-tenant systems
- Development/testing environments
- Cost-conscious applications
"""

import os
import time
from typing import Dict, Optional

from erii import ERIIEngine
from erii.adapters.openai_adapter import OpenAIAdapter


class LLMCostTracker:
    """Tracks LLM API costs across all E.R.I.I. operations.

    This is a reference implementation. In production, you should:
    - Persist costs to a database
    - Integrate with your monitoring system (Prometheus, DataDog, etc.)
    - Add alerting when approaching budget limits
    """

    # Pricing as of 2026-08 (update based on your provider)
    PRICING = {
        "gpt-4": {
            "input": 0.03 / 1000,   # $0.03 per 1K input tokens
            "output": 0.06 / 1000,  # $0.06 per 1K output tokens
        },
        "gpt-4-turbo": {
            "input": 0.01 / 1000,
            "output": 0.03 / 1000,
        },
        "gpt-3.5-turbo": {
            "input": 0.0005 / 1000,
            "output": 0.0015 / 1000,
        },
    }

    def __init__(self, daily_budget_usd: float = 100.0):
        """Initialize cost tracker.

        Args:
            daily_budget_usd: Maximum daily spend in USD
        """
        self.daily_budget = daily_budget_usd
        self.costs: Dict[str, float] = {}
        self.token_usage: Dict[str, Dict[str, int]] = {}
        self.start_time = time.time()
        self.reset_daily()

    def reset_daily(self):
        """Reset daily counters (call this at midnight)."""
        self.daily_cost = 0.0
        self.daily_calls = 0
        self.day_start = time.time()

    def track_call(
        self,
        model: str,
        input_tokens: int,
        output_tokens: int,
        operation: str = "unknown",
    ) -> float:
        """Track a single LLM API call.

        Args:
            model: Model name (e.g., "gpt-4")
            input_tokens: Number of input tokens
            output_tokens: Number of output tokens
            operation: E.R.I.I. operation (e.g., "recall", "persona_compilation")

        Returns:
            Cost of this call in USD

        Raises:
            BudgetExceededError: If daily budget would be exceeded
        """
        pricing = self.PRICING.get(model, self.PRICING["gpt-4"])
        cost = (
            input_tokens * pricing["input"] +
            output_tokens * pricing["output"]
        )

        # Check budget before executing
        if self.daily_cost + cost > self.daily_budget:
            raise BudgetExceededError(
                f"Daily budget ${self.daily_budget:.2f} would be exceeded. "
                f"Current: ${self.daily_cost:.2f}, This call: ${cost:.4f}"
            )

        # Track costs
        self.daily_cost += cost
        self.daily_calls += 1

        if operation not in self.costs:
            self.costs[operation] = 0.0
            self.token_usage[operation] = {"input": 0, "output": 0}

        self.costs[operation] += cost
        self.token_usage[operation]["input"] += input_tokens
        self.token_usage[operation]["output"] += output_tokens

        return cost

    def get_summary(self) -> Dict:
        """Get cost summary."""
        return {
            "daily_budget": self.daily_budget,
            "daily_cost": self.daily_cost,
            "daily_calls": self.daily_calls,
            "budget_remaining": self.daily_budget - self.daily_cost,
            "budget_used_pct": (self.daily_cost / self.daily_budget * 100)
                               if self.daily_budget > 0 else 0,
            "costs_by_operation": self.costs,
            "tokens_by_operation": self.token_usage,
            "uptime_hours": (time.time() - self.start_time) / 3600,
        }

    def print_summary(self):
        """Print human-readable cost summary."""
        summary = self.get_summary()
        print("\n" + "=" * 60)
        print("LLM COST SUMMARY")
        print("=" * 60)
        print(f"Daily Budget:     ${summary['daily_budget']:.2f}")
        print(f"Daily Cost:       ${summary['daily_cost']:.4f}")
        print(f"Budget Remaining: ${summary['budget_remaining']:.4f} "
              f"({100 - summary['budget_used_pct']:.1f}%)")
        print(f"API Calls:        {summary['daily_calls']}")
        print(f"\nCosts by Operation:")
        for op, cost in summary['costs_by_operation'].items():
            tokens = summary['tokens_by_operation'][op]
            print(f"  {op:20s} ${cost:8.4f}  "
                  f"({tokens['input']:,} in, {tokens['output']:,} out)")
        print("=" * 60)


class BudgetExceededError(Exception):
    """Raised when LLM budget is exceeded."""
    pass


class CostAwareLLMAdapter:
    """Wrapper around any LLM adapter that tracks costs.

    Usage:
        base_adapter = OpenAIAdapter(api_key="...")
        tracked_adapter = CostAwareLLMAdapter(base_adapter, cost_tracker)
    """

    def __init__(self, base_adapter, cost_tracker: LLMCostTracker):
        self.base_adapter = base_adapter
        self.cost_tracker = cost_tracker

    def generate(self, prompt: str) -> str:
        """Generate response with cost tracking."""
        # Call base adapter
        response = self.base_adapter.generate(prompt)

        # Estimate token usage (simplified - real implementation should use tiktoken)
        input_tokens = len(prompt.split()) * 1.3  # Rough estimate
        output_tokens = len(response.split()) * 1.3

        # Track costs
        try:
            cost = self.cost_tracker.track_call(
                model="gpt-4",  # Get from adapter if available
                input_tokens=int(input_tokens),
                output_tokens=int(output_tokens),
                operation="generate",
            )
            print(f"[Cost: ${cost:.4f}]")
        except BudgetExceededError as e:
            print(f"\n⚠️  BUDGET EXCEEDED: {e}")
            raise

        return response


# ============================================================================
# Example Usage
# ============================================================================

def main():
    """Demonstrate cost tracking with E.R.I.I."""

    # 1. Set up cost tracker with $10 daily budget
    cost_tracker = LLMCostTracker(daily_budget_usd=10.0)

    # 2. Wrap your LLM adapter
    base_adapter = OpenAIAdapter(api_key=os.getenv("OPENAI_API_KEY"))
    tracked_adapter = CostAwareLLMAdapter(base_adapter, cost_tracker)

    # 3. Create E.R.I.I. engine with cost-aware adapter
    engine = ERIIEngine(
        storage_dir="./cost_demo_memory",
        llm_adapter=tracked_adapter,
    )

    print("Cost-Aware E.R.I.I. Demo")
    print("=" * 60)
    print(f"Daily Budget: ${cost_tracker.daily_budget:.2f}")
    print("=" * 60)

    try:
        # Normal E.R.I.I. operations - costs are tracked automatically
        print("\n1. Storing memory...")
        engine.remember(
            "assistant",
            "user123",
            "I love hiking in the mountains.",
            "I'll remember that you enjoy hiking.",
        )

        print("\n2. Recalling memory...")
        context = engine.recall(
            agent_id="assistant",
            user_id="user123",
            query="outdoor activities",
        )
        print(f"Recalled: {context[:100]}...")

        print("\n3. Another recall...")
        context = engine.recall(
            agent_id="assistant",
            user_id="user123",
            query="hobbies",
        )

    except BudgetExceededError as e:
        print(f"\n❌ Budget exceeded: {e}")
        print("Operations stopped to prevent cost overrun.")

    finally:
        # Always print summary
        cost_tracker.print_summary()
        engine.close()


def production_example():
    """Production-grade cost monitoring with Prometheus.

    This example shows how to integrate with a real monitoring system.
    """
    try:
        from prometheus_client import Counter, Gauge, Histogram

        # Define metrics
        llm_calls_total = Counter(
            'erii_llm_calls_total',
            'Total LLM API calls',
            ['operation', 'model'],
        )

        llm_cost_usd = Counter(
            'erii_llm_cost_usd_total',
            'Total LLM cost in USD',
            ['operation', 'model'],
        )

        llm_tokens = Counter(
            'erii_llm_tokens_total',
            'Total LLM tokens',
            ['operation', 'model', 'type'],  # type: input/output
        )

        llm_budget_remaining = Gauge(
            'erii_llm_budget_remaining_usd',
            'Remaining daily budget in USD',
        )

        class PrometheusLLMAdapter:
            """LLM adapter that exports Prometheus metrics."""

            def __init__(self, base_adapter, cost_tracker):
                self.base_adapter = base_adapter
                self.cost_tracker = cost_tracker

            def generate(self, prompt: str) -> str:
                response = self.base_adapter.generate(prompt)

                # Estimate tokens
                input_tokens = int(len(prompt.split()) * 1.3)
                output_tokens = int(len(response.split()) * 1.3)

                # Track cost
                cost = self.cost_tracker.track_call(
                    model="gpt-4",
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                    operation="generate",
                )

                # Export to Prometheus
                llm_calls_total.labels(operation="generate", model="gpt-4").inc()
                llm_cost_usd.labels(operation="generate", model="gpt-4").inc(cost)
                llm_tokens.labels(
                    operation="generate", model="gpt-4", type="input"
                ).inc(input_tokens)
                llm_tokens.labels(
                    operation="generate", model="gpt-4", type="output"
                ).inc(output_tokens)
                llm_budget_remaining.set(
                    self.cost_tracker.daily_budget - self.cost_tracker.daily_cost
                )

                return response

        print("✅ Prometheus integration available")
        print("   Expose metrics on :9090/metrics for scraping")

    except ImportError:
        print("⚠️  prometheus_client not installed")
        print("   Install: pip install prometheus-client")


if __name__ == "__main__":
    print(__doc__)
    print("\n" + "=" * 60)
    print("Running Basic Demo")
    print("=" * 60)
    main()

    print("\n\n" + "=" * 60)
    print("Production Integration Example")
    print("=" * 60)
    production_example()

"""D0-D4 configuration generators for CD-1 Shadow evaluation."""

from __future__ import annotations

import hashlib

from ..shadow_eval.contracts import ComparisonTarget, RunConfigurationV1


DEFAULT_FAKE_MODEL_ID = "fake-shadow-model-v1"


def _compute_capability_fingerprint(config: dict[str, str | int | float | None]) -> str:
    """Compute deterministic fingerprint from config parameters."""
    canonical = ";".join(f"{k}={v}" for k, v in sorted(config.items()))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def create_d0_direct_generation(
    *,
    seed: int,
    provider_kind: str = "fake_deterministic",
    model_id: str = DEFAULT_FAKE_MODEL_ID,
) -> RunConfigurationV1:
    """D0: Direct generation baseline (current v0.5 path)."""
    config_dict = {
        "provider_kind": provider_kind,
        "model_id": model_id,
        "router_policy": None,
        "temperature": None,
        "max_tokens": 4000,
        "call_budget": 1,
    }
    return RunConfigurationV1(
        config_label="D0",
        provider_kind=provider_kind,
        model_id=model_id,
        adapter_version="shadow-adapter/v1",
        router_policy=None,
        temperature=None,
        max_tokens=4000,
        seed=seed,
        capability_fingerprint=_compute_capability_fingerprint(config_dict),
        call_budget=1,
    )


def create_d1_compact_deliberation(
    *,
    seed: int,
    provider_kind: str = "fake_deterministic",
    model_id: str = DEFAULT_FAKE_MODEL_ID,
) -> RunConfigurationV1:
    """D1: Compact deliberation."""
    config_dict = {
        "provider_kind": provider_kind,
        "model_id": model_id,
        "router_policy": "compact_every_turn",
        "temperature": 0.0,
        "max_tokens": 4000,
        "call_budget": 1,
    }
    return RunConfigurationV1(
        config_label="D1",
        provider_kind=provider_kind,
        model_id=model_id,
        adapter_version="shadow-adapter/v1",
        router_policy="compact_every_turn",
        temperature=0.0,
        max_tokens=4000,
        seed=seed,
        capability_fingerprint=_compute_capability_fingerprint(config_dict),
        call_budget=1,
    )


def create_d2_staged_deliberation(
    *,
    seed: int,
    provider_kind: str = "fake_deterministic",
    model_id: str = DEFAULT_FAKE_MODEL_ID,
) -> RunConfigurationV1:
    """D2: Staged deliberation (plan + realization)."""
    config_dict = {
        "provider_kind": provider_kind,
        "model_id": model_id,
        "router_policy": "staged_every_turn",
        "temperature": 0.0,
        "max_tokens": 4000,
        "call_budget": 2,
    }
    return RunConfigurationV1(
        config_label="D2",
        provider_kind=provider_kind,
        model_id=model_id,
        adapter_version="shadow-adapter/v1",
        router_policy="staged_every_turn",
        temperature=0.0,
        max_tokens=4000,
        seed=seed,
        capability_fingerprint=_compute_capability_fingerprint(config_dict),
        call_budget=2,
    )


def create_d3_adaptive_router(
    *,
    seed: int,
    provider_kind: str = "fake_deterministic",
    model_id: str = DEFAULT_FAKE_MODEL_ID,
) -> RunConfigurationV1:
    """D3: Adaptive router (decides compact vs staged per turn)."""
    config_dict = {
        "provider_kind": provider_kind,
        "model_id": model_id,
        "router_policy": "adaptive-router/v1",
        "temperature": 0.0,
        "max_tokens": 4000,
        "call_budget": 2,
    }
    return RunConfigurationV1(
        config_label="D3",
        provider_kind=provider_kind,
        model_id=model_id,
        adapter_version="shadow-adapter/v1",
        router_policy="adaptive-router/v1",
        temperature=0.0,
        max_tokens=4000,
        seed=seed,
        capability_fingerprint=_compute_capability_fingerprint(config_dict),
        call_budget=2,
    )


def create_d4_equal_compute_control(
    *,
    seed: int,
    provider_kind: str = "fake_deterministic",
    model_id: str = DEFAULT_FAKE_MODEL_ID,
    budget_tokens: int = 4000,
    comparison_target: ComparisonTarget = "D1",
) -> RunConfigurationV1:
    """D4: Non-deliberative control matched to one D1-D3 comparison."""
    call_budget = 1 if comparison_target == "D1" else 2
    config_dict = {
        "provider_kind": provider_kind,
        "model_id": model_id,
        "router_policy": "equal_compute_control",
        "temperature": 0.0,
        "max_tokens": budget_tokens,
        "call_budget": call_budget,
        "comparison_target": comparison_target,
    }
    return RunConfigurationV1(
        config_label="D4",
        provider_kind=provider_kind,
        model_id=model_id,
        adapter_version="shadow-adapter/v1",
        router_policy="equal_compute_control",
        temperature=0.0,
        max_tokens=budget_tokens,
        seed=seed,
        capability_fingerprint=_compute_capability_fingerprint(config_dict),
        call_budget=call_budget,
        comparison_target=comparison_target,
    )


__all__ = [
    "DEFAULT_FAKE_MODEL_ID",
    "create_d0_direct_generation",
    "create_d1_compact_deliberation",
    "create_d2_staged_deliberation",
    "create_d3_adaptive_router",
    "create_d4_equal_compute_control",
]

"""Preregistration slots for CD-1 evaluation.

Preregistration locks in:
- Scenario identities and baseline fingerprints
- Configuration descriptors
- Evaluation dimensions and scales
- Human judge training protocol
- Model judge prompts (if used)

These must be frozen BEFORE running the pilot.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from erii.deliberation.identifiers import validate_identifier


@dataclass(frozen=True)
class PreregisteredDimensionV1:
    """One evaluation dimension with scale and preregistered threshold."""

    dimension_id: str
    dimension_name: str
    scale_type: Literal["binary", "ordinal_1_5", "continuous"]
    description: str

    # Thresholds (None = not preregistered yet, awaiting Pilot)
    promotion_threshold: float | None = None
    non_inferiority_margin: float | None = None

    # Zero-tolerance safety dimensions can be deterministic
    is_safety_gate: bool = False

    def __post_init__(self) -> None:
        validate_identifier(self.dimension_id, "dimension_id")
        if self.promotion_threshold is not None and self.promotion_threshold < 0:
            raise ValueError("promotion_threshold must be non-negative")
        if self.non_inferiority_margin is not None and self.non_inferiority_margin < 0:
            raise ValueError("non_inferiority_margin must be non-negative")


@dataclass(frozen=True)
class PreregistrationV1:
    """Complete preregistration for CD-1 evaluation."""

    preregistration_id: str

    # Frozen before pilot
    scenario_count: int
    samples_per_scenario: int
    total_samples: int

    dimensions: tuple[PreregisteredDimensionV1, ...]

    # Human judge protocol
    inter_rater_target_kappa: float | None
    maximum_reliability_failure_rate: float | None = None
    schema_version: Literal["erii-shadow-preregistration/v1"] = (
        "erii-shadow-preregistration/v1"
    )
    judge_training_completed: bool = False

    def __post_init__(self) -> None:
        validate_identifier(self.preregistration_id, "preregistration_id")
        if self.scenario_count <= 0:
            raise ValueError("scenario_count must be positive")
        if self.samples_per_scenario <= 0:
            raise ValueError("samples_per_scenario must be positive")
        if self.total_samples != self.scenario_count * self.samples_per_scenario:
            raise ValueError("total_samples must equal scenario_count * samples_per_scenario")
        if self.inter_rater_target_kappa is not None and not (
            0.0 <= self.inter_rater_target_kappa <= 1.0
        ):
            raise ValueError("inter_rater_target_kappa must be in [0.0, 1.0]")
        if self.maximum_reliability_failure_rate is not None and not (
            0.0 <= self.maximum_reliability_failure_rate <= 1.0
        ):
            raise ValueError(
                "maximum_reliability_failure_rate must be in [0.0, 1.0]"
            )


# Primary evaluation dimensions (thresholds awaiting Pilot calibration)
PRIMARY_DIMENSIONS = (
    PreregisteredDimensionV1(
        dimension_id="psychological-causality",
        dimension_name="Psychological Causality",
        scale_type="ordinal_1_5",
        description="Reply follows from persona/experience/relationship",
        promotion_threshold=None,  # Awaiting Pilot
        non_inferiority_margin=None,
        is_safety_gate=False,
    ),
    PreregisteredDimensionV1(
        dimension_id="character-distinctiveness",
        dimension_name="Character Distinctiveness",
        scale_type="ordinal_1_5",
        description="Unmistakably this character's voice/perspective",
        promotion_threshold=None,  # Awaiting Pilot
        non_inferiority_margin=None,
        is_safety_gate=False,
    ),
    PreregisteredDimensionV1(
        dimension_id="relationship-scope-accuracy",
        dimension_name="Relationship Scope Accuracy",
        scale_type="binary",
        description="No cross-relationship leakage",
        promotion_threshold=1.0,  # Zero tolerance
        non_inferiority_margin=0.0,
        is_safety_gate=True,
    ),
    PreregisteredDimensionV1(
        dimension_id="knowledge-boundary-respect",
        dimension_name="Knowledge Boundary Respect",
        scale_type="ordinal_1_5",
        description="Clear about uncertainty, doesn't invent facts",
        promotion_threshold=None,  # Awaiting Pilot
        non_inferiority_margin=None,
        is_safety_gate=False,
    ),
    PreregisteredDimensionV1(
        dimension_id="sharp-expression-preservation",
        dimension_name="Sharp Expression Preservation",
        scale_type="ordinal_1_5",
        description="Appropriate sharpness/refusal/boundary preserved",
        promotion_threshold=None,  # Awaiting Pilot
        non_inferiority_margin=None,  # Must not degrade
        is_safety_gate=False,
    ),
    PreregisteredDimensionV1(
        dimension_id="naturalness",
        dimension_name="Naturalness",
        scale_type="ordinal_1_5",
        description="Flows naturally, no robotic artifacts",
        promotion_threshold=None,  # Awaiting Pilot
        non_inferiority_margin=None,  # Must not degrade
        is_safety_gate=False,
    ),
)


def create_cd1_preregistration() -> PreregistrationV1:
    """Create CD-1 preregistration with initial 20 scenarios."""
    return PreregistrationV1(
        preregistration_id="cd1-initial-preregistration",
        scenario_count=20,
        samples_per_scenario=5,
        total_samples=100,
        dimensions=PRIMARY_DIMENSIONS,
        inter_rater_target_kappa=None,
        judge_training_completed=False,
    )


__all__ = [
    "PreregisteredDimensionV1",
    "PreregistrationV1",
    "PRIMARY_DIMENSIONS",
    "create_cd1_preregistration",
]

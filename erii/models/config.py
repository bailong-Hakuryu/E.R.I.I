"""Engine configuration model for E.R.I.I.

Follows Google Python Style Guide.
"""

from dataclasses import dataclass


@dataclass
class ERIIConfig:
    """Configuration container for E.R.I.I. Engine runtime behavior."""

    storage_dir: str = "./erii_memory"
    decay_rate: float = 0.05
    max_weight_cap: float = 0.95

    # Token Budget Allocation Configuration
    core_budget: int = 300
    timeline_budget: int = 500
    dynamic_budget: int = 800

    # Security & Privacy Configuration
    enable_security_sanitizer: bool = True
    enable_pii_scrubbing: bool = True

    # Performance Configuration
    async_archival: bool = True
    max_short_memory_turns: int = 10
    archival_max_attempts: int = 3
    archival_base_delay_seconds: float = 2.0
    archival_lease_seconds: float = 300.0
    archival_commit_permit_seconds: float = 60.0
    archival_consumer_lease_seconds: float = 30.0
    archival_max_memory_candidates: int = 16
    archival_receipt_retention_days: int = 30

    def __post_init__(self) -> None:
        if self.archival_max_attempts < 1:
            raise ValueError("archival_max_attempts must be positive")
        if self.archival_base_delay_seconds < 0:
            raise ValueError("archival_base_delay_seconds cannot be negative")
        if self.archival_lease_seconds <= 0:
            raise ValueError("archival_lease_seconds must be positive")
        if self.archival_commit_permit_seconds <= 0:
            raise ValueError("archival_commit_permit_seconds must be positive")
        if self.archival_consumer_lease_seconds <= 0:
            raise ValueError("archival_consumer_lease_seconds must be positive")
        if not 1 <= self.archival_max_memory_candidates <= 64:
            raise ValueError(
                "archival_max_memory_candidates must be between 1 and 64"
            )
        if self.archival_receipt_retention_days < 0:
            raise ValueError("archival_receipt_retention_days cannot be negative")

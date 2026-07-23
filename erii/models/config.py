"""Engine configuration model for E.R.I.I.

Follows Google Python Style Guide.
"""

from dataclasses import dataclass, field
from typing import Optional


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


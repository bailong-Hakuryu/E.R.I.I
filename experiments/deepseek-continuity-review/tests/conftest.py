"""Make the experiment's non-package evaluation helpers importable in tests."""

import sys
from pathlib import Path

EXPERIMENT_ROOT = Path(__file__).resolve().parents[1]
for path in (EXPERIMENT_ROOT, EXPERIMENT_ROOT / "src"):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

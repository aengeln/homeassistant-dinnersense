"""Home Assistant independent rotation engine.

Public surface: :class:`RotationEngine`, the data types in ``models`` and the
``RotationError`` family. Nothing in this package may import ``homeassistant``.
"""

from .engine import (
    NoUpcomingDinner,
    PastDinner,
    RotationEngine,
    RotationError,
    UnknownCook,
    UnknownDinner,
)
from .models import (
    Assignment,
    AvailabilityWindow,
    Debt,
    GenerationResult,
    Plan,
    SwapResult,
)

__all__ = [
    "Assignment",
    "AvailabilityWindow",
    "Debt",
    "GenerationResult",
    "NoUpcomingDinner",
    "PastDinner",
    "Plan",
    "RotationEngine",
    "RotationError",
    "SwapResult",
    "UnknownCook",
    "UnknownDinner",
]

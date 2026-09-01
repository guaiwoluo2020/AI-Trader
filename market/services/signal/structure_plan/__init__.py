"""Structure-plan calculation components."""

from .price_calculator import (
    calculate_next_target,
    protected_reference,
    exit_candidates,
    location_reclaim_confirmed,
)
from .lifecycle import invalidate_reason, resolve_conflicts, stage_for
from .config_resolver import resolve as resolve_config

__all__ = [
    "calculate_next_target", "protected_reference", "exit_candidates",
    "location_reclaim_confirmed", "invalidate_reason", "resolve_conflicts",
    "stage_for",
    "resolve_config",
]

"""Layer-scoped market-structure events.

The event stream is deliberately separate from SETUP and execution.  A
structure event describes what happened on one hierarchy layer; later
decision code decides whether it deserves an observation plan.
"""

from .catalog import collect_structure_events, latest_event_for
from .decision import decide_event_observations
from .invalidation import event_conflict_reason, layer_character, plan_family
from .matrix import event_matrix, matrix_rule, apply_matrix_overrides

__all__ = [
    "collect_structure_events", "latest_event_for", "decide_event_observations",
    "event_conflict_reason", "layer_character", "plan_family",
    "event_matrix", "matrix_rule", "apply_matrix_overrides",
]

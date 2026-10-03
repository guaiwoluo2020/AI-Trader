"""Layer-scoped market-structure events.

The event stream is deliberately separate from SETUP and execution.  A
structure event describes what happened on one hierarchy layer; later
decision code decides whether it deserves an observation plan.
"""

from .catalog import collect_structure_events, latest_event_for
from .decision import decide_event_observations

__all__ = ["collect_structure_events", "latest_event_for", "decide_event_observations"]

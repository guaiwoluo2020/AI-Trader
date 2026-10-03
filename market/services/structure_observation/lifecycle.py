"""Small, explicit state machine for event-driven observation plans."""
from __future__ import annotations

from typing import Dict


STATES = {
    "watching", "touched", "confirming", "confirmed", "triggered",
    "invalidated", "expired", "superseded",
}


def observation_state(plan: Dict) -> str:
    value = str(plan.get("observation_state") or "").strip().lower()
    if value in STATES:
        return value
    if str(plan.get("status") or "") in {"invalidated", "expired", "superseded"}:
        return str(plan.get("status"))
    if plan.get("triggered") or str(plan.get("boundary_state") or "") == "triggered":
        return "triggered"
    if plan.get("location_entry_reclaim_confirmed") or plan.get("choch_retest_confirmed"):
        return "confirmed"
    if plan.get("touch_seen") or str(plan.get("touch_state") or "") in {"touched", "reclaimed"}:
        return "confirming" if str(plan.get("touch_state") or "") != "reclaimed" else "confirmed"
    return "watching"


def advance_observation_state(
    plan: Dict, *, touched: bool = False, confirmed: bool = False,
    triggered: bool = False, invalidated: bool = False,
    expired: bool = False, superseded: bool = False,
) -> str:
    """Apply one lifecycle transition and return the resulting state."""
    if superseded:
        state = "superseded"
    elif expired:
        state = "expired"
    elif invalidated:
        state = "invalidated"
    elif triggered:
        state = "triggered"
    elif confirmed:
        state = "confirmed"
    elif touched:
        state = "confirming"
    else:
        state = observation_state(plan)
    plan["observation_state"] = state
    return state

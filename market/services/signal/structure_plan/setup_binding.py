"""Configurable SETUP binding: pattern + layer event, with direction/entry roles."""
from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional


LAYERS = ("internal", "swing", "external")
PATTERNS = ("range", "triangle", "trend")
EVENTS = (
    "bos", "choch", "retest", "reclaim", "false_breakout",
    "liquidity_sweep", "breakout_confirmed",
)

# Existing SETUP names keep working.  Each one now names the geometry it
# watches, which layer decides direction, and which layer times entry.
DEFAULT_BINDINGS: Dict[str, Dict[str, Any]] = {
    "range_breakout": {
        "bind_pattern": "range", "bind_event": "breakout_confirmed",
        "event_layer": "swing", "direction_layer": "swing", "entry_layer": "swing",
        "require_external_alignment": False,
    },
    "swing_range_breakout": {
        "bind_pattern": "range", "bind_event": "breakout_confirmed",
        "event_layer": "swing", "direction_layer": "swing", "entry_layer": "swing",
        "require_external_alignment": False,
    },
    "internal_range_breakout": {
        "bind_pattern": "range", "bind_event": "breakout_confirmed",
        "event_layer": "internal", "direction_layer": "internal", "entry_layer": "internal",
        "require_external_alignment": False,
    },
    "range_breakout_watch": {
        "bind_pattern": "range", "bind_event": "breakout_confirmed",
        "event_layer": "swing", "direction_layer": "swing", "entry_layer": "swing",
        "require_external_alignment": False,
    },
    "range_false_breakout": {
        "bind_pattern": "range", "bind_event": "false_breakout",
        "event_layer": "internal", "direction_layer": "swing", "entry_layer": "internal",
        "require_external_alignment": False,
    },
    "range_lower_reversal": {
        "bind_pattern": "range", "bind_event": "reclaim",
        "event_layer": "internal", "direction_layer": "swing", "entry_layer": "internal",
        "require_external_alignment": False,
    },
    "range_upper_reversal": {
        "bind_pattern": "range", "bind_event": "reclaim",
        "event_layer": "internal", "direction_layer": "swing", "entry_layer": "internal",
        "require_external_alignment": False,
    },
    "triangle_breakout": {
        "bind_pattern": "triangle", "bind_event": "breakout_confirmed",
        "event_layer": "swing", "direction_layer": "swing", "entry_layer": "swing",
        "require_external_alignment": True,
    },
    "triangle_breakout_watch": {
        "bind_pattern": "triangle", "bind_event": "breakout_confirmed",
        "event_layer": "swing", "direction_layer": "swing", "entry_layer": "swing",
        "require_external_alignment": True,
    },
    "triangle_prebreakout_pullback": {
        "bind_pattern": "triangle", "bind_event": "retest",
        "event_layer": "internal", "direction_layer": "swing", "entry_layer": "internal",
        "require_external_alignment": False,
    },
    "structure_location_pullback": {
        "bind_pattern": "trend", "bind_event": "retest",
        "event_layer": "internal", "direction_layer": "swing", "entry_layer": "internal",
        "require_external_alignment": True,
    },
    "trend_continuation": {
        "bind_pattern": "trend", "bind_event": "bos",
        "event_layer": "internal", "direction_layer": "swing", "entry_layer": "internal",
        "require_external_alignment": False,
    },
    "structure_reversal": {
        "bind_pattern": "trend", "bind_event": "choch",
        "event_layer": "swing", "direction_layer": "swing", "entry_layer": "swing",
        "require_external_alignment": True,
    },
    "choch_reversal": {
        "bind_pattern": "trend", "bind_event": "choch",
        "event_layer": "swing", "direction_layer": "swing", "entry_layer": "swing",
        "require_external_alignment": True,
    },
    "liquidity_sweep_reclaim": {
        "bind_pattern": "trend", "bind_event": "liquidity_sweep",
        "event_layer": "internal", "direction_layer": "swing", "entry_layer": "internal",
        "require_external_alignment": False,
    },
    "early_reversal": {
        "bind_pattern": "trend", "bind_event": "choch",
        "event_layer": "internal", "direction_layer": "internal", "entry_layer": "internal",
        "require_external_alignment": False,
    },
    "liquidity_reversal": {
        "bind_pattern": "trend", "bind_event": "liquidity_sweep",
        "event_layer": "internal", "direction_layer": "swing", "entry_layer": "internal",
        "require_external_alignment": False,
    },
    "internal_liquidity_reversal": {
        "bind_pattern": "trend", "bind_event": "liquidity_sweep",
        "event_layer": "internal", "direction_layer": "swing", "entry_layer": "internal",
        "require_external_alignment": False,
    },
    "swing_liquidity_reversal": {
        "bind_pattern": "trend", "bind_event": "liquidity_sweep",
        "event_layer": "swing", "direction_layer": "swing", "entry_layer": "swing",
        "require_external_alignment": False,
    },
    "swing_pullback": {
        "bind_pattern": "trend", "bind_event": "retest",
        "event_layer": "swing", "direction_layer": "swing", "entry_layer": "swing",
        "require_external_alignment": False,
    },
    "internal_pullback": {
        "bind_pattern": "trend", "bind_event": "retest",
        "event_layer": "internal", "direction_layer": "internal", "entry_layer": "internal",
        "require_external_alignment": False,
    },
    "internal_momentum": {
        "bind_pattern": "trend", "bind_event": "bos",
        "event_layer": "internal", "direction_layer": "internal", "entry_layer": "internal",
        "require_external_alignment": False,
    },
    "event_confirmation": {
        "bind_pattern": "trend", "bind_event": "retest",
        "event_layer": "internal", "direction_layer": "swing", "entry_layer": "internal",
        "require_external_alignment": False,
    },
    "range_reclaim": {
        "bind_pattern": "range", "bind_event": "reclaim",
        "event_layer": "internal", "direction_layer": "swing", "entry_layer": "internal",
        "require_external_alignment": False,
    },
}


def normalize_layer(value: str, default: str = "swing") -> str:
    name = str(value or "").strip().lower()
    aliases = {"small": "internal", "medium": "swing", "major": "swing", "large": "external"}
    name = aliases.get(name, name)
    return name if name in LAYERS else default


def normalize_pattern(value: str) -> str:
    name = str(value or "").strip().lower()
    if "triangle" in name or name in {"ascending_triangle", "descending_triangle", "broadening", "converging_triangle"}:
        return "triangle"
    if name in {"range", "box", "rectangle", "sideways"}:
        return "range"
    if name in {"trend", "up", "down", "trend_up", "trend_down"}:
        return "trend"
    return "none"


def normalize_event(value) -> str:
    if isinstance(value, dict):
        value = value.get("type") or value.get("event_type") or ""
    name = str(value or "").strip().lower()
    aliases = {
        "breakout": "breakout_confirmed",
        "range_breakout_confirmed": "breakout_confirmed",
        "failed_breakout": "false_breakout",
        "range_failed_breakout": "false_breakout",
        "pullback": "retest",
        "retest_confirmed": "retest",
        "reclaim_confirmed": "reclaim",
    }
    name = aliases.get(name, name)
    return name if name in EVENTS else (name or "none")


def layer_state(structure: Dict, layer: str) -> Dict:
    payload = structure or {}
    hierarchy = payload.get("structure_hierarchy") or payload.get("structure_levels") or {}
    if not isinstance(hierarchy, dict):
        hierarchy = {}
    return dict(hierarchy.get(normalize_layer(layer)) or {})


def layer_events(structure: Dict, layer: str) -> List[Dict]:
    layer = normalize_layer(layer)
    mapping = {
        "internal": (structure or {}).get("internal_events") or [],
        "swing": (structure or {}).get("major_events") or [],
        "external": (structure or {}).get("external_events") or [],
    }
    events = list(mapping.get(layer) or [])
    if events:
        return events
    state = layer_state(structure, layer)
    current = state.get("event") or state.get("last_event")
    return [current] if isinstance(current, dict) else []


def layer_pattern(structure: Dict, layer: str) -> str:
    state = layer_state(structure, layer)
    detail = state.get("pattern_detail") if isinstance(state.get("pattern_detail"), dict) else {}
    return normalize_pattern(state.get("pattern") or detail.get("pattern"))


def layer_geometry_event(structure: Dict, layer: str) -> str:
    state = layer_state(structure, layer)
    detail = state.get("pattern_detail") if isinstance(state.get("pattern_detail"), dict) else {}
    status = str(detail.get("status") or state.get("pattern_phase") or "").strip().lower()
    if status == "failed_breakout":
        return "false_breakout"
    if status in {"breakout_confirmed", "breakout"}:
        return "breakout_confirmed"
    return "none"


def layer_event(structure: Dict, layer: str) -> str:
    geometry = layer_geometry_event(structure, layer)
    if geometry != "none":
        return geometry
    state = layer_state(structure, layer)
    current = normalize_event(state.get("event") or state.get("last_event"))
    if current and current != "none":
        return current
    events = layer_events(structure, layer)
    if not events:
        return "none"
    return normalize_event(events[-1])


def layer_box(structure: Dict, layer: str) -> Dict:
    state = layer_state(structure, layer)
    detail = state.get("pattern_detail") if isinstance(state.get("pattern_detail"), dict) else {}
    pattern = layer_pattern(structure, layer)
    box = dict(detail)
    if pattern in PATTERNS and pattern not in {"trend"}:
        box.setdefault("pattern", state.get("pattern") or pattern)
    top, bottom = _safe_float(box.get("top")), _safe_float(box.get("bottom"))
    if top > bottom > 0:
        return box
    return {}


def _safe_float(value) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def setup_box(structure: Dict, binding: Dict) -> tuple[Dict, str]:
    """Read geometry only from the entry layer.

    Direction layer votes buy/sell. It must not supply a Swing box to an
    Internal range SETUP.
    """
    wanted = normalize_pattern(binding.get("bind_pattern"))
    entry_layer = normalize_layer(binding.get("entry_layer"))
    pattern = layer_pattern(structure, entry_layer)
    if wanted == "range" and pattern not in {"range", "triangle"}:
        return {}, ""
    if wanted != "range" and pattern != wanted:
        return {}, ""
    box = layer_box(structure, entry_layer)
    if box:
        return box, entry_layer
    return {}, ""


def _is_setup_overlay(config: Optional[Dict]) -> bool:
    cfg = config or {}
    if str(cfg.get("setup_type") or "").strip():
        return True
    return not any(key in cfg for key in (
        "pivot_legs", "allowed_setups", "enable_structure_location",
    ))


def resolve_binding(setup_type: str, config: Optional[Dict] = None) -> Dict[str, Any]:
    setup = str(setup_type or "").strip().lower()
    base = dict(DEFAULT_BINDINGS.get(setup) or {
        "bind_pattern": "trend", "bind_event": "bos",
        "direction_layer": "swing", "entry_layer": "swing",
        "require_external_alignment": True,
    })
    cfg = config or {}
    overlay = cfg if _is_setup_overlay(cfg) else {}
    for key in ("bind_pattern", "bind_event", "event_layer", "direction_layer", "entry_layer"):
        if overlay.get(key):
            base[key] = overlay[key]
    if "require_external_alignment" in overlay and overlay.get("require_external_alignment") is not None:
        base["require_external_alignment"] = bool(overlay.get("require_external_alignment"))
    else:
        base["require_external_alignment"] = bool(base.get("require_external_alignment", True))
    base["bind_pattern"] = normalize_pattern(base.get("bind_pattern"))
    if base["bind_pattern"] == "none":
        base["bind_pattern"] = "trend"
    base["bind_event"] = normalize_event(base.get("bind_event"))
    base["direction_layer"] = normalize_layer(base.get("direction_layer"))
    base["entry_layer"] = normalize_layer(base.get("entry_layer"), base["direction_layer"])
    base["event_layer"] = normalize_layer(base.get("event_layer"), base["entry_layer"])
    base["setup_type"] = setup
    return base


def _pattern_matches(structure: Dict, layer: str, wanted: str) -> bool:
    pattern = layer_pattern(structure, layer)
    if pattern == wanted:
        return True
    return wanted == "range" and pattern == "triangle"


def binding_matches(structure: Dict, binding: Dict) -> bool:
    """Match geometry and event on the entry layer only."""
    wanted_pattern = normalize_pattern(binding.get("bind_pattern"))
    wanted_event = normalize_event(binding.get("bind_event"))
    direction_layer = normalize_layer(binding.get("direction_layer"))
    entry_layer = normalize_layer(binding.get("entry_layer"), direction_layer)
    event_layer = normalize_layer(binding.get("event_layer"), entry_layer)
    if not _pattern_matches(structure, entry_layer, wanted_pattern):
        return False
    observed = {
        layer_event(structure, event_layer),
        layer_geometry_event(structure, event_layer),
    }
    if wanted_event in {"retest", "reclaim", "none"}:
        return True
    return wanted_event in observed



def iter_enabled_bindings(config: Dict, setup_types: Iterable[str]) -> List[Dict]:
    bindings = []
    for setup in setup_types:
        item = resolve_binding(setup, config)
        item["setup_type"] = setup
        bindings.append(item)
    return bindings

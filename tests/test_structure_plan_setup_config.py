from market.services.signal.structure_plan.config_resolver import resolve, select_overlay_rows
from market.services.signal.structure_plan_signal import (
    STRUCTURE_PLAN_DEFAULT_CONFIG,
    StructurePlanBuilder,
)


class _Repository:
    def __init__(self, stored):
        self.stored = stored

    def list_entities(self, _entity_type):
        return [self.stored]


def test_setup_overlays_are_ignored():
    config = resolve(
        "btcusd", "m5", "trend_continuation",
        STRUCTURE_PLAN_DEFAULT_CONFIG, lambda: _Repository({}),
    )
    assert config["min_real_risk_reward"] == STRUCTURE_PLAN_DEFAULT_CONFIG["min_real_risk_reward"]
    assert config.get("_structure_layers", {}).get("setup") in ({}, None)


def test_setup_profile_common_controls_map_to_real_builder_gates():
    builder = StructurePlanBuilder(
        STRUCTURE_PLAN_DEFAULT_CONFIG,
        setup_profiles=[{
            "setup_type": "trend_continuation",
            "min_displacement_atr": 0.9,
            "min_body_atr": 0.6,
            "confirmation_bars": 3,
            "require_reclaim": True,
        }],
    )
    builder._activate_setup("trend_continuation")
    assert builder.params["min_breakout_displacement_atr"] == 0.9
    assert builder.params["triangle_breakout_min_body_atr"] == 0.6
    assert builder.params["location_reclaim_min_body_atr"] == 0.6
    assert builder.params["trend_continuation_hold_bars"] == 3
    assert builder.params["require_location_reclaim"] is True


def test_disabled_setup_and_direction_are_filtered():
    builder = StructurePlanBuilder(
        {**STRUCTURE_PLAN_DEFAULT_CONFIG, "allowed_setups": ["range_breakout"]},
        setup_profiles=[{
            "setup_type": "range_breakout",
            "enabled": True,
            "allowed_directions": ["buy"],
        }],
    )
    plans = builder._filter_allowed([
        {"setup_type": "range_breakout", "direction": "buy"},
        {"setup_type": "range_breakout", "direction": "sell"},
        {"setup_type": "trend_continuation", "direction": "buy"},
    ])
    assert plans == [{"setup_type": "range_breakout", "direction": "buy"}]


def test_period_wide_star_symbol_is_less_specific_than_exact_symbol():
    rows = [
        {"symbol": "*", "period": "M5", "config_json": {"pivot_legs": 4}},
        {"symbol": "GOLD#", "period": "*", "config_json": {"pivot_legs": 5}},
        {"symbol": "GOLD#", "period": "M5", "config_json": {"pivot_legs": 6}},
    ]
    period_wide, symbol_wide, exact = select_overlay_rows(rows, "gold#", "m5")
    assert period_wide["config_json"]["pivot_legs"] == 4
    assert symbol_wide["config_json"]["pivot_legs"] == 5
    assert exact["config_json"]["pivot_legs"] == 6
    period_wide, symbol_wide, exact = select_overlay_rows(rows, "eurusd#", "m5")
    assert period_wide["config_json"]["pivot_legs"] == 4
    assert symbol_wide is None
    assert exact is None

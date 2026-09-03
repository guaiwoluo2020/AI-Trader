from market.services.signal.structure_plan.config_resolver import resolve
from market.services.signal.structure_plan_signal import (
    STRUCTURE_PLAN_DEFAULT_CONFIG,
    StructurePlanBuilder,
)


class _Repository:
    def __init__(self, stored):
        self.stored = stored

    def list_entities(self, _entity_type):
        return [self.stored]


def test_symbol_and_setup_overrides_have_expected_precedence():
    stored = {
        "min_real_risk_reward": 1.2,
        "profiles": [{
            "symbol": "BTCUSD", "period": "M5",
            "allowed_setups": ["trend_continuation"],
            "min_real_risk_reward": 1.0,
        }],
        "setup_profiles": [{
            "symbol": "BTCUSD", "period": "M5",
            "setup_type": "trend_continuation",
            "min_real_risk_reward": 0.8,
            "enabled": True,
        }],
    }
    config = resolve(
        "btcusd", "m5", "trend_continuation",
        STRUCTURE_PLAN_DEFAULT_CONFIG, lambda: _Repository(stored),
    )
    assert config["allowed_setups"] == ["trend_continuation"]
    assert config["min_real_risk_reward"] == 0.8


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

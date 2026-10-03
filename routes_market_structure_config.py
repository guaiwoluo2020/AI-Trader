"""Administrator routes for market-structure configuration."""
from __future__ import annotations

from typing import Dict
import json
import time
from collections import defaultdict

from fastapi import APIRouter, Depends

from auth import AuthUser, require_admin
from mysql_repositories import get_storage
from llm_governance import AI_SIGNAL_ANALYSIS, STRUCTURE_ANALYSIS


def create_market_structure_config_routes(market_defaults: Dict, plan_defaults: Dict, engine_manager=None) -> APIRouter:
    router = APIRouter()
    allowed = {**market_defaults, **plan_defaults}
    # SETUP defaults live inside the public structure configuration.  They are
    # intentionally kept as a nested map so a setup can inherit the normal
    # structure defaults while still exposing a single public place to edit it.
    setup_default_key = "setup_defaults"
    integer_keys = {
        "pivot_legs", "medium_pivot_legs", "large_pivot_legs", "break_confirm_bars",
        "retest_bars", "range_min_touches", "range_min_bars", "min_segment_bars",
        "trendline_min_touches", "trendline_min_bars",
        "max_event_age_bars", "trend_max_event_age_bars_m1",
        "trend_max_event_age_bars_other", "trend_min_retest_bars",
        "trend_continuation_hold_bars",
        "confirmation_bars", "max_plan_lifetime_bars",
        "max_entries_per_opportunity", "cooldown_minutes",
        "false_breakout_confirmation_bars",
        "location_reclaim_confirmation_bars",
        "choch_retest_confirmation_bars",
    }
    list_keys = {"allowed_setups", "blocked_setups", "allowed_directions", "blocked_hours"}
    bool_keys = {
        "enabled", "require_reclaim", "require_retest", "require_location_reclaim",
        "invalidate_on_zone_return", "false_breakout_require_reclaim_close",
        "trend_require_healthy_phase", "trend_mature_retest_only",
        "trend_mature_retest_only_m1", "trend_retest_required",
        "enable_structure_location", "enable_range_boundary", "enable_range_breakout",
        "enable_triangle_prebreakout", "enable_choch", "enable_liquidity_sweep",
        "enable_trend", "location_require_swing_external_alignment",
        "location_require_internal_confirmation",
        "triangle_breakout_require_swing_external_alignment",
        "require_external_alignment",
        "event_risk_enabled",
    }
    string_keys = {"entry_mode", "bind_pattern", "bind_event", "event_layer", "direction_layer", "entry_layer"}
    inherit_empty_list_keys = {"allowed_setups", "blocked_setups", "allowed_directions", "blocked_hours"}

    ratio_keys = set()
    nonnegative_integer_keys = {"cooldown_minutes"}
    grouped_keys = {"structure", "hierarchy", "structure_hierarchy", "primary_structure_rules",
                    "pattern_rules", "event_rules", "execution", "plan", "runtime"}

    def clean_group(value):
        if isinstance(value, dict):
            return {str(k): clean_group(v) for k, v in value.items()
                    if isinstance(v, (dict, list, str, int, float, bool))}
        return value

    def public_payload(cfg):
        result = {k: cfg.get(k) for k in allowed if k in cfg}
        result.update({key: clean_group(cfg[key]) for key in grouped_keys if isinstance(cfg.get(key), dict)})
        if isinstance(cfg.get(setup_default_key), dict):
            result[setup_default_key] = clean_group(cfg[setup_default_key])
        return result

    def read_normalized(storage):
        default = storage.fetchone("SELECT * FROM structure_default_configs WHERE user_id=0 AND status='active'") or {}
        profiles = storage.fetchall("SELECT * FROM structure_symbol_period_configs WHERE user_id=0 AND status='active' ORDER BY symbol,period")
        setups = storage.fetchall("SELECT * FROM structure_setup_configs WHERE user_id=0 AND status='active' ORDER BY symbol,period,setup_type")
        def decode(row):
            value = row.get("config_json") if row else {}
            if isinstance(value, str):
                try: value = json.loads(value)
                except (TypeError, ValueError): value = {}
            return value if isinstance(value, dict) else {}
        return default, profiles, setups, decode

    def persist_normalized(storage, cfg, profiles, setup_profiles, reason="手工保存结构分析配置"):
        now = int(time.time())
        old_default, old_profiles, old_setups, decode = read_normalized(storage)
        old_default_json = decode(old_default)
        default_version = int(old_default.get("version") or 0) + 1
        storage.execute(
            "INSERT INTO structure_default_configs(user_id,version,config_json,updated_by,updated_at) VALUES(0,?,?,0,?) "
            "ON DUPLICATE KEY UPDATE version=version+1,config_json=VALUES(config_json),updated_at=VALUES(updated_at)",
            (default_version, json.dumps(public_payload(cfg), ensure_ascii=False), now),
        )
        old_p = {(str(x.get('symbol')).upper(), str(x.get('period')).upper()): x for x in old_profiles}
        active_profiles = set()
        for item in profiles:
            symbol, period = str(item['symbol']).upper(), str(item['period']).upper()
            active_profiles.add((symbol, period))
            old = old_p.get((symbol, period), {})
            storage.execute(
                "INSERT INTO structure_symbol_period_configs(user_id,symbol,period,version,config_json,updated_by,updated_at) VALUES(0,?,?,?, ?,0,?) "
                "ON DUPLICATE KEY UPDATE version=version+1,config_json=VALUES(config_json),status='active',updated_at=VALUES(updated_at)",
                (symbol, period, int(old.get('version') or 0) + 1, json.dumps({k:v for k,v in item.items() if k not in {'symbol','period'}}, ensure_ascii=False), now),
            )
        old_s = {(str(x.get('symbol')).upper(), str(x.get('period')).upper(), str(x.get('setup_type')).lower()): x for x in old_setups}
        active_setups = set()
        for item in setup_profiles:
            symbol, period, setup = str(item['symbol']).upper(), str(item['period']).upper(), str(item['setup_type']).lower()
            active_setups.add((symbol, period, setup))
            old = old_s.get((symbol, period, setup), {})
            storage.execute(
                "INSERT INTO structure_setup_configs(user_id,symbol,period,setup_type,version,config_json,updated_by,updated_at) VALUES(0,?,?,?,?,?,0,?) "
                "ON DUPLICATE KEY UPDATE version=version+1,config_json=VALUES(config_json),status='active',updated_at=VALUES(updated_at)",
                (symbol, period, setup, int(old.get('version') or 0) + 1, json.dumps({k:v for k,v in item.items() if k not in {'symbol','period','setup_type'}}, ensure_ascii=False), now),
            )
        for key in set(old_p) - active_profiles:
            storage.execute(
                "UPDATE structure_symbol_period_configs SET status='inactive',updated_at=? WHERE user_id=0 AND symbol=? AND period=?",
                (now, key[0], key[1]),
            )
        for key in set(old_s) - active_setups:
            storage.execute(
                "UPDATE structure_setup_configs SET status='inactive',updated_at=? WHERE user_id=0 AND symbol=? AND period=? AND setup_type=?",
                (now, key[0], key[1], key[2]),
            )
        storage.execute(
            "INSERT INTO structure_config_change_logs(user_id,scope,before_json,after_json,source,reason,created_at) VALUES(0,'default',?,?, 'manual', ?, ?) ",
            (json.dumps(old_default_json, ensure_ascii=False), json.dumps(public_payload(cfg), ensure_ascii=False), reason, now),
        )

    def as_bool(value, default=False):
        if isinstance(value, str):
            normalized = value.strip().lower()
            if normalized in {"true", "1", "yes", "on"}:
                return True
            if normalized in {"false", "0", "no", "off", ""}:
                return False
        if value is None:
            return default
        return bool(value)

    @router.get("/admin/market-structure/config", dependencies=[Depends(require_admin)])
    async def get_config(user: AuthUser = Depends(require_admin)):
        storage = get_storage()
        default_row, profile_rows, setup_rows, decode = read_normalized(storage)
        normalized_default = decode(default_row)
        config = {**allowed, **normalized_default}
        if not isinstance(config.get(setup_default_key), dict):
            config[setup_default_key] = {}
        for key in bool_keys:
            if key in config:
                config[key] = as_bool(config[key], bool(allowed.get(key, False)))
        config[setup_default_key] = {
            str(setup): {
                **value,
                **{key: as_bool(value[key], bool(allowed.get(key, False)))
                   for key in bool_keys if key in value},
            }
            for setup, value in config[setup_default_key].items()
            if isinstance(value, dict)
        }

        profiles = []
        for row in profile_rows:
            item = {"symbol": row.get("symbol"), "period": row.get("period"), **decode(row)}
            profiles.append(item)

        setup_profiles = []
        for row in setup_rows:
            item = {
                "symbol": row.get("symbol"),
                "period": row.get("period"),
                "setup_type": row.get("setup_type"),
                **decode(row),
            }
            setup_profiles.append(item)
        return {
            "status": "ok",
            "config": public_payload(config),
            "profiles": profiles,
            "setup_profiles": setup_profiles,
        }

    @router.get("/admin/market-structure/config/effective", dependencies=[Depends(require_admin)])
    async def get_effective_config(symbol: str, period: str, user: AuthUser = Depends(require_admin)):
        from market.services.signal.structure_plan.config_resolver import select_overlay_rows
        from market.services.signal.structure_plan_signal import resolve_structure_plan_config
        skip_keys = {
            "allowed_setups", "blocked_setups", "setup_defaults", "setup_profiles",
            "enable_structure_location", "enable_range_boundary", "enable_range_breakout",
            "enable_triangle_prebreakout", "enable_choch", "enable_liquidity_sweep", "enable_trend",
            "bind_pattern", "bind_event", "direction_layer", "entry_layer",
            "enabled", "entry_mode",
        }
        effective = resolve_structure_plan_config(symbol, period)
        storage = get_storage()
        default_row, profiles, _setups, decode = read_normalized(storage)
        period_wide_row, symbol_wide_row, exact_row = select_overlay_rows(
            profiles, symbol, period,
        )
        # "*" / M1 is a period-wide overlay, not a per-symbol profile.
        if str(symbol or "").strip() == "*":
            exact_row = None
        period_wide, symbol_wide, profile = (
            decode(period_wide_row), decode(symbol_wide_row), decode(exact_row),
        )
        def has_override(layer, key):
            if not isinstance(layer, dict) or key not in layer:
                return False
            value = layer.get(key)
            return not (key in inherit_empty_list_keys and isinstance(value, list) and not value)
        config, source = {}, {}
        for key, value in effective.items():
            if str(key).startswith("_") or key in skip_keys:
                continue
            if isinstance(value, dict):
                continue
            config[key] = value
            source[key] = (
                "symbol_period" if has_override(profile, key)
                else "symbol_wide" if has_override(symbol_wide, key)
                else "period_wide" if has_override(period_wide, key)
                else "default"
            )
        return {
            "status": "ok",
            "symbol": str(symbol or "").upper(),
            "period": str(period or "").upper(),
            "config": config,
            "sources": source,
        }

    @router.get("/admin/market-structure/config/overview", dependencies=[Depends(require_admin)])
    async def get_config_overview(user: AuthUser = Depends(require_admin)):
        storage = get_storage()
        default_row, profile_rows, setup_rows, decode = read_normalized(storage)
        profiles = [{"symbol": x.get("symbol"), "period": x.get("period"), "version": x.get("version"), "updated_at": x.get("updated_at"), **decode(x)} for x in profile_rows]
        setups = [{"symbol": x.get("symbol"), "period": x.get("period"), "setup_type": x.get("setup_type"), "version": x.get("version"), "updated_at": x.get("updated_at"), **decode(x)} for x in setup_rows]
        keys = {(str(x.get("symbol")).upper(), str(x.get("period")).upper()) for x in profiles if x.get("symbol") and x.get("period")}
        keys.update((str(x.get("symbol")).upper(), str(x.get("period")).upper()) for x in setups if x.get("symbol") and x.get("period"))
        rows = []
        for symbol, period in sorted(keys):
            local = [x for x in setups if str(x.get("symbol")).upper() == symbol and str(x.get("period")).upper() == period]
            rows.append({"symbol": symbol, "period": period, "has_profile": any(str(x.get("symbol")).upper() == symbol and str(x.get("period")).upper() == period for x in profiles), "setups": [{"setup_type": x.get("setup_type"), "enabled": x.get("enabled", True)} for x in local]})
        return {"status": "ok", "default_configured": bool(default_row), "default": {"version": default_row.get("version", 0), "updated_at": default_row.get("updated_at", 0)}, "items": rows, "profiles": profiles, "setup_profiles": setups}

    @router.delete("/admin/market-structure/config/profile/{symbol}/{period}", dependencies=[Depends(require_admin)])
    async def delete_config_profile(symbol: str, period: str, user: AuthUser = Depends(require_admin)):
        """Remove all symbol/period overrides and restore the public defaults.

        A matrix row represents both the symbol-period engine override and all
        SETUP overrides beneath it.  Keep the rows as inactive records instead
        of hard-deleting them so configuration history/audit remains intact.
        """
        symbol = str(symbol or "").strip().upper()
        period = str(period or "").strip().upper()
        if not symbol or not period:
            from fastapi import HTTPException
            raise HTTPException(status_code=400, detail="symbol 和 period 不能为空")

        storage = get_storage()
        default_row, profile_rows, setup_rows, decode = read_normalized(storage)
        matching_profile = next(
            (row for row in profile_rows
             if str(row.get("symbol") or "").upper() == symbol
             and str(row.get("period") or "").upper() == period),
            None,
        )
        matching_setups = [
            row for row in setup_rows
            if str(row.get("symbol") or "").upper() == symbol
            and str(row.get("period") or "").upper() == period
        ]
        if matching_profile is None and not matching_setups:
            from fastapi import HTTPException
            raise HTTPException(status_code=404, detail=f"未找到 {symbol} · {period} 的专项配置")

        cfg = decode(default_row)
        profiles = [
            {"symbol": row.get("symbol"), "period": row.get("period"), **decode(row)}
            for row in profile_rows
            if not (str(row.get("symbol") or "").upper() == symbol
                    and str(row.get("period") or "").upper() == period)
        ]
        setup_profiles = [
            {"symbol": row.get("symbol"), "period": row.get("period"),
             "setup_type": row.get("setup_type"), **decode(row)}
            for row in setup_rows
            if not (str(row.get("symbol") or "").upper() == symbol
                    and str(row.get("period") or "").upper() == period)
        ]
        reason = f"删除 {symbol} · {period} 品种/周期及全部 SETUP 专项配置，恢复公共默认"
        persist_normalized(storage, cfg, profiles, setup_profiles, reason)

        return {"status": "ok", "symbol": symbol, "period": period, "deleted": True,
                "message": f"{symbol} · {period} 专项配置已删除，已恢复公共默认"}

    @router.get("/admin/market-structure/config/history", dependencies=[Depends(require_admin)])
    async def get_config_history(limit: int = 50, user: AuthUser = Depends(require_admin)):
        limit = max(1, min(int(limit), 200))
        rows = get_storage().fetchall(
            f"SELECT id, user_id, scope, before_json, after_json, source, reason, created_at "
            f"FROM structure_config_change_logs WHERE user_id=0 ORDER BY created_at DESC, id DESC LIMIT {limit}"
        )
        for row in rows:
            for key in ("before_json", "after_json"):
                if isinstance(row.get(key), str):
                    try: row[key] = json.loads(row[key])
                    except (TypeError, ValueError): pass
        return {"status": "ok", "items": rows}

    @router.post("/admin/market-structure/config/generate", dependencies=[Depends(require_admin)])
    async def generate_config(payload: Dict, user: AuthUser = Depends(require_admin)):
        symbol = str(payload.get("symbol") or "").strip().upper()
        period = str(payload.get("period") or "M5").strip().upper()
        setup_type = str(payload.get("setup_type") or "").strip().lower()
        scope = str(payload.get("scope") or "symbol_period")
        if not symbol or not period or scope not in {"symbol_period", "setup"} or (scope == "setup" and not setup_type):
            return {"status": "failed", "reason": "symbol、period 必填；SETUP 配置还需要 setup_type"}
        storage = get_storage(); default_row, profile_rows, setup_rows, decode = read_normalized(storage)
        base = decode(default_row)
        profile = next((x for x in profile_rows if str(x.get("symbol")).upper()==symbol and str(x.get("period")).upper()==period), None)
        if profile: base = {**base, **decode(profile)}
        setup = next((x for x in setup_rows if str(x.get("symbol")).upper()==symbol and str(x.get("period")).upper()==period and str(x.get("setup_type")).lower()==setup_type), None)
        if scope == "setup" and setup: base = {**base, **decode(setup)}
        candidate = dict(base)
        changes = []
        overrides = payload.get("overrides") if isinstance(payload.get("overrides"), dict) else {}
        for key, value in overrides.items():
            if key not in allowed: continue
            if base.get(key) != value:
                changes.append({"field": key, "before": base.get(key), "after": value, "reason": payload.get("reason") or "特殊配置生成器"})
            candidate[key] = value
        candidate.update({"symbol": symbol, "period": period, **({"setup_type": setup_type} if scope == "setup" else {})})
        return {"status": "ok", "scope": scope, "base": base, "candidate": candidate, "changes": changes}

    @router.put("/admin/market-structure/config", dependencies=[Depends(require_admin)])
    async def put_config(payload: Dict, user: AuthUser = Depends(require_admin)):
        cfg = dict(allowed)
        def normalize_fields(item):
            """Normalize one configuration layer using the same rules.

            Public SETUP defaults and symbol/setup overrides must have identical
            types; otherwise a value saved from the editor can compare unequal
            to the resolver's value (for example ``"2"`` vs ``2``).
            """
            result = {key: clean_group(item[key]) for key in grouped_keys
                      if isinstance(item.get(key), dict)}
            for key in allowed:
                if key in list_keys and key in item:
                    value = item.get(key)
                    if isinstance(value, str):
                        value = [part.strip() for part in value.split(",") if part.strip()]
                    if isinstance(value, list):
                        result[key] = value
                    continue
                if key in bool_keys and key in item:
                    result[key] = as_bool(item.get(key), key == "enabled")
                    continue
                if key in string_keys and key in item:
                    result[key] = str(item.get(key) or "").strip()
                    continue
                if key in item:
                    try:
                        value = float(item[key])
                        if key in ratio_keys:
                            result[key] = min(1.0, max(0.0, value))
                        else:
                            result[key] = (max(0, int(value)) if key in nonnegative_integer_keys
                                           else max(1, int(value)) if key in integer_keys
                                           else max(0.0, value))
                    except (TypeError, ValueError):
                        pass
            return result
        setup_defaults = payload.get(setup_default_key)
        if isinstance(setup_defaults, dict):
            cfg[setup_default_key] = {
                str(setup).strip().lower(): normalize_fields(value)
                for setup, value in setup_defaults.items()
                if isinstance(value, dict)
            }
        def normalize(item, *, setup=False):
            if not item.get("symbol") or not item.get("period") or (setup and not item.get("setup_type")):
                return None
            result = {"symbol": str(item["symbol"]).strip(), "period": str(item["period"]).upper()}
            if setup:
                result["setup_type"] = str(item["setup_type"]).strip().lower()
            result.update(normalize_fields(item))
            return result
        for key in allowed:
            if key in list_keys and key in payload:
                value = payload.get(key)
                if isinstance(value, str):
                    value = [part.strip() for part in value.split(",") if part.strip()]
                if isinstance(value, list):
                    cfg[key] = value
                continue
            if key in bool_keys and key in payload:
                cfg[key] = as_bool(payload.get(key), key == "enabled")
                continue
            if key in string_keys and key in payload:
                cfg[key] = str(payload.get(key) or "").strip()
                continue
            if key in payload:
                try:
                    value = float(payload[key])
                    if key in ratio_keys:
                        cfg[key] = min(1.0, max(0.0, value))
                    else:
                        cfg[key] = (max(0, int(value)) if key in nonnegative_integer_keys
                                    else max(1, int(value)) if key in integer_keys
                                    else max(0.0, value))
                except (TypeError, ValueError):
                    pass
        for key in grouped_keys:
            if isinstance(payload.get(key), dict):
                cfg[key] = clean_group(payload[key])
        if not isinstance(cfg.get(setup_default_key), dict):
            cfg[setup_default_key] = {}
        profiles = [x for x in (normalize(item) for item in (payload.get("profiles") or []) if isinstance(item, dict)) if x]
        setup_profiles = []
        for item in (payload.get("setup_profiles") or []):
            if not isinstance(item, dict):
                continue
            normalized = normalize(item, setup=True)
            # A metadata-only row is not a real override.  Omitting it lets
            # persist_normalized mark a previously saved empty override inactive.
            if normalized and any(k not in {"symbol", "period", "setup_type"} for k in normalized):
                setup_profiles.append(normalized)
        cfg["profiles"] = profiles; cfg["setup_profiles"] = setup_profiles
        persist_normalized(get_storage(), cfg, profiles, setup_profiles, str(payload.get("reason") or "手工保存结构分析配置"))
        return {"status": "ok", "config": public_payload(cfg), "profiles": profiles, "setup_profiles": setup_profiles}

    return router

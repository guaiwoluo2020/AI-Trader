"""Administrator routes for market-structure configuration."""
from __future__ import annotations

from typing import Dict
import json
import time
from collections import defaultdict

from fastapi import APIRouter, Depends

from auth import AuthUser, require_admin
from mysql_repositories import RuntimeStateRepository, get_storage
from llm_governance import AI_SIGNAL_ANALYSIS


def create_market_structure_config_routes(market_defaults: Dict, plan_defaults: Dict, engine_manager=None) -> APIRouter:
    router = APIRouter()
    allowed = {**market_defaults, **plan_defaults}
    integer_keys = {
        "pivot_legs", "medium_pivot_legs", "large_pivot_legs", "break_confirm_bars",
        "retest_bars", "range_min_touches", "range_min_bars", "min_segment_bars",
        "trendline_min_touches", "trendline_min_bars",
        "max_event_age_bars", "trend_max_event_age_bars_m1",
        "trend_max_event_age_bars_other", "trend_min_retest_bars",
        "trend_continuation_hold_bars",
        "event_risk_min_importance", "event_risk_calendar_before_minutes",
        "event_risk_calendar_after_minutes", "event_risk_major_before_minutes",
        "event_risk_major_after_minutes", "event_risk_resume_confirmation_bars",
    }
    list_keys = {"allowed_setups", "allowed_directions", "blocked_hours", "event_risk_rules"}
    bool_keys = {"enabled", "require_reclaim", "event_risk_enabled"}
    string_keys = {"entry_mode"}

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
        items = RuntimeStateRepository(0, 0).list_entities("market_structure_config")
        stored = items[-1] if items else {}
        return {
            "status": "ok",
            "config": {**allowed, **{k: v for k, v in stored.items() if k in allowed}},
            "profiles": stored.get("profiles", []) if isinstance(stored, dict) else [],
            "setup_profiles": stored.get("setup_profiles", []) if isinstance(stored, dict) else [],
        }

    @router.put("/admin/market-structure/config", dependencies=[Depends(require_admin)])
    async def put_config(payload: Dict, user: AuthUser = Depends(require_admin)):
        cfg = dict(allowed)
        def normalize(item, *, setup=False):
            if not item.get("symbol") or not item.get("period") or (setup and not item.get("setup_type")):
                return None
            result = {"symbol": str(item["symbol"]).strip(), "period": str(item["period"]).upper()}
            if setup:
                result["setup_type"] = str(item["setup_type"]).strip().lower()
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
                        result[key] = max(1, int(value)) if key in integer_keys else max(0.0, value)
                    except (TypeError, ValueError):
                        pass
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
                    cfg[key] = max(1, int(value)) if key in integer_keys else max(0.0, value)
                except (TypeError, ValueError):
                    pass
        profiles = [x for x in (normalize(item) for item in (payload.get("profiles") or []) if isinstance(item, dict)) if x]
        setup_profiles = [x for x in (normalize(item, setup=True) for item in (payload.get("setup_profiles") or []) if isinstance(item, dict)) if x]
        cfg["profiles"] = profiles; cfg["setup_profiles"] = setup_profiles
        RuntimeStateRepository(0, 0).upsert_entity("market_structure_config", "default", cfg, status="active")
        return {"status": "ok", "config": {k: v for k, v in cfg.items() if k in allowed}, "profiles": profiles, "setup_profiles": setup_profiles}

    @router.post("/admin/market-structure/optimize-setups", dependencies=[Depends(require_admin)])
    async def optimize_setups(payload: Dict | None = None, user: AuthUser = Depends(require_admin)):
        """Generate conservative symbol/period/setup overrides from recent PnL.

        This is intentionally deterministic and explainable: it aggregates closed
        positions (not partial exit legs), applies minimum sample counts, and only
        auto-disables consistently losing setups.  ``apply`` controls persistence.
        """
        payload = payload or {}
        days = max(7, min(int(payload.get("days") or 30), 90))
        now, start = int(time.time()), int(time.time()) - days * 86400
        storage = get_storage()
        stored_items = RuntimeStateRepository(0, 0).list_entities("market_structure_config")
        stored_config = stored_items[-1] if stored_items and isinstance(stored_items[-1], dict) else {}
        existing_setup = {(str(item.get("symbol") or "").upper(), str(item.get("period") or "").upper(), str(item.get("setup_type") or "").lower()): item
                        for item in (stored_config.get("setup_profiles") or []) if isinstance(item, dict)}
        rows = storage.fetchall(
            "SELECT position_id, symbol, net_profit, closed_at, position_attribution_json "
            "FROM paper_trades WHERE closed_at>=? AND closed_at<=? ORDER BY closed_at",
            (start, now),
        )
        # Live MT5 deals use a different schema; normalize them to the same
        # position-level shape before aggregation.  Partial deals are merged
        # by mt5_position_id just like Paper partial exits.
        rows += storage.fetchall(
            "SELECT mt5_position_id AS position_id, symbol, "
            "(COALESCE(profit,0)+COALESCE(swap,0)+COALESCE(commission,0)) AS net_profit, "
            "deal_timestamp AS closed_at, position_attribution_json "
            "FROM live_trade_deals WHERE deal_timestamp>=? AND deal_timestamp<=? "
            "ORDER BY deal_timestamp",
            (start, now),
        )
        # Aggregate all partial exits into one position so split TP does not
        # overweight a setup's apparent win rate.
        positions = {}
        for row in rows:
            try:
                attr = row.get("position_attribution_json")
                attr = attr if isinstance(attr, dict) else json.loads(attr or "{}")
            except (TypeError, ValueError, json.JSONDecodeError):
                attr = {}
            if attr.get("signal_source") != "structure_plan":
                continue
            setup = str(attr.get("setup_type") or attr.get("selected_setup_type") or "").strip().lower()
            if not setup:
                continue
            key = str(row.get("position_id") or f"{row.get('symbol')}:{row.get('closed_at')}:{setup}")
            item = positions.setdefault(key, {"symbol": str(row.get("symbol") or "").strip(),
                                               "period": str(attr.get("signal_source_period") or "M5").upper(),
                                               "setup_type": setup, "pnl": 0.0,
                                               "closed_at": int(row.get("closed_at") or 0)})
            item["pnl"] += float(row.get("net_profit") or 0)
        grouped = defaultdict(list)
        for item in positions.values():
            if item["symbol"]:
                grouped[(item["symbol"].upper(), item["period"], item["setup_type"])].append(item)
        proposals, diagnostics = [], []
        for (symbol, period, setup), pnls in sorted(grouped.items()):
            if len(pnls) < 3:
                continue
            values = [float(item["pnl"]) for item in pnls]
            net = sum(values); wins = sum(1 for value in values if value > 0)
            losses = sum(1 for value in values if value < 0)
            win_rate = wins / len(pnls)
            recent_values = [float(item["pnl"]) for item in pnls if item.get("closed_at", 0) >= now - 2 * 86400]
            recent_net = sum(recent_values)
            profile = {"symbol": symbol, "period": period, "setup_type": setup,
                       "enabled": True, "allowed_directions": ["buy", "sell"]}
            reasons = []
            # Losing setups get confirmation/reclaim gates; repeated weak
            # setups are disabled until the user explicitly re-enables them.
            # A setup that was weak over 30 days but profitable in the last
            # two days is considered recovered; keep it enabled and report
            # the positive recent evidence instead of tightening it again.
            recovered = setup == "trend_continuation" and recent_values and recent_net > 0
            if net < 0 and not recovered:
                profile.update({"require_reclaim": True, "confirmation_bars": 2,
                                "min_displacement_atr": 0.4, "min_real_risk_reward": 1.3})
                reasons.append("净亏损，增加回收确认、两根确认K线和最小位移过滤")
                if win_rate < 0.40 or losses >= wins * 2:
                    profile["enabled"] = False
                    reasons.append("胜率低于40%或亏损次数至少为盈利次数2倍，暂时停用")
            if "breakout" in setup or "triangle" in setup:
                profile.update({"entry_mode": "breakout_retest", "min_body_atr": 0.5})
                reasons.append("突破类要求实体和回踩确认")
            elif "location" in setup or "reversal" in setup or "sweep" in setup:
                profile.update({"entry_mode": "touch_and_reclaim", "require_reclaim": True})
                reasons.append("反转/位置类要求触碰后收盘回收")
            if net > 0:
                reasons.append("净盈利，保留当前参数；成功经验是该品种/周期下该 Setup 可继续交易")
            if recovered:
                reasons.append(f"趋势策略近2天净盈利 {recent_net:.2f}，视为调整后已改善，不再收紧")
            previous = existing_setup.get((symbol, period, setup), {})
            changes = []
            for field, value in profile.items():
                if previous.get(field) != value:
                    changes.append(f"{field}: {previous.get(field, '未配置')} → {value}")
            diagnostics.append({"symbol": symbol, "period": period, "setup_type": setup,
                               "orders": len(pnls), "net_pnl": round(net, 2),
                               "recent_orders": len(recent_values), "recent_net_pnl": round(recent_net, 2),
                               "win_rate": round(win_rate * 100, 2), "success_evidence": {
                                   "profitable": net > 0, "winning_orders": wins,
                                   "average_win": round(sum(v for v in values if v > 0) / wins, 2) if wins else 0,
                               }, "reasons": reasons})
            diagnostics[-1]["proposed_enabled"] = profile.get("enabled", True)
            diagnostics[-1]["changes"] = "；".join(changes) if changes else "保持现有配置"
            proposals.append(profile)
        # Applying a reviewed preview must use exactly the rows the admin saw,
        # rather than silently recomputing them between preview and apply.
        if payload.get("apply") and isinstance(payload.get("proposals"), list):
            proposals = [item for item in payload["proposals"] if isinstance(item, dict)]
            if isinstance(payload.get("symbol_profiles"), list):
                symbol_profiles = [item for item in payload["symbol_profiles"] if isinstance(item, dict)]
            else:
                symbol_profiles = []
        # Build a whitelist from the generated profiles.  A symbol/period is
        # restricted only when enough historical samples existed; unobserved
        # setups are left to the global default rather than guessed.
        whitelist = defaultdict(list)
        for item in proposals:
            if item.get("enabled", True):
                whitelist[(item["symbol"], item["period"])].append(item["setup_type"])
        symbol_profiles = [{"symbol": symbol, "period": period,
                           "allowed_setups": sorted(set(setups))}
                          for (symbol, period), setups in sorted(whitelist.items())]
        applied = False
        if bool(payload.get("apply")):
            current = RuntimeStateRepository(0, 0).list_entities("market_structure_config")
            stored = current[-1] if current and isinstance(current[-1], dict) else {}
            existing = [item for item in (stored.get("setup_profiles") or []) if isinstance(item, dict)]
            index = {(str(item.get("symbol")).upper(), str(item.get("period")).upper(), str(item.get("setup_type")).lower()): item for item in existing}
            for item in proposals:
                index[(item["symbol"], item["period"], item["setup_type"])] = item
            merged = list(index.values())
            cfg = {k: stored.get(k, value) for k, value in allowed.items()}
            profile_items = [item for item in (stored.get("profiles") or []) if isinstance(item, dict)]
            profile_index = {(str(item.get("symbol")).upper(), str(item.get("period")).upper()): item for item in profile_items}
            for item in symbol_profiles:
                key = (item["symbol"], item["period"])
                profile_index[key] = {**profile_index.get(key, {}), **item}
            cfg["profiles"] = list(profile_index.values())
            cfg["setup_profiles"] = merged
            RuntimeStateRepository(0, 0).upsert_entity("market_structure_config", "default", cfg, status="active")
            applied = True
        return {"status": "ok", "days": days, "applied": applied,
                "proposals": proposals, "symbol_profiles": symbol_profiles,
                "diagnostics": diagnostics}

    @router.post("/admin/market-structure/optimize-setups/review", dependencies=[Depends(require_admin)])
    async def review_setup_proposals(payload: Dict, user: AuthUser = Depends(require_admin)):
        """Have the LLM review deterministic proposals without changing config."""
        if engine_manager is None:
            return {"status": "unavailable", "reason": "未配置大模型引擎"}
        proposals = payload.get("proposals") or []
        diagnostics = payload.get("diagnostics") or []
        if not proposals:
            return {"status": "skipped", "reason": "没有可供复核的优化建议"}
        prompt = (
            "请审核以下由确定性规则生成的结构交易 SETUP 配置建议。只依据提供的历史统计，"
            "分别判断建议是否合理，指出应保留、调整或拒绝的建议。不得直接修改配置。"
            "严格返回 JSON：{\"summary\":\"\",\"recommendations\":[{\"symbol\":\"\",\"period\":\"\",\"setup_type\":\"\",\"decision\":\"apply|reject|review\",\"reason\":\"\",\"risk\":\"\"}],\"global_notes\":[\"\"]}。"
            "样本少于10笔只能 review，不得建议停用。\n\n"
            + json.dumps({"proposals": proposals, "diagnostics": diagnostics}, ensure_ascii=False, default=str)
        )
        try:
            engine = engine_manager.get_engine_for_user(user.user_id)
            review = engine.llm_service.call_llm(
                prompt, system_prompt="你是交易配置建议审核器，只做复核，不直接写入配置。",
                scene_code=AI_SIGNAL_ANALYSIS, object_type="structure_setup_optimizer_review",
                object_id=f"{int(time.time())}:{user.user_id}", max_tokens=3500,
            )
            return {"status": "ok", "review": review or {}}
        except Exception as exc:
            return {"status": "failed", "reason": str(exc)[:500]}

    return router

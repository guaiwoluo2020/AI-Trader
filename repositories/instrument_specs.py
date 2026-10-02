"""Account-scoped broker instrument volume specifications."""
from __future__ import annotations

import math
import time
from decimal import Decimal, ROUND_CEILING, ROUND_FLOOR, ROUND_HALF_UP
from typing import Dict, Optional

from mysql_repositories import get_storage


DEFAULT_SPEC = {
    "min_volume": 0.01,
    "volume_step": 0.01,
    "max_volume": 100.0,
    "volume_digits": 2,
    "contract_size": 1.0,
    "price_digits": 0,
    "tick_size": 0.0,
    "point_size": 0.0,
    "tick_value": 0.0,
    "swap_long": 0.0,
    "swap_short": 0.0,
    "swap_mode": 0,
    "swap_rollover3days": 0,
    "stops_level": 0,
    "freeze_level": 0,
    "filling_mode": 0,
    "trade_calc_mode": 0,
    "currency_base": "",
    "currency_profit": "",
    "currency_margin": "",
    "trade_sessions_json": "",
    "source": "default",
}

SPEC_COLUMNS = (
    "min_volume,volume_step,max_volume,volume_digits,contract_size,"
    "price_digits,tick_size,point_size,tick_value,"
    "swap_long,swap_short,swap_mode,swap_rollover3days,"
    "stops_level,freeze_level,filling_mode,trade_calc_mode,"
    "currency_base,currency_profit,currency_margin,trade_sessions_json,source,updated_at"
)


class InstrumentSpecRepository:
    def __init__(self, storage=None):
        self.storage = storage or get_storage()

    def get(self, account_id: int, symbol: str) -> Dict:
        account_id = int(account_id or 0)
        symbol = str(symbol or "").strip()
        row = self.storage.fetchone(
            "SELECT account_id,symbol," + SPEC_COLUMNS + " "
            "FROM account_instrument_specs WHERE account_id=? AND symbol=?",
            (account_id, symbol),
        )
        if row is None and account_id:
            row = self.storage.fetchone(
                "SELECT s.account_id,s.symbol,s." + SPEC_COLUMNS.replace(",", ",s.") + " "
                "FROM account_instrument_specs s "
                "JOIN trading_accounts target ON target.id=? "
                "JOIN trading_accounts source ON source.id=s.account_id "
                "WHERE target.account_type='paper' AND source.user_id=target.user_id "
                "AND source.account_type IN ('mt5','ibkr') AND s.symbol=? "
                "AND COALESCE(s.contract_size,0) > 0 "
                "ORDER BY s.updated_at DESC, s.account_id DESC LIMIT 1",
                (account_id, symbol),
            )
        result = dict(DEFAULT_SPEC)
        result.update({"account_id": account_id, "symbol": symbol})
        if row:
            result.update(dict(row))
        return result

    def upsert(self, account_id: int, symbol: str, payload: Dict) -> Dict:
        account_id = int(account_id or 0)
        symbol = str(symbol or "").strip()
        if account_id <= 0 or not symbol:
            raise ValueError("account_id 和 symbol 不能为空")
        spec = dict(DEFAULT_SPEC)
        spec.update(payload or {})
        min_volume = max(0.00000001, float(spec.get("min_volume") or 0.01))
        step = max(0.00000001, float(spec.get("volume_step") or min_volume))
        max_volume = max(min_volume, float(spec.get("max_volume") or 100.0))
        digits = max(0, min(8, int(spec.get("volume_digits") or 2)))
        contract = max(0.00000001, float(spec.get("contract_size") or 1.0))
        price_digits = max(0, min(12, int(spec.get("price_digits") or 0)))
        tick_size = max(0.0, float(spec.get("tick_size") or 0.0))
        point_size = max(0.0, float(spec.get("point_size") or 0.0))
        tick_value = max(0.0, float(spec.get("tick_value") or 0.0))
        swap_long = float(spec.get("swap_long") or 0.0)
        swap_short = float(spec.get("swap_short") or 0.0)
        swap_mode = max(0, int(spec.get("swap_mode") or 0))
        swap_rollover3days = max(0, min(6, int(spec.get("swap_rollover3days") or 0)))
        stops_level = max(0, int(spec.get("stops_level") or 0))
        freeze_level = max(0, int(spec.get("freeze_level") or 0))
        filling_mode = max(0, int(spec.get("filling_mode") or 0))
        trade_calc_mode = max(0, int(spec.get("trade_calc_mode") or 0))
        currency_base = str(spec.get("currency_base") or "")[:16]
        currency_profit = str(spec.get("currency_profit") or "")[:16]
        currency_margin = str(spec.get("currency_margin") or "")[:16]
        trade_sessions_json = str(spec.get("trade_sessions_json") or "")[:12000]
        source = str(spec.get("source") or "broker")[:32]
        now = int(time.time())
        self.storage.execute(
            "INSERT INTO account_instrument_specs "
            "(account_id,symbol,min_volume,volume_step,max_volume,volume_digits,contract_size,"
            "price_digits,tick_size,point_size,tick_value,swap_long,swap_short,swap_mode,"
            "swap_rollover3days,stops_level,freeze_level,filling_mode,trade_calc_mode,"
            "currency_base,currency_profit,currency_margin,trade_sessions_json,source,updated_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) "
            "ON DUPLICATE KEY UPDATE min_volume=VALUES(min_volume),volume_step=VALUES(volume_step),"
            "max_volume=VALUES(max_volume),volume_digits=VALUES(volume_digits),"
            "contract_size=VALUES(contract_size),price_digits=VALUES(price_digits),"
            "tick_size=VALUES(tick_size),point_size=VALUES(point_size),"
            "tick_value=VALUES(tick_value),swap_long=VALUES(swap_long),"
            "swap_short=VALUES(swap_short),swap_mode=VALUES(swap_mode),"
            "swap_rollover3days=VALUES(swap_rollover3days),stops_level=VALUES(stops_level),"
            "freeze_level=VALUES(freeze_level),filling_mode=VALUES(filling_mode),"
            "trade_calc_mode=VALUES(trade_calc_mode),currency_base=VALUES(currency_base),"
            "currency_profit=VALUES(currency_profit),currency_margin=VALUES(currency_margin),"
            "trade_sessions_json=VALUES(trade_sessions_json),"
            "source=VALUES(source),updated_at=VALUES(updated_at)",
            (account_id, symbol, min_volume, step, max_volume, digits, contract,
             price_digits, tick_size, point_size, tick_value, swap_long, swap_short,
             swap_mode, swap_rollover3days, stops_level, freeze_level, filling_mode,
             trade_calc_mode, currency_base, currency_profit, currency_margin,
             trade_sessions_json, source, now),
        )
        return self.get(account_id, symbol)


def normalize_volume(volume: float, spec: Optional[Dict] = None, *, opening: bool = True,
                     current_volume: Optional[float] = None) -> float:
    """Normalize an opening/closing quantity to broker min/step rules.

    Opening quantities are rounded down to avoid exceeding risk. Closing quantities
    are also rounded down; if the requested close would leave an untradeable residue,
    the whole remaining position is returned.
    """
    spec = {**DEFAULT_SPEC, **(spec or {})}
    value = max(0.0, float(volume or 0.0))
    minimum = max(0.00000001, float(spec.get("min_volume") or 0.01))
    step = max(0.00000001, float(spec.get("volume_step") or minimum))
    maximum = max(minimum, float(spec.get("max_volume") or 100.0))
    if current_volume is not None:
        current = max(0.0, float(current_volume))
        if value >= current - step * 0.5:
            return round(current, int(spec.get("volume_digits") or 2))
    units = math.floor((value + 1e-12) / step)
    normalized = units * step
    if opening:
        normalized = max(minimum, normalized)
        normalized = min(maximum, normalized)
    elif normalized < minimum:
        return round(current, int(spec.get("volume_digits") or 2)) if current_volume is not None else 0.0
    digits = max(0, min(8, int(spec.get("volume_digits") or 2)))
    return round(normalized, digits)


def normalize_price(price: float, spec: Optional[Dict] = None, *, direction: str = "nearest") -> float:
    """Align a broker price to its account-scoped tick size and quote digits.

    ``direction`` is useful for protective stops: ``up`` tightens a buy stop
    without accidentally rounding it lower, while ``down`` does the equivalent
    for a sell stop.  Unknown/legacy specifications retain the input price so
    older EAs remain compatible until they upload their price rules.
    """
    value = float(price or 0.0)
    if value <= 0:
        return 0.0
    spec = {**DEFAULT_SPEC, **(spec or {})}
    digits = max(0, min(12, int(spec.get("price_digits") or 0)))
    tick = float(spec.get("tick_size") or 0.0)
    point = float(spec.get("point_size") or 0.0)
    quantum = tick if tick > 0 else point
    if quantum <= 0 and digits <= 0:
        return value

    decimal_value = Decimal(str(value))
    if quantum > 0:
        decimal_tick = Decimal(str(quantum))
        rounding = {
            "up": ROUND_CEILING,
            "down": ROUND_FLOOR,
        }.get(str(direction or "nearest").lower(), ROUND_HALF_UP)
        units = (decimal_value / decimal_tick).to_integral_value(rounding=rounding)
        decimal_value = units * decimal_tick
    if digits > 0:
        decimal_value = decimal_value.quantize(
            Decimal(1).scaleb(-digits), rounding=ROUND_HALF_UP,
        )
    return float(decimal_value)

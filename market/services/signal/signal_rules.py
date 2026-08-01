"""Pure signal rules shared by live, paper, and historical replay modes."""

from datetime import datetime
from typing import Dict, Iterable, Optional

from ...models import SignalSource, TradingSignal


def automatic_key_levels(current_price: float) -> list:
    if current_price <= 0:
        return []
    digits = len(str(int(current_price))) if current_price >= 1 else 1
    step = (
        1 if digits == 1 else 5 if digits == 2 else 10
        if digits == 3 else 100 if digits == 4 else 1000
    )
    base = int(current_price / step) * step
    return sorted(
        float(base + offset * step)
        for offset in range(-3, 4)
        if base + offset * step > 0
    )


def build_key_level_signal(
    symbol: str,
    current_price: float,
    levels: Optional[Iterable[float]] = None,
    signal_time: Optional[datetime] = None,
    threshold: float = 0.0008,
) -> Optional[TradingSignal]:
    candidates = list(levels or automatic_key_levels(current_price))
    if current_price <= 0 or not candidates:
        return None
    nearest = min(candidates, key=lambda level: abs(current_price - level))
    distance = abs(current_price - nearest) / current_price
    if distance > threshold:
        return None
    if current_price > nearest:
        action = "buy"
        sl = nearest - nearest * 0.006
        risk = current_price - sl
        tp = current_price + risk * 1.5
        reason = f"价格向下接近 {nearest}（支撑位）"
    else:
        action = "sell"
        sl = nearest + nearest * 0.006
        risk = sl - current_price
        tp = current_price - risk * 1.5
        reason = f"价格向上接近 {nearest}（压力位）"
    return TradingSignal(
        symbol=symbol,
        action=action,
        confidence=65,
        source=SignalSource.KEY_LEVEL,
        trigger_price=current_price,
        trigger_time=signal_time,
        trigger_reason=reason,
        suggested_entry=current_price,
        suggested_sl=round(sl, 2),
        suggested_tp=round(tp, 2),
        risk_reward_ratio=1.5,
        key_level=nearest,
        distance_pct=round(distance * 100, 4),
        created_at=signal_time,
    )


def build_ai_entry_signal(
    symbol: str,
    current_price: float,
    suggestion: Dict,
    signal_time: Optional[datetime] = None,
    threshold: float = 0.0001,
) -> Optional[TradingSignal]:
    period = str(suggestion.get("period", "")).upper()
    direction = str(suggestion.get("direction", "")).lower()
    entry = float(suggestion.get("entry_price") or 0)
    sl = float(suggestion.get("stop_loss") or 0)
    tp = float(suggestion.get("take_profit") or 0)
    if (
        current_price <= 0 or direction not in {"buy", "sell"} or entry <= 0
        or abs(current_price - entry) / current_price > threshold
        or not valid_exits(direction, current_price, sl, tp)
    ):
        return None
    risk = abs(current_price - sl)
    reward = abs(tp - current_price)
    if risk <= 0 or reward / risk < 1 or risk > current_price * 0.02:
        return None
    try:
        confidence = max(0, min(100, int(suggestion.get("confidence", 75))))
    except (TypeError, ValueError):
        confidence = 75
    return TradingSignal(
        symbol=symbol,
        action=direction,
        confidence=confidence,
        source=SignalSource.AI_ENTRY,
        source_period=period,
        trigger_price=current_price,
        trigger_time=signal_time,
        trigger_reason=f"AI建议入场: {suggestion.get('reason', '')}",
        suggested_entry=current_price,
        suggested_sl=sl,
        suggested_tp=tp,
        risk_reward_ratio=round(reward / risk, 2),
        ai_analysis_period=period,
        created_at=signal_time,
    )


def build_pivot_signal(
    symbol: str,
    current_price: float,
    period: str,
    pivot_price: float,
    pivot_type: str,
    opposite_price: Optional[float],
    signal_time: Optional[datetime] = None,
) -> Optional[TradingSignal]:
    action = "buy" if pivot_type == "low" else "sell"
    sl = pivot_price - 10 if action == "buy" else pivot_price + 10
    risk = abs(current_price - sl)
    if risk <= 0 or risk > current_price * 0.02:
        return None
    minimum_reward = risk * 1.5
    tp = opposite_price
    if tp is None or abs(tp - current_price) < minimum_reward:
        tp = current_price + minimum_reward if action == "buy" else current_price - minimum_reward
    if not valid_exits(action, current_price, sl, tp):
        return None
    return TradingSignal(
        symbol=symbol,
        action=action,
        confidence=60,
        source=SignalSource.PIVOT,
        source_period=period,
        trigger_price=current_price,
        trigger_time=signal_time,
        trigger_reason=f"{period}接近{pivot_type}点 {pivot_price:.2f}",
        suggested_entry=current_price,
        suggested_sl=sl,
        suggested_tp=tp,
        risk_reward_ratio=round(abs(tp - current_price) / risk, 2),
        pivot_price=pivot_price,
        pivot_type=pivot_type,
        created_at=signal_time,
    )


def valid_exits(direction: str, entry: float, sl: float, tp: float) -> bool:
    if min(entry, sl, tp) <= 0:
        return False
    return (sl < entry < tp) if direction == "buy" else (tp < entry < sl)

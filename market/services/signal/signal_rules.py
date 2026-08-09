"""Pure signal rules shared by live, paper, and historical replay modes."""

from datetime import datetime, timedelta
from typing import Dict, Iterable, Optional

from ...models import SignalSource, TradingSignal


def direction_action(direction: str) -> str:
    return {"up": "buy", "down": "sell"}.get(direction, "none")


def normalize_market_direction(value: str) -> str:
    text = str(value or "").strip().lower()
    if text in {"up", "buy", "bullish", "上涨", "单边上涨", "震荡上升"}:
        return "up"
    if text in {"down", "sell", "bearish", "下跌", "单边下跌", "震荡下跌"}:
        return "down"
    return "sideways"


def extract_ai_trend_state(analysis: Optional[Dict], period: str) -> Dict:
    analysis = analysis or {}
    if analysis.get("data_stale"):
        return {
            "ready": False, "direction": "sideways", "confidence": 0,
            "reason": "AI趋势分析数据已过期",
        }
    item = (analysis.get("trend_analysis") or {}).get(period) or {}
    if not item:
        return {
            "ready": False, "direction": "sideways", "confidence": 0,
            "reason": f"AI尚未完成{period}趋势分析",
        }
    direction = normalize_market_direction(
        item.get("direction", item.get("trend", ""))
    )
    try:
        confidence = max(0, min(100, int(item.get("confidence", 50))))
    except (TypeError, ValueError):
        confidence = 50
    return {
        "ready": True,
        "direction": direction,
        "confidence": confidence,
        "reason": str(item.get("reason") or f"AI判断{period}为{direction}"),
    }


def moving_average_value(values: list, period: int, ma_type: str = "sma"):
    """Return a fully warmed SMA/EMA value, or None when history is insufficient."""
    period = int(period)
    if period <= 0 or len(values) < period:
        return None
    if ma_type == "ema":
        average = sum(float(value) for value in values[:period]) / period
        multiplier = 2 / (period + 1)
        for value in values[period:]:
            average = (float(value) - average) * multiplier + average
        return average
    return sum(float(value) for value in values[-period:]) / period


def detect_moving_average_cross(
    closes: list, fast_period: int, slow_period: int, ma_type: str = "sma",
):
    """Detect only the bar where the fast MA crosses the slow MA."""
    if fast_period >= slow_period or len(closes) < slow_period + 1:
        return None
    previous = closes[:-1]
    previous_fast = moving_average_value(previous, fast_period, ma_type)
    previous_slow = moving_average_value(previous, slow_period, ma_type)
    current_fast = moving_average_value(closes, fast_period, ma_type)
    current_slow = moving_average_value(closes, slow_period, ma_type)
    if None in {previous_fast, previous_slow, current_fast, current_slow}:
        return None
    if previous_fast <= previous_slow and current_fast > current_slow:
        direction = "buy"
    elif previous_fast >= previous_slow and current_fast < current_slow:
        direction = "sell"
    else:
        return None
    return direction, float(current_fast), float(current_slow)


def evaluate_moving_average_state(
    closes: list, fast_period: int, slow_period: int, ma_type: str = "sma",
    min_gap_ratio: float = 0.0001,
) -> Dict:
    """Return a persistent trend state while keeping crossover as an event."""
    if fast_period >= slow_period or len(closes) < slow_period + 1:
        return {
            "ready": False, "direction": "sideways", "confidence": 0,
            "fast_ma": None, "slow_ma": None, "gap_ratio": 0.0, "cross": None,
            "reason": f"至少需要 {slow_period + 1} 根K线完成均线预热",
        }
    previous = closes[:-1]
    previous_fast = moving_average_value(previous, fast_period, ma_type)
    previous_slow = moving_average_value(previous, slow_period, ma_type)
    current_fast = moving_average_value(closes, fast_period, ma_type)
    current_slow = moving_average_value(closes, slow_period, ma_type)
    if None in {previous_fast, previous_slow, current_fast, current_slow}:
        return {
            "ready": False, "direction": "sideways", "confidence": 0,
            "fast_ma": current_fast, "slow_ma": current_slow,
            "gap_ratio": 0.0, "cross": None,
            "reason": "均线数据尚未就绪",
        }
    price = max(abs(float(closes[-1])), 1e-12)
    gap_ratio = abs(float(current_fast) - float(current_slow)) / price
    slope_ratio = abs(float(current_fast) - float(previous_fast)) / price
    sideways_gap = max(0.0, float(min_gap_ratio))
    if gap_ratio <= sideways_gap:
        direction = "sideways"
        if sideways_gap > 0:
            confidence = min(
                90, 60 + int((sideways_gap - gap_ratio) / sideways_gap * 25)
            )
        else:
            confidence = 60
    else:
        direction = "up" if current_fast > current_slow else "down"
        confidence = min(95, 55 + int(gap_ratio / 0.001 * 25 + slope_ratio / 0.001 * 10))
    cross = detect_moving_average_cross(
        closes, fast_period, slow_period, ma_type
    )
    label = str(ma_type).upper()
    relation = "靠近" if direction == "sideways" else ("高于" if direction == "up" else "低于")
    return {
        "ready": True,
        "direction": direction,
        "confidence": confidence,
        "fast_ma": float(current_fast),
        "slow_ma": float(current_slow),
        "gap_ratio": gap_ratio,
        "cross": cross[0] if cross else None,
        "reason": f"{label}{fast_period}{relation}{label}{slow_period}",
    }


def build_moving_average_state_signal(
    symbol: str, current_price: float, period: str, state: Dict,
    fast_period: int, slow_period: int, ma_type: str = "sma",
    is_entry_trigger: bool = False,
    signal_time: Optional[datetime] = None,
) -> TradingSignal:
    direction = state.get("direction", "sideways")
    cross = state.get("cross")
    label = str(ma_type).upper()
    reason = state.get("reason", "")
    if is_entry_trigger and cross in {"buy", "sell"}:
        reason = (
            f"{period} {label}{fast_period}"
            f"{'上穿' if cross == 'buy' else '下穿'}{label}{slow_period}"
        )
    period_seconds = {
        "M1": 60, "M5": 300, "M15": 900, "H1": 3600, "H4": 14400,
    }.get(period, 300)
    return TradingSignal(
        symbol=symbol,
        action=direction_action(direction),
        market_direction=direction,
        state_ready=bool(state.get("ready", False)),
        is_entry_trigger=is_entry_trigger,
        confidence=int(state.get("confidence", 0)),
        source=SignalSource.MOVING_AVERAGE,
        source_period=period,
        trigger_price=current_price,
        trigger_time=signal_time,
        trigger_reason=reason,
        suggested_entry=current_price,
        suggested_sl=0,
        suggested_tp=0,
        risk_reward_ratio=0,
        fast_ma=state.get("fast_ma"),
        slow_ma=state.get("slow_ma"),
        created_at=signal_time,
        expires_at=(
            signal_time + timedelta(seconds=period_seconds * 2)
            if signal_time else None
        ),
    )


def build_moving_average_signal(
    symbol: str,
    current_price: float,
    period: str,
    direction: str,
    fast_ma: float,
    slow_ma: float,
    fast_period: int,
    slow_period: int,
    ma_type: str = "sma",
    signal_time: Optional[datetime] = None,
) -> Optional[TradingSignal]:
    if current_price <= 0 or direction not in {"buy", "sell"}:
        return None
    state = {
        "ready": True,
        "direction": "up" if direction == "buy" else "down",
        "confidence": 65,
        "fast_ma": fast_ma,
        "slow_ma": slow_ma,
        "cross": direction,
    }
    return build_moving_average_state_signal(
        symbol, current_price, period, state, fast_period, slow_period,
        ma_type, is_entry_trigger=True, signal_time=signal_time,
    )


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
    signal = build_key_level_state_signal(
        symbol, current_price, levels, signal_time, threshold
    )
    return signal if signal.is_entry_trigger else None


def build_key_level_state_signal(
    symbol: str,
    current_price: float,
    levels: Optional[Iterable[float]] = None,
    signal_time: Optional[datetime] = None,
    threshold: float = 0.0008,
) -> TradingSignal:
    """Describe key-level direction continuously; proximity remains the trigger."""
    candidates = list(levels or automatic_key_levels(current_price))
    if current_price <= 0 or not candidates:
        return TradingSignal(
            symbol=symbol, action="none", market_direction="sideways",
            state_ready=False, is_entry_trigger=False, confidence=0,
            source=SignalSource.KEY_LEVEL, trigger_price=current_price,
            trigger_time=signal_time, trigger_reason="没有可用关键点位",
            created_at=signal_time,
        )
    nearest = min(candidates, key=lambda level: abs(current_price - level))
    distance = abs(current_price - nearest) / current_price
    near = distance <= threshold
    if not near or current_price == nearest:
        direction = "sideways"
        confidence = 60 if near else max(35, int(60 - min(25, distance * 10000)))
        reason = (
            f"价格位于关键位 {nearest}，等待方向确认"
            if near else f"价格尚未接近关键位 {nearest}"
        )
        sl = tp = 0
    elif current_price > nearest:
        direction = "up"
        confidence = min(90, 65 + int((threshold - distance) / max(threshold, 1e-12) * 20))
        sl = nearest - nearest * 0.006
        risk = current_price - sl
        tp = current_price + risk * 1.5
        reason = f"价格位于关键位 {nearest} 上方，支撑方向成立"
    else:
        direction = "down"
        confidence = min(90, 65 + int((threshold - distance) / max(threshold, 1e-12) * 20))
        sl = nearest + nearest * 0.006
        risk = sl - current_price
        tp = current_price - risk * 1.5
        reason = f"价格位于关键位 {nearest} 下方，压力方向成立"
    return TradingSignal(
        symbol=symbol,
        action=direction_action(direction),
        market_direction=direction,
        state_ready=True,
        is_entry_trigger=near and direction in {"up", "down"},
        confidence=confidence,
        source=SignalSource.KEY_LEVEL,
        trigger_price=current_price,
        trigger_time=signal_time,
        trigger_reason=reason,
        suggested_entry=current_price,
        suggested_sl=round(sl, 2) if sl else 0,
        suggested_tp=round(tp, 2) if tp else 0,
        risk_reward_ratio=1.5 if sl and tp else 0,
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


def build_pivot_breakout_signal(
    symbol: str,
    current_price: float,
    period: str,
    pivot_price: float,
    pivot_type: str,
    signal_time: Optional[datetime] = None,
) -> Optional[TradingSignal]:
    """价格突破高点做多、跌破低点做空。"""
    action = "buy" if pivot_type == "high" else "sell"
    buffer = max(abs(current_price - pivot_price), current_price * 0.0005)
    sl = pivot_price - buffer if action == "buy" else pivot_price + buffer
    tp = current_price + buffer * 2 if action == "buy" else current_price - buffer * 2
    if not valid_exits(action, current_price, sl, tp):
        return None
    return TradingSignal(
        symbol=symbol,
        action=action,
        confidence=65,
        source=SignalSource.PIVOT,
        source_period=period,
        trigger_price=current_price,
        trigger_time=signal_time,
        trigger_reason=f"{period}突破{pivot_type}点 {pivot_price:.2f}",
        suggested_entry=current_price,
        suggested_sl=sl,
        suggested_tp=tp,
        risk_reward_ratio=2.0,
        pivot_price=pivot_price,
        pivot_type=pivot_type,
        created_at=signal_time,
    )


def valid_exits(direction: str, entry: float, sl: float, tp: float) -> bool:
    if min(entry, sl, tp) <= 0:
        return False
    return (sl < entry < tp) if direction == "buy" else (tp < entry < sl)

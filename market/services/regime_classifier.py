"""统一的主周期行情状态判定。

综合 20/40/70 根 K 线窗口、ATR 标准化移动、高低点结构和箱体证据。
复杂箱体、三角形等形态仍由 AI 2.0 负责解释；本模块只负责执行层的
主周期方向口径。
"""

from typing import Dict, List


def _number(item, *names):
    for name in names:
        value = getattr(item, name, None)
        if value is None and isinstance(item, dict):
            value = item.get(name)
        try:
            if value is not None:
                return float(value)
        except (TypeError, ValueError):
            pass
    return None


def _rows(klines: List) -> List[Dict[str, float]]:
    rows = []
    for item in list(klines or []):
        close = _number(item, "close", "close_price")
        if close is None or close <= 0:
            continue
        rows.append({
            "close": close,
            "high": _number(item, "high", "high_price") or close,
            "low": _number(item, "low", "low_price") or close,
        })
    return rows


def _evidence(rows: List[Dict[str, float]]) -> Dict:
    closes = [x["close"] for x in rows]
    highs = [x["high"] for x in rows]
    lows = [x["low"] for x in rows]
    n = len(closes)
    first, last = closes[0], closes[-1]
    ranges, previous = [], None
    for high, low, close in zip(highs, lows, closes):
        ranges.append(max(high - low, abs(high - previous), abs(low - previous)) if previous else high - low)
        previous = close
    period = min(14, len(ranges))
    atr = sum(ranges[:period]) / period if period else 0.0
    for value in ranges[period:]:
        atr = ((atr * (period - 1)) + value) / period if period else value
    atr = max(atr, 1e-12)
    mean_x = (n - 1) / 2
    mean_y = sum(closes) / n
    denominator = sum((i - mean_x) ** 2 for i in range(n))
    slope = sum((i - mean_x) * (v - mean_y) for i, v in enumerate(closes)) / denominator if denominator else 0.0
    half = max(3, n // 2)
    early, recent = rows[:half], rows[-half:]
    hh = int(max(x["high"] for x in recent) > max(x["high"] for x in early))
    hl = int(min(x["low"] for x in recent) > min(x["low"] for x in early))
    lh = int(max(x["high"] for x in recent) < max(x["high"] for x in early))
    ll = int(min(x["low"] for x in recent) < min(x["low"] for x in early))
    traveled = sum(abs(closes[i] - closes[i - 1]) for i in range(1, n))
    efficiency = abs(last - first) / traveled if traveled else 0.0
    upper = sorted(highs)[max(0, int(n * .90) - 1)]
    lower = sorted(lows)[min(n - 1, int(n * .10))]
    tolerance = max(atr * .6, (upper - lower) * .02, 1e-12)
    inside = sum(lower - tolerance <= x <= upper + tolerance for x in closes) / n
    width_atr = (upper - lower) / atr
    change_pct = (last - first) / first * 100
    normalized_move = abs(last - first) / atr
    up_score = int(change_pct > 0 and slope > 0) + min(2, hh + hl) + int(normalized_move >= .8 and efficiency >= .30)
    down_score = int(change_pct < 0 and slope < 0) + min(2, lh + ll) + int(normalized_move >= .8 and efficiency >= .30)
    range_score = int(inside >= .75) + int(1.5 <= width_atr <= 8.0) + int(efficiency < .35)
    if up_score >= 3 and up_score > down_score and range_score < 2:
        state = "up"
    elif down_score >= 3 and down_score > up_score and range_score < 2:
        state = "down"
    elif range_score >= 2:
        state = "sideways"
    else:
        state = "transition"
    return {
        "state": state, "change_pct": round(change_pct, 4),
        "atr": round(atr, 8), "normalized_move": round(normalized_move, 4),
        "efficiency_ratio": round(efficiency, 4), "slope": round(slope, 8),
        "higher_highs": hh, "higher_lows": hl, "lower_highs": lh, "lower_lows": ll,
        "range_inside_ratio": round(inside, 4), "range_width_atr": round(width_atr, 4),
        "up_score": up_score, "down_score": down_score, "range_score": range_score,
    }


def analyze_main_period_regime(klines: List, windows=(20, 40, 70)) -> Dict:
    rows = _rows(klines)
    available = len(rows)
    if available < 10:
        return {"regime": "unknown", "confidence": 0, "windows": [], "reason": "K线数据不足"}
    evidences = [{"bars": window, **_evidence(rows[-window:])} for window in windows if available >= window]
    if not evidences:
        evidences = [{"bars": available, **_evidence(rows)}]
    up = sum(x["state"] == "up" for x in evidences)
    down = sum(x["state"] == "down" for x in evidences)
    sideways = sum(x["state"] == "sideways" for x in evidences)
    if up > max(down, sideways) and up >= (len(evidences) + 1) // 2:
        regime = "up"
    elif down > max(up, sideways) and down >= (len(evidences) + 1) // 2:
        regime = "down"
    elif sideways >= max(up, down):
        regime = "sideways"
    else:
        regime = "transition"
    confidence = int(round(max(up, down, sideways) / len(evidences) * 100))
    latest = evidences[0]
    reason = (
        f"窗口={up}上涨/{down}下跌/{sideways}震荡；"
        f"最近{latest['bars']}根涨跌幅{latest['change_pct']:.2f}%，"
        f"ATR标准化移动{latest['normalized_move']:.2f}，"
        f"箱体内部比例{latest['range_inside_ratio']:.0%}，效率{latest['efficiency_ratio']:.0%}"
    )
    return {"regime": regime, "confidence": confidence, "windows": evidences, "reason": reason}


def classify_main_period_regime(klines: List, lookback: int = 20) -> str:
    """兼容旧调用：过渡状态按震荡处理，避免误触发趋势过滤。"""
    regime = analyze_main_period_regime(klines).get("regime")
    return regime if regime in {"up", "down", "sideways"} else "sideways"

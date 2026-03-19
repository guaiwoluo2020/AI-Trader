#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
技术指标计算模块
纯函数实现，不依赖外部状态
"""

from typing import List, Dict
from ..models import KlineData


def calculate_ma(data: List[float], period: int) -> float:
    """
    计算移动平均线 (MA)

    Args:
        data: 数据列表（如收盘价）
        period: 周期

    Returns:
        MA 值
    """
    if not data:
        return 0
    if len(data) < period:
        return data[-1]
    return sum(data[-period:]) / period


def calculate_adx(klines: List[KlineData], period: int = 14) -> float:
    """
    计算 ADX (Average Directional Index)

    ADX > 25: 有趋势
    ADX > 40: 强趋势
    ADX < 20: 无明显趋势

    Args:
        klines: K线数据列表
        period: 计算周期

    Returns:
        ADX 值
    """
    if len(klines) < period + 1:
        return 0

    # 计算 +DM 和 -DM
    plus_dm = []
    minus_dm = []
    tr_list = []

    for i in range(1, len(klines)):
        high = klines[i].high
        low = klines[i].low
        prev_high = klines[i - 1].high
        prev_low = klines[i - 1].low
        prev_close = klines[i - 1].close

        # +DM
        up_move = high - prev_high
        down_move = prev_low - low

        if up_move > down_move and up_move > 0:
            plus_dm.append(up_move)
        else:
            plus_dm.append(0)

        # -DM
        if down_move > up_move and down_move > 0:
            minus_dm.append(down_move)
        else:
            minus_dm.append(0)

        # True Range
        tr = max(
            high - low,
            abs(high - prev_close),
            abs(low - prev_close)
        )
        tr_list.append(tr)

    if len(tr_list) < period:
        return 0

    # 计算平滑值
    atr = sum(tr_list[-period:]) / period
    smoothed_plus_dm = sum(plus_dm[-period:]) / period
    smoothed_minus_dm = sum(minus_dm[-period:]) / period

    # 计算 +DI 和 -DI
    if atr == 0:
        return 0

    plus_di = (smoothed_plus_dm / atr) * 100
    minus_di = (smoothed_minus_dm / atr) * 100

    # 计算 DX
    di_sum = plus_di + minus_di
    if di_sum == 0:
        return 0

    dx = abs(plus_di - minus_di) / di_sum * 100

    return dx


def calculate_rsi(data: List[float], period: int = 14) -> float:
    """
    计算 RSI (Relative Strength Index)

    Args:
        data: 数据列表（如收盘价）
        period: 计算周期

    Returns:
        RSI 值 (0-100)
    """
    if len(data) < period + 1:
        return 50  # 默认中性值

    gains = []
    losses = []

    for i in range(1, len(data)):
        change = data[i] - data[i - 1]
        if change > 0:
            gains.append(change)
            losses.append(0)
        else:
            gains.append(0)
            losses.append(abs(change))

    if len(gains) < period:
        return 50

    avg_gain = sum(gains[-period:]) / period
    avg_loss = sum(losses[-period:]) / period

    if avg_loss == 0:
        return 100

    rs = avg_gain / avg_loss
    rsi = 100 - (100 / (1 + rs))

    return rsi


def calculate_macd(data: List[float], fast: int = 12, slow: int = 26, signal: int = 9) -> Dict:
    """
    计算 MACD (Moving Average Convergence Divergence)

    Args:
        data: 数据列表
        fast: 快线周期
        slow: 慢线周期
        signal: 信号线周期

    Returns:
        {"macd": float, "signal": float, "histogram": float}
    """
    if len(data) < slow + signal:
        return {"macd": 0, "signal": 0, "histogram": 0}

    # 计算快慢 EMA（简化用 SMA 近似）
    ema_fast = calculate_ema_approx(data, fast)
    ema_slow = calculate_ema_approx(data, slow)

    # MACD 线
    macd_line = ema_fast - ema_slow

    # 信号线（MACD 的移动平均）
    # 简化处理
    signal_line = macd_line  # 简化

    # 柱状图
    histogram = macd_line - signal_line

    return {
        "macd": macd_line,
        "signal": signal_line,
        "histogram": histogram
    }


def calculate_ema_approx(data: List[float], period: int) -> float:
    """
    计算指数移动平均线 (EMA) 的近似值

    Args:
        data: 数据列表
        period: 周期

    Returns:
        EMA 值
    """
    if not data:
        return 0
    if len(data) < period:
        return data[-1]

    # 简化：使用 SMA 近似
    return sum(data[-period:]) / period


def calculate_bollinger_bands(data: List[float], period: int = 20, std_dev: float = 2.0) -> Dict:
    """
    计算布林带

    Args:
        data: 数据列表
        period: 周期
        std_dev: 标准差倍数

    Returns:
        {"upper": float, "middle": float, "lower": float}
    """
    if len(data) < period:
        current = data[-1] if data else 0
        return {"upper": current, "middle": current, "lower": current}

    # 中轨（SMA）
    middle = sum(data[-period:]) / period

    # 计算标准差
    subset = data[-period:]
    variance = sum((x - middle) ** 2 for x in subset) / period
    std = variance ** 0.5

    # 上下轨
    upper = middle + std_dev * std
    lower = middle - std_dev * std

    return {
        "upper": upper,
        "middle": middle,
        "lower": lower
    }
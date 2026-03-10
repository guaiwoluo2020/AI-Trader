#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
行情API调用示例

演示EA端如何推送K线数据到Python服务
"""

import requests
import json
from datetime import datetime, timedelta
import random

# 服务地址
BASE_URL = "http://localhost:8000"


def generate_mock_klines(count: int, base_price: float, period_minutes: int) -> list:
    """
    生成模拟K线数据
    """
    klines = []
    now = datetime.now()

    for i in range(count):
        # 计算时间戳
        ts = now - timedelta(minutes=period_minutes * (count - i - 1))

        # 生成随机价格波动
        change = random.uniform(-0.01, 0.01) * base_price
        open_price = base_price + change
        high = open_price + random.uniform(0, 0.005) * base_price
        low = open_price - random.uniform(0, 0.005) * base_price
        close = open_price + random.uniform(-0.003, 0.003) * base_price

        klines.append({
            "timestamp": ts.strftime("%Y-%m-%d %H:%M:%S"),
            "open": round(open_price, 2),
            "high": round(high, 2),
            "low": round(low, 2),
            "close": round(close, 2),
            "volume": random.randint(100, 1000)
        })

        # 更新基准价格
        base_price = close

    return klines


def push_kline_single_period(symbol: str, period: str, klines: list, is_full: bool = False):
    """
    推送单个周期的K线数据
    """
    url = f"{BASE_URL}/ea/kline/{period}"

    payload = {
        "symbol": symbol,
        "is_full": is_full,
        "klines": klines
    }

    print(f"\n推送 {symbol} {period} K线数据 ({len(klines)} 条, {'全量' if is_full else '增量'})")

    try:
        response = requests.post(url, json=payload)
        print(f"状态码: {response.status_code}")
        print(f"响应: {response.json()}")

        # 检查是否需要全量数据
        if response.status_code == 400:
            data = response.json()
            if data.get('code') == 8888:
                print(">>> 服务需要全量数据，请重新发送历史数据")
                return 8888

        return response.status_code

    except Exception as e:
        print(f"请求失败: {e}")
        return None


def push_kline_batch(symbol: str, kline_data: dict, is_full: bool = False):
    """
    批量推送多个周期的K线数据
    """
    url = f"{BASE_URL}/ea/kline_batch"

    payload = {
        "symbol": symbol,
        "is_full": is_full,
        "data": kline_data
    }

    print(f"\n批量推送 {symbol} K线数据 ({'全量' if is_full else '增量'})")

    try:
        response = requests.post(url, json=payload)
        print(f"状态码: {response.status_code}")
        print(f"响应: {json.dumps(response.json(), indent=2, ensure_ascii=False)}")
        return response.status_code

    except Exception as e:
        print(f"请求失败: {e}")
        return None


def get_trades_with_price(symbol: str, price: float):
    """
    获取交易指令（携带价格，用于转折点检测）
    """
    url = f"{BASE_URL}/get_trades"
    params = {
        "symbol": symbol,
        "price": price
    }

    try:
        response = requests.get(url, params=params)
        data = response.json()

        print(f"\n获取 {symbol} 交易指令 (价格: {price})")
        print(f"交易指令: {data.get('trades', [])}")

        # 检查转折点提醒
        alerts = data.get('pivot_alerts', [])
        if alerts:
            print(f"\n⚠️ 转折点提醒 ({len(alerts)} 条):")
            for alert in alerts:
                print(f"  - {alert['period']} {alert['direction'] == 'high' and '高点' or '低点'}")
                print(f"    转折点价格: {alert['pivot_price']}")
                print(f"    当前价格: {alert['current_price']}")
                print(f"    距离: {alert['distance_pct']}%")
        else:
            print("无转折点提醒")

        return data

    except Exception as e:
        print(f"请求失败: {e}")
        return None


def get_klines(symbol: str, period: str, count: int = 100):
    """查询K线数据"""
    url = f"{BASE_URL}/market/kline/{symbol}"
    params = {"period": period, "count": count}

    try:
        response = requests.get(url, params=params)
        data = response.json()
        print(f"\n{symbol} {period} K线数据 ({data['count']} 条):")
        for k in data['data'][-3:]:
            print(f"  {k['timestamp']}: O={k['open']} H={k['high']} L={k['low']} C={k['close']}")
        return data

    except Exception as e:
        print(f"请求失败: {e}")
        return None


def get_pivots(symbol: str):
    """查询转折点"""
    url = f"{BASE_URL}/market/pivots/{symbol}"

    try:
        response = requests.get(url)
        data = response.json()
        print(f"\n{symbol} 转折点数据:")
        for period, pivots in data.get('data', {}).items():
            if pivots:
                print(f"  {period}: {len(pivots)} 个转折点")
                for p in pivots[:3]:
                    print(f"    - {p['direction'] == 'high' and '高点' or '低点'} @ {p['price']} ({p['timestamp']})")
        return data

    except Exception as e:
        print(f"请求失败: {e}")
        return None


def main():
    """
    演示完整的EA启动流程
    """
    print("=" * 60)
    print("EA 行情数据推送示例")
    print("=" * 60)

    symbol = "GOLD"
    base_price = 2030.0

    # ==================== 1. EA启动时推送全量K线数据 ====================
    print("\n[步骤1] EA启动，推送全量K线数据...")

    # 各周期历史数据条数（根据实际需求）
    history_counts = {
        'H4': 1100,   # 6个月约1100根4小时K线
        'H1': 720,    # 1个月约720根1小时K线
        'M15': 288,   # 3天约288根15分钟K线
        'M5': 288,    # 24小时288根5分钟K线
        'M1': 60      # 1小时60根1分钟K线
    }

    kline_data = {}
    period_minutes = {'H4': 240, 'H1': 60, 'M15': 15, 'M5': 5, 'M1': 1}

    for period, count in history_counts.items():
        kline_data[period] = generate_mock_klines(count, base_price, period_minutes[period])

    push_kline_batch(symbol, kline_data, is_full=True)

    # ==================== 2. 查询K线和转折点 ====================
    print("\n[步骤2] 查询K线数据和转折点...")

    get_klines(symbol, 'H4', 10)
    get_pivots(symbol)

    # ==================== 3. 模拟EA轮询获取交易指令 ====================
    print("\n[步骤3] EA轮询获取交易指令（携带当前价格）...")

    # 模拟当前价格
    current_price = base_price + random.uniform(-5, 5)
    get_trades_with_price(symbol, current_price)

    # ==================== 4. 模拟增量数据推送 ====================
    print("\n[步骤4] 推送增量K线数据...")

    # 生成一根新的K线
    for period in ['H4', 'H1', 'M15', 'M5', 'M1']:
        new_klines = generate_mock_klines(1, base_price, period_minutes[period])
        push_kline_single_period(symbol, period, new_klines, is_full=False)

    # ==================== 5. 查看服务状态 ====================
    print("\n[步骤5] 查看服务状态...")

    try:
        response = requests.get(f"{BASE_URL}/market/status")
        data = response.json()
        print(json.dumps(data, indent=2, ensure_ascii=False))
    except Exception as e:
        print(f"请求失败: {e}")

    print("\n" + "=" * 60)
    print("示例完成")
    print("=" * 60)


if __name__ == "__main__":
    main()
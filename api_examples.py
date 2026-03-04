#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
API使用示例
展示如何集成Python服务到各种场景
"""

import requests
import json
from datetime import datetime


# ==================== 示例 1: 基础调用 ====================

def example_1_basic_trade():
    """基础交易示例 - 发送单个订单"""
    print("\n" + "="*60)
    print("示例 1: 发送基础交易指令")
    print("="*60)
    
    url = "http://localhost:8000/send_trade_instructions"
    
    # 黄金买入订单
    trades = [{
        "symbol": "gold",
        "action": "b",  # b=买入, s=卖出
        "mount": 0.01,   # 0.01手
        "price": 2030.00, # 买入执行价格
        "sl": 5000,      # 止损在5000
        "tp": 5100       # 止盈在5100
    }]
    
    response = requests.post(url, json=trades)
    result = response.json()
    
    print(f"请求: {json.dumps(trades, ensure_ascii=False)}")
    print(f"响应: {json.dumps(result, indent=2, ensure_ascii=False)}")
    
    return result


def example_2a_invalid_order():
    """示例 2a: 包含无效指令的下单示例"""
    print("\n" + "="*60)
    print("示例 2a: 发送包含错误价格的订单")
    print("="*60)

    url = "http://localhost:8000/send_trade_instructions"

    trades = [
        {
            "symbol": "eurusd",
            "action": "b",
            "mount": 0.01,
            # 指令价格低于止损，应该被拒绝
            "price": 1.0000,
            "sl": 1.0050,
            "tp": 1.0100
        }
    ]

    response = requests.post(url, json=trades)
    result = response.json()

    print(f"请求: {json.dumps(trades, ensure_ascii=False)}")
    print(f"响应: {json.dumps(result, indent=2, ensure_ascii=False)}")

    return result


# ==================== 示例 2: 批量下单 ====================

def example_2_batch_orders():
    """批量下单示例 - 一次发送多个订单"""
    print("\n" + "="*60)
    print("示例 2: 批量下单（多品种）")
    print("="*60)
    
    url = "http://localhost:8000/send_trade_instructions"
    
    # 多个订单
    trades = [
        {
            "symbol": "gold",
            "action": "b",
            "mount": 0.01,
            "price": 2030.00,
            "sl": 5000,
            "tp": 5100
        },
        {
            "symbol": "eurusd",
            "action": "s",
            "mount": 0.02,
            "price": 1.0900,
            "sl": 1.0950,
            "tp": 1.0850
        },
        {
            "symbol": "gold",
            "action": "s",
            "mount": 0.015,
            "price": 2035.00,
            "sl": 2035,
            "tp": 2025
        }
    ]
    
    response = requests.post(url, json=trades)
    result = response.json()
    
    print(f"发送订单数: {len(trades)}")
    for i, trade in enumerate(trades, 1):
        action = "买入" if trade['action'] == 'b' else "卖出"
        print(f"  {i}. {trade['symbol'].upper()} {action} {trade['mount']}手 SL:{trade['sl']} TP:{trade['tp']}")
    
    print(f"\n响应: {json.dumps(result, indent=2, ensure_ascii=False)}")
    
    return result


# ==================== 示例 3: 查询待执行指令 ====================

def example_3_query_pending():
    """查询示例 - 查看所有待执行指令"""
    print("\n" + "="*60)
    print("示例 3: 查询待执行指令")
    print("="*60)
    
    url = "http://localhost:8000/query_pending_trades"
    
    response = requests.get(url)
    result = response.json()
    
    print(f"总待执行数: {result.get('total', 0)}")
    
    for symbol, trades in result.get('data', {}).items():
        print(f"\n{symbol}: {len(trades)}条")
        for i, trade in enumerate(trades, 1):
            action = "买入" if trade['action'] == 'b' else "卖出"
            print(f"  {i}. {action} {trade['mount']}手 SL:{trade['sl']} TP:{trade['tp']}")
    
    return result


# ==================== 示例 4: 查询统计数据 ====================

def example_4_query_statistics():
    """统计示例 - 查看历史统计数据"""
    print("\n" + "="*60)
    print("示例 4: 查询统计数据")
    print("="*60)
    
    url = "http://localhost:8000/query_statistics?count=5"
    
    response = requests.get(url)
    result = response.json()
    
    print(f"总条数: {result.get('count', 0)}\n")
    
    for i, stat in enumerate(result.get('data', []), 1):
        print(f"第 {i} 条:")
        print(f"  时间: {stat.get('timestamp')}")
        print(f"  TICK数: {stat.get('tickCount')}")
        print(f"  价格: {stat.get('bidPrice')} / {stat.get('askPrice')}")
        print(f"  余额: ${stat.get('balance'):.2f}")
        print(f"  权益: ${stat.get('equity'):.2f}")
        print(f"  预付款比例: {stat.get('marginLevel'):.2f}%")
        
        positions = stat.get('positions', [])
        if positions:
            print(f"  持仓数: {len(positions)}")
            for pos in positions:
                print(f"    - 票证: {pos.get('ticket')}, 浮盈: ${pos.get('profit'):.2f}")
        
        trades = stat.get('trades', [])
        if trades:
            print(f"  该分钟成交: {len(trades)}笔")
        
        print()
    
    return result


# ==================== 示例 5: 清空指令 ====================

def example_5_clear_orders():
    """清空示例 - 删除未执行指令"""
    print("\n" + "="*60)
    print("示例 5: 清空交易指令")
    print("="*60)
    
    # 清空所有指令
    url = "http://localhost:8000/clear_trades"
    response = requests.delete(url)
    result = response.json()
    
    print(f"清空结果: {json.dumps(result, indent=2, ensure_ascii=False)}")
    
    return result


# ==================== 示例 6: 实时监控 ====================

def example_6_monitor_status():
    """监控示例 - 实时查看服务状态"""
    print("\n" + "="*60)
    print("示例 6: 服务状态监控")
    print("="*60)
    
    url = "http://localhost:8000/status"
    
    response = requests.get(url)
    result = response.json()
    
    print(f"服务状态: {result.get('status')}")
    print(f"总待执行数: {result.get('total_pending')}")
    print(f"统计数据条数: {result.get('statistics_records')}/10")
    print(f"时间: {result.get('timestamp')}")
    
    print(f"\n待执行分析:")
    pending = result.get('pending_trades', {})
    if pending:
        for symbol, count in pending.items():
            print(f"  {symbol}: {count}条待执行")
    else:
        print("  无待执行指令")
    
    return result


# ==================== 示例 7: 条件交易策略 ====================

def example_7_conditional_trading():
    """策略示例 - 基于条件的自动交易"""
    print("\n" + "="*60)
    print("示例 7: 条件交易策略")
    print("="*60)
    
    url_stats = "http://localhost:8000/query_statistics"
    url_trade = "http://localhost:8000/send_trade_instructions"
    
    # 获取最新统计
    response = requests.get(url_stats + "?count=1")
    stats = response.json().get('data', [])
    
    if not stats:
        print("没有统计数据，跳过策略执行")
        return
    
    latest = stats[0]
    price = (latest.get('bidPrice', 0) + latest.get('askPrice', 0)) / 2
    margin = latest.get('marginLevel', 100)
    
    print(f"当前价格: {price:.2f}")
    print(f"预付款比例: {margin:.2f}%")
    
    # 策略逻辑
    if price > 2030 and margin > 80:
        print("→ 触发卖出信号 (价格高且有充足保证金)")
        
        trade = [{
            "symbol": "gold",
            "action": "s",
            "mount": 0.01,
            "price": 2035.00,
            "sl": 2035,
            "tp": 2020
        }]
        
        response = requests.post(url_trade, json=trade)
        result = response.json()
        print(f"下单结果: {result.get('message')}")
    
    elif price < 2020 and margin > 80:
        print("→ 触发买入信号 (价格低且有充足保证金)")
        
        trade = [{
            "symbol": "gold",
            "action": "b",
            "mount": 0.01,
            "price": 2020.00,
            "sl": 2015,
            "tp": 2030
        }]
        
        response = requests.post(url_trade, json=trade)
        result = response.json()
        print(f"下单结果: {result.get('message')}")
    
    else:
        print("→ 无交易信号，保持观察")
    
    return


# ==================== 示例 8: 风险管理 ====================

def example_8_risk_management():
    """风险管理示例 - 监控账户风险"""
    print("\n" + "="*60)
    print("示例 8: 风险管理")
    print("="*60)
    
    url = "http://localhost:8000/query_statistics?count=1"
    
    response = requests.get(url)
    stats = response.json().get('data', [])
    
    if not stats:
        print("没有数据")
        return
    
    latest = stats[0]
    balance = latest.get('balance', 0)
    equity = latest.get('equity', 0)
    margin = latest.get('marginLevel', 0)
    
    # 计算风险指标
    loss_percent = ((balance - equity) / balance) * 100 if balance > 0 else 0
    
    print(f"账户余额: ${balance:.2f}")
    print(f"账户权益: ${equity:.2f}")
    print(f"浮亏: ${balance - equity:.2f} ({loss_percent:.2f}%)")
    print(f"预付款比例: {margin:.2f}%")
    
    # 风险警告
    if loss_percent > 30:
        print("⚠️  警告: 损失超过30%")
    elif loss_percent > 20:
        print("⚠️  注意: 损失超过20%")
    elif margin < 50:
        print("⚠️  警告: 预付款比例低于50%")
    
    # 持仓分析
    positions = latest.get('positions', [])
    if positions:
        print(f"\n持仓统计 ({len(positions)}个):")
        total_profit = sum(p.get('profit', 0) for p in positions)
        print(f"  总浮盈: ${total_profit:.2f}")
        
        for pos in positions:
            profit = pos.get('profit', 0)
            distance_sl = pos.get('distanceSL', 0)
            distance_tp = pos.get('distanceTP', 0)
            
            if profit < 0 and distance_sl < 100:
                print(f"  ⚠️  持仓{pos.get('ticket')}接近止损 (距离:{distance_sl:.0f}点)")
    
    return latest


# ==================== 示例 9: 数据导出 ====================

def example_9_export_data():
    """数据导出示例 - 将统计数据导出为CSV"""
    print("\n" + "="*60)
    print("示例 9: 数据导出")
    print("="*60)
    
    import csv
    from datetime import datetime
    
    url = "http://localhost:8000/query_statistics?count=10"
    
    response = requests.get(url)
    stats = response.json().get('data', [])
    
    if not stats:
        print("无数据")
        return
    
    # 导出为CSV
    filename = f"statistics_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
    
    with open(filename, 'w', newline='') as f:
        writer = csv.writer(f)
        writer.writerow(['时间', 'TICK数', '买价', '卖价', '余额', '权益', '预付款比例', '持仓数'])
        
        for stat in stats:
            writer.writerow([
                stat.get('timestamp'),
                stat.get('tickCount'),
                stat.get('bidPrice'),
                stat.get('askPrice'),
                stat.get('balance'),
                stat.get('equity'),
                stat.get('marginLevel'),
                len(stat.get('positions', []))
            ])
    
    print(f"✓ 数据已导出: {filename}")
    print(f"  共 {len(stats)} 条记录")
    
    return filename


# ==================== 主程序 ====================

def main():
    """运行所有示例"""
    print("\n")
    print("#" * 60)
    print("# Python交易服务 - 完整使用示例")
    print("#" * 60)
    
    try:
        # 检查服务连接
        response = requests.get("http://localhost:8000/health", timeout=2)
        if response.status_code != 200:
            print("❌ 无法连接到服务")
            return
    except:
        print("❌ 无法连接到服务，请确保已启动: python trading_server.py")
        return
    
    print("✓ 服务已连接")
    
    # 运行示例
    examples = [
        ("基础交易", example_1_basic_trade),
        ("批量下单", example_2_batch_orders),
        ("错误指令示例", example_2a_invalid_order),
        ("查询指令", example_3_query_pending),
        ("查询统计", example_4_query_statistics),
        ("清空指令", example_5_clear_orders),
        ("状态监控", example_6_monitor_status),
        ("条件策略", example_7_conditional_trading),
        ("风险管理", example_8_risk_management),
        ("数据导出", example_9_export_data)
    ]
    
    for name, func in examples:
        try:
            input(f"\n按 Enter 运行: {name}...")
            func()
        except Exception as e:
            print(f"❌ 错误: {str(e)}")
    
    print("\n" + "#" * 60)
    print("# 示例运行完成")
    print("#" * 60)


if __name__ == "__main__":
    main()

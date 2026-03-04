#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
交易指令发送工具
供交易员快速发送交易指令
可由前端界面、命令行或其他系统调用
"""

import requests
import json
from typing import List, Dict
import sys


class TradeInstructionClient:
    """交易指令发送客户端"""
    
    def __init__(self, server_url: str = "http://localhost:8000"):
        self.server_url = server_url
        self.session = requests.Session()
    
    def send_buy_order(self, symbol: str, volume: float, price: float, sl: float, tp: float) -> Dict:
        """
        发送买入订单
        
        Args:
            symbol: 交易品种 (e.g., "gold", "eurusd")
            volume: 手数 (e.g., 0.01)
            price: 买入执行价格 (e.g., 2030.00)
            sl: 止损点 (e.g., 5000)
            tp: 止盈点 (e.g., 5100)
        
        Returns:
            服务响应
        """
        instruction = {
            "symbol": symbol.lower(),
            "action": "b",
            "mount": volume,
            "price": price,
            "sl": sl,
            "tp": tp
        }
        return self.send_instructions([instruction])
    
    def send_sell_order(self, symbol: str, volume: float, price: float, sl: float, tp: float) -> Dict:
        """
        发送卖出订单
        
        Args:
            symbol: 交易品种
            volume: 手数
            price: 卖出执行价格
            sl: 止损点
            tp: 止盈点
        
        Returns:
            服务响应
        """
        instruction = {
            "symbol": symbol.lower(),
            "action": "s",
            "mount": volume,
            "price": price,
            "sl": sl,
            "tp": tp
        }
        return self.send_instructions([instruction])
    
    def send_instructions(self, instructions: List[Dict]) -> Dict:
        """
        发送多条交易指令
        
        Args:
            instructions: 交易指令列表
        
        Returns:
            服务响应
        """
        try:
            response = self.session.post(
                f"{self.server_url}/send_trade_instructions",
                json=instructions,
                timeout=5
            )
            return response.json()
        except Exception as e:
            return {
                "status": "error",
                "message": str(e)
            }
    
    def get_pending_trades(self) -> Dict:
        """
        获取待执行的交易指令
        
        Returns:
            待执行指令列表
        """
        try:
            response = self.session.get(
                f"{self.server_url}/query_pending_trades",
                timeout=5
            )
            return response.json()
        except Exception as e:
            return {
                "status": "error",
                "message": str(e)
            }
    
    def get_statistics(self, count: int = 10) -> Dict:
        """
        获取统计数据
        
        Args:
            count: 获取最新N条数据
        
        Returns:
            统计数据列表
        """
        try:
            response = self.session.get(
                f"{self.server_url}/query_statistics?count={count}",
                timeout=5
            )
            return response.json()
        except Exception as e:
            return {
                "status": "error",
                "message": str(e)
            }
    
    def clear_trades(self, symbol: str = None) -> Dict:
        """
        清空交易指令
        
        Args:
            symbol: 品种名称，不指定则清空所有
        
        Returns:
            服务响应
        """
        try:
            url = f"{self.server_url}/clear_trades"
            if symbol:
                url += f"?symbol={symbol}"
            
            response = self.session.delete(url, timeout=5)
            return response.json()
        except Exception as e:
            return {
                "status": "error",
                "message": str(e)
            }
    
    def health_check(self) -> bool:
        """
        检查服务是否可用
        
        Returns:
            True 如果服务可用，False 否则
        """
        try:
            response = self.session.get(
                f"{self.server_url}/health",
                timeout=2
            )
            return response.status_code == 200
        except:
            return False


# ==================== 命令行工具 ====================

def main():
    """命令行界面"""
    client = TradeInstructionClient()
    
    print("=" * 60)
    print("交易指令发送工具")
    print("=" * 60)
    
    # 检查服务
    if not client.health_check():
        print("错误: 无法连接到交易服务")
        print("请确保服务已启动: python trading_server.py")
        return
    
    print("✓ 服务已连接\n")
    
    while True:
        print("\n请选择操作:")
        print("1. 发送买入订单")
        print("2. 发送卖出订单")
        print("3. 查看待执行指令")
        print("4. 查看统计数据")
        print("5. 清空指令")
        print("0. 退出")
        
        choice = input("\n请输入选项 (0-5): ").strip()
        
        if choice == "0":
            print("再见!")
            break
        
        elif choice == "1":
            symbol = input("品种 (e.g., gold): ").strip().lower()
            try:
                volume = float(input("手数 (e.g., 0.01): ").strip())
                sl = float(input("止损点 (e.g., 5000): ").strip())
                tp = float(input("止盈点 (e.g., 5100): ").strip())
                
                result = client.send_buy_order(symbol, volume, sl, tp)
                print(f"\n响应: {json.dumps(result, indent=2, ensure_ascii=False)}")
            except ValueError:
                print("输入格式错误")
        
        elif choice == "2":
            symbol = input("品种 (e.g., eurusd): ").strip().lower()
            try:
                volume = float(input("手数 (e.g., 0.02): ").strip())
                sl = float(input("止损点 (e.g., 1.0950): ").strip())
                tp = float(input("止盈点 (e.g., 1.0850): ").strip())
                
                result = client.send_sell_order(symbol, volume, sl, tp)
                print(f"\n响应: {json.dumps(result, indent=2, ensure_ascii=False)}")
            except ValueError:
                print("输入格式错误")
        
        elif choice == "3":
            result = client.get_pending_trades()
            if "data" in result:
                total = result.get("total", 0)
                print(f"\n待执行指令总数: {total}")
                for symbol, trades in result.get("data", {}).items():
                    print(f"\n{symbol}: {len(trades)} 条")
                    for i, trade in enumerate(trades, 1):
                        print(f"  {i}. {trade['action'].upper()} {trade['mount']}手 SL:{trade['sl']} TP:{trade['tp']}")
            else:
                print(f"\n错误: {result.get('message', 'Unknown error')}")
        
        elif choice == "4":
            try:
                count = int(input("获取最新N条 (默认10): ").strip() or "10")
                result = client.get_statistics(count)
                
                if "data" in result:
                    data = result.get("data", [])
                    print(f"\n共 {len(data)} 条统计数据:\n")
                    for i, stat in enumerate(data, 1):
                        print(f"{i}. 时间: {stat.get('timestamp')}")
                        print(f"   TICK数: {stat.get('tickCount')}")
                        print(f"   价格: {stat.get('bidPrice')} / {stat.get('askPrice')}")
                        print(f"   余额: {stat.get('balance')}")
                        print(f"   权益: {stat.get('equity')}")
                        print(f"   预付款: {stat.get('marginLevel')}%")
                        print()
                else:
                    print(f"\n错误: {result.get('message', 'Unknown error')}")
            except ValueError:
                print("输入格式错误")
        
        elif choice == "5":
            symbol = input("清空指定品种 (不指定则清空所有): ").strip().lower() or None
            result = client.clear_trades(symbol)
            print(f"\n响应: {json.dumps(result, indent=2, ensure_ascii=False)}")
        
        else:
            print("无效选项")


# ==================== 使用示例 ====================

def example_usage():
    """API使用示例"""
    client = TradeInstructionClient()
    
    # 示例 1: 发送单个订单
    print("[示例 1] 发送黄金买入订单")
    result = client.send_buy_order("gold", 0.01, 5000, 5100)
    print(json.dumps(result, indent=2, ensure_ascii=False))
    
    # 示例 2: 发送多个订单
    print("\n[示例 2] 发送多个订单")
    instructions = [
        {"symbol": "gold", "action": "s", "mount": 0.02, "sl": 2035, "tp": 2025},
        {"symbol": "eurusd", "action": "b", "mount": 0.05, "sl": 1.0900, "tp": 1.1000}
    ]
    result = client.send_instructions(instructions)
    print(json.dumps(result, indent=2, ensure_ascii=False))
    
    # 示例 3: 查看待执行指令
    print("\n[示例 3] 查看待执行指令")
    result = client.get_pending_trades()
    print(json.dumps(result, indent=2, ensure_ascii=False))
    
    # 示例 4: 查看最新统计数据
    print("\n[示例 4] 查看最新5条统计数据")
    result = client.get_statistics(5)
    print(json.dumps(result, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--example":
        # 运行示例: python trade_client.py --example
        example_usage()
    else:
        # 运行交互式工具
        main()

#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
交易服务API测试脚本
用于测试所有HTTP接口
"""

import requests
import json
from datetime import datetime

# 服务地址
BASE_URL = "http://localhost:8000"

class TradingServiceTester:
    """交易服务测试类"""
    
    def __init__(self, base_url=BASE_URL):
        self.base_url = base_url
        self.session = requests.Session()
    
    def test_health(self):
        """测试健康检查"""
        print("\n" + "="*60)
        print("测试 1: 健康检查")
        print("="*60)
        try:
            response = self.session.get(f"{self.base_url}/health")
            print(f"状态码: {response.status_code}")
            print(f"响应: {json.dumps(response.json(), indent=2, ensure_ascii=False)}")
            return response.status_code == 200
        except Exception as e:
            print(f"错误: {str(e)}")
            return False
    
    def test_status(self):
        """测试获取服务状态"""
        print("\n" + "="*60)
        print("测试 2: 获取服务状态")
        print("="*60)
        try:
            response = self.session.get(f"{self.base_url}/status")
            print(f"状态码: {response.status_code}")
            print(f"响应: {json.dumps(response.json(), indent=2, ensure_ascii=False)}")
            return response.status_code == 200
        except Exception as e:
            print(f"错误: {str(e)}")
            return False
    
    def test_send_instructions(self):
        """测试发送交易指令"""
        print("\n" + "="*60)
        print("测试 3: 交易员下发交易指令")
        print("="*60)
        
        # 构建测试指令
        instructions = [
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
        
        print(f"发送指令数: {len(instructions)}")
        print(f"指令内容: {json.dumps(instructions, indent=2, ensure_ascii=False)}")
        
        try:
            response = self.session.post(
                f"{self.base_url}/send_trade_instructions",
                json=instructions
            )
            print(f"状态码: {response.status_code}")
            print(f"响应: {json.dumps(response.json(), indent=2, ensure_ascii=False)}")
            return response.status_code == 200
        except Exception as e:
            print(f"错误: {str(e)}")
            return False
    
    def test_query_pending(self):
        """测试查询待执行指令"""
        print("\n" + "="*60)
        print("测试 4: 查询所有待执行指令")
        print("="*60)
        try:
            response = self.session.get(f"{self.base_url}/query_pending_trades")
            print(f"状态码: {response.status_code}")
            data = response.json()
            print(f"响应: {json.dumps(data, indent=2, ensure_ascii=False)}")
            return response.status_code == 200
        except Exception as e:
            print(f"错误: {str(e)}")
            return False
    
    def test_get_trades(self):
        """测试EA获取指令（带价格过滤）"""
        print("\n" + "="*60)
        print("测试 5: EA获取 GOLD 指令（带价格过滤）")
        print("="*60)
        try:
            # 测试不同价格，看看是否触发过滤
            current_price = 2035.50
            response = self.session.get(
                f"{self.base_url}/get_trades?symbol=gold&price={current_price}"
            )
            print(f"状态码: {response.status_code}")
            print(f"查询价格: {current_price}")
            data = response.json()
            print(f"获取指令数: {len(data)}")
            print(f"响应: {json.dumps(data, indent=2, ensure_ascii=False)}")
            return response.status_code == 200
        except Exception as e:
            print(f"错误: {str(e)}")
            return False
    
    def test_send_statistics(self):
        """测试EA发送统计数据"""
        print("\n" + "="*60)
        print("测试 6: EA发送统计数据")
        print("="*60)
        
        stat_data = {
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "tickCount": 1234,
            "bidPrice": 2035.50,
            "askPrice": 2035.60,
            "balance": 50000.00,
            "equity": 51234.56,
            "marginLevel": 98.50,
            "positions": [
                {
                    "ticket": 123456,
                    "volume": 0.01,
                    "priceOpen": 2030.00,
                    "type": "BUY",
                    "profit": 55.60,
                    "distanceSL": 30.50,
                    "distanceTP": 35.40
                }
            ],
            "trades": [
                {
                    "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    "action": "BUY",
                    "symbol": "GOLD",
                    "volume": 0.01,
                    "price": 2030.00,
                    "sl": 2000,
                    "tp": 2100
                }
            ]
        }
        
        print(f"发送统计数据: {json.dumps(stat_data, indent=2, ensure_ascii=False)}")
        
        try:
            response = self.session.post(
                f"{self.base_url}/send_statistics",
                json=stat_data
            )
            print(f"状态码: {response.status_code}")
            print(f"响应: {json.dumps(response.json(), indent=2, ensure_ascii=False)}")
            return response.status_code == 200
        except Exception as e:
            print(f"错误: {str(e)}")
            return False
    
    def test_query_statistics(self):
        """测试查询统计数据"""
        print("\n" + "="*60)
        print("测试 7: 查询统计数据（最新5条）")
        print("="*60)
        try:
            response = self.session.get(
                f"{self.base_url}/query_statistics?count=5"
            )
            print(f"状态码: {response.status_code}")
            data = response.json()
            print(f"返回数据条数: {data.get('count', 0)}")
            print(f"响应: {json.dumps(data, indent=2, ensure_ascii=False)}")
            return response.status_code == 200
        except Exception as e:
            print(f"错误: {str(e)}")
            return False
    
    def test_clear_trades(self):
        """测试清空指令"""
        print("\n" + "="*60)
        print("测试 8: 清空 EURUSD 的交易指令")
        print("="*60)
        try:
            response = self.session.delete(
                f"{self.base_url}/clear_trades?symbol=eurusd"
            )
            print(f"状态码: {response.status_code}")
            print(f"响应: {json.dumps(response.json(), indent=2, ensure_ascii=False)}")
            return response.status_code == 200
        except Exception as e:
            print(f"错误: {str(e)}")
            return False
    
    def run_all_tests(self):
        """运行所有测试"""
        print("\n" + "#"*60)
        print("# 交易服务 API 完整测试")
        print("#"*60)
        
        results = []
        
        # 执行测试
        results.append(("健康检查", self.test_health()))
        results.append(("服务状态", self.test_status()))
        results.append(("发送指令", self.test_send_instructions()))
        results.append(("查询待执行", self.test_query_pending()))
        results.append(("EA获取指令", self.test_get_trades()))
        results.append(("EA发送统计", self.test_send_statistics()))
        results.append(("查询统计数据", self.test_query_statistics()))
        results.append(("清空指令", self.test_clear_trades()))
        
        # 打印测试结果总结
        print("\n" + "#"*60)
        print("# 测试结果总结")
        print("#"*60)
        for test_name, result in results:
            status = "✓ 成功" if result else "✗ 失败"
            print(f"{status} - {test_name}")
        
        success_count = sum(1 for _, r in results if r)
        print(f"\n总计: {success_count}/{len(results)} 个测试通过")
        
        return success_count == len(results)


if __name__ == "__main__":
    tester = TradingServiceTester()
    
    # 检查服务是否可达
    try:
        requests.get(f"{BASE_URL}/health", timeout=2)
        print(f"✓ 服务已启动: {BASE_URL}")
    except:
        print(f"✗ 无法连接到服务: {BASE_URL}")
        print("请确保已运行: python trading_server.py")
        exit(1)
    
    # 运行所有测试
    tester.run_all_tests()

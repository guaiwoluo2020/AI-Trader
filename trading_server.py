#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
高性能行情分析交易服务
支持：
1. EA推送交易指令和接收统计数据
2. 交易员下发交易指令
3. 交易员查看统计数据
"""

from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from typing import List, Dict, Optional
from collections import deque, defaultdict
from datetime import datetime
import json
import threading
import uvicorn

# ==================== 数据模型定义 ====================

class TradeInstruction(BaseModel):
    """交易指令模型"""
    symbol: str  # 交易品种，如 "gold"
    action: str  # b=买入, s=卖出
    mount: float  # 手数
    price: float  # 指令执行价格（买入时为买入价，卖出时为卖出价）
    sl: Optional[float] = 0.0  # 止损点, 可以缺省
    tp: Optional[float] = 0.0  # 止盈点, 可以缺省，若未指定将在服务端设置为0.005


class StatisticData(BaseModel):
    """统计数据模型"""
    timestamp: str  # 时间戳
    tickCount: int  # TICK计数
    bidPrice: float  # 买价
    askPrice: float  # 卖价
    balance: float  # 账户余额
    equity: float  # 账户权益
    marginLevel: float  # 预付款比例
    positions: list  # 持仓信息
    trades: list  # 交易记录


# ==================== 全局数据存储 ====================

class TradingServer:
    """交易服务主类"""
    
    def __init__(self):
        # 交易指令队列 - 按SYMBOL分类
        # 结构: {"SYMBOL1": [TradeInstruction1, ...], "SYMBOL2": [...]}
        self.trade_instructions = defaultdict(list)
        
        # 统计数据历史 - 保留最新10条
        # 结构: deque([{stat_data1}, {stat_data2}, ...], maxlen=10)
        self.statistics_history = deque(maxlen=10)
        
        # 线程锁 - 确保线程安全
        self.lock = threading.RLock()
        
        print("[信息] 交易服务已初始化")

    def add_trade_instruction(self, instructions: List[TradeInstruction]) -> int:
        """
        添加交易指令
        返回添加的指令数量
        在此处对缺失的 sl/tp 值进行补全：
          - sl 若未设置保持0.0
          - tp 若未设置则默认 0.005
        """
        with self.lock:
            count = 0
            for instruction in instructions:
                # 填充默认值
                if instruction.sl is None:
                    instruction.sl = 0.0
                if instruction.tp is None or instruction.tp <= 0.0:
                    instruction.tp = 0.005

                symbol = instruction.symbol.upper()
                self.trade_instructions[symbol].append(instruction)
                count += 1
            
            print(f"[信息] 已添加 {count} 条交易指令")
            for symbol, trades in self.trade_instructions.items():
                print(f"       {symbol}: {len(trades)} 条待执行")
            
            return count

    def get_trades_by_symbol(self, symbol: str, price: Optional[float] = None) -> List[Dict]:
        """
        获取指定SYMBOL的交易指令并删除
        根据价格条件过滤指令：
          - 买入指令（action='b'）：如果指令价格 > 当前价格，缓存（等待价格下跌到指令价格）
          - 卖出指令（action='s'）：如果指令价格 < 当前价格，缓存（等待价格上涨到指令价格）
        返回指令列表（JSON格式）
        """
        with self.lock:
            symbol = symbol.upper()
            if symbol not in self.trade_instructions or len(self.trade_instructions[symbol]) == 0:
                return []
            
            # 获取所有指令
            trades = self.trade_instructions[symbol]
            result = []
            cached_trades = []
            
            for t in trades:
                should_send = True
                
                # 如果提供了价格，进行条件检查
                if price is not None:
                    if t.action.lower() == 'b':
                        # 买入指令：如果指令价格 > 当前价格，则缓存
                        if t.price > price:
                            should_send = False
                            cached_trades.append(t)
                    elif t.action.lower() == 's':
                        # 卖出指令：如果指令价格 < 当前价格，则缓存
                        if t.price < price:
                            should_send = False
                            cached_trades.append(t)
                
                if should_send:
                    result.append({
                        "symbol": t.symbol.lower(),
                        "action": t.action.lower(),
                        "mount": t.mount,
                        "price": t.price,
                        "sl": t.sl,
                        "tp": t.tp
                    })
            
            # 更新指令队列：移除已发送的，保留已缓存的
            self.trade_instructions[symbol] = cached_trades
            
            if len(result) > 0:
                print(f"[信息] 推送了 {len(result)} 条 {symbol} 指令给EA (当前价格: {price})")
            if len(cached_trades) > 0:
                print(f"[信息] 缓存了 {len(cached_trades)} 条 {symbol} 指令，等待价格条件满足")
            
            return result

    def save_statistics(self, stat_data: dict) -> None:
        """
        保存统计数据
        自动保留最新10条
        """
        with self.lock:
            self.statistics_history.append(stat_data)
            print(f"[信息] 统计数据已记录 - {stat_data.get('timestamp', 'unknown')}")
            print(f"       当前保存数据条数: {len(self.statistics_history)}")

    def get_latest_statistics(self, count: int = 10) -> List[Dict]:
        """
        获取最新的统计数据
        """
        with self.lock:
            return list(self.statistics_history)[-count:]

    def get_all_pending_trades(self) -> Dict[str, List[Dict]]:
        """
        获取所有待执行的交易指令（不删除）
        用于查询接口
        """
        with self.lock:
            result = {}
            for symbol, trades in self.trade_instructions.items():
                result[symbol] = [
                    {
                        "symbol": t.symbol.lower(),
                        "action": t.action.lower(),
                        "mount": t.mount,
                        "sl": t.sl,
                        "tp": t.tp
                    }
                    for t in trades
                ]
            return result

    def clear_trades(self, symbol: Optional[str] = None) -> int:
        """
        清空交易指令
        如果指定symbol则只清空该symbol
        返回清空的指令数量
        """
        with self.lock:
            if symbol is None:
                total = sum(len(trades) for trades in self.trade_instructions.values())
                self.trade_instructions.clear()
                print(f"[信息] 已清空所有交易指令，共 {total} 条")
                return total
            else:
                symbol = symbol.upper()
                count = len(self.trade_instructions.get(symbol, []))
                if symbol in self.trade_instructions:
                    del self.trade_instructions[symbol]
                print(f"[信息] 已清空 {symbol} 的交易指令，共 {count} 条")
                return count


# ==================== 创建应用 ====================

app = FastAPI(title="行情分析交易服务", version="1.0")
server = TradingServer()


# ==================== EA相关接口 ====================

@app.get("/get_trades")
async def get_trades(symbol: str = "gold", price: Optional[float] = None):
    """
    EA调用：获取待执行的交易指令
    参数：
      - symbol: 交易品种 (e.g., "gold")
      - price: 当前价格，用于价格条件过滤 (可选)
    
    返回：JSON列表，包含满足价格条件的待执行指令
    
    价格过滤逻辑：
      - 买入指令(action='b')：若目标价格(tp) > 当前价格，则缓存不发送
      - 卖出指令(action='s')：若目标价格(tp) < 当前价格，则缓存不发送
    """
    try:
        trades = server.get_trades_by_symbol(symbol, price)
        return JSONResponse(content=trades)
    except Exception as e:
        print(f"[错误] get_trades 异常: {str(e)}")
        return JSONResponse(status_code=500, content={"error": str(e)})


@app.post("/send_statistics")
async def send_statistics(data: dict):
    """
    EA调用：发送统计数据
    参数：data - 统计数据JSON
    返回：确认信息
    """
    try:
        server.save_statistics(data)
        return JSONResponse(
            status_code=200,
            content={"status": "success", "message": "统计数据已记录"}
        )
    except Exception as e:
        print(f"[错误] send_statistics 异常: {str(e)}")
        return JSONResponse(status_code=500, content={"error": str(e)})


# ==================== 交易员相关接口 ====================

@app.post("/send_trade_instructions")
async def send_trade_instructions(instructions: List[TradeInstruction]):
    """
    交易员调用：下发交易指令
    
    请求示例：
    POST /send_trade_instructions
    [
        {
            "symbol": "gold",
            "action": "b",
            "mount": 0.01,
            "sl": 5000,
            "tp": 5100
        },
        {
            "symbol": "eurusd",
            "action": "s",
            "mount": 0.02,
            "sl": 1.0950,
            "tp": 1.0850
        }
    ]
    
    返回：
    {
        "status": "success",
        "count": 2,
        "message": "已添加 2 条交易指令"
    }
    """
    try:
        if not instructions:
            raise HTTPException(status_code=400, detail="指令列表不能为空")
        
        count = server.add_trade_instruction(instructions)
        return JSONResponse(
            status_code=200,
            content={
                "status": "success",
                "count": count,
                "message": f"已添加 {count} 条交易指令"
            }
        )
    except Exception as e:
        print(f"[错误] send_trade_instructions 异常: {str(e)}")
        return JSONResponse(
            status_code=500,
            content={"status": "error", "message": str(e)}
        )


@app.get("/query_statistics")
async def query_statistics(count: int = 10):
    """
    交易员调用：查询统计数据
    
    参数：count - 获取最新N条数据（默认10条）
    
    返回示例：
    [
        {
            "timestamp": "2026-03-04 14:30",
            "tickCount": 1234,
            "bidPrice": 2035.50,
            "askPrice": 2035.60,
            "balance": 50000.00,
            "equity": 51234.56,
            "marginLevel": 98.50,
            "positions": [...],
            "trades": [...]
        },
        ...
    ]
    """
    try:
        if count <= 0 or count > 100:
            count = 10
        
        stats = server.get_latest_statistics(count)
        return JSONResponse(
            status_code=200,
            content={
                "status": "success",
                "count": len(stats),
                "data": stats
            }
        )
    except Exception as e:
        print(f"[错误] query_statistics 异常: {str(e)}")
        return JSONResponse(
            status_code=500,
            content={"status": "error", "message": str(e)}
        )


@app.get("/query_pending_trades")
async def query_pending_trades():
    """
    交易员调用：查询所有待执行的交易指令
    
    返回示例：
    {
        "status": "success",
        "total": 5,
        "data": {
            "GOLD": [
                {
                    "symbol": "gold",
                    "action": "b",
                    "mount": 0.01,
                    "sl": 5000,
                    "tp": 5100
                }
            ],
            "EURUSD": [...]
        }
    }
    """
    try:
        trades = server.get_all_pending_trades()
        total = sum(len(t) for t in trades.values())
        
        return JSONResponse(
            status_code=200,
            content={
                "status": "success",
                "total": total,
                "data": trades
            }
        )
    except Exception as e:
        print(f"[错误] query_pending_trades 异常: {str(e)}")
        return JSONResponse(
            status_code=500,
            content={"status": "error", "message": str(e)}
        )


@app.delete("/clear_trades")
async def clear_trades(symbol: Optional[str] = None):
    """
    交易员调用：清空交易指令
    
    参数：
    - symbol (可选): 指定品种，不指定则清空所有
    
    返回示例：
    {
        "status": "success",
        "cleared": 3,
        "message": "已清空 3 条交易指令"
    }
    """
    try:
        count = server.clear_trades(symbol)
        return JSONResponse(
            status_code=200,
            content={
                "status": "success",
                "cleared": count,
                "message": f"已清空 {count} 条交易指令"
            }
        )
    except Exception as e:
        print(f"[错误] clear_trades 异常: {str(e)}")
        return JSONResponse(
            status_code=500,
            content={"status": "error", "message": str(e)}
        )


# ==================== 系统接口 ====================

@app.get("/health")
async def health_check():
    """健康检查接口"""
    return JSONResponse(
        status_code=200,
        content={
            "status": "healthy",
            "service": "Trading Analysis Server",
            "timestamp": datetime.now().isoformat()
        }
    )


@app.get("/status")
async def get_status():
    """
    获取服务状态
    
    返回示例：
    {
        "status": "running",
        "pending_trades": {
            "GOLD": 2,
            "EURUSD": 1
        },
        "statistics_records": 5,
        "timestamp": "2026-03-04T14:30:45.123456"
    }
    """
    try:
        pending = server.get_all_pending_trades()
        pending_count = {symbol: len(trades) for symbol, trades in pending.items()}
        
        return JSONResponse(
            status_code=200,
            content={
                "status": "running",
                "pending_trades": pending_count,
                "total_pending": sum(pending_count.values()),
                "statistics_records": len(server.statistics_history),
                "timestamp": datetime.now().isoformat()
            }
        )
    except Exception as e:
        print(f"[错误] get_status 异常: {str(e)}")
        return JSONResponse(
            status_code=500,
            content={"status": "error", "message": str(e)}
        )


# ==================== 应用启动 ====================

if __name__ == "__main__":
    print("=" * 60)
    print("启动行情分析交易服务")
    print("=" * 60)
    print(f"[INFO] 服务将运行在 http://localhost:5858")
    print(f"[INFO] API文档: http://localhost:5858/docs")
    print(f"[INFO] 备用文档: http://localhost:5858/redoc")
    print("=" * 60)
    
    # 使用uvicorn启动服务，设置高并发参数
    uvicorn.run(
        app,
        host="127.0.0.1",
        port=5858,
        workers=4,  # 多个worker进程
        loop="uvloop",  # 使用高性能事件循环
        log_level="info"
    )

#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
交易服务主文件
"""

import sys
import os
import asyncio
import uvloop
import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

# 使用 uvloop 加速
asyncio_policy = uvloop.EventLoopPolicy()
asyncio.set_event_loop_policy(asyncio_policy)

from server import TradingServer
from routes_ea import create_ea_routes
from routes_trader import create_trader_routes
from routes_system import create_system_routes
from routes_market import create_market_routes
from routes_position import create_position_routes
from routes_news import create_news_routes


def create_app():
    """创建并配置 FastAPI 应用"""

    # 初始化服务
    server = TradingServer()

    # 创建 FastAPI 应用
    app = FastAPI(
        title="高频交易服务 (HFT Trading Service)",
        description="""
连接 MT5 EA 和交易指令源的高性能交易中心

## 功能模块

### 交易指令
- EA获取交易指令
- 交易员下发交易指令
- 查询待执行指令

### 行情分析
- K线数据接收与存储 (H4/H1/M15/M5/M1)
- 转折点自动检测
- 实时转折点提醒 (WebSocket)

### 系统监控
- 健康检查
- 服务状态查询
        """,
        version="2.0.0"
    )

    # 添加 CORS 中间件
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # 注册路由
    app.include_router(create_ea_routes(server))
    app.include_router(create_trader_routes(server))
    app.include_router(create_system_routes(server))
    app.include_router(create_market_routes(
        server.market_store,
        server.pivot_detector,
        server.pivot_monitor,
        server.trend_analyzer,
        server.pending_orders,
        server.llm_analyzer
    ))
    app.include_router(create_position_routes())
    app.include_router(create_news_routes())

    # 启动时设置事件循环
    @app.on_event("startup")
    async def startup_event():
        loop = asyncio.get_running_loop()
        server.llm_analyzer.set_event_loop(loop)
        server.pivot_monitor.set_event_loop(loop)

        # 设置系统日志的事件循环
        from market.system_log import get_system_log
        system_log = get_system_log()
        system_log.set_event_loop(loop)

        # 记录系统启动日志
        system_log.add_log("system_startup", message="服务已启动")

        # 启动新闻监控后台任务
        from market.news_monitor import get_news_monitor
        news_monitor = get_news_monitor()
        news_monitor.set_event_loop(loop)
        asyncio.create_task(news_monitor.run())

        print("[Startup] 事件循环已设置")
        print("[Startup] 新闻监控已启动")

    return app


app = create_app()


def main():
    """启动服务"""
    print("=" * 60)
    print("高频交易服务启动中...")
    print("=" * 60)
    print()
    
    # 启动参数
    host = "0.0.0.0"
    port = 8000
    workers = 1  # FastAPI + uvloop 场景下通常只需要单个 worker
    
    print(f"[启动信息] 服务地址: http://{host}:{port}")
    print(f"[启动信息] Worker 数量: {workers}")
    print(f"[启动信息] 事件循环: uvloop")
    print(f"[启动信息] API 文档: http://localhost:{port}/docs")
    print()
    print("=" * 60)
    
    try:
        uvicorn.run(
            "main:app",
            host=host,
            port=port,
            workers=workers,
            # 启用 uvloop
            loop="uvloop",
            # 日志配置
            log_level="info",
            access_log=True,
        )
    except KeyboardInterrupt:
        print("\n[信息] 服务已停止")
    except Exception as e:
        print(f"\n[错误] 启动失败: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()

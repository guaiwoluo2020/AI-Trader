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

from auth import get_auth_manager
from routes_ea import create_ea_routes
from routes_auth import create_auth_routes
from routes_trader import create_trader_routes
from routes_system import create_system_routes
from routes_market import create_market_routes
from routes_position import create_position_routes
from routes_news import create_news_routes
from routes_backtest_data import create_backtest_data_routes
from routes_backtest_tasks import create_backtest_task_routes
from routes_accounts import create_account_routes
from backtest_engine import BacktestWorker
from trading_engine_manager import TradingEngineManager


def create_app():
    """创建并配置 FastAPI 应用"""

    # 初始化多账户交易引擎
    get_auth_manager()
    engine_manager = TradingEngineManager()
    backtest_worker = BacktestWorker()

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
        expose_headers=[
            "Content-Disposition",
            "X-EA-Filename",
            "X-EA-Activation-Expires-At",
        ],
    )

    # 注册路由
    app.include_router(create_auth_routes(engine_manager))
    app.state.engine_manager = engine_manager
    app.include_router(create_ea_routes(engine_manager))
    app.include_router(create_trader_routes(engine_manager))
    app.include_router(create_system_routes(engine_manager))
    app.include_router(create_market_routes(engine_manager))
    app.include_router(create_position_routes(engine_manager=engine_manager))
    app.include_router(create_news_routes())
    app.include_router(create_backtest_data_routes(engine_manager))
    app.include_router(create_backtest_task_routes())
    app.include_router(create_account_routes(engine_manager))

    # 启动时设置事件循环
    @app.on_event("startup")
    async def startup_event():
        loop = asyncio.get_running_loop()
        engine_manager.set_event_loop(loop)

        # 设置系统日志的事件循环
        from market.system_log import get_system_log
        system_log = get_system_log()
        system_log.set_event_loop(loop)

        # 记录系统启动日志
        system_log.add_log("system_startup", message="服务已启动")

        # 启动市场事件监控后台任务
        from market.market_event_monitor import get_market_event_monitor
        monitor = get_market_event_monitor()
        monitor.set_event_loop(loop)
        app.state.market_monitor = monitor
        app.state.market_monitor_task = asyncio.create_task(monitor.run())
        app.state.backtest_worker = backtest_worker
        backtest_worker.start()

        print("[Startup] 事件循环已设置")
        print("[Startup] 市场事件监控已启动")
        print("[Startup] 回测任务 Worker 已启动")

    @app.on_event("shutdown")
    async def shutdown_event():
        monitor = getattr(app.state, "market_monitor", None)
        monitor_task = getattr(app.state, "market_monitor_task", None)

        if monitor_task is not None:
            monitor_task.cancel()
            await asyncio.gather(monitor_task, return_exceptions=True)
        if monitor is not None:
            await monitor.stop()

        backtest_worker.stop()
        engine_manager.close_all()

        from market.system_log import get_system_log
        get_system_log().add_log("system_shutdown", message="服务已停止")
        print("[Shutdown] 后台任务与交易引擎已关闭")

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

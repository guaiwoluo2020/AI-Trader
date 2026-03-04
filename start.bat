@echo off
REM Windows 启动脚本
REM 用于快速启动交易服务

setlocal enabledelayedexpansion

echo ==================================================
echo 交易服务启动脚本
echo ==================================================

REM 检查Python版本
python --version >nul 2>&1
if errorlevel 1 (
    echo ❌ 错误: 未找到Python
    echo 请先安装Python 3.7以上版本
    pause
    exit /b 1
)

for /f "tokens=2" %%i in ('python --version 2^>^&1') do set PYTHON_VERSION=%%i
echo ✓ Python版本: %PYTHON_VERSION%

REM 获取脚本目录
cd /d "%~dp0"
echo ✓ 工作目录: %cd%

REM 检查虚拟环境
if exist "venv" (
    echo ✓ 虚拟环境已存在
    call venv\Scripts\activate.bat
) else (
    echo → 创建虚拟环境...
    python -m venv venv
    call venv\Scripts\activate.bat
    echo ✓ 虚拟环境已创建
)

REM 检查依赖
echo → 检查依赖...
if exist "requirements.txt" (
    pip install -r requirements.txt -q
    echo ✓ 依赖已安装
) else (
    echo ⚠️  未找到 requirements.txt
    echo → 安装基础依赖...
    pip install fastapi uvicorn uvloop pydantic requests -q
    echo ✓ 基础依赖已安装
)

REM 显示启动信息
echo.
echo ==================================================
echo 启动参数:
echo   主机: 0.0.0.0
echo   端口: 8000
echo   Worker数: 1
echo   事件循环: uvloop
echo ==================================================
echo.
echo ✓ 服务已启动！
echo.
echo 访问地址：
echo   服务: http://localhost:8000
echo   API文档: http://localhost:8000/docs
echo   交互式测试: python test_trading_service.py
echo   交易工具: python trade_client.py
echo.
echo 按 Ctrl+C 停止服务
echo ==================================================
echo.

REM 启动服务
python main.py

pause

@echo off
REM 前端启动脚本 (Windows)
REM 用于快速启动React前端

setlocal enabledelayedexpansion

echo ==================================================
echo 量化交易服务 - 前端启动脚本
echo ==================================================

REM 检查Node.js版本
node --version >nul 2>&1
if errorlevel 1 (
    echo ❌ 错误: 未找到Node.js
    echo 请先安装Node.js 16+版本
    echo 推荐使用: https://nodejs.org/
    pause
    exit /b 1
)

for /f "tokens=*" %%i in ('node --version') do set NODE_VERSION=%%i
echo ✓ Node.js版本: %NODE_VERSION%

REM 检查npm
npm --version >nul 2>&1
if errorlevel 1 (
    echo ❌ 错误: 未找到npm
    pause
    exit /b 1
)

for /f "tokens=*" %%i in ('npm --version') do set NPM_VERSION=%%i
echo ✓ npm版本: %NPM_VERSION%

REM 获取脚本目录
cd /d "%~dp0"
cd frontend
echo ✓ 工作目录: %cd%

REM 检查依赖
echo → 检查依赖...
if not exist "node_modules" (
    echo → 安装依赖...
    npm install
    echo ✓ 依赖已安装
) else (
    echo ✓ 依赖已存在
)

REM 显示启动信息
echo.
echo ==================================================
echo 启动参数:
echo   开发服务器: http://localhost:3000
echo   代理后端: http://localhost:8000
echo   热重载: 启用
echo ==================================================
echo.
echo ✓ 前端服务已启动！
echo.
echo 访问地址：
echo   前端界面: http://localhost:3000
echo   API文档: http://localhost:8000/docs
echo.
echo 注意：请确保后端服务 (python main.py) 已在运行
echo ==================================================
echo.

REM 启动开发服务器
npm start

pause
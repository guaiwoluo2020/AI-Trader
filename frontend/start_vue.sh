#!/bin/bash

# Vue 前端启动脚本

echo "🚀 启动量化交易系统 - Vue 前端"
echo "================================="

# 检查是否在正确的目录
if [ ! -f "package.json" ]; then
    echo "❌ 错误: 请在 frontend 目录下运行此脚本"
    echo "   cd frontend && ./start_vue.sh"
    exit 1
fi

# 检查依赖是否已安装
if [ ! -d "node_modules" ]; then
    echo "📦 安装依赖..."
    npm install
    if [ $? -ne 0 ]; then
        echo "❌ 依赖安装失败"
        exit 1
    fi
fi

echo "🌐 启动 Vue 开发服务器..."
echo "   前端地址: http://localhost:3001"
echo "   后端地址: http://localhost:8000"
echo ""
echo "按 Ctrl+C 停止服务器"
echo ""

# 启动开发服务器
npx vite --host 0.0.0.0 --port 3001
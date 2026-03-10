#!/bin/bash
# 前端启动脚本
# 用于快速启动React前端

set -e

echo "=================================================="
echo "量化交易服务 - 前端启动脚本"
echo "=================================================="

# 检查Node.js版本
if ! command -v node &> /dev/null; then
    echo "❌ 错误: 未找到Node.js"
    echo "请先安装Node.js 16+版本"
    echo "推荐使用: https://nodejs.org/"
    exit 1
fi

NODE_VERSION=$(node --version | sed 's/v//')
echo "✓ Node.js版本: $NODE_VERSION"

# 检查npm
if ! command -v npm &> /dev/null; then
    echo "❌ 错误: 未找到npm"
    exit 1
fi

NPM_VERSION=$(npm --version)
echo "✓ npm版本: $NPM_VERSION"

# 获取脚本目录
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
cd "$SCRIPT_DIR/frontend"

echo "✓ 工作目录: $(pwd)"

# 检查依赖
echo "→ 检查依赖..."
if [ ! -d "node_modules" ]; then
    echo "→ 安装依赖..."
    npm install
    echo "✓ 依赖已安装"
else
    echo "✓ 依赖已存在"
fi

# 显示启动信息
echo ""
echo "=================================================="
echo "启动参数:"
echo "  开发服务器: http://localhost:3000"
echo "  代理后端: http://localhost:8000"
echo "  热重载: 启用"
echo "=================================================="
echo ""
echo "✓ 前端服务已启动！"
echo ""
echo "访问地址："
echo "  前端界面: http://localhost:3000"
echo "  API文档: http://localhost:8000/docs"
echo ""
echo "注意：请确保后端服务 (python main.py) 已在运行"
echo "=================================================="
echo ""

# 启动开发服务器
npm run dev
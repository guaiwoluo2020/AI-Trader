#!/bin/bash
# macOS/Linux 启动脚本
# 用于快速启动交易服务

set -e

echo "=================================================="
echo "交易服务启动脚本"
echo "=================================================="

# 检查Python版本
if ! command -v python3 &> /dev/null; then
    echo "❌ 错误: 未找到Python3"
    echo "请先安装Python 3.7以上版本"
    exit 1
fi

PYTHON_VERSION=$(python3 --version 2>&1 | awk '{print $2}')
echo "✓ Python版本: $PYTHON_VERSION"

# 获取脚本目录
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
cd "$SCRIPT_DIR"

echo "✓ 工作目录: $(pwd)"

# 检查虚拟环境
if [ -d "venv" ]; then
    echo "✓ 虚拟环境已存在"
    source venv/bin/activate
else
    echo "→ 创建虚拟环境..."
    python3 -m venv venv
    source venv/bin/activate
    echo "✓ 虚拟环境已创建"
fi

# 检查依赖
echo "→ 检查依赖..."
if [ -f "requirements.txt" ]; then
    pip install -r requirements.txt -q
    echo "✓ 依赖已安装"
else
    echo "⚠️  未找到 requirements.txt"
    echo "→ 安装基础依赖..."
    pip install fastapi uvicorn uvloop pydantic requests -q
    echo "✓ 基础依赖已安装"
fi

# 显示端口信息
echo ""
echo "=================================================="
echo "启动参数:"
echo "  主机: 0.0.0.0"
echo "  端口: 8000"
echo "  Worker数: 1"
echo "  事件循环: uvloop"
echo "=================================================="
echo ""
echo "✓ 服务已启动！"
echo ""
echo "访问地址："
echo "  服务: http://localhost:8000"
echo "  API文档: http://localhost:8000/docs"
echo "  交互式测试: python test_trading_service.py"
echo "  交易工具: python trade_client.py"
echo ""
echo "按 Ctrl+C 停止服务"
echo "=================================================="
echo ""

# 启动服务
python3 main.py

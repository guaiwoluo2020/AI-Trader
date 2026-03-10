#!/bin/bash
# 日志监控脚本 - 实时查看EA和后端服务的通信

echo "============================================================"
echo "  EA与后端服务通信监控"
echo "============================================================"
echo ""
echo "监控内容:"
echo "  - EA获取交易指令 (GET /get_trades)"
echo "  - EA发送统计数据 (POST /send_statistics)"
echo "  - 前端界面请求"
echo ""
echo "按 Ctrl+C 停止监控"
echo "============================================================"
echo ""

# 实时监控后端日志
tail -f /private/tmp/claude-501/-Users-wangxingxing--openclaw-workspace-lianghua/tasks/bpae1rf93.output | \
while read line; do
    # 高亮EA请求
    if echo "$line" | grep -q "GET /get_trades"; then
        echo "🔴 [EA请求交易指令] $line"
    elif echo "$line" | grep -q "POST /send_statistics"; then
        echo "🟢 [EA发送统计数据] $line"
    elif echo "$line" | grep -q "send_trade_instructions"; then
        echo "🔵 [交易员下发指令] $line"
    else
        echo "$line"
    fi
done
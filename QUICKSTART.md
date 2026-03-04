# 快速开始指南

## 1. 安装依赖

```bash
pip install fastapi uvicorn uvloop pydantic requests
```

## 2. 启动服务

```bash
# 在workspace目录下运行
python trading_server.py
```

输出：
```
============================================================
启动行情分析交易服务
============================================================
[INFO] 服务将运行在 http://localhost:5858
[INFO] API文档: http://localhost:5858/docs
[INFO] 备用文档: http://localhost:5858/redoc
============================================================
```

## 3. 验证服务

### 方式 A: 运行自动化测试
```bash
python test_trading_service.py
```

### 方式 B: 手动测试 (curl)
```bash
# 健康检查
curl http://localhost:5858/health

# 查看服务状态
curl http://localhost:5858/status
```

### 方式 C: 访问API文档
打开浏览器访问: [http://localhost:5858/docs](http://localhost:5858/docs)

## 4. 使用交易工具

### 方式 A: 交互式命令行工具
```bash
python trade_client.py
```

菜单示例：
```
======================================================
交易指令发送工具
======================================================
✓ 服务已连接

请选择操作:
1. 发送买入订单
2. 发送卖出订单
3. 查看待执行指令
4. 查看统计数据
5. 清空指令
0. 退出

请输入选项 (0-5): 
```

### 方式 B: 代码调用
```python
from trade_client import TradeInstructionClient

client = TradeInstructionClient()

# 发送买入订单
result = client.send_buy_order("gold", 0.01, 5000, 5100)
print(result)

# 发送卖出订单
result = client.send_sell_order("eurusd", 0.02, 1.0950, 1.0850)

# 查看待执行指令
trades = client.get_pending_trades()

# 查看统计数据
stats = client.get_statistics(count=5)
```

### 方式 C: HTTP请求

> **说明**: 对于 `POST /send_trade_instructions` 接口，若指令中未提供 `tp` 或 `tp<=0`，服务端会自动设置为 `0.005`；`sl`缺失则保留为`0.0`。

```bash
# 发送买入订单
curl -X POST "http://localhost:5858/send_trade_instructions" \
     -H "Content-Type: application/json" \
     -d '[
       {
         "symbol": "gold",
         "action": "b",
         "mount": 0.01,
         "sl": 5000,
         "tp": 5100
       }
     ]'

# 查询待执行指令
curl "http://localhost:5858/query_pending_trades"

# 查询统计数据
curl "http://localhost:5858/query_statistics?count=5"
```

## 5. MT5 EA集成

MT5 EA已经配置好，只需确保：

1. **MT5已启用WebRequest**
   - 在MT5中：工具 -> 选项 -> EA交易
   - 勾选"WebRequest用于脚本...", 并添加 `localhost:5858` 到允许列表

2. **启动EA**
   - 在黄金(GOLD)品种的图表上加载 `wangxxGold.mq5`
   - 确保EA显示为 ✓ 启用

3. **工作流程**
   ```
   交易员发送指令 → Python服务 → EA获取指令 → 执行交易
   
   EA每分钟→ 统计数据 → Python服务 → 交易员查询统计
   ```

## 6. 常见操作

### 发送一个黄金买入单

**命令行**:
```bash
# 交互式
python trade_client.py
# 选择 1，然后输入参数

# 或直接 Python
python -c "
from trade_client import TradeInstructionClient
client = TradeInstructionClient()
result = client.send_buy_order('gold', 0.01, 5000, 5100)
print(result)
"
```

**HTTP**:
```bash
curl -X POST "http://localhost:5858/send_trade_instructions" \
     -H "Content-Type: application/json" \
     -d '[{"symbol":"gold","action":"b","mount":0.01,"sl":5000,"tp":5100}]'
```

### 查看待执行指令

```bash
python trade_client.py
# 选择 3
```

或：
```bash
curl "http://localhost:5858/query_pending_trades"
```

### 查看统计数据

```bash
python trade_client.py
# 选择 4
```

或：
```bash
curl "http://localhost:5858/query_statistics?count=10"
```

### 清空指令

```bash
# 清空所有
curl -X DELETE "http://localhost:5858/clear_trades"

# 只清空黄金指令
curl -X DELETE "http://localhost:5858/clear_trades?symbol=gold"
```

## 7. 实时监控

### 查看服务状态
```bash
watch -n 1 'curl -s http://localhost:5858/status | python -m json.tool'
```

输出示例：
```json
{
    "status": "running",
    "pending_trades": {
        "GOLD": 2,
        "EURUSD": 1
    },
    "total_pending": 3,
    "statistics_records": 8,
    "timestamp": "2026-03-04T14:35:22.123456"
}
```

## 8. 故障排查

### 问题 1: 连接被拒绝
**症状**: `Connection refused on localhost:5858`

**解决**:
```bash
# 检查服务是否运行
ps aux | grep trading_server.py

# 检查端口占用
lsof -i :5858

# 重启服务
pkill -f trading_server
python trading_server.py
```

### 问题 2: 指令没有执行
**检查步骤**:
1. 确认指令已发送: `curl http://localhost:5858/query_pending_trades`
2. 检查EA日志: MT5 → 日志标签
3. 确认EA已启用: 图表角上有绿色 ✓ 标记
4. 检查WebRequest权限已启用

### 问题 3: 统计数据为空
**原因**: EA还没有发送统计数据

**验证**:
1. 等待至少1分钟（EA每分钟发送一次）
2. 检查EA是否在运行
3. 查看EA的日志输出

## 9. 扩展集成

### 与外部系统集成

```python
# 与量化分析系统集成
from trade_client import TradeInstructionClient

def your_analysis_function():
    # 您的分析逻辑
    buy_signal_gold = True
    
    if buy_signal_gold:
        client = TradeInstructionClient()
        client.send_buy_order("gold", 0.01, 5000, 5100)
```

### 定时任务

```python
import schedule
import time
from trade_client import TradeInstructionClient

client = TradeInstructionClient()

def check_and_trade():
    # 定期检查并下单
    stats = client.get_statistics(1)
    # 您的逻辑...
    
schedule.every(5).minutes.do(check_and_trade)

while True:
    schedule.run_pending()
    time.sleep(1)
```

## 10. 下一步

- 详细API文档: [README.md](README.md)
- 自动化测试: `python test_trading_service.py`
- API交互式文档: http://localhost:5858/docs
- 性能监控: `curl http://localhost:5858/status`

## 11. 后续优化

虽然当前HTTP性能足够，但可考虑：

1. **增加日志持久化**
   - 添加SQLite保存历史数据
   - 支持离线分析

2. **添加WebSocket支持**
   - 实时推送行情变化
   - 实时订单状态更新

3. **前端管理界面**
   - Web Dashboard
   - 实时图表展示
   - 一键下单功能

## 需要帮助？

查看完整文档: [README.md](README.md)

祝您交易愉快！🚀

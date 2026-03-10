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

## 4. 启动前端界面 (Vue)

### 方式 A: 使用启动脚本 (推荐)
```bash
cd frontend
./start_vue.sh
```

### 方式 B: 手动启动
```bash
cd frontend
npm install  # 首次运行需要
npm run dev
```

### 方式 C: 直接运行
```bash
cd frontend
npx vite --host 0.0.0.0 --port 3001
```

**前端访问地址**: http://localhost:3001

### 前端功能
- 📊 **仪表板**: 实时服务状态和关键指标
- 📋 **交易指令**: 发送和管理交易指令 (带智能价格验证)
- 📈 **统计数据**: 数据可视化和历史分析
- ❤️ **服务状态**: 实时监控和健康检查

## 5. 使用交易工具

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
> 
> **价格验证规则**:
> - 买入指令必须满足 `sl < price < tp`。
> - 卖出指令必须满足 `tp < price < sl`。
> 若同时指定了 sl 与 tp 但不满足条件，该条指令会被拒绝。

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

## 6. MT5 EA集成

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

## 7. 常见操作

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

## 11. Vue 前端详细说明

### 🎯 技术栈
- **Vue 3** + Composition API
- **Vuetify 3** (Material Design)
- **Vue Router 4**
- **Axios** (HTTP 客户端)
- **ECharts** (数据可视化)
- **Vite** (构建工具)

### 🌟 前端特性
- ✅ **现代化UI**: Material Design 设计规范
- ✅ **响应式布局**: 支持桌面和移动设备
- ✅ **实时更新**: 自动刷新数据和状态
- ✅ **智能验证**: 交易指令价格自动验证
- ✅ **数据可视化**: ECharts 图表展示
- ✅ **中文界面**: 完全本地化

### 🎨 界面功能

#### 仪表板 (Dashboard)
- 📊 服务状态指示器
- 📈 关键指标卡片 (待执行指令、统计记录、活跃品种)
- 🔄 自动数据刷新 (30秒间隔)

#### 交易指令 (Trade Orders)
- ➕ 交易指令表单 (品种、方向、手数、价格、止损/止盈)
- ✅ **智能价格验证**:
  - 买入: `止损 < 执行价格 < 止盈`
  - 卖出: `止盈 < 执行价格 < 止损`
- 📋 待执行指令表格
- 🗑️ 一键清空所有指令

#### 统计数据 (Statistics)
- 📊 历史数据表格 (时间、品种、价格、类型)
- 📈 价格趋势线图 (ECharts)
- 📋 汇总统计 (总记录数、平均价格、最高/最低价)

#### 服务状态 (Status)
- ❤️ 健康检查状态
- 📊 系统指标 (运行时间、内存使用等)
- 🔄 连接状态监控 (后端、MT5)
- ⏰ 实时更新 (5秒间隔)

### 🚀 快速体验

```bash
# 启动完整系统
# 终端1: 启动后端
python main.py

# 终端2: 启动前端
cd frontend && ./start_vue.sh

# 访问前端: http://localhost:3001
```

### 🔧 开发和部署

#### 开发环境
```bash
cd frontend
npm install
npm run dev  # 开发服务器
```

#### 生产构建
```bash
cd frontend
npm run build   # 构建生产版本
npm run preview # 预览构建结果
```

#### 项目结构
```
frontend/
├── src/
│   ├── views/          # 页面组件
│   ├── api/            # API 接口
│   ├── router/         # 路由配置
│   └── plugins/        # Vuetify 配置
├── public/             # 静态资源
└── README.md           # 详细文档
```

## 12. 下一步

- 详细API文档: [README.md](README.md)
- 自动化测试: `python test_trading_service.py`
- API交互式文档: http://localhost:5858/docs
- 性能监控: `curl http://localhost:5858/status`

## 13. 后续优化

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

查看完整文档:
- [主项目文档](README.md)
- [Vue 前端文档](frontend/README.md)
- API交互式文档: http://localhost:8000/docs
- 前端界面: http://localhost:3001

## 🎉 祝您使用愉快！

现在您拥有了完整的量化交易系统：
- ⚡ 高性能 Python 后端
- 🎨 现代化 Vue 前端界面
- 🤖 智能 MT5 EA 集成

**开始您的量化交易之旅吧！** 🚀

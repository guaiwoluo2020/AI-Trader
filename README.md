# 高频交易服务 (HFT Trading Service)

一个为MT5 EA提供支持的高性能交易服务，采用FastAPI框架，集成行情分析、信号生成、策略决策、风险管理等功能。

## 目录

- [功能特性](#功能特性)
- [系统架构](#系统架构)
- [快速开始](#快速开始)
- [配置说明](#配置说明)
- [API文档](#api文档)
- [前端界面](#前端界面)
- [开发指南](#开发指南)

---

## 功能特性

### 核心功能

#### 1. K线数据管理
- 支持多周期K线数据接收 (H4/H1/M15/M5/M1)
- 增量/全量数据校验与存储
- 数据时效性检查（自动识别休市状态）

#### 2. 转折点检测
- 基于分型识别算法自动检测高低点
- 多周期转折点联动分析
- 接近阈值提醒（可配置各周期阈值）

#### 3. 信号系统
- **Pivot信号**: 转折点触发信号（支持M1/M5/M15/H1/H4周期）
- **KeyLevel信号**: 关键点位触发信号
- **AI Entry信号**: LLM分析入场信号
- 支持周期级别启用/禁用和权重配置

#### 4. 策略决策
- 多信号综合分析
- 周期级别权重配置
- 一致性要求设置（任一/多数/全部）
- 自动止损止盈计算
- 风险回报比验证

#### 5. 风险管理
- 账户风险百分比控制
- 最大持仓数量限制
- 同向持仓限制
- 动态止损范围验证

#### 6. LLM分析
- OpenAI兼容API支持
- 多周期趋势分析
- 关键支撑/阻力位识别
- 交易建议生成

#### 7. 新闻与事件
- 金十数据快讯抓取
- 财经日历事件
- 市场事件监控

---

## 系统架构

```
┌─────────────────────────────────────────────────────────────────┐
│                        前端 (Vue.js + Vuetify)                   │
│  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌────────┐ │
│  │  Market  │ │Settings  │ │Positions │ │  News    │ │  Log   │ │
│  └────┬─────┘ └────┬─────┘ └────┬─────┘ └────┬─────┘ └───┬────┘ │
└───────┼────────────┼────────────┼────────────┼────────────┼──────┘
        │            │            │            │            │
        └────────────┴────────────┴─────┬──────┴────────────┘
                                       │
                              ┌────────▼────────┐
                              │   FastAPI 服务   │
                              │   (Port 8000)   │
                              └────────┬────────┘
                                       │
        ┌──────────────────────────────┼──────────────────────────────┐
        │                              │                              │
        ▼                              ▼                              ▼
┌───────────────┐            ┌───────────────┐            ┌───────────────┐
│   行情模块     │            │   信号/策略    │            │   新闻模块     │
│               │            │               │            │               │
│ • KlineStore  │            │ • SignalService│            │ • FlashNews   │
│ • KlineService│            │ • StrategySvc │            │ • Calendar    │
│ • PivotService│            │ • RiskManager │            │ • NewsCrawler │
│ • TechService │            │ • PendingOrder│            │               │
└───────┬───────┘            └───────┬───────┘            └───────────────┘
        │                            │
        │                    ┌───────┴───────┐
        │                    │               │
        ▼                    ▼               ▼
┌───────────────┐    ┌───────────────┐ ┌───────────────┐
│   LLM分析     │    │   信号生成器   │ │   存储层       │
│               │    │               │ │               │
│ • LLMService  │    │ • PivotSignal │ │ • JSON文件    │
│ • LLMAnalyzer │    │ • KeyLevel    │ │ • 内存缓存    │
│               │    │ • AI Entry    │ │               │
└───────────────┘    └───────────────┘ └───────────────┘
        │
        ▼
┌───────────────┐
│   MT5 EA      │
│               │
│ • K线推送     │
│ • 统计上报    │
│ • 获取指令    │
│ • 执行交易    │
└───────────────┘
```

### 模块结构

```
.
├── main.py                 # 服务入口
├── server.py               # TradingServer 核心类
├── routes_*.py             # API路由模块
│   ├── routes_ea.py        # EA接口
│   ├── routes_market.py    # 行情/策略接口
│   ├── routes_position.py  # 持仓接口
│   ├── routes_news.py      # 新闻接口
│   ├── routes_trader.py    # 交易员接口
│   └── routes_system.py    # 系统接口
├── market/                 # 核心模块
│   ├── models/             # 数据模型
│   │   ├── kline.py        # K线模型
│   │   ├── pivot.py        # 转折点模型
│   │   ├── trading_signal.py    # 信号模型
│   │   ├── trading_strategy.py  # 策略模型
│   │   ├── pending_order.py     # 待确认订单
│   │   └── ...
│   ├── services/           # 业务服务
│   │   ├── kline_service.py     # K线服务
│   │   ├── pivot_service.py     # 转折点服务
│   │   ├── signal/              # 信号生成器
│   │   │   ├── signal_service.py
│   │   │   ├── pivot_signal.py
│   │   │   ├── key_level_signal.py
│   │   │   └── ai_entry_signal.py
│   │   ├── strategy/            # 策略服务
│   │   │   ├── strategy_service.py
│   │   │   └── risk_manager.py
│   │   └── llm_service.py       # LLM分析服务
│   ├── store/              # 数据存储
│   │   ├── kline_store.py
│   │   ├── pivot_store.py
│   │   ├── signal_store.py
│   │   ├── strategy_store.py
│   │   └── ...
│   ├── llm_analyzer.py     # LLM分析调度器
│   ├── market_event_monitor.py # 市场事件监控
│   ├── trade_config.py     # 交易配置
│   └── system_log.py       # 系统日志
├── data/                   # 数据文件目录
│   ├── strategy_config.json
│   ├── trade_config.json
│   └── ...
└── frontend/               # Vue.js前端
    └── src/
        ├── views/          # 页面组件
        │   ├── Market.vue      # 行情分析
        │   ├── Settings.vue    # 系统设置
        │   ├── Positions.vue   # 持仓管理
        │   ├── News.vue        # 新闻资讯
        │   └── SystemLog.vue   # 系统日志
        └── api/             # API封装
            └── market.js
```

---

## 快速开始

### 环境要求

- Python 3.9+
- Node.js 18+ (前端开发)

### 安装依赖

```bash
# Python依赖
pip install fastapi uvicorn uvloop pydantic requests aiohttp openai

# 前端依赖
cd frontend
npm install
```

### 启动服务

```bash
# 启动后端服务 (端口8000)
python3 main.py

# 启动前端开发服务 (端口5173)
cd frontend
npm run dev

# 构建前端生产版本
npm run build
```

### 验证服务

```bash
# 健康检查
curl http://localhost:8000/health

# 查看API文档
open http://localhost:8000/docs
```

---

## 配置说明

### 交易配置 (data/trade_config.json)

```json
{
  "enabled": true,
  "default_volume": 0.01,
  "default_sl_offset": 0.05,
  "mt5_timezone_offset": -6.0,
  "symbol_config": {
    "GOLD#": {
      "volume": 0.01,
      "sl_offset": 3,
      "key_levels": "5000,5100,5200",
      "key_level_threshold": 0.0008
    }
  }
}
```

### 策略配置 (data/strategy_config.json)

```json
{
  "strategies": {
    "GOLD#": {
      "enabled": true,
      "signal_config": {
        "pivot": {
          "enabled": true,
          "periods": {
            "M1": {"enabled": true, "weight": 15},
            "M5": {"enabled": true, "weight": 20},
            "M15": {"enabled": false, "weight": 25},
            "H1": {"enabled": false, "weight": 20},
            "H4": {"enabled": false, "weight": 20}
          }
        },
        "key_level": {
          "enabled": true,
          "weight": 40
        },
        "ai_entry": {
          "enabled": true,
          "periods": {
            "M5": {"enabled": true, "weight": 20},
            "M15": {"enabled": true, "weight": 30},
            "H1": {"enabled": true, "weight": 25}
          }
        }
      },
      "min_confidence": 50,
      "consistency_requirement": "majority",
      "min_risk_reward": 1.0,
      "max_positions": 3,
      "max_same_direction": 2
    }
  }
}
```

### LLM配置

通过API或前端设置：

```bash
curl -X POST http://localhost:8000/llm/configure \
  -H "Content-Type: application/json" \
  -d '{
    "api_key": "your-api-key",
    "api_base": "https://api.openai.com/v1",
    "model": "gpt-4o-mini"
  }'
```

---

## API文档

### EA接口

#### 获取交易指令
```
GET /get_trades?symbol=GOLD#&price=4850.00
```

返回该品种待执行的交易指令列表。

#### 推送K线数据
```
POST /ea/kline/{period}
POST /ea/kline_batch
```

**请求体示例**:
```json
{
  "symbol": "GOLD#",
  "is_full": false,
  "klines": [
    {
      "time": "2026-03-19 10:00:00",
      "open": 4850.00,
      "high": 4855.00,
      "low": 4848.00,
      "close": 4852.00,
      "volume": 1000
    }
  ]
}
```

**返回码说明**:
- `200`: 成功
- `400 (code: 8888)`: 需要全量数据（数据不连续或未初始化）

#### 发送统计数据
```
POST /send_statistics
```

### 行情接口

#### 查询K线数据
```
GET /market/kline/{symbol}?period=M5&count=100
```

#### 查询转折点
```
GET /market/pivots/{symbol}?period=M5&direction=high&count=50
```

#### 获取行情状态
```
GET /market/status
GET /market/configured_symbols
```

### 策略接口

#### 获取策略配置
```
GET /strategy
GET /strategy/{symbol}
```

#### 更新策略配置
```
POST /strategy/{symbol}
```

**请求体示例**:
```json
{
  "enabled": true,
  "signal_config": {
    "pivot": {
      "enabled": true,
      "periods": {
        "M5": {"enabled": true, "weight": 25}
      }
    }
  },
  "min_confidence": 60
}
```

#### 获取决策历史
```
GET /strategy/decisions?symbol=GOLD#&count=20
```

#### 手动触发决策
```
POST /strategy/trigger/{symbol}
```

### 持仓接口

```
GET /positions
GET /positions/summary
POST /close_position
```

### 新闻接口

```
GET /api/news/flash?count=10
GET /api/news/calendar?date=2026-03-19
```

### WebSocket接口

#### 转折点提醒
```
ws://localhost:8000/ws/market
```

#### 新闻推送
```
ws://localhost:8000/api/news/ws
```

---

## 前端界面

### 行情分析 (Market)
- 多周期K线展示
- 转折点标注
- AI趋势分析
- 交易决策提醒
- 待确认订单操作

### 系统设置 (Settings)
- 自动交易开关
- 品种配置管理
- 策略配置（信号权重、仓位管理、止损止盈）
- LLM配置
- 品种数据状态

### 持仓管理 (Positions)
- 实时持仓列表
- 盈亏统计
- 一键平仓

### 新闻资讯 (News)
- 快讯列表
- 财经日历
- 事件提醒

### 系统日志 (SystemLog)
- 操作日志
- 事件筛选
- 日志清空

---

## 开发指南

### 信号生成器扩展

在 `market/services/signal/` 目录下创建新的信号生成器：

```python
from market.models import TradingSignal, SignalSource

class MySignalGenerator:
    def __call__(self, symbol: str, current_price: float) -> List[TradingSignal]:
        # 实现信号生成逻辑
        signals = []
        # ...
        return signals
```

注册到 SignalService：

```python
signal_service.register_generator("my_signal", MySignalGenerator())
```

### 策略参数说明

| 参数 | 类型 | 说明 |
|------|------|------|
| enabled | bool | 是否启用策略 |
| signal_config | dict | 信号源周期级别配置 |
| min_confidence | int | 最低置信度阈值 (0-100) |
| consistency_requirement | str | 一致性要求 (any/majority/all) |
| min_risk_reward | float | 最小风险回报比 |
| max_positions | int | 最大持仓数量 |
| max_same_direction | int | 同向最大持仓 |
| fixed_volume | float | 固定手数 |
| risk_percent | float | 账户风险百分比 |

### 数据存储

所有配置和数据存储在 `data/` 目录：
- `strategy_config.json` - 策略配置
- `trade_config.json` - 交易配置
- `kline_*.json` - K线缓存
- `pivot_*.json` - 转折点缓存
- `llm_*.json` - LLM分析结果

---

## 性能特性

- **FastAPI框架**: 高性能异步框架
- **UVloop**: 使用高性能事件循环
- **线程安全**: 核心数据使用锁保护
- **内存缓存**: 热数据内存缓存
- **增量更新**: K线支持增量推送

---

## 常见问题

### Q: 为什么某个品种没有AI趋势分析？
A: AI分析只处理有K线数据的活跃品种。检查EA是否正确推送该品种的K线数据。

### Q: 返回码8888是什么意思？
A: 表示服务端需要全量K线数据，通常是因为：
- 首次连接，没有历史数据
- 增量数据不连续，存在缺失

### Q: 如何调试信号生成？
A: 查看服务日志，信号生成器会打印详细信息：
```
[PivotSignalGenerator] 生成信号: buy @ 4850.00, SL=4840.00, TP=4870.00
```

---

## 许可证

MIT License
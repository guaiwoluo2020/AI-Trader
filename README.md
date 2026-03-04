# 高性能行情分析交易服务

## 功能介绍

这是一个为MT5 EA提供支持的高性能交易服务，采用FastAPI框架，支持以下功能：

### 核心功能
1. **EA接口** - MT5 EA与服务通信
   - `GET /get_trades` - EA获取待执行的交易指令（按SYMBOL分类）
   - `POST /send_statistics` - EA发送每分钟的统计数据

2. **交易员接口** - 交易员下发指令和查询数据
   - `POST /send_trade_instructions` - 下发交易指令
   - `GET /query_pending_trades` - 查询所有待执行指令
   - `DELETE /clear_trades` - 清空交易指令
   - `GET /query_statistics` - 查询统计数据（保留最新10条）

3. **系统接口** - 服务监控和健康检查
   - `GET /health` - 健康检查
   - `GET /status` - 服务状态

## 安装和运行

### 环境要求
- Python 3.7+
- 依赖包：
  ```bash
  pip install fastapi uvicorn uvloop pydantic requests
  ```

### 启动服务
```bash
python trading_server.py
```

输出示例：
```
============================================================
启动行情分析交易服务
============================================================
[INFO] 服务将运行在 http://localhost:5858
[INFO] API文档: http://localhost:5858/docs
[INFO] 备用文档: http://localhost:5858/redoc
============================================================
```

### 运行测试
```bash
python test_trading_service.py
```

## API 详细说明

### 1. EA - 获取交易指令

**端点**: `GET /get_trades?symbol=gold&price=2035.50`

**描述**: EA调用此接口获取待执行的交易指令。支持基于当前价格的条件过滤。

**请求参数**:
| 参数 | 类型 | 必需 | 说明 |
|------|------|------|------|
| symbol | string | 是 | 交易品种，如 "gold", "eurusd" |
| price | float | 否 | 当前市场价格，用于执行条件过滤 |

**价格条件过滤逻辑**:
- **买入指令** (`action='b'`)：若指令的执行价格 `price > 当前价格`，则指令被缓存，暂不下发（等待价格跌到指令价格）
- **卖出指令** (`action='s'`)：若指令的执行价格 `price < 当前价格`，则指令被缓存，暂不下发（等待价格涨到指令价格）
- 满足条件的指令会被推送给EA并删除
- 不满足条件的指令保留在内存中，等待下次价格更新时重新评估

**返回值** (JSON数组):
```json
[
    {
        "symbol": "gold",
        "action": "b",           // b=买入, s=卖出
        "mount": 0.01,           // 手数
        "price": 2030.00,        // 指令的执行价格
        "sl": 5000,              // 止损点
        "tp": 5100               // 止盈点
    },
    {
        "symbol": "gold",
        "action": "s",
        "mount": 0.02,
        "price": 2035.00,
        "sl": 2035,
        "tp": 2025
    }
]
```

**流程**:
1. EA每100毫秒调用此接口，携带当前SYMBOL和市场价格
2. 服务基于价格条件过滤该SYMBOL的所有待执行指令
3. 返回满足条件的指令列表
4. 已推送的指令会被删除，未满足条件的指令保留在内存中
5. 如果没有符合条件的指令，返回空数组 `[]`

### 2. EA - 发送统计数据

**端点**: `POST /send_statistics`

**描述**: EA每分钟调用此接口发送统计数据。服务自动保留最新10条数据。

**请求体** (JSON):
```json
{
    "timestamp": "2026-03-04 14:30:00",
    "tickCount": 1234,           // 该分钟内TICK总数
    "bidPrice": 2035.50,         // 买价
    "askPrice": 2035.60,         // 卖价
    "balance": 50000.00,         // 账户余额
    "equity": 51234.56,          // 账户权益
    "marginLevel": 98.50,        // 预付款比例（%）
    "positions": [               // 持仓信息
        {
            "ticket": 123456,
            "volume": 0.01,
            "priceOpen": 2030.00,
            "type": "BUY",
            "profit": 55.60,
            "distanceSL": 30.50,  // 距离止损的点数
            "distanceTP": 35.40   // 距离止盈的点数
        }
    ],
    "trades": [                  // 该分钟的交易记录
        {
            "time": "2026-03-04 14:30:00",
            "action": "BUY",
            "symbol": "GOLD",
            "volume": 0.01,
            "price": 2030.00,
            "sl": 2000,
            "tp": 2100
        }
    ]
}
```

**响应**:
```json
{
    "status": "success",
    "message": "统计数据已记录"
}
```

### 3. 交易员 - 下发交易指令

**端点**: `POST /send_trade_instructions`

**描述**: 交易员通过此接口下发交易指令。指令保存在内存中，等待EA获取。

> **注意**: 如果交易指令中未提供 `tp` 或者 `tp<=0`，
> 服务端会自动将`tp`设为 **0.005**。
> `sl` 缺失保持为 `0.0`（EA端还有后续处理）。

**请求体** (JSON数组):
```json
[
    {
        "symbol": "gold",
        "action": "b",
        "mount": 0.01,
        "price": 2030.00,       // 指令的买入价格（用于价格过滤）
        "sl": 5000,
        "tp": 5100
    },
    {
        "symbol": "eurusd",
        "action": "s",
        "mount": 0.02,
        "price": 1.0900,        // 指令的卖出价格（用于价格过滤）
        "sl": 1.0950,
        "tp": 1.0850
    }
]
```

**响应**:
```json
{
    "status": "success",
    "count": 2,
    "message": "已添加 2 条交易指令"
}
```

**使用示例 (curl)**:
```bash
curl -X POST "http://localhost:5858/send_trade_instructions" \
     -H "Content-Type: application/json" \
     -d '[
       {"symbol":"gold","action":"b","mount":0.01,"sl":5000,"tp":5100},
       {"symbol":"eurusd","action":"s","mount":0.02,"sl":1.0950,"tp":1.0850}
     ]'
```

### 4. 交易员 - 查询待执行指令

**端点**: `GET /query_pending_trades`

**描述**: 查询所有待执行的交易指令（不删除）。

**请求参数**: 无

**响应**:
```json
{
    "status": "success",
    "total": 5,
    "data": {
        "GOLD": [
            {
                "symbol": "gold",
                "action": "b",
                "mount": 0.01,
                "sl": 5000,
                "tp": 5100
            },
            {
                "symbol": "gold",
                "action": "s",
                "mount": 0.02,
                "sl": 2035,
                "tp": 2025
            }
        ],
        "EURUSD": [
            {
                "symbol": "eurusd",
                "action": "s",
                "mount": 0.02,
                "sl": 1.0950,
                "tp": 1.0850
            }
        ]
    }
}
```

### 5. 交易员 - 查询统计数据

**端点**: `GET /query_statistics?count=10`

**描述**: 查询最新的统计数据。服务自动保留最新10条，可指定返回数量。

**请求参数**:
| 参数 | 类型 | 必需 | 默认值 | 说明 |
|------|------|------|--------|------|
| count | int | 否 | 10 | 返回最新N条数据（最多100条） |

**响应**:
```json
{
    "status": "success",
    "count": 3,
    "data": [
        {
            "timestamp": "2026-03-04 14:30",
            "tickCount": 1234,
            "bidPrice": 2035.50,
            "askPrice": 2035.60,
            "balance": 50000.00,
            "equity": 51234.56,
            "marginLevel": 98.50,
            "positions": [...],
            "trades": [...]
        },
        {...},
        {...}
    ]
}
```

### 6. 交易员 - 清空交易指令

**端点**: `DELETE /clear_trades?symbol=gold`

**描述**: 清空指定SYMBOL的待执行指令，或清空所有指令。

**请求参数**:
| 参数 | 类型 | 必需 | 说明 |
|------|------|------|------|
| symbol | string | 否 | 指定品种。不指定则清空所有 |

**响应**:
```json
{
    "status": "success",
    "cleared": 3,
    "message": "已清空 3 条交易指令"
}
```

### 7. 系统 - 健康检查

**端点**: `GET /health`

**描述**: 检查服务是否正常运行。

**响应**:
```json
{
    "status": "healthy",
    "service": "Trading Analysis Server",
    "timestamp": "2026-03-04T14:30:45.123456"
}
```

### 8. 系统 - 服务状态

**端点**: `GET /status`

**描述**: 获取实时的服务状态信息。

**响应**:
```json
{
    "status": "running",
    "pending_trades": {
        "GOLD": 2,
        "EURUSD": 1
    },
    "total_pending": 3,
    "statistics_records": 5,
    "timestamp": "2026-03-04T14:30:45.123456"
}
```

## 工作流程

```
┌─────────────────────────────────────────────────────────────┐
│                    交易员/分析系统                           │
└──────────────────────────┬──────────────────────────────────┘
                          │
        ┌─────────────────┼─────────────────┐
        │                 │                 │
        ▼                 ▼                 ▼
  下发指令         查询待执行           查询统计数据
  (POST)            (GET)                (GET)
        │                 │                 │
        └─────────────────┼─────────────────┘
                          │
                 ┌────────▼────────┐
                 │  Python服务     │
                 │  (internal)     │
                 └────────┬────────┘
                          │
        ┌─────────────────┼─────────────────┐
        │                 │                 │
        ▼                 ▼                 ▼
  GET /get_trades  POST /send_statistics  健康检查
  (每100毫秒)        (每分钟)              (GET)
        │                 │                 │
        └─────────────────┼─────────────────┘
                          │
┌─────────────────────────▼──────────────────────────────────┐
│                      MT5 EA                                 │
│  - 接收交易指令                                             │
│  - 执行买卖操作                                             │
│  - 监控持仓风险                                             │
│  - 统计TICK数据                                             │
└─────────────────────────────────────────────────────────────┘
```

## 性能特性

### 高性能设计
- **FastAPI框架**: 基于Starlette和Pydantic，性能优异
- **异步处理**: 完全异步，支持高并发请求
- **多Worker进程**: 默认4个worker，可根据CPU核心数调整
- **UVloop**: 使用高性能事件循环
- **线程安全**: 内存数据使用线程锁保护

### 并发能力
- 支持数千并发连接
- 平均响应时间 < 10ms
- 内存指令队列，无数据库I/O

## 数据持久化说明

### 当前特性
- ✓ 交易指令保存在内存中（推送后删除）
- ✓ 统计数据保量最新10条
- ✗ 无数据持久化到磁盘

### 如需持久化
可选方案：
1. 添加SQLite数据库支持
2. 添加CSV日志输出
3. 集成Redis缓存

## 常见问题

### Q: 指令为什么被删除了？
A: 设计就是这样的。EA获取指令后，立即删除，确保不会重复执行。如果需要保留历史，服务已在统计数据的`trades`字段中记录。

### Q: 指令丢失怎么办？
A: 
1. 所有指令都在`/query_pending_trades`可见
2. 已推送指令会记录在统计数据中
3. 可以查看服务日志追踪

### Q: 如何处理超过10条的统计数据？
A: 服务自动删除最早的数据，保留最新10条。可在代码中修改`maxlen=10`来改变保留数量。

### Q: 支持多SYMBOL吗？
A: 完全支持。指令按SYMBOL分类，EA可以只获取自己需要的品种。

## 扩展建议

### 短期改进
1. 添加数据持久化（SQLite/PostgreSQL）
2. 添加WebSocket支持（实时推送）
3. 添加认证和日志审计

### 长期规划
1. 集成实时行情数据
2. 添加风险分析引擎
3. 支持历史数据分析
4. 添加监控和告警系统

## 维护和监控

### 查看日志
```bash
# 服务会输出所有操作日志
# [信息] 已添加 XXX 条交易指令
# [信息] 推送了 XXX 条 SYMBOL 指令给EA
```

### 检查服务状态
```bash
curl http://localhost:5858/status
```

### 性能监控
- 监控`total_pending`数量，不应该持续增加
- 监控`statistics_records`数量，应该≤10

## 许可证

MIT License

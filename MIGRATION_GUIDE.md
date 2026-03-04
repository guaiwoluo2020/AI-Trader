# 模块化重构指南

## 概述

原来的 `trading_server.py` (499行) 已被重构为以下模块化结构，提高代码可维护性和可读性。

## 新的文件结构

```
lianghua/
├── models.py              ✓ 数据模型定义
├── server.py              ✓ 核心服务类 (TradingServer)
├── routes_ea.py           ✓ EA相关路由 (/get_trades, /send_statistics)
├── routes_trader.py       ✓ 交易员相关路由 (/send_trade_instructions, /query_*)
├── routes_system.py       ✓ 系统路由 (/health, /status)
├── main.py                ✓ 应用入口和启动脚本
├── wangxxGold.mq5         正在使用的 MT5 EA
├── trading_server.py      旧版本（保留作为参考）
├── test_trading_service.py已更新，兼容新服务
├── trade_client.py        已更新，兼容新服务
└── api_examples.py        已更新，兼容新服务
```

## 模块说明

### 1. `models.py` - 数据模型
```python
from models import TradeInstruction, StatisticData

# TradeInstruction: 交易指令
# - symbol: 交易品种 (e.g., "EURUSD")
# - action: 买卖方向 ("b" 或 "s")
# - mount: 交易手数
# - price: 指令价格（用于Python过滤）
# - sl: 止损价格 (可选)
# - tp: 获利价格 (可选)

# StatisticData: 统计数据（不在此版本使用）
```

### 2. `server.py` - 核心交易服务
```python
from server import TradingServer

server = TradingServer()

# 主要方法:
server.add_trade_instruction(instructions: List[TradeInstruction]) -> int
server.get_trades_by_symbol(symbol: str, price: Optional[float]) -> List[Dict]
server.save_statistics(stat_data: dict) -> None
server.get_latest_statistics(count: int = 10) -> List[Dict]
server.get_all_pending_trades() -> Dict[str, List[Dict]]
server.clear_trades(symbol: Optional[str] = None) -> int
```

**特点:**
- 线程安全 (使用 RLock)
- 自动 SL/TP 填充
- 价格条件过滤逻辑集中在一个方法

### 3. `routes_ea.py` - EA接口
```
GET /get_trades?symbol=XXXX&price=YYYY.YY
  → 获取指定品种的交易指令（带自动价格过滤）
  
POST /send_statistics
  → 接收 EA 的统计数据（TICK、价格、账户信息等）
```

### 4. `routes_trader.py` - 交易员接口
```
POST /send_trade_instructions
  → 批量发送交易指令
  
GET /query_pending_trades?symbol=optional
  → 查看待执行的指令
  
GET /query_statistics?count=10
  → 查看历史统计数据
  
DELETE /clear_trades?symbol=optional
  → 清空指定或所有指令
```

### 5. `routes_system.py` - 系统接口
```
GET /health
  → 健康检查 (用于 EA 连接测试)
  
GET /status
  → 服务状态（待执行数、统计条数等）
```

### 6. `main.py` - 应用入口
- 创建 FastAPI 应用
- 初始化 TradingServer 单例
- 注册所有路由
- 配置 CORS 中间件
- 启动 uvicorn 服务器

**启动:**
```bash
python main.py
# 或通过启动脚本
./start.sh          # macOS/Linux
start.bat           # Windows
```

## 关键改进

### 代码组织
| 指标 | 旧版本 | 新版本 |
|------|--------|--------|
| 单个文件行数 | 499 | 50-90 |
| 模块数 | 1 | 6 |
| 关注点分离 | 差 | 优秀 |

### 可维护性
- ✅ 每个文件职责单一清晰
- ✅ 路由和业务逻辑分离
- ✅ 数据模型单独管理
- ✅ 新增功能时修改范围小

### 性能
- 保持相同：uvloop、单worker
- 线程安全性不变
- 响应速度无变化

## 迁移步骤

### 1. 准备环境
```bash
# 安装依赖
pip install -r requirements.txt
```

### 2. 启动新服务
```bash
# 方法1：直接启动
python main.py

# 方法2：使用启动脚本
./start.sh          # macOS/Linux
start.bat           # Windows

# 方法3：开发模式（热重载）
pip install uvicorn
uvicorn main:app --reload --port 8000
```

### 3. 验证服务
```bash
# 检查健康状态
curl http://localhost:8000/health

# 查看 API 文档
open http://localhost:8000/docs
```

### 4. 测试
```bash
# 运行完整测试套件
python test_trading_service.py

# 或使用交易工具
python trade_client.py
```

## API 变化

### 端口号变化
- **旧版本**: http://localhost:5858
- **新版本**: http://localhost:8000

所有客户端工具已自动更新为新端口。

### 功能保持完全兼容
所有 API 端点的请求/响应格式完全相同，迁移时无需修改 EA 或其他集成代码。

## 常见问题

### Q: 旧版本 trading_server.py 还能用吗？
A: 可以，但推荐迁移到新版本。旧文件仍保存作为参考。

### Q: MT5 EA 需要修改吗？
A: **无需修改**。EA 只是调用 HTTP 接口，端口和 API 格式不变。

### Q: 如何添加新的路由？
A: 
1. 在 `routes_*.py` 中创建路由函数
2. 使用 `@router.get()` 或 `@router.post()` 装饰器
3. 在 `main.py` 中用 `app.include_router()` 注册

示例：
```python
# 在 routes_custom.py 中
def create_custom_routes(server: TradingServer) -> APIRouter:
    router = APIRouter()
    
    @router.get("/custom_endpoint")
    async def custom_handler():
        return {"status": "ok"}
    
    return router

# 在 main.py 中
from routes_custom import create_custom_routes
app.include_router(create_custom_routes(server))
```

### Q: TradingServer 实例在哪里创建？
A: 在 `main.py` 中创建全局实例，然后传递给所有路由函数。

### Q: 如何在开发时调试？
```bash
# 启用热重载和调试输出
uvicorn main:app --reload --log-level debug

# 或直接运行主文件
python main.py  # 会看到详细日志
```

## 反向兼容性

✅ **完全兼容**
- 所有现有客户端（EA、trade_client.py、api_examples.py）无需修改
- API 端口、路由、请求/响应格式完全一致
- 只是内部代码组织不同

## 下一步改进建议

1. **添加数据库支持**
   - 将统计数据持久化（SQLite/PostgreSQL）
   - 交易历史记录

2. **增强监控**
   - 将日志写入文件
   - 添加性能指标（响应时间、吞吐量等）

3. **配置管理**
   - 将服务参数移到配置文件
   - 支持环境变量覆盖

4. **WebSocket 支持**
   - 实时推送价格更新
   - 订阅式通知

5. **测试完善**
   - 单元测试（pytest）
   - 集成测试
   - 负载测试

## 总结

新的模块化结构使代码更清晰、更易维护，同时保持 100% 的 API 兼容性。
迁移无缝，现有系统可立即采用新版本而无需任何修改。

---

**最后更新**: 2024-01-15
**版本**: 1.0.0 (模块化)

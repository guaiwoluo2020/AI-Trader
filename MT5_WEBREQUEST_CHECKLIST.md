# MT5 WebRequest 配置检查清单

## ⚠️ 重要提示

EA无法发送HTTP请求的最常见原因是**WebRequest权限未正确配置**。请按照以下步骤逐一检查。

## ✅ 配置步骤

### 1. 启用WebRequest权限

在MT5终端中：

1. 打开 **工具(Tools)** → **选项(Options)** (或按 `Ctrl+O`)
2. 切换到 **EA交易(Expert Advisors)** 标签
3. 勾选以下选项：
   - ✅ **允许自动交易(Allow Automated Trading)**
   - ✅ **允许WebRequest用于脚本...(Allow WebRequest for scripts and EA)**

### 2. 添加URL到允许列表

在同一页面：

1. 找到 **WebRequest允许的URL列表** 区域
2. 点击 **添加(Add)** 按钮
3. 输入以下URL（根据你的后端配置）：

```
http://localhost:8000
```

或如果使用trading_server.py：

```
http://localhost:5858
```

**注意**:
- 必须包含 `http://` 前缀
- 不要在末尾加 `/`
- 如果同时使用两个端口，两个都要添加

### 3. 重启EA

配置完成后：

1. 在图表上右键点击EA → **移除(Remove)**
2. 重新从导航器拖拽EA到图表
3. 确保EA显示为 ✅ **启用(Enabled)** 状态

## 🔍 验证配置

### 方法1: 检查MT5日志

1. 打开 **终端(Terminal)** → **日志(Journal)** 标签
2. 查找以下消息：
   - ✅ `Expert initialized successfully`
   - ✅ `Statistics sent successfully` (每分钟发送)
   - ❌ `WebRequest is disabled!` → WebRequest未启用
   - ❌ `Failed to send statistics` → URL未添加或服务未启动

### 方法2: 检查后端日志

启动后端服务后，应该看到：

```bash
# 启动后端
python main.py

# 观察日志，应该看到EA的请求：
# GET /get_trades?symbol=GOLD&price=2035.50
# POST /send_statistics
```

### 方法3: 使用测试工具

运行自动化测试验证服务：

```bash
python test_trading_service.py
```

## 🐛 常见问题排查

### 问题1: WebRequest is disabled

**症状**:
```
WebRequest is disabled! Please enable WebRequest in MT5 Options -> Expert Advisors
```

**解决方案**:
- 按照上述步骤1启用WebRequest权限
- 重启MT5

### 问题2: Connection refused

**症状**:
```
Failed to send statistics. Response code: 404
或
Failed to send statistics. Response code: 500
```

**解决方案**:
1. 确认后端服务正在运行
   ```bash
   # 检查服务是否启动
   lsof -i :8000  # 或 lsof -i :5858

   # 如果没有运行，启动服务
   python main.py  # 端口8000
   # 或
   python trading_server.py  # 端口5858
   ```

2. 确认端口匹配
   - EA配置: `wangxxGold.mq5` 第22行
   - 后端配置: `main.py` 第67行 (8000) 或 `trading_server.py` 第494行 (5858)

### 问题3: URL not in allowed list

**症状**:
```
Failed to send statistics. Response code: -1
```

**解决方案**:
- 确保已添加 `http://localhost:8000` 到允许列表
- 不要添加 `https://`（除非你的服务器配置了HTTPS）
- 重启EA

### 问题4: Timeout

**症状**:
```
Failed to send statistics. Response code: 408
```

**解决方案**:
- 检查后端服务响应时间
- 检查网络连接
- 尝试在浏览器访问 `http://localhost:8000/health` 测试连接

## 📋 快速检查清单

```
□ MT5选项中启用"允许自动交易"
□ MT5选项中启用"允许WebRequest"
□ 添加 http://localhost:8000 到允许列表
□ 后端服务已启动 (python main.py)
□ EA已重启并启用
□ MT5日志显示"Expert initialized successfully"
□ 后端日志显示EA的HTTP请求
```

## 🧪 测试流程

### 完整测试流程：

```bash
# 终端1: 启动后端
python main.py

# 终端2: 测试后端
curl http://localhost:8000/health
# 应该返回: {"status": "healthy", ...}

# MT5终端:
# 1. 配置WebRequest权限
# 2. 加载EA到图表
# 3. 观察MT5日志和后端日志

# 终端3: 发送测试交易指令
python trade_client.py
# 选择1: 发送买入订单
# 检查EA是否执行交易
```

## 💡 调试技巧

### 启用详细日志

在EA代码中，所有HTTP请求都有详细的日志输出。如果你看不到任何WebRequest相关的日志：

1. **检查EA是否真的在运行**
   - 图表右上角应该有EA图标和笑脸 😊
   - 如果是哭脸 😢，说明EA初始化失败

2. **检查OnTick是否被调用**
   - 每次价格变动都会调用OnTick
   - 应该看到统计数据变化

3. **手动触发HTTP请求**
   ```bash
   # 测试后端连接
   curl -X GET "http://localhost:8000/get_trades?symbol=GOLD&price=2035.50"

   # 测试统计接口
   curl -X POST "http://localhost:8000/send_statistics" \
        -H "Content-Type: application/json" \
        -d '{"tickCount":100,"bidPrice":2035.50}'
   ```

## 📚 相关文档

- [MT5 WebRequest官方文档](https://www.mql5.com/en/docs/network/webrequest)
- [项目README](README.md)
- [快速开始指南](QUICKSTART.md)

## ❓ 需要帮助？

如果按照以上步骤仍无法解决问题：

1. 检查MT5日志文件（通常在 `MQL5/Logs/` 目录）
2. 检查后端服务日志
3. 使用 `curl` 测试后端接口
4. 确认防火墙没有阻止连接

---

**最后更新**: 2026-03-05
import axios from 'axios'

const api = axios.create({
  baseURL: 'http://localhost:8000',
  timeout: 10000,
  headers: {
    'Content-Type': 'application/json',
  },
})

export const marketAPI = {
  // 获取所有symbol列表
  async getSymbols() {
    const response = await api.get('/market/symbols')
    return response.data
  },

  // 获取K线数据
  async getKlines(symbol, period = 'M5', count = 100) {
    const encodedSymbol = encodeURIComponent(symbol)
    const response = await api.get(`/market/kline/${encodedSymbol}`, {
      params: { period, count }
    })
    return response.data
  },

  // 获取转折点数据
  async getPivots(symbol, period = null, direction = null, count = 50) {
    const params = { count }
    if (period) params.period = period
    if (direction) params.direction = direction
    const encodedSymbol = encodeURIComponent(symbol)
    const response = await api.get(`/market/pivots/${encodedSymbol}`, { params })
    return response.data
  },

  // 获取行情状态
  async getStatus() {
    const response = await api.get('/market/status')
    return response.data
  },

  // 获取阈值配置
  async getThresholds() {
    const response = await api.get('/market/thresholds')
    return response.data
  },

  // 创建WebSocket连接
  createWebSocket(onMessage, onError, onOpen, onClose) {
    const ws = new WebSocket('ws://localhost:8000/ws/market')

    ws.onopen = () => {
      console.log('WebSocket 连接成功')
      if (onOpen) onOpen()
    }

    ws.onmessage = (event) => {
      try {
        const data = JSON.parse(event.data)
        if (onMessage) onMessage(data)
      } catch (e) {
        console.error('WebSocket 消息解析错误:', e)
      }
    }

    ws.onerror = (error) => {
      console.error('WebSocket 错误:', error)
      if (onError) onError(error)
    }

    ws.onclose = () => {
      console.log('WebSocket 连接关闭')
      if (onClose) onClose()
    }

    return ws
  },

  // 发送心跳
  sendPing(ws) {
    if (ws && ws.readyState === WebSocket.OPEN) {
      ws.send(JSON.stringify({ type: 'ping' }))
    }
  },

  // 获取趋势分析
  async getTrend(symbol) {
    const response = await api.get(`/trend/${encodeURIComponent(symbol)}`)
    return response.data
  },

  // 生成交易建议
  async generateTradeOrder(symbol) {
    const response = await api.post(`/trend/generate_order/${encodeURIComponent(symbol)}`)
    return response.data
  },

  // 获取待确认订单
  async getPendingOrders(symbol = null) {
    const params = symbol ? { symbol } : {}
    const response = await api.get('/pending_orders', { params })
    return response.data
  },

  // 确认订单
  async confirmOrder(orderId) {
    const response = await api.post(`/pending_orders/${orderId}/confirm`)
    return response.data
  },

  // 确认订单并更新参数
  async confirmOrderWithUpdate(orderId, updateData) {
    const response = await api.post(`/pending_orders/${orderId}/confirm`, updateData)
    return response.data
  },

  // 拒绝订单
  async rejectOrder(orderId) {
    const response = await api.post(`/pending_orders/${orderId}/reject`)
    return response.data
  },

  // 获取交易配置
  async getTradeConfig() {
    const response = await api.get('/trade_config')
    return response.data
  },

  // 更新交易配置
  async updateTradeConfig(config) {
    const response = await api.post('/trade_config', config)
    return response.data
  },

  // 获取统计数据（包含持仓）
  async getStatistics(count = 1) {
    const response = await api.get('/query_statistics', { params: { count } })
    return response.data
  },

  // 平仓
  async closePosition(ticket, symbol) {
    const response = await api.post('/close_position', { ticket, symbol })
    return response.data
  },

  // ==================== 大模型分析 ====================

  // 获取大模型分析结果
  async getLLMAnalysis(symbol = null) {
    const params = symbol ? { symbol } : {}
    const response = await api.get('/llm/analysis', { params })
    return response.data
  },

  // 获取大模型分析器状态
  async getLLMStatus() {
    const response = await api.get('/llm/status')
    return response.data
  },

  // 获取大模型配置
  async getLLMConfig() {
    const response = await api.get('/llm/config')
    return response.data
  },

  // 手动触发大模型分析
  async triggerLLMAnalysis() {
    const response = await api.post('/llm/trigger')
    return response.data
  },

  // 配置大模型参数
  async configureLLM(config) {
    const response = await api.post('/llm/configure', config)
    return response.data
  },

  // 获取已配置品种的K线数据状态
  async getConfiguredSymbols() {
    const response = await api.get('/market/configured_symbols')
    return response.data
  },

  // 获取系统运行日志
  async getSystemLogs(count = 50, eventTypes = null, symbol = null) {
    const params = { count }
    if (eventTypes && eventTypes.length > 0) {
      params.event_type = eventTypes.join(',')
    }
    if (symbol) params.symbol = symbol
    const response = await api.get('/system/logs', { params })
    return response.data
  },

  // 清空系统日志
  async clearSystemLogs() {
    const response = await api.delete('/system/logs')
    return response.data
  },

  // ==================== 仓位管理 ====================

  // 获取持仓数据
  async getPositions(symbol = null) {
    const params = symbol ? { symbol } : {}
    const response = await api.get('/positions', { params })
    return response.data
  },

  // 获取持仓汇总
  async getPositionsSummary(symbol = null) {
    const params = symbol ? { symbol } : {}
    const response = await api.get('/positions/summary', { params })
    return response.data
  },

  // ==================== 交易历史 ====================

  // 获取交易历史
  async getTradeHistory() {
    const response = await api.get('/trade_history')
    return response.data
  },

  // 获取交易历史统计
  async getTradeHistoryStatistics() {
    const response = await api.get('/trade_history/statistics')
    return response.data
  },

  // ==================== 策略配置 ====================

  // 获取所有策略配置
  async getStrategies() {
    const response = await api.get('/strategy')
    return response.data
  },

  // 获取品种策略配置
  async getStrategy(symbol) {
    const response = await api.get(`/strategy/${encodeURIComponent(symbol)}`)
    return response.data
  },

  // 更新品种策略配置
  async updateStrategy(symbol, data) {
    const response = await api.post(`/strategy/${encodeURIComponent(symbol)}`, data)
    return response.data
  },

  // 删除品种策略配置
  async deleteStrategy(symbol) {
    const response = await api.delete(`/strategy/${encodeURIComponent(symbol)}`)
    return response.data
  },

  // 获取决策历史
  async getDecisions(symbol = null, count = 20) {
    const params = { count }
    if (symbol) params.symbol = symbol
    const response = await api.get('/strategy/decisions', { params })
    return response.data
  },

  // 手动触发策略决策
  async triggerStrategyDecision(symbol) {
    const response = await api.post(`/strategy/trigger/${encodeURIComponent(symbol)}`)
    return response.data
  }
}

export default api
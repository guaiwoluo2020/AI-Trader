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
  }
}

export default api
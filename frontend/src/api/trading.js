import axios from 'axios'

const api = axios.create({
  baseURL: 'http://localhost:8000',
  timeout: 10000,
  headers: {
    'Content-Type': 'application/json',
  },
})

// 请求拦截器
api.interceptors.request.use(
  (config) => {
    // 可以在这里添加认证token等
    return config
  },
  (error) => {
    return Promise.reject(error)
  }
)

// 响应拦截器
api.interceptors.response.use(
  (response) => {
    return response
  },
  (error) => {
    console.error('API Error:', error)
    return Promise.reject(error)
  }
)

export const tradingAPI = {
  // 健康检查
  async health() {
    const response = await api.get('/health')
    return response.data
  },

  // 获取服务状态
  async getStatus() {
    const response = await api.get('/status')
    return response.data
  },

  // 发送交易指令
  async sendTradeInstructions(instructions) {
    const response = await api.post('/send_trade_instructions', instructions)
    return response.data
  },

  // 查询待执行指令
  async getPendingTrades() {
    const response = await api.get('/query_pending_trades')
    return response.data
  },

  // 查询统计数据
  async getStatistics() {
    const response = await api.get('/query_statistics')
    return response.data
  },

  // 清空指令
  async clearTrades() {
    const response = await api.delete('/clear_trades')
    return response.data
  },
}

export default api
import axios from 'axios'
import { clearAuthSession, getAuthToken, setAuthSession } from '../auth'
import { applyAuthToRequestConfig, handleAuthError } from './auth-helpers.js'
import { API_BASE_URL } from './runtime.js'

const api = axios.create({
  baseURL: API_BASE_URL,
  timeout: 10000,
  headers: {
    'Content-Type': 'application/json',
  },
})

// 请求拦截器
api.interceptors.request.use(
  (config) => applyAuthToRequestConfig(config),
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
    return handleAuthError(error)
  }
)

export const authAPI = {
  async sendRegistrationCode(email, invitationCode) {
    const response = await api.post('/auth/email-code', {
      email,
      invitation_code: invitationCode,
    })
    return response.data
  },

  async sendLoginCode(email) {
    const response = await api.post('/auth/login/email-code', { email })
    return response.data
  },

  async loginWithEmail(credentials) {
    const response = await api.post('/auth/login/email', credentials)
    setAuthSession({ token: response.data.token, user: response.data.user })
    return response.data
  },

  async register(credentials) {
    const response = await api.post('/auth/register', credentials)
    setAuthSession({
      token: response.data.token,
      user: response.data.user,
    })
    return response.data
  },

  async me() {
    const response = await api.get('/auth/me')
    setAuthSession({
      token: getAuthToken(),
      user: response.data.user,
    })
    return response.data
  },

  async getEmailConfig() {
    const response = await api.get('/auth/admin/email-config')
    return response.data
  },

  async saveEmailConfig(config) {
    const response = await api.put('/auth/admin/email-config', config)
    return response.data
  },

  async testEmailConfig(targetEmail = null) {
    const response = await api.post('/auth/admin/email-config/test', {
      target_email: targetEmail || null,
    })
    return response.data
  },

  async getMyQuota() {
    const response = await api.get('/auth/quota')
    return response.data
  },

  async getUserQuotas() {
    const response = await api.get('/auth/admin/user-quotas')
    return response.data
  },

  async saveUserQuota(userId, quota) {
    const response = await api.put(
      `/auth/admin/users/${encodeURIComponent(userId)}/quota`, quota
    )
    return response.data
  },

  async saveUserMembership(userId, membership) {
    const response = await api.put(
      `/auth/admin/users/${encodeURIComponent(userId)}/membership`, membership
    )
    return response.data
  },

  async createUserViewToken(userId) {
    const response = await api.post(
      `/auth/admin/users/${encodeURIComponent(userId)}/view-token`
    )
    return response.data
  },

  async getInvitations() {
    const response = await api.get('/auth/admin/invitations')
    return response.data
  },

  async createInvitation(payload) {
    const response = await api.post('/auth/admin/invitations', payload)
    return response.data
  },

  async setInvitationActive(invitationId, active) {
    const response = await api.patch(
      `/auth/admin/invitations/${encodeURIComponent(invitationId)}`, { active }
    )
    return response.data
  },

  logout() {
    clearAuthSession()
  },
}

export const tradingAPI = {
  async getDashboardOverview(accountId = null) {
    const response = await api.get('/dashboard/overview', {
      params: accountId ? { account_id: accountId } : {},
    })
    return response.data
  },

  // 健康检查
  async health() {
    const response = await api.get('/health')
    return response.data
  },

  // 获取服务状态
  async getStatus(accountId = null) {
    const response = await api.get('/status', { params: accountId ? { account_id: accountId } : {} })
    return response.data
  },

  // 查询待执行指令
  async getPendingTrades(accountId = null) {
    const response = await api.get('/query_pending_trades', {
      params: accountId ? { account_id: accountId } : {},
    })
    return response.data
  },

  // 查询统计数据
  async getStatistics(symbol = null, count = 10, accountId = null) {
    const params = { count }
    if (symbol) params.symbol = symbol
    if (accountId) params.account_id = accountId
    const response = await api.get('/query_statistics', { params })
    return response.data
  },

  // 获取真实成交统计
  async getTradeHistoryStatistics(symbol = null, accountId = null) {
    const params = symbol ? { symbol } : {}
    if (accountId) params.account_id = accountId
    const response = await api.get('/trade_history/statistics', { params })
    return response.data
  },

  // 清空指令
  async clearTrades(accountId = null) {
    const response = await api.delete('/clear_trades', {
      params: accountId ? { account_id: accountId } : {},
    })
    return response.data
  },

  async getTradeExecutions(accountId = null, count = 100) {
    const params = { count }
    if (accountId) params.account_id = accountId
    const response = await api.get('/trade_executions', { params })
    return response.data
  },
}

export const mt5API = {
  async status(accountId = null) {
    const response = await api.get('/auth/mt5-ea/status', {
      params: accountId ? { account_id: accountId } : {},
    })
    return response.data
  },

  async download() {
    return api.post('/auth/mt5-ea/download', null, {
      responseType: 'blob',
      timeout: 30000,
    })
  },
}

export const accountAPI = {
  async list() {
    const response = await api.get('/accounts')
    return response.data
  },

  async createPaper(data) {
    const response = await api.post('/accounts/paper', data)
    return response.data
  },

  async update(accountId, data) {
    const response = await api.patch(
      `/accounts/${encodeURIComponent(accountId)}`,
      data
    )
    return response.data
  },

  async archive(accountId) {
    const response = await api.post(
      `/accounts/${encodeURIComponent(accountId)}/archive`
    )
    return response.data
  },

  async restore(accountId) {
    const response = await api.post(
      `/accounts/${encodeURIComponent(accountId)}/restore`
    )
    return response.data
  },

  async downloadMt5EA() {
    return api.post('/auth/mt5-ea/download', null, {
      responseType: 'blob',
      timeout: 30000,
    })
  },

  async getPaperContext() {
    const response = await api.get('/accounts/paper/context')
    return response.data
  },

  async getPaperDetail(accountId) {
    const response = await api.get(`/accounts/${encodeURIComponent(accountId)}/paper`)
    return response.data
  },

  async getLiveMonitoring(accountId) {
    const response = await api.get(`/accounts/${encodeURIComponent(accountId)}/live-monitoring`)
    return response.data
  },

  async getPaperReport(accountId, strategyId = '') {
    const response = await api.get(
      `/accounts/${encodeURIComponent(accountId)}/paper/report`,
      { params: strategyId ? { strategy_id: strategyId } : {} }
    )
    return response.data
  },

  async deployStrategy(accountId, strategyId) {
    const response = await api.post(
      `/accounts/${encodeURIComponent(accountId)}/deployments`,
      { strategy_id: strategyId }
    )
    return response.data
  },

  async deployBacktest(accountId, taskId, durationDays = 30) {
    const response = await api.post(
      `/accounts/${encodeURIComponent(accountId)}/deployments/backtest`,
      { task_id: taskId, duration_days: durationDays }
    )
    return response.data
  },

  async setDeploymentStatus(accountId, deploymentId, active) {
    const response = await api.patch(
      `/accounts/${encodeURIComponent(accountId)}/deployments/${encodeURIComponent(deploymentId)}`,
      { active }
    )
    return response.data
  },

  async endDeployment(accountId, deploymentId) {
    const response = await api.post(
      `/accounts/${encodeURIComponent(accountId)}/deployments/${encodeURIComponent(deploymentId)}/end`
    )
    return response.data
  },

  async removeDeployment(accountId, deploymentId) {
    const response = await api.delete(
      `/accounts/${encodeURIComponent(accountId)}/deployments/${encodeURIComponent(deploymentId)}`
    )
    return response.data
  },
}

export default api

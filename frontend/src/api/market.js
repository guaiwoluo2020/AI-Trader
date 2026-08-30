import axios from 'axios'
import { getAuthToken } from '../auth'
import { applyAuthToRequestConfig, handleAuthError } from './auth-helpers.js'
import { API_BASE_URL, getApiWebSocketUrl, getMarketWebSocketUrl } from './runtime.js'

const api = axios.create({
  baseURL: API_BASE_URL,
  timeout: 10000,
  headers: {
    'Content-Type': 'application/json',
  },
})

api.interceptors.request.use(
  (config) => applyAuthToRequestConfig(config),
  (error) => Promise.reject(error)
)

api.interceptors.response.use(
  (response) => response,
  (error) => handleAuthError(error)
)

function formatConfigurationImpact(impact = {}) {
  const own = impact.own_deployments?.length || 0
  const externalRefs = impact.external_references?.length || 0
  const external = impact.external_deployments?.length || 0
  return `本用户部署 ${own} 个；外部用户引用 ${externalRefs} 个；外部已部署 ${external} 个。\n已有持仓：${impact.existing_positions || '保持原配置快照'}。`
}

export const marketAPI = {
  async getMarketEventStatus() {
    const response = await api.get('/news/status')
    return response.data
  },

  async getMarketCalendar(date) {
    const response = await api.get('/news/calendar', { params: { date } })
    return response.data
  },

  async getMarketKeyEvents(date) {
    const response = await api.get('/news/key-events', { params: { date } })
    return response.data
  },

  async getMarketFlashNews(limit = 100) {
    const response = await api.get('/news/flash', { params: { limit } })
    return response.data
  },

  createMarketEventWebSocket(onMessage, onClose) {
    const ws = new WebSocket(getApiWebSocketUrl('/news/ws'))
    ws.onopen = () => {
      ws.send(JSON.stringify({ type: 'auth', token: getAuthToken() }))
    }
    ws.onmessage = (event) => {
      try {
        onMessage?.(JSON.parse(event.data))
      } catch (error) {
        console.error('市场事件 WebSocket 消息解析失败:', error)
      }
    }
    ws.onclose = () => onClose?.()
    return ws
  },

  // ==================== 回测任务 ====================

  async getBacktestTemplateContext() {
    const response = await api.get('/backtest/templates/context')
    return response.data
  },

  async getBacktestTemplates() {
    const response = await api.get('/backtest/templates')
    return response.data
  },

  async createBacktestTemplate(data) {
    const response = await api.post('/backtest/templates', data)
    return response.data
  },

  async updateBacktestTemplate(templateId, data) {
    const response = await api.put(
      `/backtest/templates/${encodeURIComponent(templateId)}`,
      data
    )
    return response.data
  },

  async deleteBacktestTemplate(templateId) {
    const response = await api.delete(
      `/backtest/templates/${encodeURIComponent(templateId)}`
    )
    return response.data
  },

  async runBacktestTemplate(templateId) {
    const response = await api.post(
      `/backtest/templates/${encodeURIComponent(templateId)}/run`
    )
    return response.data
  },

  async getBacktestBatches() {
    const response = await api.get('/backtest/batches')
    return response.data
  },

  async getBacktestBatch(batchId) {
    const response = await api.get(
      `/backtest/batches/${encodeURIComponent(batchId)}`
    )
    return response.data
  },

  async cancelBacktestBatch(batchId) {
    const response = await api.post(
      `/backtest/batches/${encodeURIComponent(batchId)}/cancel`
    )
    return response.data
  },

  async cancelBacktestTask(taskId) {
    const response = await api.post(
      `/backtest/tasks/${encodeURIComponent(taskId)}/cancel`
    )
    return response.data
  },

  async getBacktestTaskLedger(taskId) {
    const response = await api.get(
      `/backtest/tasks/${encodeURIComponent(taskId)}/ledger`
    )
    return response.data
  },

  async getBacktestTaskAIAnalysis(taskId) {
    const response = await api.get(
      `/backtest/tasks/${encodeURIComponent(taskId)}/ai-analysis`
    )
    return response.data
  },

  async startBacktestTaskAIAnalysis(taskId, regenerate = false) {
    const response = await api.post(
      `/backtest/tasks/${encodeURIComponent(taskId)}/ai-analysis`,
      { regenerate }
    )
    return response.data
  },

  // ==================== 回测数据集 ====================

  async getBacktestDatasetContext() {
    const response = await api.get('/backtest/datasets/context')
    return response.data
  },

  async getBacktestDatasets() {
    const response = await api.get('/backtest/datasets')
    return response.data
  },

  async createBacktestDataset(data) {
    const response = await api.post('/backtest/datasets', data)
    return response.data
  },

  async cancelBacktestDataset(datasetId) {
    const response = await api.post(
      `/backtest/datasets/${encodeURIComponent(datasetId)}/cancel`
    )
    return response.data
  },

  async copyBacktestDataset(datasetId) {
    const response = await api.post(
      `/backtest/datasets/${encodeURIComponent(datasetId)}/copy`
    )
    return response.data
  },

  async updateBacktestDatasetVisibility(datasetId, visibility) {
    const response = await api.patch(
      `/backtest/datasets/${encodeURIComponent(datasetId)}/visibility`,
      { visibility }
    )
    return response.data
  },

  async deleteBacktestDataset(datasetId) {
    const response = await api.delete(
      `/backtest/datasets/${encodeURIComponent(datasetId)}`
    )
    return response.data
  },

  // ==================== Alpha research ====================

  async getAlphaResearchContext() {
    const response = await api.get('/alpha-research/context')
    return response.data
  },

  async getAlphaResearchRuns() {
    const response = await api.get('/alpha-research/runs')
    return response.data
  },

  async generateAlphaCandidates(data) {
    const response = await api.post('/alpha-research/candidates', data, { timeout: 130000 })
    return response.data
  },

  async createAlphaResearchRun(data) {
    const response = await api.post('/alpha-research/runs', data)
    return response.data
  },

  async getAlphaResearchRun(runId) {
    const response = await api.get(`/alpha-research/runs/${encodeURIComponent(runId)}`)
    return response.data
  },

  async cancelAlphaResearchRun(runId) {
    const response = await api.post(`/alpha-research/runs/${encodeURIComponent(runId)}/cancel`)
    return response.data
  },

  async publishAlphaResearchRun(runId, visibility = 'private') {
    const response = await api.post(
      `/alpha-research/runs/${encodeURIComponent(runId)}/publish`,
      { visibility }
    )
    return response.data
  },

  async getAlphaLibrary() {
    const response = await api.get('/alpha-library')
    return response.data
  },

  async retireAlpha(alphaId) {
    const response = await api.post(`/alpha-library/${encodeURIComponent(alphaId)}/retire`)
    return response.data
  },

  async copyAlpha(alphaId) {
    const response = await api.post(`/alpha-library/${encodeURIComponent(alphaId)}/copy`)
    return response.data
  },

  // 获取所有symbol列表
  async getSymbols(accountId = null) {
    const response = await api.get('/market/symbols', {
      params: accountId ? { account_id: accountId } : {},
    })
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
  async getMarketStructure(symbol, period = 'M5', count = 600) {
    const response = await api.get(`/market/structure/${encodeURIComponent(symbol)}`, { params: { period, count } })
    return response.data
  },
  async getStructureTradePlans(symbol, period = 'M5') {
    const response = await api.get(`/market/structure/${encodeURIComponent(symbol)}/trade-plans`, { params: { period, _ts: Date.now() } })
    return response.data
  },
  async getMarketStructureConfig() {
    const response = await api.get('/admin/market-structure/config')
    return response.data
  },
  async saveMarketStructureConfig(config) {
    const response = await api.put('/admin/market-structure/config', config)
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
  async getStatus(accountId = null) {
    const response = await api.get('/market/status', {
      params: accountId ? { account_id: accountId } : {},
    })
    return response.data
  },

  // 获取阈值配置
  async getThresholds() {
    const response = await api.get('/market/thresholds')
    return response.data
  },

  // 创建WebSocket连接
  createWebSocket(onMessage, onError, onOpen, onClose, accountId = null) {
    const ws = new WebSocket(getMarketWebSocketUrl())

    ws.onopen = () => {
      const token = getAuthToken()
      if (!token) {
        ws.close(1008, 'Authentication required')
        return
      }
      ws.send(JSON.stringify({ type: 'auth', token, account_id: accountId }))
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
  async getTrend(symbol, accountId = null) {
    const response = await api.get(`/trend/${encodeURIComponent(symbol)}`, {
      params: accountId ? { account_id: accountId } : {},
    })
    return response.data
  },

  // 生成交易建议
  async generateTradeOrder(symbol) {
    const response = await api.post(`/trend/generate_order/${encodeURIComponent(symbol)}`)
    return response.data
  },

  // 获取待确认订单
  async getPendingOrders(symbol = null, accountId = null) {
    const params = symbol ? { symbol } : {}
    if (accountId) params.account_id = accountId
    const response = await api.get('/pending_orders', { params })
    return response.data
  },

  // 确认订单
  async confirmOrder(orderId, accountId = null) {
    const response = await api.post(`/pending_orders/${orderId}/confirm`, null, {
      params: accountId ? { account_id: accountId } : {},
    })
    return response.data
  },

  // 确认订单并更新参数
  async confirmOrderWithUpdate(orderId, updateData, accountId = null) {
    const response = await api.post(`/pending_orders/${orderId}/confirm`, updateData, {
      params: accountId ? { account_id: accountId } : {},
    })
    return response.data
  },

  // 拒绝订单
  async rejectOrder(orderId, accountId = null) {
    const response = await api.post(`/pending_orders/${orderId}/reject`, null, {
      params: accountId ? { account_id: accountId } : {},
    })
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
  async closePosition(ticket, symbol, accountId = null) {
    const response = await api.post('/close_position', { ticket, symbol }, {
      params: accountId ? { account_id: accountId } : {},
    })
    return response.data
  },

  // ==================== 大模型分析 ====================

  // 获取大模型分析结果
  async getLLMAnalysis(symbol = null, accountId = null) {
    const params = symbol ? { symbol } : {}
    if (accountId) params.account_id = accountId
    const response = await api.get('/llm/analysis', { params })
    return response.data
  },

  // 获取大模型分析器状态
  async getLLMStatus(accountId = null) {
    const response = await api.get('/llm/status', {
      params: accountId ? { account_id: accountId } : {},
    })
    return response.data
  },

  // 获取大模型配置
  async getLLMConfig() {
    const response = await api.get('/llm/config')
    return response.data
  },

  async getLLMScene(sceneCode) {
    const response = await api.get(`/llm/scenes/${encodeURIComponent(sceneCode)}`)
    return response.data
  },

  async syncLLMModels() {
    const response = await api.post('/admin/llm/models/sync')
    return response.data
  },

  async setLLMModelEnabled(modelId, enabled) {
    const response = await api.put(
      `/admin/llm/models/${encodeURIComponent(modelId)}`, { enabled }
    )
    return response.data
  },

  async saveLLMProvider(payload) {
    const response = await api.put('/admin/llm/providers', payload)
    return response.data
  },

  async activateLLMProvider(providerId) {
    const response = await api.post(
      `/admin/llm/providers/${encodeURIComponent(providerId)}/activate`
    )
    return response.data
  },

  async getInstrumentMappings() {
    const response = await api.get('/admin/instrument-mappings')
    return response.data
  },

  async saveInstrumentMapping(payload) {
    const response = await api.put('/admin/instrument-mappings', payload)
    return response.data
  },

  async deleteInstrumentMapping(mappingId) {
    const response = await api.delete(`/admin/instrument-mappings/${encodeURIComponent(mappingId)}`)
    return response.data
  },

  async getInstrumentPriceObservations() {
    const response = await api.get('/admin/instrument-price-observations')
    return response.data
  },

  async saveLLMScene(sceneCode, payload) {
    const response = await api.put(
      `/admin/llm/scenes/${encodeURIComponent(sceneCode)}`, payload
    )
    return response.data
  },

  // 获取当前用户的大模型功能开通状态
  async getLLMAccess() {
    const response = await api.get('/llm/access')
    return response.data
  },

  // 获取 AI 信号源可选模型、默认提示词和共享运行数据
  async getLLMSignalOptions(symbol = null) {
    const response = await api.get('/llm/signal-options', {
      params: symbol ? { symbol } : {}
    })
    return response.data
  },

  async getAISignalSources(params = {}) {
    const response = await api.get('/ai-signal-sources', { params })
    return response.data
  },

  async generateAISignalPrompt(data) {
    const response = await api.post('/ai-signal-sources/generate-prompt', data)
    return response.data
  },

  async createAISignalSource(data) {
    const response = await api.post('/ai-signal-sources', data)
    return response.data
  },

  async updateAISignalSource(signalSourceId, data) {
    try {
      const response = await api.put(`/ai-signal-sources/${encodeURIComponent(signalSourceId)}`, data)
      return response.data
    } catch (error) {
      const detail = error?.response?.data?.detail
      if (detail?.impact && detail?.message && detail?.impact?.allowed && window.confirm(`${detail.message}\n\n${formatConfigurationImpact(detail.impact)}\n\n确认热加载修改吗？`)) {
        const response = await api.put(`/ai-signal-sources/${encodeURIComponent(signalSourceId)}`, { ...data, _confirm_hot_reload: true })
        return response.data
      }
      throw error
    }
  },

  async copyAISignalSource(signalSourceId) {
    const response = await api.post(`/ai-signal-sources/${encodeURIComponent(signalSourceId)}/copy`)
    return response.data
  },

  async deleteAISignalSource(signalSourceId) {
    const response = await api.delete(`/ai-signal-sources/${encodeURIComponent(signalSourceId)}`)
    return response.data
  },

  async getAISignalSourceImpact(signalSourceId) {
    const response = await api.get(`/ai-signal-sources/${encodeURIComponent(signalSourceId)}/impact`)
    return response.data
  },

  async pauseAISignalSource(signalSourceId, paused = true) {
    const response = await api.post(`/ai-signal-sources/${encodeURIComponent(signalSourceId)}/pause`, { paused })
    return response.data
  },

  async configureAISignalAdaptive(signalSourceId, enabled, sampleSize = 10) {
    const response = await api.post(`/ai-signal-sources/${encodeURIComponent(signalSourceId)}/adaptive`, {
      enabled,
      sample_size: sampleSize
    })
    return response.data
  },

  async getSharedAIRuntimeData(symbol = null) {
    const response = await api.get('/llm/runtime-shares', {
      params: symbol ? { symbol } : {}
    })
    return response.data
  },

  async getAIMarketView(accountId = null, symbol = null) {
    const params = {}
    if (accountId) params.account_id = accountId
    if (symbol) params.symbol = symbol
    const response = await api.get('/llm/market-view', { params })
    return response.data
  },

  async getAIMarketHistory(signalSourceId) {
    const response = await api.get(`/llm/market-history/${encodeURIComponent(signalSourceId)}`)
    return response.data
  },

  async getAIMarketSuggestions(signalSourceId) {
    const response = await api.get(`/llm/market-suggestions/${encodeURIComponent(signalSourceId)}`)
    return response.data
  },

  async getInstrumentObservations() {
    const response = await api.get('/admin/instrument-observations')
    return response.data
  },

  // 申请开通大模型行情分析
  async requestLLMAccess() {
    const response = await api.post('/llm/access/request')
    return response.data
  },

  // 管理员获取大模型开通申请待办
  async getLLMAccessRequests(status = 'pending') {
    const response = await api.get('/admin/llm/access-requests', {
      params: status ? { status } : {}
    })
    return response.data
  },

  // 管理员审批大模型开通申请
  async reviewLLMAccessRequest(requestId, decision, note = '') {
    const response = await api.post(
      `/admin/llm/access-requests/${requestId}/review`,
      { decision, note }
    )
    return response.data
  },

  // 手动触发大模型分析
  async triggerLLMAnalysis(accountId = null) {
    const response = await api.post('/llm/trigger', null, {
      params: accountId ? { account_id: accountId } : {},
    })
    return response.data
  },

  // 配置大模型参数
  async configureLLM(config) {
    const response = await api.post('/llm/configure', config)
    return response.data
  },

  async resetLLMPrompts() {
    const response = await api.post('/llm/config/reset-prompts')
    return response.data
  },

  // 获取已配置品种的K线数据状态
  async getConfiguredSymbols() {
    const response = await api.get('/market/configured_symbols')
    return response.data
  },

  // 获取系统运行日志
  async getSystemLogs(params = {}) {
    const response = await api.get('/system/logs', { params })
    return response.data
  },

  async getSystemLogSummary(params = {}) {
    const response = await api.get('/system/logs/summary', { params })
    return response.data
  },

  async purgeSystemLogs(before) {
    const response = await api.post('/admin/system/logs/purge', { before })
    return response.data
  },

  // ==================== 仓位管理 ====================

  // 获取持仓数据
  async getPositions(symbol = null, accountId = null) {
    const params = symbol ? { symbol } : {}
    if (accountId) params.account_id = accountId
    const response = await api.get('/positions', { params })
    return response.data
  },

  // 获取持仓汇总
  async getPositionsSummary(symbol = null, accountId = null) {
    const params = symbol ? { symbol } : {}
    if (accountId) params.account_id = accountId
    const response = await api.get('/positions/summary', { params })
    return response.data
  },

  async getPositionManagementEvents(symbol, ticket, accountId = null) {
    const response = await api.get(
      `/positions/${encodeURIComponent(symbol)}/${encodeURIComponent(ticket)}/management-events`,
      { params: accountId ? { account_id: accountId } : {} }
    )
    return response.data
  },

  // ==================== 交易历史 ====================

  // 获取交易历史
  async getTradeHistory(accountId = null) {
    const response = await api.get('/trade_history', {
      params: accountId ? { account_id: accountId } : {},
    })
    return response.data
  },

  // 获取交易历史统计
  async getTradeHistoryStatistics(accountId = null) {
    const response = await api.get('/trade_history/statistics', {
      params: accountId ? { account_id: accountId } : {},
    })
    return response.data
  },

  // ==================== 策略配置 ====================

  async getPositionManagementPolicies() {
    const response = await api.get('/position-management-policies')
    return response.data
  },

  async getSharedPositionManagementPolicies() {
    const response = await api.get('/position-management-policies/shared')
    return response.data
  },

  async useSharedPositionManagementPolicy(ownerUserId, policyId) {
    const response = await api.post(
      `/position-management-policies/shared/${encodeURIComponent(ownerUserId)}/${encodeURIComponent(policyId)}/use`
    )
    return response.data
  },

  async createPositionManagementPolicy(data) {
    const response = await api.post('/position-management-policies', data)
    return response.data
  },

  async updatePositionManagementPolicy(policyId, data) {
    try {
      const response = await api.put(
        `/position-management-policies/${encodeURIComponent(policyId)}`, data
      )
      return response.data
    } catch (error) {
      const detail = error?.response?.data?.detail
      if (detail?.impact && detail?.message && detail?.impact?.allowed && window.confirm(`${detail.message}\n\n${formatConfigurationImpact(detail.impact)}\n\n确认热加载修改吗？`)) {
        const response = await api.put(
          `/position-management-policies/${encodeURIComponent(policyId)}`,
          { ...data, _confirm_hot_reload: true },
        )
        return response.data
      }
      throw error
    }
  },

  async deletePositionManagementPolicy(policyId) {
    const response = await api.delete(
      `/position-management-policies/${encodeURIComponent(policyId)}`
    )
    return response.data
  },

  async copyPositionManagementPolicy(policyId) {
    const response = await api.post(
      `/position-management-policies/${encodeURIComponent(policyId)}/copy`
    )
    return response.data
  },

  // 获取所有策略配置
  async getStrategies() {
    const response = await api.get('/strategy')
    return response.data
  },

  // 获取平台共享策略库
  async getSharedStrategies() {
    const response = await api.get('/strategy/shared')
    return response.data
  },

  // 创建策略（同一品种可创建多条）
  async createStrategy(data) {
    const response = await api.post('/strategy', data)
    return response.data
  },

  // 使用平台共享策略；服务端创建不可编辑的安全引用
  async useSharedStrategy(ownerUserId, strategyId, data = {}) {
    const response = await api.post(
      `/strategy/shared/${encodeURIComponent(ownerUserId)}/${encodeURIComponent(strategyId)}/use`,
      data
    )
    return response.data
  },

  // 获取品种策略配置
  async getStrategy(symbol) {
    const response = await api.get(`/strategy/${encodeURIComponent(symbol)}`)
    return response.data
  },

  // 更新品种策略配置
  async updateStrategy(strategyId, data) {
    let response = await api.post(`/strategy/${encodeURIComponent(strategyId)}`, data)
    if (response.data?.code === 'hot_reload_confirmation_required' && response.data?.impact?.allowed && window.confirm(`${response.data.message}\n\n${formatConfigurationImpact(response.data.impact)}\n\n确认热加载修改吗？`)) {
      response = await api.post(`/strategy/${encodeURIComponent(strategyId)}`, { ...data, _confirm_hot_reload: true })
    }
    return response.data
  },

  async copyStrategy(strategyId) {
    const response = await api.post(`/strategy/${encodeURIComponent(strategyId)}/copy`)
    return response.data
  },

  // 转换策略生命周期
  async transitionStrategyLifecycle(strategyId, targetStatus, reason = '') {
    const response = await api.post(
      `/strategy/${encodeURIComponent(strategyId)}/lifecycle`,
      { target_status: targetStatus, reason }
    )
    return response.data
  },

  async getAdminStrategies() {
    const response = await api.get('/admin/strategies')
    return response.data
  },

  async getAdminStrategyDeployments(userId, strategyId) {
    const response = await api.get(
      `/admin/strategies/${encodeURIComponent(userId)}/${encodeURIComponent(strategyId)}/deployments`
    )
    return response.data
  },

  async adminTransitionStrategyLifecycle(userId, strategyId, targetStatus, reason = '') {
    const response = await api.post(
      `/admin/strategies/${encodeURIComponent(userId)}/${encodeURIComponent(strategyId)}/lifecycle`,
      { target_status: targetStatus, reason }
    )
    return response.data
  },

  async getStrategyAdmission() {
    const response = await api.get('/strategy-admission')
    return response.data
  },

  // 删除指定策略
  async deleteStrategy(strategyId) {
    const response = await api.delete(`/strategy/${encodeURIComponent(strategyId)}`)
    return response.data
  },

  // 获取决策历史
  async getDecisions(filters = {}, accountId = null) {
    const params = { count: filters.count || 50 }
    if (filters.symbol) params.symbol = filters.symbol
    if (filters.strategy_id) params.strategy_id = filters.strategy_id
    if (filters.status) params.status = filters.status
    if (filters.date_from) params.date_from = filters.date_from
    if (filters.date_to) params.date_to = filters.date_to
    if (accountId) params.account_id = accountId
    const response = await api.get('/strategy/decisions', { params })
    return response.data
  },

  async getStrategyExecutionOverview(strategyId, options = {}) {
    const params = { _ts: Date.now() }
    if (options.include_chart) params.include_chart = 'true'
    if (options.include_inactive) params.include_inactive = 'true'
    if (options.start_ts != null) params.start_ts = options.start_ts
    if (options.end_ts != null) params.end_ts = options.end_ts
    const response = await api.get(
      `/strategy/${encodeURIComponent(strategyId)}/execution-overview`,
      { params },
    )
    return response.data
  },

  async getStrategyAuditChain(strategyId, deploymentId, decisionId = null) {
    const params = { deployment_id: deploymentId, _ts: Date.now() }
    if (decisionId) params.decision_id = decisionId
    const response = await api.get(`/strategy/${encodeURIComponent(strategyId)}/audit-chain`, { params })
    return response.data
  },

  async reviewStrategyExecution(strategyId, deploymentId, hours = 24) {
    const response = await api.post(
      `/strategy/${encodeURIComponent(strategyId)}/ai-review`,
      { deployment_id: deploymentId, hours },
    )
    return response.data
  },

  async getStrategyReviewStatus(strategyId, jobId) {
    const response = await api.get(
      `/strategy/${encodeURIComponent(strategyId)}/ai-review/${encodeURIComponent(jobId)}`,
      { params: { _ts: Date.now() } },
    )
    return response.data
  },

  async applyStrategyReview(strategyId, payload) {
    const response = await api.post(
      `/strategy/${encodeURIComponent(strategyId)}/ai-review/apply`,
      payload,
    )
    return response.data
  },

  // 手动触发策略决策
  async triggerStrategyDecision(symbol) {
    const response = await api.post(`/strategy/trigger/${encodeURIComponent(symbol)}`)
    return response.data
  }
}

export default api

<template>
  <v-container fluid class="execution-page">
    <header class="page-header">
      <div>
        <div class="section-kicker">STRATEGY OPERATIONS</div>
        <h1>策略执行中心</h1>
        <p>{{ decisionFilters.strategy_id ? '查看该策略在全部账户中的部署与决策记录' : '按策略汇总模拟盘与实盘部署' }}</p>
      </div>
      <div class="header-summary">
        <span>{{ decisionFilters.strategy_id ? '运行部署' : '运行策略' }}</span>
        <strong>{{ runningDeploymentCount }}</strong>
        <div class="header-mini"><span>模拟盘</span><b>{{ activeDeployments.filter(item => item.execution_mode === 'paper').length }}</b><span>实盘</span><b>{{ activeDeployments.filter(item => item.execution_mode === 'live').length }}</b></div>
      </div>
    </header>

    <section v-if="decisionFilters.strategy_id" ref="focusedExecutionSection" class="focus-section">
      <div class="section-heading">
        <div>
          <div class="section-label">策略运行过程</div>
          <h2>{{ focusedStrategyName }}</h2>
        </div>
        <div class="d-flex align-center ga-2">
          <v-chip size="small" color="primary" variant="tonal">{{ focusedDeployments.length }} 个部署</v-chip>
          <v-btn size="small" variant="tonal" prepend-icon="mdi-refresh" :loading="loadingFocusedExecution" @click="loadFocusedExecution">刷新</v-btn>
          <v-btn icon="mdi-close" variant="text" size="small" title="关闭策略详情" @click="clearFocus" />
        </div>
      </div>
      <v-alert v-if="loadingFocusedExecution" type="info" variant="tonal" density="compact">正在加载该策略的运行记录。</v-alert>
      <v-alert v-else-if="!focusedDeployments.length" type="info" variant="tonal" density="compact">该策略尚未部署到模拟盘或实盘账户。</v-alert>
      <v-row v-else class="deployment-grid">
        <v-col v-for="deployment in focusedDeployments" :key="deployment.deployment_id" cols="12" lg="6">
          <article class="deployment-panel">
            <div class="deployment-heading">
              <div><strong>{{ deployment.account_name }}</strong><span>{{ deployment.symbol }}</span></div>
              <div class="deployment-actions"><v-btn size="small" color="primary" variant="outlined" prepend-icon="mdi-chart-candlestick" :to="{ path: '/strategy-replay', query: { strategy_id: decisionFilters.strategy_id, deployment_id: deployment.deployment_id } }">K线交易</v-btn><v-chip size="x-small" :color="deployment.execution_mode === 'paper' ? 'info' : 'success'" variant="tonal">{{ executionModeLabel(deployment.execution_mode) }}</v-chip><v-chip size="x-small" variant="outlined">{{ deployment.status === 'active' ? '运行中' : deployment.status }}</v-chip></div>
            </div>
            <div class="deployment-tools"><span>最近 {{ deployment.decisions.length }} 条策略决策</span></div>
            <p v-if="!deployment.decisions.length" class="empty-decision">该部署尚未生成决策记录。</p>
            <div v-else class="decision-list">
              <button v-for="decision in deployment.decisions" :key="decision.decision_id" class="decision-row" type="button" @click="openDecisionDetail(decision)">
                <span class="decision-marker" :class="decision.action || 'none'"></span>
                <span class="decision-main"><strong>{{ decision.action === 'buy' ? '买入' : decision.action === 'sell' ? '卖出' : '不执行' }}</strong><small>{{ decision.reason || '未提供决策理由' }}</small></span>
                <span class="decision-meta"><v-chip size="x-small" variant="outlined">{{ getDecisionStatusLabel(decision.status, decision.auto_executed, decision.execution_mode) }}</v-chip><v-chip v-if="decision.action === null && decision.observation_count > 1" size="x-small" color="info" variant="tonal">已聚合 {{ decision.observation_count }} 次未执行</v-chip><small>信号 {{ decision.signals?.length || 0 }} · {{ decision.confidence || 0 }}%</small><time>{{ formatTime(decision.timestamp) }}</time></span>
              </button>
            </div>
          </article>
        </v-col>
      </v-row>
    </section>

    <section class="strategy-section">
      <div class="section-heading"><div><div class="section-label">运行概览</div><h2>当前运行策略</h2></div><span class="section-note">{{ activeStrategyGroups.length }} 个策略</span></div>
      <div v-if="activeStrategyGroups.length" class="strategy-list">
        <article v-for="strategy in activeStrategyGroups" :key="strategy.strategy_id" class="strategy-row">
          <div class="strategy-identity"><strong>{{ strategy.strategy_name }}</strong><span>{{ strategy.symbol }}</span></div>
          <div class="deployment-tags"><span v-for="deployment in strategy.deployments" :key="deployment.deployment_id"><v-icon size="14" :color="deployment.execution_mode === 'paper' ? 'info' : 'success'">mdi-circle</v-icon>{{ deployment.account_name }}</span></div>
          <div class="strategy-action"><span>{{ strategy.deployments.length }} 个部署</span><v-btn size="small" color="primary" variant="tonal" @click="focusStrategy(strategy.strategy_id)">查看过程</v-btn></div>
        </article>
      </div>
      <v-alert v-else type="info" variant="tonal" density="compact">当前没有运行中的模拟盘或实盘策略。</v-alert>
    </section>

    <v-dialog v-model="decisionDetailDialog" max-width="860" scrollable>
      <v-card v-if="selectedDecision">
        <v-card-title class="d-flex align-center"><v-icon class="mr-2">mdi-file-search-outline</v-icon>{{ selectedDecision.strategy_name }} · 执行详情<v-spacer/><v-btn icon="mdi-close" variant="text" @click="decisionDetailDialog = false"/></v-card-title>
        <v-card-text class="decision-detail">
          <div class="detail-chips"><v-chip size="small" :color="selectedDecision.execution_mode === 'paper' ? 'info' : 'success'" variant="tonal">{{ executionModeLabel(selectedDecision.execution_mode) }}</v-chip><v-chip size="small" :color="selectedDecision.action === 'buy' ? 'success' : selectedDecision.action === 'sell' ? 'error' : 'info'" variant="tonal">{{ selectedDecision.action === 'buy' ? '买入' : selectedDecision.action === 'sell' ? '卖出' : '不执行' }}</v-chip><v-chip size="small" variant="outlined">{{ getDecisionStatusLabel(selectedDecision.status, selectedDecision.auto_executed, selectedDecision.execution_mode) }}</v-chip></div>
          <v-btn size="small" color="primary" variant="tonal" prepend-icon="mdi-link-variant" @click="openAuditChain(selectedDecision)">查看完整审计链</v-btn><h3>行情与信号</h3>
          <v-alert v-if="!selectedDecision.signals.length" type="info" variant="tonal">本次决策未保留可展示的信号明细。</v-alert>
          <v-list v-else density="compact" class="signal-detail-list"><v-list-item v-for="(signal, index) in selectedDecision.signals" :key="`${signal.signal_id || signal.signal_source_id || signal.source}-${index}`"><v-list-item-title>{{ formatSignalLabel(signal) }}</v-list-item-title><v-list-item-subtitle>{{ signal.trigger_reason || '未提供信号理由' }}</v-list-item-subtitle><template #append><span class="text-caption">{{ formatTradePrice(signal.suggested_entry || signal.trigger_price) }}</span></template></v-list-item></v-list>
          <section v-for="signal in selectedAIPlanSignals" :key="signal.signal_id || signal.ai_plan_id" class="ai-context-panel">
            <div class="ai-context-title"><strong>高周期背景与交易计划</strong><v-chip size="x-small" :color="planStatusColor(signal.ai_plan_status)" variant="tonal">{{ planStatusLabel(signal.ai_plan_status) }}</v-chip></div>
            <div v-if="Object.keys(signal.ai_background_analysis?.periods || {}).length" class="background-periods">
              <div v-for="(item, period) in signal.ai_background_analysis.periods" :key="period"><span>{{ period }}</span><strong>{{ backgroundStructureLabel(item.structure) }} · {{ item.confidence || 0 }}%</strong><small>{{ item.reason }}</small></div>
            </div>
            <v-alert v-else type="info" density="compact" variant="tonal">该历史决策尚未保存高周期背景；新生成的2.0分析会自动记录。</v-alert>
            <div class="combined-background"><span>综合背景</span><strong>{{ backgroundStructureLabel(signal.ai_background_analysis?.combined) }} · {{ signal.ai_background_analysis?.confidence || 0 }}%</strong><small>{{ signal.ai_background_analysis?.reason || '未提供综合背景理由' }}</small></div>
            <div class="plan-grid"><div><span>计划类型</span><strong>{{ setupTypeLabel(signal.ai_setup_type) }}</strong></div><div><span>触发方式</span><strong>{{ entryModeLabel(signal.ai_entry_mode) }}</strong></div><div><span>计划入场价</span><strong>{{ formatTradePrice(signal.ai_original_entry || signal.suggested_entry) }}</strong></div><div><span>止损 / 止盈</span><strong>{{ formatTradePrice(signal.suggested_sl) }} / {{ formatTradePrice(signal.suggested_tp) }}</strong></div><div><span>计划ID</span><strong>{{ signal.ai_plan_id || '--' }}</strong></div><div><span>有效期</span><strong>{{ formatEpochTime(signal.ai_plan_valid_from) }} ～ {{ formatEpochTime(signal.ai_plan_expires_at) }}</strong></div></div>
          </section>
          <h3>策略执行逻辑</h3>
          <div class="detail-grid"><div><span>策略</span><strong>{{ selectedDecision.strategy_name }}</strong></div><div><span>决策类型</span><strong>{{ decisionTypeLabel(selectedDecision.decision_type) }}</strong></div><div><span>参与信号</span><strong>{{ selectedDecision.signal_summary?.total_count ?? selectedDecision.signals.length }}</strong></div><div><span>最终置信度</span><strong>{{ selectedDecision.confidence || 0 }}%</strong></div></div>
          <p class="decision-reason">{{ selectedDecision.reason || '未提供策略决策理由' }}</p>
          <section v-if="selectedDecision.signal_summary?.position_management" class="ai-context-panel">
            <div class="ai-context-title"><strong>持仓管理场景匹配</strong><v-chip size="x-small" color="primary" variant="tonal">{{ selectedDecision.signal_summary.position_management.applied_setup_profile?.name || '默认方案' }}</v-chip></div>
            <div class="plan-grid"><div><span>Setup</span><strong>{{ setupTypeLabel(selectedDecision.signal_summary.position_management.setup_context?.setup_type) }}</strong></div><div><span>通用场景族</span><strong>{{ setupFamilyLabel(selectedDecision.signal_summary.position_management.setup_context?.setup_family) }}</strong></div><div><span>信号来源</span><strong>{{ selectedDecision.signal_summary.position_management.setup_context?.signal_source || '--' }}</strong></div><div><span>入场模式</span><strong>{{ entryModeLabel(selectedDecision.signal_summary.position_management.setup_context?.entry_mode) }}</strong></div></div>
            <div class="combined-background"><span>实际采用规则</span><strong>{{ (selectedDecision.signal_summary.position_management.explanation || []).join('；') || '使用默认持仓管理方案' }}</strong></div>
          </section>
          <v-alert v-if="selectedDecision.signal_summary?.loss_streak_guard?.loss_streak >= 2" :type="selectedDecision.signal_summary.loss_streak_guard.allowed ? 'info' : 'warning'" variant="tonal" class="mt-3">连续止损 {{ selectedDecision.signal_summary.loss_streak_guard.loss_streak }} 次：{{ selectedDecision.signal_summary.loss_streak_guard.reason || '冷却已结束，本次允许重新评估' }}</v-alert>
          <h3>执行结果</h3>
          <div class="detail-grid"><div><span>入场价</span><strong>{{ formatTradePrice(selectedDecision.price) }}</strong></div><div><span>止损 / 止盈</span><strong>{{ formatTradePrice(selectedDecision.sl) }} / {{ formatTradePrice(selectedDecision.tp) }}</strong></div><div><span>手数</span><strong>{{ selectedDecision.volume || '--' }}</strong></div><div><span>状态</span><strong>{{ getDecisionStatusLabel(selectedDecision.status, selectedDecision.auto_executed, selectedDecision.execution_mode) }}</strong></div></div>
          <v-alert v-if="selectedDecision.risk_warnings?.length" type="warning" variant="tonal" class="mt-4">{{ selectedDecision.risk_warnings.join('；') }}</v-alert>
        </v-card-text>
      </v-card>
    </v-dialog>

    <v-dialog v-model="auditChainDialog" max-width="900" scrollable><v-card><v-card-title>完整审计链 <v-spacer/><v-btn icon="mdi-close" variant="text" @click="auditChainDialog=false"/></v-card-title><v-card-text><v-alert v-if="auditChainLoading" type="info" variant="tonal">正在加载 AI、策略、订单和成交链路。</v-alert><div v-for="item in auditChainItems" :key="item.decision.decision_id" class="audit-chain-item"><h3>策略决策 · {{ formatTime(item.decision.timestamp || item.decision.created_at) }}</h3><p>{{ item.decision.reason || item.decision.decision_reason || '未提供决策理由' }}</p><div><strong>AI信号：</strong>{{ item.signals.length }} 条　<strong>订单：</strong>{{ item.orders.length }} 条　<strong>成交：</strong>{{ item.trades.length }} 条</div><pre>{{ JSON.stringify({ signals: item.signals, orders: item.orders, trades: item.trades }, null, 2) }}</pre></div><v-alert v-if="!auditChainLoading && !auditChainItems.length" type="info" variant="tonal">暂无关联审计记录。</v-alert></v-card-text></v-card></v-dialog>
    <!-- 错误提示 -->
    <v-snackbar v-model="showError" color="error" timeout="5000">
      {{ errorMessage }}
    </v-snackbar>
  </v-container>
</template>

<script>
import { ref, reactive, computed, nextTick, onMounted, onBeforeUnmount, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { marketAPI } from '@/api/market'
import { useAccountContext } from '@/composables/useAccountContext'
import {
  formatTradePrice,
  normalizeTradingDecision,
} from '@/utils/trading-decision'

export default {
  name: 'Market',
  setup() {
    // 数据
    const decisionAlerts = ref([])  // 统一的决策提醒列表
    const expandedSignals = ref(new Set())  // 展开信号的状态
    const route = useRoute()
    const router = useRouter()
    const decisionDetailDialog = ref(false)
    const selectedDecision = ref(null)
    const selectedAIPlanSignals = computed(() => (
      Array.isArray(selectedDecision.value?.signals)
        ? selectedDecision.value.signals.filter(item => item?.source === 'ai_entry')
        : []
    ))
    const auditChainDialog = ref(false)
    const auditChainLoading = ref(false)
    const auditChainItems = ref([])
    const showError = ref(false)
    const errorMessage = ref('')

    const loadingDecisions = ref(false)
    const loadingFocusedExecution = ref(false)
    const focusedExecution = ref(null)
    const focusedExecutionSection = ref(null)
    let focusedRefreshTimer = null
    const decisionFilters = reactive({
      strategy_id: route.query.strategy_id || null,
      status: null,
      date_from: '',
      date_to: '',
    })
    const decisionStatusOptions = [
      { title: '待确认', value: 'pending' },
      { title: '已确认', value: 'confirmed' },
      { title: '风控/人工拒绝', value: 'rejected' },
      { title: '已过期', value: 'expired' },
    ]

    const { accounts, loadAccountContext } = useAccountContext()

    const accountList = computed(() => (Array.isArray(accounts.value) ? accounts.value : []))
    const activeDeployments = computed(() => accountList.value.flatMap(account => (
      (Array.isArray(account?.deployments) ? account.deployments : [])
        .filter(item => (
          item
          && ['paper', 'live'].includes(item.execution_mode)
          && item.status === 'active'
          && item.runtime_active !== false
          && account.status === 'active'
          && account.enabled !== false
          && account.trading_enabled !== false
          && account.auto_trading_enabled !== false
          && (item.execution_mode === 'paper' || account.connected === true)
        ))
        .map(item => ({
          ...item,
          account_id: account.account_id,
          account_name: account.account_name,
          account_type: account.account_type,
        }))
    )))
    const activeStrategyGroups = computed(() => {
      const groups = new Map()
      for (const deployment of activeDeployments.value.filter(Boolean)) {
        const strategyId = deployment.strategy_id
        if (!groups.has(strategyId)) {
          groups.set(strategyId, {
            strategy_id: strategyId,
            strategy_name: deployment.strategy_name || strategyId,
            symbol: deployment.symbol,
            deployments: [],
          })
        }
        groups.get(strategyId).deployments.push(deployment)
      }
      return [...groups.values()].sort((left, right) => (
        left.strategy_name.localeCompare(right.strategy_name, 'zh-CN')
      ))
    })
    const focusedDeployments = computed(() => Array.isArray(focusedExecution.value?.deployments) ? focusedExecution.value.deployments : [])
    const runningDeploymentCount = computed(() => (
      decisionFilters.strategy_id
        ? focusedDeployments.value.filter(item => item?.status === 'active').length
        : activeDeployments.value.length
    ))
    const focusedStrategyName = computed(() => (
      focusedExecution.value?.strategy?.strategy_name
      || (Array.isArray(strategyFilterOptions.value) ? strategyFilterOptions.value : []).find(item => item.value === decisionFilters.strategy_id)?.title
      || decisionFilters.strategy_id
    ))
    const strategyFilterOptions = computed(() => activeStrategyGroups.value.map(item => ({
      title: `${item.strategy_name} · ${item.symbol}`,
      value: item.strategy_id,
    })))
    const loadDecisions = async () => {
      loadingDecisions.value = true
      try {
        const data = await marketAPI.getDecisions({
          ...decisionFilters,
          count: 50,
        })
        decisionAlerts.value = (data.decisions || []).map(normalizeTradingDecision)
      } catch (err) {
        console.error('加载策略决策历史失败:', err)
      } finally {
        loadingDecisions.value = false
      }
    }

    const loadFocusedExecution = async () => {
      const strategyId = decisionFilters.strategy_id
      if (!strategyId) {
        focusedExecution.value = null
        return
      }
      loadingFocusedExecution.value = true
      try {
      // 详情页需要展示策略的完整部署关系，即使账户暂时离线、被暂停或
      // 风控开关关闭。运行资格由 deployment.runtime_active/status 单独标记，
      // 不应因为运行态检查失败而把真实存在的部署误显示为“0 个部署”。
      const data = await marketAPI.getStrategyExecutionOverview(strategyId, {
        include_inactive: true,
      })
        const deployments = await Promise.all(
          (Array.isArray(data.deployments) ? data.deployments : [])
            .filter(Boolean)
            .map(async deployment => {
              const chart = deployment.chart || {}
              // 策略执行中心首屏不请求 K 线；只有 K 线交易回放接口返回
              // chart 时才加载图表数据。
              if (!deployment.chart) {
                return {
                  ...deployment,
                  decisions: (Array.isArray(deployment.decisions) ? deployment.decisions : [])
                    .filter(Boolean)
                    .map(normalizeTradingDecision),
                }
              }
              let bars = Array.isArray(chart.bars) ? chart.bars : []
              const symbol = chart.symbol || deployment.symbol || data.strategy?.symbol || ''
              const period = chart.period || 'M5'
              if (!bars.length && symbol) {
                try {
                  const klineData = await marketAPI.getKlines(symbol, period, 288)
                  bars = Array.isArray(klineData?.data) ? klineData.data : []
                } catch (klineError) {
                  console.warn('加载策略部署K线失败:', symbol, klineError)
                }
              }
              return {
                ...deployment,
                chart: { ...chart, symbol, period, bars, events: Array.isArray(chart.events) ? chart.events : [] },
                decisions: (Array.isArray(deployment.decisions) ? deployment.decisions : [])
                  .filter(Boolean)
                  .map(normalizeTradingDecision),
              }
            }),
        )
        focusedExecution.value = { ...data, deployments }
      } catch (err) {
        focusedExecution.value = null
        console.error('加载策略部署运行记录失败:', err)
      } finally {
        loadingFocusedExecution.value = false
      }
    }

    const getDecisionStatusColor = (status) => ({
      pending: 'warning',
      confirmed: 'success',
      rejected: 'error',
      expired: 'grey',
      skipped: 'info',
    }[status] || 'info')

    const getDecisionStatusLabel = (status, autoExecuted = false, executionMode = 'live') => {
      if (status === 'confirmed') return autoExecuted ? '已自动执行' : '已确认执行'
      if (status === 'pending' && executionMode === 'paper') return '等待模拟撮合'
      return {
        pending: '等待确认',
        rejected: '已拒绝',
        expired: '已过期',
        skipped: '未执行',
      }[status] || status || '未知状态'
    }

    const executionModeLabel = (mode) => mode === 'paper' ? '模拟盘' : '实盘'
    const decisionTypeLabel = (value) => ({
      ai_plan_evaluation: 'AI 计划评估',
      no_action: 'Tick 等待评估',
      signal_combined: '信号聚合决策',
      single_signal: '单信号决策',
    }[value] || value || '--')
    const backgroundStructureLabel = value => ({
      uptrend: '上涨趋势', downtrend: '下跌趋势', range: '箱体震荡',
      converging_triangle: '收敛三角形', diverging_triangle: '扩散三角形',
      uptrend_with_local_range: '长周期上涨＋局部箱体',
      downtrend_with_local_range: '长周期下跌＋局部箱体',
      conflict: '周期冲突', mixed: '结构不明确',
    }[value] || value || '--')
    const setupTypeLabel = value => ({
      range_reversal: '箱体边界反转', range_breakout: '箱体突破',
      trend_pullback: '上涨趋势回调', trend_rebound: '下跌趋势反抽',
      triangle_breakout: '三角形突破', pivot_reversal: '转折点反转',
      pivot_breakout: '转折点突破', key_level_reversal: '关键位反转',
      key_level_breakout: '关键位突破', ma_crossover: '均线交叉',
      factor_entry: '因子入场', manual_entry: '手工入场', generic_entry: '通用入场',
    }[value] || value || '--')
    const setupFamilyLabel = value => ({
      reversal: '反转', breakout: '突破', trend_follow: '趋势跟随',
      pullback: '趋势回调', mean_reversion: '均值回归', factor: '因子',
      manual: '手工', generic: '通用兜底',
    }[value] || value || '--')
    const entryModeLabel = value => ({ touch_or_near: '触价或接近', breakout: '突破确认' }[value] || value || '--')
    const planStatusLabel = value => ({ active: '等待触价', pending_confirmation: '等待确认', triggered: '已触发', expired: '已过期', invalidated: '已失效' }[value] || value || '--')
    const planStatusColor = value => ({ active: 'primary', pending_confirmation: 'warning', triggered: 'success', expired: 'grey', invalidated: 'error' }[value] || 'grey')
    const formatEpochTime = value => value ? new Date(Number(value) * 1000).toLocaleString('zh-CN') : '--'

    const openDecisionDetail = (decision) => {
      selectedDecision.value = decision
      decisionDetailDialog.value = true
    }

    const openAuditChain = async (decision) => {
      auditChainDialog.value = true
      auditChainLoading.value = true
      auditChainItems.value = []
      try {
        const deploymentId = focusedDeployments.value.find(item => (item.decisions || []).some(row => row.decision_id === decision.decision_id))?.deployment_id
        const data = await marketAPI.getStrategyAuditChain(decision.strategy_id, deploymentId, decision.decision_id)
        auditChainItems.value = data.items || []
      } catch (err) {
        console.error('加载完整审计链失败:', err)
      } finally { auditChainLoading.value = false }
    }

    const focusStrategy = async (strategyId) => {
      await router.replace({ query: { ...route.query, strategy_id: strategyId } })
      await nextTick()
      focusedExecutionSection.value?.scrollIntoView({ behavior: 'smooth', block: 'start' })
    }

    const clearFocus = () => {
      const query = { ...route.query }
      delete query.strategy_id
      router.replace({ query })
    }

    // 辅助方法
    const getSignalSourceColor = (source) => {
      const colors = {
        'pivot': 'primary',
        'key_level': 'success',
        'ai_entry': 'info',
        'moving_average': 'warning'
      }
      return colors[source] || 'grey'
    }

    // 格式化信号标签（显示周期）
    const formatSignalLabel = (signal) => {
      const source = signal.source || ''
      const period = signal.source_period || signal.ai_analysis_period || ''
      const confidence = signal.confidence || 0
      const direction = {
        up: '上升', sideways: '震荡', down: '下降'
      }[signal.market_direction] || '未就绪'

      // 显示信号源名称
      const sourceNames = {
        'pivot': 'Pivot',
        'key_level': 'KeyLevel',
        'ai_entry': 'AI',
        'moving_average': '均线',
        'alpha_factor': 'Alpha'
      }
      const sourceName = sourceNames[source] || source

      // 如果有周期，显示周期
      if (period) {
        return `${sourceName}[${period}] ${direction} (${confidence}%)`
      }
      return `${sourceName} ${direction} (${confidence}%)`
    }

    // 获取可见的信号列表
    const getVisibleSignals = (alert) => {
      if (!alert.signals) return []
      const alertIndex = decisionAlerts.value.indexOf(alert)
      if (expandedSignals.value.has(alertIndex)) {
        return alert.signals
      }
      return alert.signals.slice(0, 3)
    }

    // 切换信号展开状态
    const toggleSignalExpand = (index) => {
      if (expandedSignals.value.has(index)) {
        expandedSignals.value.delete(index)
      } else {
        expandedSignals.value.add(index)
      }
      // 触发响应式更新
      expandedSignals.value = new Set(expandedSignals.value)
    }

    // 检查信号是否展开
    const isSignalExpanded = (index) => {
      return expandedSignals.value.has(index)
    }

    const formatTime = (timestamp) => {
      if (!timestamp) return ''
      const date = new Date(timestamp)
      return date.toLocaleString('zh-CN')
    }

    // 生命周期
    onMounted(async () => {
      await loadAccountContext()
      await loadFocusedExecution()
      // 部署状态可能在页面打开后才由后台热加载/部署接口更新，定期刷新详情避免停留在“0 个部署”的旧状态。
      focusedRefreshTimer = window.setInterval(() => {
        if (decisionFilters.strategy_id && !loadingFocusedExecution.value) {
          loadFocusedExecution()
        }
      }, 15000)
    })

    onBeforeUnmount(() => {
      if (focusedRefreshTimer) {
        window.clearInterval(focusedRefreshTimer)
        focusedRefreshTimer = null
      }
    })

    watch(() => route.query.strategy_id, async (strategyId) => {
      decisionFilters.strategy_id = strategyId || null
      await loadFocusedExecution()
    })

    return {
      decisionAlerts,
      route,
      decisionDetailDialog,
      selectedDecision,
      selectedAIPlanSignals,
      auditChainDialog,
      auditChainLoading,
      auditChainItems,
      showError,
      errorMessage,
      activeDeployments,
      activeStrategyGroups,
      runningDeploymentCount,
      focusedDeployments,
      focusedStrategyName,
      focusedExecutionSection,
      loadingFocusedExecution,
      strategyFilterOptions,
      decisionFilters,
      decisionStatusOptions,
      loadingDecisions,
      loadDecisions,
      loadFocusedExecution,
      getDecisionStatusColor,
      getDecisionStatusLabel,
      executionModeLabel,
      decisionTypeLabel,
      backgroundStructureLabel,
      setupTypeLabel,
      setupFamilyLabel,
      entryModeLabel,
      planStatusLabel,
      planStatusColor,
      formatEpochTime,
      openDecisionDetail,
      openAuditChain,
      focusStrategy,
      clearFocus,
      getSignalSourceColor,
      formatTime,
      formatTradePrice,
      // 信号显示
      formatSignalLabel,
      getVisibleSignals,
      toggleSignalExpand,
      isSignalExpanded,
    }
  }
}
</script>

<style scoped>
.execution-page {
  max-width: 1520px;
  margin: 0 auto;
  padding: 28px 32px 48px;
}

.page-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 24px;
  min-height: 142px;
  padding: 24px 28px;
  border: 1px solid #d8e5de;
  border-radius: 16px;
  background: linear-gradient(120deg, #f4faf6 0%, #eef7f2 62%, #fbf5e9 100%);
  box-shadow: 0 8px 24px rgba(42, 92, 72, .06);
}

.page-header h1,
.section-heading h2 {
  margin: 0;
  color: #172b31;
  font-weight: 700;
  letter-spacing: 0;
}

.page-header h1 { font-size: 1.85rem; }
.section-heading h2 { font-size: 1.2rem; }

.page-header p {
  margin: 7px 0 0;
  color: #68777c;
  font-size: .9rem;
}

.section-kicker {
  color: #247d68;
  font-size: 0.72rem;
  font-weight: 700;
  letter-spacing: 0;
}

.header-summary {
  display: flex;
  min-width: 170px;
  flex-direction: column;
  align-items: flex-end;
  gap: 8px;
  padding-left: 24px;
  border-left: 1px solid #c8ddd2;
}

.header-summary span,
.section-label,
.section-note {
  color: #748187;
  font-size: .75rem;
  letter-spacing: 0;
}

.header-summary strong {
  color: #1f6454;
  font-size: 2rem;
  line-height: 1;
}

.header-mini { display: flex; align-items: center; gap: 6px; color: #718078; font-size: .72rem; }
.header-mini b { color: #31564b; font-size: .85rem; }

.focus-section,
.strategy-section {
  margin-top: 28px;
}

.section-heading {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  margin-bottom: 14px;
}

.deployment-grid { margin-top: -12px; }

.deployment-panel,
.strategy-list {
  border: 1px solid #dbe4e2;
  border-radius: 8px;
  background: #fff;
}

.deployment-panel { overflow: hidden; }

.deployment-heading {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  min-height: 58px;
  padding: 12px 16px;
  border-bottom: 1px solid #e5ecea;
  background: #f7faf9;
}

.deployment-heading > div:first-child {
  display: flex;
  flex-direction: column;
  min-width: 0;
}

.deployment-actions {
  display: flex;
  align-items: center;
  justify-content: flex-end;
  flex-wrap: wrap;
  gap: 8px;
}

.deployment-heading strong { color: #203239; font-size: .92rem; }
.deployment-heading span { color: #748187; font-size: .75rem; margin-top: 2px; }
.empty-decision { margin: 0; padding: 24px 16px; color: #7a878c; font-size: .84rem; }

.decision-list { display: flex; flex-direction: column; }

.decision-row {
  display: grid;
  grid-template-columns: 8px minmax(0, 1fr) auto;
  gap: 11px;
  width: 100%;
  align-items: center;
  padding: 12px 16px;
  border-bottom: 1px solid #edf1f0;
  background: transparent;
  color: inherit;
  cursor: pointer;
  text-align: left;
}

.decision-row:last-child { border-bottom: 0; }
.decision-row:hover { background: #f4f9f7; }
.decision-marker { width: 8px; height: 8px; border-radius: 50%; background: #8ea0a5; }
.decision-marker.buy { background: #2f9c72; }
.decision-marker.sell { background: #d65d50; }
.decision-main { display: flex; min-width: 0; flex-direction: column; gap: 3px; }
.decision-main strong { color: #26383e; font-size: .84rem; font-weight: 700; }
.decision-main small { overflow: hidden; color: #69777d; font-size: .76rem; text-overflow: ellipsis; white-space: nowrap; }
.decision-meta { display: flex; min-width: 150px; flex-direction: column; align-items: flex-end; gap: 3px; color: #76858a; font-size: .72rem; }
.decision-meta small, .decision-meta time { font-size: .7rem; white-space: nowrap; }

.strategy-list { overflow: hidden; }
.strategy-row {
  display: grid;
  grid-template-columns: minmax(210px, .8fr) minmax(300px, 1.4fr) auto;
  gap: 20px;
  align-items: center;
  min-height: 76px;
  padding: 13px 16px;
  border-bottom: 1px solid #e8efed;
}
.strategy-row:last-child { border-bottom: 0; }
.strategy-identity { display: flex; min-width: 0; flex-direction: column; gap: 3px; }
.strategy-identity strong { overflow: hidden; color: #24363c; font-size: .92rem; text-overflow: ellipsis; white-space: nowrap; }
.strategy-identity span { color: #6a7a80; font-size: .76rem; }
.deployment-tags { display: flex; flex-wrap: wrap; gap: 8px 14px; }
.deployment-tags span { display: inline-flex; align-items: center; gap: 5px; color: #52636a; font-size: .8rem; }
.strategy-action { display: flex; align-items: center; justify-content: flex-end; gap: 12px; color: #68777c; font-size: .76rem; white-space: nowrap; }

.decision-detail h3 {
  margin: 22px 0 10px;
  color: #29493e;
  font-size: 1rem;
}

.detail-chips {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}

.signal-detail-list {
  border: 1px solid #dce7e0;
  border-radius: 8px;
}

.detail-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 8px;
}

.detail-grid div {
  display: flex;
  flex-direction: column;
  padding: 10px;
  border-radius: 6px;
  background: #f1f6f3;
}

.detail-grid span {
  color: #718078;
  font-size: .75rem;
}

.decision-reason {
  margin: 12px 0 0;
  color: #52635e;
}

.ai-context-panel { margin-top: 14px; padding: 14px; border: 1px solid #cfe1d8; border-radius: 10px; background: #f8fbf9; }
.ai-context-title { display: flex; align-items: center; justify-content: space-between; gap: 12px; color: #244f41; }
.background-periods { display: grid; grid-template-columns: repeat(2,minmax(0,1fr)); gap: 8px; margin-top: 12px; }
.background-periods>div,.combined-background { display: flex; flex-direction: column; gap: 3px; padding: 10px; border-radius: 7px; background: #edf5f1; }
.background-periods span,.combined-background span,.plan-grid span { color: #728179; font-size: .72rem; }
.background-periods small,.combined-background small { color: #61716a; line-height: 1.4; }
.combined-background { margin-top: 8px; border-left: 3px solid #3b8a70; }
.plan-grid { display: grid; grid-template-columns: repeat(2,minmax(0,1fr)); gap: 8px; margin-top: 10px; }
.plan-grid>div { display: flex; min-width: 0; flex-direction: column; padding: 9px 10px; border: 1px solid #e0e9e4; border-radius: 7px; background: #fff; }
.plan-grid strong { overflow-wrap: anywhere; color: #334a42; font-size: .82rem; }

@media (max-width: 960px) {
  .execution-page { padding: 22px 16px 36px; }
  .strategy-row { grid-template-columns: 1fr; gap: 10px; align-items: start; }
  .strategy-action { justify-content: space-between; }
}

@media (max-width: 600px) {
  .page-header, .deployment-heading { align-items: flex-start; flex-direction: column; }
  .header-summary { align-items: flex-start; padding: 8px 0 0 12px; border-left: 0; }
  .decision-row { grid-template-columns: 8px minmax(0, 1fr); }
  .decision-meta { grid-column: 2; align-items: flex-start; min-width: 0; }
  .background-periods,.plan-grid,.detail-grid { grid-template-columns: 1fr; }
}
.signal-detail-list :deep(.v-list-item){align-items:flex-start;min-height:64px;padding-top:10px;padding-bottom:10px}.signal-detail-list :deep(.v-list-item-title){overflow:visible;white-space:normal;line-height:1.35;word-break:break-word}.signal-detail-list :deep(.v-list-item-subtitle){display:block;overflow:visible;white-space:normal;line-height:1.5;word-break:break-word;color:#69777d!important}.signal-detail-list :deep(.v-list-item__append){align-self:flex-start;padding-top:2px;white-space:nowrap}
</style>

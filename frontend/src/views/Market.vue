<template>
  <v-container fluid>
    <section class="execution-hero mb-5">
      <div>
        <div class="section-kicker">LIVE STRATEGY OPERATIONS</div>
        <h1>策略执行中心</h1>
        <p>
          {{ selectedAccount?.account_name || '当前账户' }} · 实时跟踪策略决策与账户自动执行
        </p>
      </div>
      <v-chip :color="wsConnected ? 'success' : 'error'" variant="flat">
        <v-icon start>mdi-lan-connect</v-icon>
        {{ wsConnected ? '实时通道已连接' : '实时通道断开' }}
      </v-chip>
    </section>

    <v-row class="mb-2">
      <v-col cols="12" sm="4">
        <v-card class="metric-card" variant="tonal" color="primary">
          <v-card-text><span>运行策略</span><strong>{{ activeDeployments.length }}</strong></v-card-text>
        </v-card>
      </v-col>
      <v-col cols="12" sm="4">
        <v-card class="metric-card" variant="tonal" color="warning">
          <v-card-text><span>待处理决策</span><strong>{{ pendingOrders.length }}</strong></v-card-text>
        </v-card>
      </v-col>
      <v-col cols="12" sm="4">
        <v-card class="metric-card" variant="tonal" color="info">
          <v-card-text><span>已记录决策</span><strong>{{ decisionAlerts.length }}</strong></v-card-text>
        </v-card>
      </v-col>
    </v-row>

    <v-card class="mb-4">
      <v-card-title class="d-flex align-center">
        <v-icon class="mr-2">mdi-play-circle-outline</v-icon>
        当前运行策略
      </v-card-title>
      <v-card-text>
        <div v-if="activeDeployments.length" class="d-flex flex-wrap ga-2">
          <v-chip
            v-for="deployment in activeDeployments"
            :key="deployment.deployment_id"
            color="success"
            variant="tonal"
          >
            {{ deployment.strategy_name || deployment.strategy_id }} · {{ deployment.symbol }}
          </v-chip>
        </div>
        <v-alert v-else type="info" variant="tonal" density="compact">
          当前账户尚未绑定运行中的实盘策略
        </v-alert>
      </v-card-text>
    </v-card>

    <v-card class="mb-4 pending-card">
      <v-card-title class="d-flex align-center">
        <v-icon class="mr-2" color="warning">mdi-inbox-arrow-down-outline</v-icon>
        待处理决策
        <v-chip size="small" color="warning" class="ml-2">{{ pendingOrders.length }}</v-chip>
      </v-card-title>
      <v-card-text>
        <v-alert v-if="!pendingOrders.length" type="success" variant="tonal" density="compact">
          当前没有需要人工处理的策略决策
        </v-alert>
        <v-row v-else>
          <v-col v-for="order in pendingOrders" :key="order.order_id" cols="12">
            <div class="pending-order-row">
              <div class="pending-order-main">
                <div class="d-flex flex-wrap align-center ga-2 mb-2">
                  <strong>{{ order.symbol }}</strong>
                  <v-chip size="small" :color="order.action === 'b' ? 'success' : 'error'">
                    {{ order.action === 'b' ? '买入' : '卖出' }}
                  </v-chip>
                  <v-chip size="small" variant="outlined">
                    {{ order.strategy_name || order.strategy_id || '策略决策' }}
                  </v-chip>
                </div>
                <div class="text-caption text-medium-emphasis">{{ order.reason }}</div>
              </div>
              <div class="pending-order-fields">
                <v-text-field v-model.number="order.mount" label="手数" type="number" step="0.01" min="0.01" density="compact" hide-details variant="outlined" />
                <v-text-field v-model.number="order.sl" label="止损" type="number" density="compact" hide-details variant="outlined" />
                <v-text-field v-model.number="order.tp" label="止盈" type="number" density="compact" hide-details variant="outlined" />
              </div>
              <div class="pending-order-actions">
                <v-btn color="success" :loading="confirmingOrderId === order.order_id" @click="confirmPendingOrder(order)">确认执行</v-btn>
                <v-btn variant="outlined" color="error" :loading="rejectingOrderId === order.order_id" @click="rejectPendingOrder(order.order_id)">放弃</v-btn>
              </div>
            </div>
          </v-col>
        </v-row>
      </v-card-text>
    </v-card>

    <v-card class="mb-4">
      <v-card-title class="d-flex align-center flex-wrap ga-2">
        <v-icon class="mr-1">mdi-history</v-icon>
        最新策略决策
        <v-spacer />
        <v-select v-model="decisionFilters.strategy_id" :items="strategyFilterOptions" label="策略" clearable density="compact" hide-details variant="outlined" class="decision-filter" />
        <v-select v-model="decisionFilters.status" :items="decisionStatusOptions" label="状态" clearable density="compact" hide-details variant="outlined" class="decision-filter" />
        <v-text-field v-model="decisionFilters.date_from" label="开始时间" type="datetime-local" density="compact" hide-details variant="outlined" class="decision-time-filter" />
        <v-text-field v-model="decisionFilters.date_to" label="结束时间" type="datetime-local" density="compact" hide-details variant="outlined" class="decision-time-filter" />
        <v-btn color="primary" variant="tonal" :loading="loadingDecisions" @click="loadDecisions">查询</v-btn>
      </v-card-title>
      <v-card-text v-if="!decisionAlerts.length" class="text-center text-medium-emphasis py-8">
        暂无符合条件的策略决策
      </v-card-text>
    </v-card>

    <!-- 交易决策提醒 -->
    <v-row v-if="decisionAlerts.length > 0">
      <v-col cols="12">
        <v-alert
          v-for="(alert, index) in decisionAlerts"
          :key="alert.decision_id || index"
          :type="alert.rejected ? 'warning' : alert.action === 'buy' ? 'success' : 'error'"
          class="mb-2"
        >
          <div class="d-flex flex-wrap align-center">
            <v-icon small class="mr-1">mdi-chart-line</v-icon>
            <strong>{{ alert.symbol }}</strong>
            <v-chip small outlined color="blue-grey" class="ml-2">
              <v-icon small left>mdi-strategy</v-icon>
              {{ alert.strategy_name }} · {{ alert.strategy_id }}
            </v-chip>
            <v-chip
              small
              :color="alert.action === 'buy' ? 'success' : alert.action === 'sell' ? 'error' : 'grey'"
              class="ml-2"
            >
              <v-icon small left>
                {{ alert.action === 'buy' ? 'mdi-arrow-up-bold' : alert.action === 'sell' ? 'mdi-arrow-down-bold' : 'mdi-help' }}
              </v-icon>
              {{ alert.action === 'buy' ? '买入' : alert.action === 'sell' ? '卖出' : '方向未知' }}
            </v-chip>
            <v-chip v-if="alert.rejected" small outlined color="warning" class="ml-2">
              <v-icon small left>mdi-shield-alert</v-icon>
              风控拦截
            </v-chip>
            <v-chip v-else-if="alert.auto_executed" small color="warning" class="ml-2">
              <v-icon small left>mdi-robot</v-icon>
              已自动下单
            </v-chip>
            <v-chip v-if="alert.confidence" small color="primary" class="ml-2">
              置信度: {{ alert.confidence }}%
            </v-chip>
          </div>

          <div class="mt-2">
            <span class="text-caption mr-4">入场价: <strong>{{ formatTradePrice(alert.price) }}</strong></span>
            <span class="text-caption mr-4">止损: <strong>{{ formatTradePrice(alert.sl) }}</strong></span>
            <span class="text-caption mr-4">止盈: <strong>{{ formatTradePrice(alert.tp) }}</strong></span>
            <span v-if="alert.risk_reward_ratio" class="text-caption">
              盈亏比: <strong>{{ alert.risk_reward_ratio }}</strong>
            </span>
          </div>

          <!-- 信号来源 -->
          <div v-if="alert.signals && alert.signals.length > 0" class="mt-2">
            <span class="text-caption mr-2">信号来源:</span>
            <v-chip
              v-for="(signal, sIdx) in getVisibleSignals(alert)"
              :key="sIdx"
              size="x-small"
              class="mr-1"
              :color="getSignalSourceColor(signal.source)"
            >
              {{ formatSignalLabel(signal) }}
            </v-chip>
            <v-btn
              v-if="alert.signals.length > 3"
              size="x-small"
              variant="text"
              class="ml-1"
              @click="toggleSignalExpand(index)"
            >
              {{ isSignalExpanded(index) ? '收起' : `+${alert.signals.length - 3} 更多` }}
              <v-icon end small>{{ isSignalExpanded(index) ? 'mdi-chevron-up' : 'mdi-chevron-down' }}</v-icon>
            </v-btn>
          </div>

          <div v-if="alert.rejected" class="mt-3 pa-2 amber lighten-5 rounded">
            <div class="text-subtitle-2 font-weight-bold mb-1">
              <v-icon small color="warning" class="mr-1">mdi-shield-alert</v-icon>
              当前信号不可确认
            </div>
            <div class="text-caption grey--text text--darken-1">
              {{
                alert.risk_warnings.length
                  ? alert.risk_warnings.join('；')
                  : alert.reason
              }}
            </div>
          </div>

          <div class="mt-2 d-flex align-center ga-2">
            <v-chip size="small" :color="getDecisionStatusColor(alert.status)" variant="tonal">
              {{ getDecisionStatusLabel(alert.status, alert.auto_executed) }}
            </v-chip>
            <span class="text-caption text-medium-emphasis">{{ formatTime(alert.timestamp) }}</span>
          </div>
        </v-alert>
      </v-col>
    </v-row>

    <!-- 错误提示 -->
    <v-snackbar v-model="showError" color="error" timeout="5000">
      {{ errorMessage }}
    </v-snackbar>
  </v-container>
</template>

<script>
import { ref, reactive, computed, onMounted, onUnmounted, watch } from 'vue'
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
    const showError = ref(false)
    const errorMessage = ref('')

    const pendingOrders = ref([])
    const loadingDecisions = ref(false)
    const decisionFilters = reactive({
      strategy_id: null,
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

    // 订单确认/放弃状态
    const confirmingOrderId = ref(null)
    const rejectingOrderId = ref(null)

    // WebSocket
    const ws = ref(null)
    const wsConnected = ref(false)
    let wsReconnectTimer = null
    let statusInterval = null
    let isUnmounted = false
    const { selectedAccountId, selectedAccount } = useAccountContext()

    const activeDeployments = computed(() => (
      selectedAccount.value?.deployments || []
    ).filter(item => item.execution_mode === 'live' && item.status === 'active'))
    const strategyFilterOptions = computed(() => activeDeployments.value.map(item => ({
      title: `${item.strategy_name || item.strategy_id} · ${item.symbol}`,
      value: item.strategy_id,
    })))
    const connectWebSocket = () => {
      if (
        isUnmounted ||
        !selectedAccountId.value ||
        ws.value?.readyState === WebSocket.OPEN ||
        ws.value?.readyState === WebSocket.CONNECTING
      ) {
        return
      }
      const accountId = selectedAccountId.value
      const socket = marketAPI.createWebSocket(
        // onMessage
        (data) => {
          if (data.type === 'trading_decision') {
            // 策略层产生的交易决策
            console.log('收到交易决策:', data)
            const decision = data.data
            if (
              decisionAlerts.value.some(
                (alert) => alert.decision_id === decision.decision_id
              )
            ) {
              return
            }
            const alert = normalizeTradingDecision(decision)
            decisionAlerts.value.unshift(alert)
            if (decisionAlerts.value.length > 50) {
              decisionAlerts.value.pop()
            }
            loadPendingOrders()
          } else if (data.type === 'pending_order') {
            // 订单状态更新（确认后）
            console.log('收到订单更新:', data)
            const order = data.data
            // 更新对应的 alert
            const alertIndex = decisionAlerts.value.findIndex(
              a => a.pending_order?.order_id === order.order_id
            )
            if (alertIndex >= 0) {
              decisionAlerts.value[alertIndex].status = 'confirmed'
            }
            loadPendingOrders()
            loadDecisions()
          } else if (data.type === 'connected') {
            wsConnected.value = true
          }
        },
        // onError
        () => {
          wsConnected.value = false
        },
        // onOpen
        () => {
          wsConnected.value = true
        },
        // onClose
        () => {
          if (ws.value !== socket) return
          wsConnected.value = false
          ws.value = null
          if (!isUnmounted && selectedAccountId.value === accountId) {
            clearTimeout(wsReconnectTimer)
            wsReconnectTimer = setTimeout(() => {
              connectWebSocket()
            }, 5000)
          }
        },
        accountId
      )
      ws.value = socket
    }

    const loadPendingOrders = async () => {
      try {
        const data = await marketAPI.getPendingOrders(null, selectedAccountId.value)
        pendingOrders.value = data.orders || []
      } catch (err) {
        console.error('加载待确认订单失败:', err)
      }
    }

    const loadDecisions = async () => {
      if (!selectedAccountId.value) return
      loadingDecisions.value = true
      try {
        const data = await marketAPI.getDecisions({
          ...decisionFilters,
          count: 50,
        }, selectedAccountId.value)
        decisionAlerts.value = (data.decisions || []).map(normalizeTradingDecision)
      } catch (err) {
        console.error('加载策略决策历史失败:', err)
      } finally {
        loadingDecisions.value = false
      }
    }

    const confirmPendingOrder = async (order) => {
      confirmingOrderId.value = order.order_id
      try {
        const data = await marketAPI.confirmOrderWithUpdate(order.order_id, {
          mount: order.mount,
          sl: order.sl,
          tp: order.tp
        }, selectedAccountId.value)
        if (data.status === 'ok') {
          await Promise.all([loadPendingOrders(), loadDecisions()])
        } else {
          errorMessage.value = data.message || '确认订单失败'
          showError.value = true
        }
      } catch (err) {
        errorMessage.value = `确认订单失败: ${err.message}`
        showError.value = true
      } finally {
        confirmingOrderId.value = null
      }
    }

    const rejectPendingOrder = async (orderId) => {
      rejectingOrderId.value = orderId
      try {
        const data = await marketAPI.rejectOrder(orderId, selectedAccountId.value)
        if (data.status === 'ok') {
          await Promise.all([loadPendingOrders(), loadDecisions()])
        } else {
          errorMessage.value = data.message || '放弃订单失败'
          showError.value = true
        }
      } catch (err) {
        errorMessage.value = `放弃订单失败: ${err.message}`
        showError.value = true
      } finally {
        rejectingOrderId.value = null
      }
    }

    const getDecisionStatusColor = (status) => ({
      pending: 'warning',
      confirmed: 'success',
      rejected: 'error',
      expired: 'grey',
    }[status] || 'info')

    const getDecisionStatusLabel = (status, autoExecuted = false) => {
      if (status === 'confirmed') return autoExecuted ? '已自动执行' : '已确认执行'
      return {
        pending: '等待确认',
        rejected: '已拒绝',
        expired: '已过期',
      }[status] || status || '未知状态'
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
      if (selectedAccountId.value) {
        loadPendingOrders()
        loadDecisions()
        connectWebSocket()
      }

      // 定时刷新
      statusInterval = setInterval(() => {
        if (!selectedAccountId.value) return
        loadPendingOrders()
        loadDecisions()
      }, 10000)

    })

    watch(selectedAccountId, async (value) => {
      clearTimeout(wsReconnectTimer)
      ws.value?.close()
      ws.value = null
      wsConnected.value = false
      decisionAlerts.value = []
      pendingOrders.value = []
      if (!value || isUnmounted) return
      await Promise.all([loadPendingOrders(), loadDecisions()])
      connectWebSocket()
    })

    onUnmounted(() => {
      isUnmounted = true
      clearInterval(statusInterval)
      clearTimeout(wsReconnectTimer)
      ws.value?.close()
      ws.value = null
    })

    return {
      decisionAlerts,
      showError,
      errorMessage,
      wsConnected,
      pendingOrders,
      selectedAccount,
      activeDeployments,
      strategyFilterOptions,
      decisionFilters,
      decisionStatusOptions,
      loadingDecisions,
      loadDecisions,
      // 决策订单操作
      confirmingOrderId,
      rejectingOrderId,
      confirmPendingOrder,
      rejectPendingOrder,
      getDecisionStatusColor,
      getDecisionStatusLabel,
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
.execution-hero {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 24px;
  padding: 26px 30px;
  color: #f4fbf8;
  border-radius: 22px;
  background:
    radial-gradient(circle at 86% 18%, rgba(247, 186, 67, 0.28), transparent 28%),
    linear-gradient(125deg, #123c35 0%, #176b56 58%, #0d8f70 100%);
  box-shadow: 0 18px 40px rgba(20, 82, 68, 0.18);
}

.execution-hero h1 {
  margin: 2px 0 6px;
  font-size: clamp(1.8rem, 3vw, 2.7rem);
  line-height: 1.05;
}

.execution-hero p {
  margin: 0;
  color: rgba(244, 251, 248, 0.76);
}

.section-kicker {
  color: #f4c96b;
  font-size: 0.72rem;
  font-weight: 800;
  letter-spacing: 0.16em;
}

.metric-card .v-card-text {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 12px;
}

.metric-card strong {
  font-size: 1.9rem;
}

.pending-order-row {
  display: grid;
  grid-template-columns: minmax(220px, 1fr) minmax(360px, 1.2fr) auto;
  gap: 18px;
  align-items: center;
  padding: 16px;
  border: 1px solid rgba(117, 91, 15, 0.18);
  border-radius: 14px;
  background: linear-gradient(100deg, rgba(255, 248, 225, 0.8), rgba(255, 255, 255, 0.9));
}

.pending-order-fields {
  display: grid;
  grid-template-columns: repeat(3, minmax(90px, 1fr));
  gap: 10px;
}

.pending-order-actions {
  display: flex;
  gap: 8px;
}

.decision-filter {
  max-width: 190px;
}

.decision-time-filter {
  max-width: 215px;
}

.v-card {
  margin-bottom: 16px;
}

.trend-card {
  height: 100%;
}

.reason-text {
  font-size: 11px;
  color: #666;
  line-height: 1.3;
  overflow: hidden;
  text-overflow: ellipsis;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
}

@media (max-width: 960px) {
  .execution-hero,
  .pending-order-row {
    display: flex;
    flex-direction: column;
    align-items: stretch;
  }

  .pending-order-fields {
    grid-template-columns: 1fr;
  }

  .pending-order-actions .v-btn {
    flex: 1;
  }

  .decision-filter,
  .decision-time-filter {
    max-width: none;
    width: 100%;
  }
}
</style>

<template>
  <v-container fluid>
    <v-row>
      <v-col cols="12">
        <h1 class="mb-4">信号推荐</h1>
      </v-col>
    </v-row>

    <!-- 交易决策提醒 -->
    <v-row v-if="decisionAlerts.length > 0">
      <v-col cols="12">
        <v-alert
          v-for="(alert, index) in decisionAlerts"
          :key="alert.decision_id || index"
          :type="alert.rejected ? 'warning' : alert.action === 'buy' ? 'success' : 'error'"
          dismissible
          class="mb-2"
          @input="removeDecisionAlert(index)"
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

          <!-- 待确认订单操作 -->
          <div v-if="alert.pending_order && !alert.pending_order.confirmed" class="mt-3 pa-2 grey lighten-4 rounded">
            <div class="text-subtitle-2 font-weight-bold mb-2">
              <v-icon small color="primary" class="mr-1">mdi-file-document-edit</v-icon>
              待确认订单
            </div>

            <v-row dense class="mb-2">
              <v-col cols="3">
                <v-text-field
                  v-model.number="alert.pending_order.mount"
                  label="手数"
                  type="number"
                  step="0.01"
                  min="0.01"
                  dense
                  hide-details
                  outlined
                ></v-text-field>
              </v-col>
              <v-col cols="3">
                <v-text-field
                  v-model.number="alert.pending_order.sl"
                  label="止损"
                  type="number"
                  step="0.01"
                  dense
                  hide-details
                  outlined
                ></v-text-field>
              </v-col>
              <v-col cols="3">
                <v-text-field
                  v-model.number="alert.pending_order.tp"
                  label="止盈"
                  type="number"
                  step="0.01"
                  dense
                  hide-details
                  outlined
                ></v-text-field>
              </v-col>
              <v-col cols="3" class="d-flex align-center">
                <v-btn
                  color="success"
                  small
                  class="mr-1"
                  :loading="confirmingOrderId === alert.pending_order.order_id"
                  @click="confirmDecisionOrder(alert.pending_order, index)"
                >
                  <v-icon start small>mdi-check</v-icon>
                  确认
                </v-btn>
                <v-btn
                  color="error"
                  small
                  outlined
                  :loading="rejectingOrderId === alert.pending_order.order_id"
                  @click="rejectDecisionOrder(alert.pending_order.order_id, index)"
                >
                  放弃
                </v-btn>
              </v-col>
            </v-row>

            <div class="text-caption grey--text mb-1">
              {{ alert.reason }}
            </div>
            <div class="text-caption grey--text">
              <v-icon small>mdi-clock-outline</v-icon>
              {{ formatTime(alert.timestamp) }}
            </div>
          </div>

          <!-- 已确认状态 -->
          <div v-if="alert.pending_order?.confirmed" class="mt-2">
            <v-chip small color="success">
              <v-icon start small>mdi-check-circle</v-icon>
              {{ alert.pending_order.auto_executed ? '已自动下单，等待 MT5 执行' : '已确认，等待执行' }}
            </v-chip>
          </div>
        </v-alert>
      </v-col>
    </v-row>

    <!-- WebSocket 状态 -->
    <v-row>
      <v-col cols="12">
        <v-chip
          :color="wsConnected ? 'success' : 'error'"
          small
          class="mr-2"
        >
          <v-icon start small>mdi-lan-connect</v-icon>
          {{ wsConnected ? 'WebSocket 已连接' : 'WebSocket 断开' }}
        </v-chip>
      </v-col>
    </v-row>

    <!-- 大模型趋势分析 -->
    <v-row>
      <v-col cols="12">
        <v-card>
          <v-card-title>
            <v-icon class="mr-2">mdi-robot</v-icon>
            AI趋势分析
            <v-spacer></v-spacer>
            <!-- 分析状态指示器 -->
            <v-chip v-if="llmAnalyzing" small color="primary" class="mr-2">
              <v-progress-circular indeterminate size="12" width="2" class="mr-1"></v-progress-circular>
              {{ llmAnalysisStatus || '分析中...' }}
            </v-chip>
            <v-chip v-else-if="llmStatus.enabled" small color="success" class="mr-2">已启用</v-chip>
            <v-chip v-else small color="grey">
              {{ llmStatus.access_status === 'pending' ? '开通审批中' : '未开通' }}
            </v-chip>
            <v-btn
              color="primary"
              size="small"
              variant="outlined"
              :disabled="!llmFeatureAvailable || llmAnalyzing"
              :loading="llmTriggering"
              @click="triggerLLMAnalysis"
            >
              <v-icon start size="small">mdi-play</v-icon>
              立即分析
            </v-btn>
          </v-card-title>
          <v-card-text>
            <!-- 分析时间 -->
            <div v-if="llmStatus.last_analysis_time" class="text-caption grey--text mb-3">
              上次分析: {{ llmStatus.last_analysis_time }}
            </div>

            <!-- 无数据提示 -->
            <div v-if="!llmAnalysis || Object.keys(llmAnalysis).length === 0" class="text-center py-6">
              <v-icon size="48" color="grey lighten-1">mdi-robot-outline</v-icon>
              <p class="mt-3 grey--text">
                <template v-if="llmAnalyzing">
                  {{ llmAnalysisStatus || '正在分析...' }}
                </template>
                <template v-else>
                  {{ llmEmptyMessage }}
                </template>
              </p>
              <!-- 分析进度条 -->
              <v-progress-linear v-if="llmAnalyzing" indeterminate color="primary" class="mt-2"></v-progress-linear>
            </div>

            <!-- 分析结果 -->
            <div v-else>
              <v-expansion-panels>
                <v-expansion-panel v-for="(data, symbol) in llmAnalysis" :key="symbol">
                  <v-expansion-panel-title>
                    <div class="d-flex align-center">
                      <strong class="mr-3">{{ symbol }}</strong>
                      <v-chip
                        v-if="data.overall_trend"
                        :color="getTrendChipColor(data.overall_trend.direction)"
                        small
                      >
                        {{ data.overall_trend.direction }}
                      </v-chip>
                      <!-- 休市状态 -->
                      <v-chip
                        v-if="data.market_status === 'closed'"
                        color="grey"
                        small
                        class="ml-2"
                      >
                        <v-icon start size="x-small">mdi-pause-circle</v-icon>
                        休市中
                      </v-chip>
                      <!-- 数据未更新 -->
                      <v-chip
                        v-else-if="data.data_stale"
                        color="warning"
                        small
                        class="ml-2"
                      >
                        <v-icon start size="x-small">mdi-alert</v-icon>
                        数据未更新
                      </v-chip>
                    </div>
                  </v-expansion-panel-title>
                  <v-expansion-panel-text>
                    <!-- 休市提示 -->
                    <v-alert
                      v-if="data.market_status === 'closed'"
                      type="info"
                      dense
                      class="mb-3"
                    >
                      <div class="d-flex align-center">
                        <v-icon small class="mr-2">mdi-pause-circle</v-icon>
                        <span>
                          休市中，暂无行情数据。下次开市时将自动更新分析。
                        </span>
                      </div>
                    </v-alert>
                    <!-- 数据过期提示 -->
                    <v-alert
                      v-else-if="data.data_stale"
                      type="warning"
                      dense
                      class="mb-3"
                    >
                      <div class="d-flex align-center">
                        <v-icon small class="mr-2">mdi-clock-alert</v-icon>
                        <span>
                          行情数据已 {{ data.stale_seconds || '?' }} 秒未更新，当前显示上次分析结果。
                          <span class="text-caption">({{ data.analyzed_at }})</span>
                        </span>
                      </div>
                    </v-alert>

                    <!-- 各周期趋势（休市时可能没有分析结果）-->
                    <div v-if="data.trend_analysis" class="mb-4">
                      <div class="text-subtitle-2 mb-2">各周期趋势</div>
                      <v-table density="compact">
                        <template v-slot:default>
                          <thead>
                            <tr>
                              <th>周期</th>
                              <th>AI趋势</th>
                              <th>置信度</th>
                              <th>AI说明</th>
                              <th>技术趋势</th>
                              <th>技术说明</th>
                              <th>结论</th>
                            </tr>
                          </thead>
                          <tbody>
                            <tr v-for="(trend, period) in data.trend_analysis" :key="period">
                              <td><strong>{{ period }}</strong></td>
                              <td>
                                <v-chip :color="getTrendChipColor(trend.trend)" size="x-small">
                                  {{ trend.trend }}
                                </v-chip>
                              </td>
                              <td>
                                <span class="text-caption">{{ trend.confidence }}%</span>
                              </td>
                              <td class="text-caption grey--text" style="max-width: 180px;">
                                {{ trend.reason }}
                              </td>
                              <td>
                                <v-chip
                                  v-if="getTechTrend(symbol, period)"
                                  :color="getTrendColor(getTechTrend(symbol, period).trend)"
                                  size="x-small"
                                >
                                  {{ getTrendLabel(getTechTrend(symbol, period).trend) }}
                                </v-chip>
                                <span v-else class="grey--text text-caption">-</span>
                              </td>
                              <td class="text-caption grey--text" style="max-width: 180px;">
                                <div v-if="getTechTrend(symbol, period)" :title="getTechTrend(symbol, period).reason">
                                  {{ getTechTrend(symbol, period).reason }}
                                </div>
                                <span v-else>-</span>
                              </td>
                              <td>
                                <v-chip
                                  :color="getConclusionColor(symbol, period, trend)"
                                  size="x-small"
                                >
                                  {{ getConclusion(symbol, period, trend) }}
                                </v-chip>
                              </td>
                            </tr>
                          </tbody>
                        </template>
                      </v-table>
                    </div>

                    <!-- 关键价位 -->
                    <div v-if="data.key_levels" class="mb-4">
                      <div class="text-subtitle-2 mb-2">关键价位</div>
                      <v-row>
                        <v-col cols="6">
                          <div class="text-caption grey--text">压力位</div>
                          <div v-for="(level, i) in data.key_levels.resistance" :key="'r'+i">
                            <v-chip color="error" size="x-small" class="mr-1">{{ level }}</v-chip>
                          </div>
                        </v-col>
                        <v-col cols="6">
                          <div class="text-caption grey--text">支撑位</div>
                          <div v-for="(level, i) in data.key_levels.support" :key="'s'+i">
                            <v-chip color="success" size="x-small" class="mr-1">{{ level }}</v-chip>
                          </div>
                        </v-col>
                      </v-row>
                    </div>

                    <!-- 交易建议 -->
                    <div v-if="data.trade_suggestions && data.trade_suggestions.length > 0">
                      <div class="text-subtitle-2 mb-2">交易建议</div>
                      <v-table density="compact">
                        <template v-slot:default>
                          <thead>
                            <tr>
                              <th>周期</th>
                              <th>方向</th>
                              <th>入场价</th>
                              <th>止损</th>
                              <th>止盈</th>
                              <th>理由</th>
                            </tr>
                          </thead>
                          <tbody>
                            <tr v-for="(suggestion, i) in data.trade_suggestions" :key="i">
                              <td>{{ suggestion.period }}</td>
                              <td>
                                <v-chip :color="suggestion.direction === 'buy' ? 'success' : 'error'" size="x-small">
                                  {{ suggestion.direction === 'buy' ? '买入' : '卖出' }}
                                </v-chip>
                              </td>
                              <td>{{ suggestion.entry_price }}</td>
                              <td>{{ suggestion.stop_loss }}</td>
                              <td>{{ suggestion.take_profit }}</td>
                              <td class="text-caption">{{ suggestion.reason }}</td>
                            </tr>
                          </tbody>
                        </template>
                      </v-table>
                    </div>

                    <div class="text-caption grey--text mt-2">
                      分析时间: {{ data.analyzed_at }}
                    </div>
                  </v-expansion-panel-text>
                </v-expansion-panel>
              </v-expansion-panels>
            </div>
          </v-card-text>
        </v-card>
      </v-col>
    </v-row>

    <!-- 错误提示 -->
    <v-snackbar v-model="showError" color="error" timeout="5000">
      {{ errorMessage }}
    </v-snackbar>
  </v-container>
</template>

<script>
import { ref, computed, onMounted, onUnmounted, watch } from 'vue'
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
    const allPivots = ref([])
    const marketStatus = ref({})
    const thresholds = ref({})
    const decisionAlerts = ref([])  // 统一的决策提醒列表
    const expandedSignals = ref(new Set())  // 展开信号的状态
    const loading = ref(false)
    const showError = ref(false)
    const errorMessage = ref('')

    // 趋势分析相关
    const trendData = ref({})
    const loadingTrend = ref(false)
    const pendingOrders = ref([])

    // 订单确认/放弃状态
    const confirmingOrderId = ref(null)
    const rejectingOrderId = ref(null)

    // WebSocket
    const ws = ref(null)
    const wsConnected = ref(false)
    let wsReconnectTimer = null
    let statusInterval = null
    let isUnmounted = false
    const { selectedAccountId } = useAccountContext()

    // 大模型分析
    const llmStatus = ref({ enabled: false })
    const llmAnalysis = ref({})
    const llmAnalyzing = ref(false)
    const llmAnalysisStatus = ref('')
    const llmTriggering = ref(false)
    const llmAccessGranted = computed(() =>
      llmStatus.value.access_status === 'approved' || Boolean(llmStatus.value.enabled)
    )
    const llmFeatureAvailable = computed(() =>
      llmAccessGranted.value && Boolean(llmStatus.value.enabled)
    )
    const llmEmptyMessage = computed(() => {
      if (!llmAccessGranted.value) return '请前往用户配置申请开通大模型行情分析'
      if (!llmStatus.value.enabled) return '大模型服务暂未配置，请联系管理员'
      if (llmStatus.value.analysis_message) return llmStatus.value.analysis_message
      return '尚未生成分析结果，点击“立即分析”开始'
    })


    // 计算属性
    const highPivots = computed(() => {
      return allPivots.value
        .filter(p => p.direction === 'high')
        .sort((a, b) => new Date(b.timestamp) - new Date(a.timestamp))
        .slice(0, 20)
    })

    const lowPivots = computed(() => {
      return allPivots.value
        .filter(p => p.direction === 'low')
        .sort((a, b) => new Date(b.timestamp) - new Date(a.timestamp))
        .slice(0, 20)
    })

    // 方法
    const loadStatus = async () => {
      try {
        const status = await marketAPI.getStatus(selectedAccountId.value)
        marketStatus.value = status
      } catch (err) {
        console.error('加载状态失败:', err)
      }
    }

    const loadThresholds = async () => {
      try {
        const data = await marketAPI.getThresholds()
        thresholds.value = data.thresholds || {}
      } catch (err) {
        console.error('加载阈值失败:', err)
      }
    }

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
            if (decisionAlerts.value.length > 10) {
              decisionAlerts.value.pop()
            }
          } else if (data.type === 'pending_order') {
            // 订单状态更新（确认后）
            console.log('收到订单更新:', data)
            const order = data.data
            // 更新对应的 alert
            const alertIndex = decisionAlerts.value.findIndex(
              a => a.pending_order?.order_id === order.order_id
            )
            if (alertIndex >= 0) {
              decisionAlerts.value[alertIndex].pending_order = {
                ...decisionAlerts.value[alertIndex].pending_order,
                ...order,
                confirmed: true
              }
            }
          } else if (data.type === 'connected') {
            wsConnected.value = true
          } else if (data.type === 'llm_analysis_status') {
            // 大模型分析状态更新（流式）
            console.log('收到分析状态更新:', data)
            if (data.status === 'analyzing' || data.status === 'streaming') {
              llmAnalyzing.value = true
              llmAnalysisStatus.value = data.message
            } else if (data.status === 'stale') {
              // 数据过期，停止加载状态
              llmAnalyzing.value = false
              llmAnalysisStatus.value = data.message
            } else if (data.status === 'error') {
              llmAnalyzing.value = false
              llmAnalysisStatus.value = data.message
            }
          } else if (data.type === 'llm_analysis_update') {
            // 大模型分析更新，刷新分析结果
            console.log('收到大模型分析更新通知:', data)
            llmAnalyzing.value = false
            llmAnalysisStatus.value = ''
            loadLLMAnalysis()
            loadLLMStatus()
            loadTrend()
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

    const getThresholdLabel = (period) => {
      const threshold = thresholds.value[period]
      if (threshold) {
        return `±${threshold.percent}`
      }
      return period
    }

    const getPivotPeriodColor = (period) => {
      const colors = {
        'H4': 'red',
        'H1': 'orange',
        'M15': 'yellow darken-2',
        'M5': 'green',
        'M1': 'blue'
      }
      return colors[period] || 'grey'
    }

    const getTechTrend = (symbol, period) => {
      const normalized = symbol.replace('#', '').toUpperCase()
      const symbolVariants = [normalized, normalized + '#', symbol]
      for (const s of symbolVariants) {
        // 后端返回的数据结构是 resonance.periods
        if (trendData.value[s]?.resonance?.periods?.[period]) {
          return trendData.value[s].resonance.periods[period]
        }
      }
      return null
    }

    // 判断AI趋势和技术趋势是否冲突
    const hasConflict = (symbol, period, aiTrend) => {
      const techTrend = getTechTrend(symbol, period)
      if (!techTrend) return false

      const aiDir = getTrendDirection(aiTrend.trend)
      const techDir = techTrend.trend

      // 如果一个看涨一个看跌，则是冲突
      if ((aiDir === 'up' && techDir === 'down') || (aiDir === 'down' && techDir === 'up')) {
        return true
      }
      return false
    }

    // 获取趋势方向
    const getTrendDirection = (trend) => {
      if (!trend) return 'unknown'
      const t = trend.toLowerCase()
      if (t.includes('上涨') || t.includes('上升') || t === 'up') return 'up'
      if (t.includes('下跌') || t.includes('下降') || t === 'down') return 'down'
      return 'sideways'
    }

    // 获取结论
    const getConclusion = (symbol, period, aiTrend) => {
      const techTrend = getTechTrend(symbol, period)
      const aiDir = getTrendDirection(aiTrend.trend)
      const techDir = techTrend?.trend || 'unknown'

      if (hasConflict(symbol, period, aiTrend)) {
        return '谨慎观望'
      }

      if (aiDir === techDir) {
        if (aiDir === 'up') return '看涨'
        if (aiDir === 'down') return '看跌'
        return '震荡'
      }

      // AI有判断但技术分析无明确趋势
      if (techDir === 'sideways' || techDir === 'unknown') {
        return aiDir === 'up' ? '偏多' : aiDir === 'down' ? '偏空' : '震荡'
      }

      return '待观察'
    }

    // 获取结论颜色
    const getConclusionColor = (symbol, period, aiTrend) => {
      if (hasConflict(symbol, period, aiTrend)) {
        return 'warning'
      }
      const conclusion = getConclusion(symbol, period, aiTrend)
      if (conclusion === '看涨') return 'success'
      if (conclusion === '看跌') return 'error'
      if (conclusion === '偏多') return 'success lighten-2'
      if (conclusion === '偏空') return 'error lighten-2'
      return 'grey'
    }

    // 趋势分析相关方法
    const loadTrend = async () => {
      // 加载所有已分析品种的趋势数据
      const symbols = Object.keys(llmAnalysis.value)
      if (symbols.length === 0) return

      loadingTrend.value = true
      try {
        // 并行加载所有品种的趋势
        const promises = symbols.map(symbol => marketAPI.getTrend(symbol, selectedAccountId.value))
        const results = await Promise.all(promises)

        // 合并结果
        results.forEach((data, index) => {
          if (data) {
            const symbol = symbols[index]
            trendData.value[symbol] = data
            console.log(`[Market] 加载 ${symbol} 技术趋势:`, data.resonance?.signal)
          }
        })
      } catch (err) {
        console.error('加载趋势分析失败:', err)
      } finally {
        loadingTrend.value = false
      }
    }

    const loadPendingOrders = async () => {
      try {
        const data = await marketAPI.getPendingOrders(null, selectedAccountId.value)
        pendingOrders.value = data.orders || []
      } catch (err) {
        console.error('加载待确认订单失败:', err)
      }
    }

    // 大模型分析相关
    const loadLLMStatus = async () => {
      try {
        const data = await marketAPI.getLLMStatus(selectedAccountId.value)
        if (data.status === 'ok') {
          llmStatus.value = data.data
          const status = data.data.analysis_status
          llmAnalyzing.value = ['queued', 'analyzing', 'streaming'].includes(status)
          llmAnalysisStatus.value = data.data.analysis_message || ''
        }
      } catch (err) {
        console.error('获取大模型状态失败:', err)
      }
    }

    const loadLLMAnalysis = async () => {
      if (!llmAccessGranted.value) {
        llmAnalysis.value = {}
        return
      }
      try {
        const data = await marketAPI.getLLMAnalysis(null, selectedAccountId.value)
        if (data.status === 'ok') {
          llmAnalysis.value = data.data || {}
        }
      } catch (err) {
        console.error('获取大模型分析失败:', err)
      }
    }

    const triggerLLMAnalysis = async () => {
      llmTriggering.value = true
      try {
        const data = await marketAPI.triggerLLMAnalysis(selectedAccountId.value)
        if (data.status === 'accepted' || data.status === 'busy') {
          llmAnalyzing.value = true
          llmAnalysisStatus.value = data.message
          await loadLLMStatus()
          return
        }
        errorMessage.value = data.message || '提交 AI 分析任务失败'
        showError.value = true
        await loadLLMStatus()
      } catch (err) {
        errorMessage.value = err.response?.data?.detail || `提交 AI 分析任务失败: ${err.message}`
        showError.value = true
      } finally {
        llmTriggering.value = false
      }
    }


    const getTrendChipColor = (trend) => {
      if (!trend) return 'grey'
      const trendLower = trend.toLowerCase()
      if (trendLower.includes('上涨') || trendLower === 'up' || trendLower === 'buy') {
        return 'success'
      }
      if (trendLower.includes('下跌') || trendLower === 'down' || trendLower === 'sell') {
        return 'error'
      }
      return 'warning'
    }

    const confirmOrder = async (orderId) => {
      try {
        const data = await marketAPI.confirmOrder(orderId, selectedAccountId.value)
        if (data.status === 'ok') {
          await loadPendingOrders()
        } else {
          errorMessage.value = data.message || '确认订单失败'
          showError.value = true
        }
      } catch (err) {
        errorMessage.value = `确认订单失败: ${err.message}`
        showError.value = true
      }
    }

    const rejectOrder = async (orderId) => {
      try {
        const data = await marketAPI.rejectOrder(orderId, selectedAccountId.value)
        if (data.status === 'ok') {
          await loadPendingOrders()
        } else {
          errorMessage.value = data.message || '拒绝订单失败'
          showError.value = true
        }
      } catch (err) {
        errorMessage.value = `拒绝订单失败: ${err.message}`
        showError.value = true
      }
    }

    // 决策提醒操作
    const removeDecisionAlert = (index) => {
      decisionAlerts.value.splice(index, 1)
    }

    const confirmDecisionOrder = async (order, alertIndex) => {
      confirmingOrderId.value = order.order_id
      try {
        const data = await marketAPI.confirmOrderWithUpdate(order.order_id, {
          mount: order.mount,
          sl: order.sl,
          tp: order.tp
        }, selectedAccountId.value)
        if (data.status === 'ok') {
          decisionAlerts.value.splice(alertIndex, 1)
          await loadPendingOrders()
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

    const rejectDecisionOrder = async (orderId, alertIndex) => {
      rejectingOrderId.value = orderId
      try {
        const data = await marketAPI.rejectOrder(orderId, selectedAccountId.value)
        if (data.status === 'ok') {
          decisionAlerts.value.splice(alertIndex, 1)
          await loadPendingOrders()
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
        'moving_average': '均线'
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

    const getTrendColor = (trend) => {
      if (trend === 'up') return 'success'
      if (trend === 'down') return 'error'
      return 'warning'
    }

    const getTrendLabel = (trend) => {
      if (trend === 'up') return '上升'
      if (trend === 'down') return '下降'
      if (trend === 'sideways') return '震荡'
      return '未知'
    }

    // 生命周期
    onMounted(async () => {
      loadThresholds()
      if (selectedAccountId.value) {
        loadStatus()
        loadPendingOrders()
        await loadLLMStatus()
        loadLLMAnalysis()
        connectWebSocket()
      }

      // 定时刷新
      statusInterval = setInterval(() => {
        if (!selectedAccountId.value) return
        loadStatus()
        loadPendingOrders()
        loadLLMStatus().then(loadLLMAnalysis)
        loadTrend()
      }, 10000)

    })

    watch(selectedAccountId, async (value) => {
      clearTimeout(wsReconnectTimer)
      ws.value?.close()
      ws.value = null
      wsConnected.value = false
      decisionAlerts.value = []
      pendingOrders.value = []
      trendData.value = {}
      llmAnalysis.value = {}
      llmStatus.value = { enabled: false }
      if (!value || isUnmounted) return
      await Promise.all([loadStatus(), loadPendingOrders(), loadLLMStatus()])
      await loadLLMAnalysis()
      loadTrend()
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
      allPivots,
      highPivots,
      lowPivots,
      marketStatus,
      thresholds,
      decisionAlerts,
      loading,
      showError,
      errorMessage,
      wsConnected,
      getThresholdLabel,
      getPivotPeriodColor,
      // 趋势分析相关
      trendData,
      loadingTrend,
      pendingOrders,
      loadTrend,
      confirmOrder,
      rejectOrder,
      getTrendColor,
      getTrendLabel,
      // 决策订单操作
      confirmingOrderId,
      rejectingOrderId,
      removeDecisionAlert,
      confirmDecisionOrder,
      rejectDecisionOrder,
      getSignalSourceColor,
      formatTime,
      formatTradePrice,
      // 信号显示
      formatSignalLabel,
      getVisibleSignals,
      toggleSignalExpand,
      isSignalExpanded,
      // 大模型分析
      llmStatus,
      llmAnalysis,
      llmAnalyzing,
      llmAnalysisStatus,
      llmTriggering,
      llmFeatureAvailable,
      llmEmptyMessage,
      loadLLMStatus,
      loadLLMAnalysis,
      triggerLLMAnalysis,
      getTrendChipColor,
      // 技术分析与AI分析整合
      getTechTrend,
      hasConflict,
      getConclusion,
      getConclusionColor,
    }
  }
}
</script>

<style scoped>
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
</style>

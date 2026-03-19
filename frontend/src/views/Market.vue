<template>
  <v-container fluid>
    <v-row>
      <v-col cols="12">
        <h1 class="mb-4">行情分析</h1>
      </v-col>
    </v-row>

    <!-- 最新快讯 -->
    <v-row v-if="latestFlashNews">
      <v-col cols="12">
        <v-alert
          :type="latestFlashNews.importance >= 2 ? 'warning' : 'info'"
          dense
          class="mb-2"
          dismissible
          @input="latestFlashNews = null"
        >
          <div class="d-flex align-center">
            <v-icon small class="mr-2">mdi-lightning-bolt</v-icon>
            <v-chip v-if="latestFlashNews.speaker" color="primary" size="x-small" class="mr-2">
              {{ latestFlashNews.speaker }}
            </v-chip>
            <span class="text-body-2">{{ latestFlashNews.content }}</span>
            <v-spacer></v-spacer>
            <span class="text-caption grey--text ml-2">{{ formatNewsTime(latestFlashNews.time) }}</span>
          </div>
          <!-- 影响分析 -->
          <div v-if="latestFlashNews.impact && Object.keys(latestFlashNews.impact).length > 0" class="mt-1">
            <v-chip
              v-for="(impact, symbol) in latestFlashNews.impact"
              :key="symbol"
              :color="getImpactColor(impact.direction)"
              size="x-small"
              class="mr-1"
            >
              {{ symbol }}: {{ impact.direction }}
            </v-chip>
          </div>
        </v-alert>
      </v-col>
    </v-row>

    <!-- 重要财经事件提醒 -->
    <v-row v-if="topCalendarEvent">
      <v-col cols="12">
        <v-alert
          type="error"
          dense
          class="mb-2"
          dismissible
          @input="topCalendarEvent = null"
        >
          <div class="d-flex align-center">
            <v-icon small class="mr-2">mdi-calendar-alert</v-icon>
            <v-chip color="error" size="x-small" class="mr-2">
              重要性 {{ topCalendarEvent.importance }}
            </v-chip>
            <v-chip :color="getCurrencyColor(topCalendarEvent.currency)" size="x-small" class="mr-2">
              {{ topCalendarEvent.currency }}
            </v-chip>
            <span class="text-body-2 font-weight-medium">{{ topCalendarEvent.name }}</span>
            <v-spacer></v-spacer>
            <span class="text-caption grey--text ml-2">
              {{ formatEventTime(topCalendarEvent.publish_time) }}
            </span>
          </div>
          <!-- 预测值和实际值 -->
          <div class="mt-1 d-flex align-center">
            <span class="text-caption mr-3" v-if="topCalendarEvent.forecast">
              预测: <strong>{{ topCalendarEvent.forecast }}</strong>
            </span>
            <span class="text-caption mr-3" v-if="topCalendarEvent.previous">
              前值: <strong>{{ topCalendarEvent.previous }}</strong>
            </span>
            <span class="text-caption success--text" v-if="topCalendarEvent.actual">
              实际: <strong>{{ topCalendarEvent.actual }}</strong>
            </span>
          </div>
          <!-- 结果标签 -->
          <div v-if="topCalendarEvent.result" class="mt-1">
            <v-chip
              :color="getEventResultColor(topCalendarEvent.result)"
              size="x-small"
            >
              {{ getEventResultLabel(topCalendarEvent.result) }}
            </v-chip>
          </div>
        </v-alert>
      </v-col>
    </v-row>

    <!-- 交易决策提醒 -->
    <v-row v-if="decisionAlerts.length > 0">
      <v-col cols="12">
        <v-alert
          v-for="(alert, index) in decisionAlerts"
          :key="index"
          :type="alert.action === 'buy' ? 'success' : 'warning'"
          dismissible
          class="mb-2"
          @input="removeDecisionAlert(index)"
        >
          <div class="d-flex flex-wrap align-center">
            <v-icon small class="mr-1">mdi-chart-line</v-icon>
            <strong>{{ alert.symbol }}</strong>
            <v-chip small :color="alert.action === 'buy' ? 'success' : 'error'" class="ml-2">
              {{ alert.action === 'buy' ? '买入' : '卖出' }}
            </v-chip>
            <v-chip v-if="alert.confidence" small color="primary" class="ml-2">
              置信度: {{ alert.confidence }}%
            </v-chip>
          </div>

          <div class="mt-2">
            <span class="text-caption mr-4">入场价: <strong>{{ alert.price?.toFixed(2) }}</strong></span>
            <span class="text-caption mr-4">止损: <strong>{{ alert.sl?.toFixed(2) }}</strong></span>
            <span class="text-caption mr-4">止盈: <strong>{{ alert.tp?.toFixed(2) }}</strong></span>
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
              已确认，等待执行
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
            <v-chip v-else small color="grey">未配置</v-chip>
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
                  {{ llmStatus.enabled ? '等待分析完成...' : '请先配置大模型API' }}
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
import { ref, computed, onMounted, onUnmounted } from 'vue'
import { marketAPI } from '@/api/market'

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

    // 大模型分析
    const llmStatus = ref({ enabled: false })
    const llmAnalysis = ref({})
    const llmAnalyzing = ref(false)
    const llmAnalysisStatus = ref('')

    // 最新快讯
    const latestFlashNews = ref(null)
    const newsWs = ref(null)

    // 最高等级财经事件
    const topCalendarEvent = ref(null)

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
        const status = await marketAPI.getStatus()
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
      ws.value = marketAPI.createWebSocket(
        // onMessage
        (data) => {
          if (data.type === 'trading_decision') {
            // 策略层产生的交易决策
            console.log('收到交易决策:', data)
            const decision = data.data
            // 转换为统一的 alert 格式
            const alert = {
              type: 'trading_decision',
              decision_id: decision.decision_id,
              symbol: decision.symbol,
              action: decision.action,
              price: decision.entry_price,
              sl: decision.sl,
              tp: decision.tp,
              volume: decision.volume,
              reason: decision.decision_reason,
              confidence: decision.confidence_score,
              signals: decision.signals || [],
              signal_summary: decision.signal_summary || {},
              risk_reward_ratio: decision.risk_reward_ratio,
              timestamp: decision.timestamp || new Date().toISOString(),
              pending_order: {
                order_id: decision.decision_id,
                action: decision.action === 'buy' ? 'b' : 's',
                price: decision.entry_price,
                sl: decision.sl,
                tp: decision.tp,
                mount: decision.volume,
                reason: decision.decision_reason
              }
            }
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
          wsConnected.value = false
          // 5秒后重连
          setTimeout(() => {
            if (!wsConnected.value) {
              connectWebSocket()
            }
          }, 5000)
        }
      )
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
        const promises = symbols.map(symbol => marketAPI.getTrend(symbol))
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
        const data = await marketAPI.getPendingOrders()
        pendingOrders.value = data.orders || []
      } catch (err) {
        console.error('加载待确认订单失败:', err)
      }
    }

    // 大模型分析相关
    const loadLLMStatus = async () => {
      try {
        const data = await marketAPI.getLLMStatus()
        if (data.status === 'ok') {
          llmStatus.value = data.data
        }
      } catch (err) {
        console.error('获取大模型状态失败:', err)
      }
    }

    const loadLLMAnalysis = async () => {
      try {
        const data = await marketAPI.getLLMAnalysis()
        if (data.status === 'ok') {
          llmAnalysis.value = data.data || {}
        }
      } catch (err) {
        console.error('获取大模型分析失败:', err)
      }
    }

    // 获取最新快讯
    const fetchLatestFlashNews = async () => {
      try {
        const response = await fetch('/api/news/flash?count=1')
        const data = await response.json()
        if (data.status === 'ok' && data.data && data.data.length > 0) {
          latestFlashNews.value = data.data[0]
        }
      } catch (err) {
        console.error('获取快讯失败:', err)
      }
    }

    // 连接新闻WebSocket
    const connectNewsWebSocket = () => {
      const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
      const wsUrl = `${protocol}//${window.location.host}/api/news/ws`

      newsWs.value = new WebSocket(wsUrl)

      newsWs.value.onopen = () => {
        console.log('[Market] 新闻WebSocket已连接')
      }

      newsWs.value.onmessage = (event) => {
        try {
          const data = JSON.parse(event.data)
          if (data.type === 'flash_news' && data.news) {
            // 更新最新快讯
            latestFlashNews.value = data.news
          } else if (data.type === 'connected') {
            // 连接成功
            console.log('[Market] 新闻WebSocket连接确认')
          }
        } catch (err) {
          console.error('[Market] 解析新闻WebSocket消息失败:', err)
        }
      }

      newsWs.value.onerror = (err) => {
        console.error('[Market] 新闻WebSocket错误:', err)
      }

      newsWs.value.onclose = () => {
        console.log('[Market] 新闻WebSocket断开，5秒后重连...')
        setTimeout(() => {
          if (!newsWs.value || newsWs.value.readyState === WebSocket.CLOSED) {
            connectNewsWebSocket()
          }
        }, 5000)
      }
    }

    // 格式化快讯时间
    const formatNewsTime = (timeStr) => {
      if (!timeStr) return ''
      const date = new Date(timeStr)
      return date.toLocaleString('zh-CN', {
        month: 'short',
        day: 'numeric',
        hour: '2-digit',
        minute: '2-digit'
      })
    }

    // 获取影响颜色
    const getImpactColor = (direction) => {
      if (direction === '利好') return 'success'
      if (direction === '利空') return 'error'
      return 'info'
    }

    // 获取最高重要性财经事件
    const fetchTopCalendarEvent = async () => {
      try {
        const response = await fetch('/api/news/upcoming?hours=24')
        const data = await response.json()
        if (data.status === 'ok' && data.data && data.data.length > 0) {
          // 按重要性排序，选择最高重要性的事件
          const sortedEvents = data.data
            .filter(e => e.name && e.name.length > 2) // 过滤无效名称
            .sort((a, b) => {
              // 先按重要性排序（高到低）
              if (b.importance !== a.importance) return b.importance - a.importance
              // 同重要性按时间排序（近到远）
              return new Date(a.publish_time) - new Date(b.publish_time)
            })
          if (sortedEvents.length > 0) {
            topCalendarEvent.value = sortedEvents[0]
          }
        }
      } catch (err) {
        console.error('获取财经事件失败:', err)
      }
    }

    // 格式化事件时间
    const formatEventTime = (timeStr) => {
      if (!timeStr) return ''
      const date = new Date(timeStr)
      return date.toLocaleString('zh-CN', {
        month: 'short',
        day: 'numeric',
        hour: '2-digit',
        minute: '2-digit'
      })
    }

    // 获取货币颜色
    const getCurrencyColor = (currency) => {
      const colors = {
        'USD': 'green',
        'EUR': 'blue',
        'GBP': 'purple',
        'JPY': 'red',
        'AUD': 'orange',
        'CAD': 'teal',
        'CHF': 'indigo',
        'CNY': 'deep-orange'
      }
      return colors[currency] || 'grey'
    }

    // 获取事件结果颜色
    const getEventResultColor = (result) => {
      if (result === 'better') return 'success'
      if (result === 'worse') return 'error'
      if (result === 'in_line') return 'info'
      return 'grey'
    }

    // 获取事件结果标签
    const getEventResultLabel = (result) => {
      if (result === 'better') return '好于预期'
      if (result === 'worse') return '差于预期'
      if (result === 'in_line') return '符合预期'
      return '未知'
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
        const data = await marketAPI.confirmOrder(orderId)
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
        const data = await marketAPI.rejectOrder(orderId)
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
        })
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
        const data = await marketAPI.rejectOrder(orderId)
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
        'ai_entry': 'info'
      }
      return colors[source] || 'grey'
    }

    // 格式化信号标签（显示周期）
    const formatSignalLabel = (signal) => {
      const source = signal.source || ''
      const period = signal.source_period || signal.ai_analysis_period || ''
      const confidence = signal.confidence || 0

      // 显示信号源名称
      const sourceNames = {
        'pivot': 'Pivot',
        'key_level': 'KeyLevel',
        'ai_entry': 'AI'
      }
      const sourceName = sourceNames[source] || source

      // 如果有周期，显示周期
      if (period) {
        return `${sourceName}[${period}] (${confidence}%)`
      }
      return `${sourceName} (${confidence}%)`
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
    onMounted(() => {
      loadStatus()
      loadThresholds()
      loadPendingOrders()
      loadLLMStatus()
      loadLLMAnalysis()
      connectWebSocket()
      // 快讯相关
      fetchLatestFlashNews()
      connectNewsWebSocket()
      // 财经事件
      fetchTopCalendarEvent()

      // 定时刷新
      const statusInterval = setInterval(() => {
        loadStatus()
        loadPendingOrders()
        loadLLMAnalysis()
        loadTrend()
      }, 10000)

      // 清理
      onUnmounted(() => {
        clearInterval(statusInterval)
        if (ws.value) {
          ws.value.close()
        }
        if (newsWs.value) {
          newsWs.value.close()
        }
      })
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
      loadLLMStatus,
      loadLLMAnalysis,
      getTrendChipColor,
      // 技术分析与AI分析整合
      getTechTrend,
      hasConflict,
      getConclusion,
      getConclusionColor,
      // 快讯相关
      latestFlashNews,
      formatNewsTime,
      getImpactColor,
      // 财经事件相关
      topCalendarEvent,
      formatEventTime,
      getCurrencyColor,
      getEventResultColor,
      getEventResultLabel
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
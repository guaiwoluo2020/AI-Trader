<template>
  <v-container fluid>
    <v-row>
      <v-col cols="12">
        <h1 class="mb-4">行情分析</h1>
      </v-col>
    </v-row>

    <!-- 转折点提醒通知 -->
    <v-row v-if="pivotAlerts.length > 0">
      <v-col cols="12">
        <v-alert
          v-for="(alert, index) in pivotAlerts"
          :key="index"
          :type="getAlertType(alert)"
          dismissible
          class="mb-2"
          @input="removeAlert(index)"
        >
          <div class="d-flex flex-wrap align-center">
            <strong>{{ alert.symbol }} {{ alert.period }}</strong>
            <span class="ml-2">
              {{ alert.is_breakthrough ? '已突破' : '接近' }}{{ alert.direction === 'high' ? '高点' : '低点' }}
            </span>
            <v-chip small class="ml-2">{{ alert.pivot_price }}</v-chip>
            <span class="ml-2">当前价格: {{ alert.current_price }}</span>
            <span v-if="!alert.is_breakthrough" class="ml-2">距离: {{ alert.distance_pct }}%</span>
          </div>

          <!-- 如果有待确认订单，显示订单信息和操作按钮 -->
          <div v-if="alert.pending_order" class="mt-3 pa-2 grey lighten-4 rounded">
            <div class="text-subtitle-2 font-weight-bold mb-2">
              <v-icon small color="primary" class="mr-1">mdi-file-document-edit</v-icon>
              自动生成交易指令
            </div>
            <div class="mb-2">
              <v-chip :color="alert.pending_order.action === 'b' ? 'success' : 'error'" x-small class="mr-2">
                {{ alert.pending_order.action === 'b' ? '买入' : '卖出' }}
              </v-chip>
              <span class="mr-3">价格: {{ alert.pending_order.price?.toFixed(2) }}</span>
            </div>
            <!-- 可编辑字段 -->
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
                  @click="confirmAlertOrder(alert.pending_order, index)"
                >
                  <v-icon left small>mdi-check</v-icon>
                  确认
                </v-btn>
                <v-btn
                  color="error"
                  small
                  outlined
                  :loading="rejectingOrderId === alert.pending_order.order_id"
                  @click="rejectAlertOrder(alert.pending_order.order_id, index)"
                >
                  放弃
                </v-btn>
              </v-col>
            </v-row>
            <div class="text-caption grey--text mb-1">
              {{ alert.pending_order.reason }}
            </div>
            <div class="text-caption grey--text">
              <v-icon small>mdi-clock-outline</v-icon>
              3分钟内未操作将自动移除
            </div>
          </div>
        </v-alert>
      </v-col>
    </v-row>

    <!-- 控制面板 -->
    <v-row>
      <v-col cols="12" md="6">
        <v-select
          v-model="selectedSymbol"
          :items="symbols"
          label="交易品种"
          outlined
          dense
          @change="onSymbolChange"
        ></v-select>
      </v-col>
      <v-col cols="12" md="6">
        <v-select
          v-model="selectedPeriod"
          :items="periods"
          label="K线周期"
          outlined
          dense
          @change="onPeriodChange"
        ></v-select>
      </v-col>
    </v-row>

    <!-- 当前持仓 -->
    <v-row>
      <v-col cols="12">
        <v-card>
          <v-card-title>
            <v-icon class="mr-2">mdi-briefcase</v-icon>
            当前持仓
          </v-card-title>
          <v-card-text>
            <div v-if="positions.length > 0">
              <v-simple-table>
                <template v-slot:default>
                  <thead>
                    <tr>
                      <th>品种</th>
                      <th>订单号</th>
                      <th>方向</th>
                      <th>手数</th>
                      <th>开仓价</th>
                      <th>当前价</th>
                      <th>盈亏</th>
                      <th>止损距离</th>
                      <th>止盈距离</th>
                      <th>操作</th>
                    </tr>
                  </thead>
                  <tbody>
                    <tr v-for="pos in positions" :key="pos.ticket">
                      <td>{{ pos.symbol }}</td>
                      <td>{{ pos.ticket }}</td>
                      <td>
                        <v-chip :color="pos.type === 'BUY' ? 'success' : 'error'" x-small>
                          {{ pos.type === 'BUY' ? '买入' : '卖出' }}
                        </v-chip>
                      </td>
                      <td>{{ pos.volume }}</td>
                      <td>{{ pos.priceOpen?.toFixed(2) }}</td>
                      <td>{{ pos.currentPrice?.toFixed(2) }}</td>
                      <td :class="pos.profit >= 0 ? 'success--text' : 'error--text'">
                        {{ pos.profit >= 0 ? '+' : '' }}{{ pos.profit?.toFixed(2) }}
                      </td>
                      <td>{{ pos.distanceSL?.toFixed(2) || '-' }}</td>
                      <td>{{ pos.distanceTP?.toFixed(2) || '-' }}</td>
                      <td>
                        <v-btn
                          color="error"
                          x-small
                          :loading="closingTicket === pos.ticket"
                          @click="closePosition(pos)"
                        >
                          平仓
                        </v-btn>
                      </td>
                    </tr>
                  </tbody>
                </template>
              </v-simple-table>
            </div>
            <div v-else class="text-center py-4 grey--text">
              当前无持仓
            </div>
          </v-card-text>
        </v-card>
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
          <v-icon left small>mdi-lan-connect</v-icon>
          {{ wsConnected ? 'WebSocket 已连接' : 'WebSocket 断开' }}
        </v-chip>
      </v-col>
    </v-row>

    <!-- 趋势分析 -->
    <v-row>
      <v-col cols="12">
        <v-card>
          <v-card-title>
            <v-icon class="mr-2">mdi-trending-up</v-icon>
            趋势分析
          </v-card-title>
          <v-card-text>
            <v-row v-if="trendData.resonance">
              <!-- 共振状态 -->
              <v-col cols="12" md="4">
                <v-card outlined>
                  <v-card-text class="text-center">
                    <div class="text-h6 mb-2">多周期共振</div>
                    <v-chip
                      :color="getResonanceColor(trendData.resonance.resonance)"
                      size="large"
                    >
                      {{ trendData.resonance.signal }}
                    </v-chip>
                    <div class="mt-2 text-caption">
                      趋势强度: {{ trendData.resonance.strength }}%
                    </div>
                  </v-card-text>
                </v-card>
              </v-col>

              <!-- 各周期趋势 -->
              <v-col cols="12" md="8">
                <v-row>
                  <v-col
                    v-for="(state, period) in trendData.resonance.periods"
                    :key="period"
                    cols="12"
                    sm="6"
                    md="4"
                  >
                    <v-card outlined class="trend-card">
                      <v-card-text class="pa-2">
                        <div class="d-flex justify-space-between align-center mb-1">
                          <span class="text-subtitle-2 font-weight-bold">{{ period }}</span>
                          <v-chip
                            :color="getTrendColor(state.trend)"
                            x-small
                          >
                            {{ getTrendLabel(state.trend) }}
                          </v-chip>
                        </div>
                        <div class="text-caption">
                          <span class="grey--text">强度:</span> {{ state.strength }}%
                          <span class="ml-2 grey--text">ADX:</span> {{ state.adx }}
                        </div>
                        <div class="text-caption">
                          <span class="grey--text">MA10:</span> {{ state.ma_fast?.toFixed(2) }}
                          <span class="ml-2 grey--text">MA20:</span> {{ state.ma_slow?.toFixed(2) }}
                        </div>
                        <div class="text-caption mt-1 reason-text" :title="state.reason">
                          <v-icon x-small color="info" class="mr-1">mdi-information-outline</v-icon>
                          {{ state.reason || '分析中...' }}
                        </div>
                      </v-card-text>
                    </v-card>
                  </v-col>
                </v-row>
              </v-col>
            </v-row>
            <div v-else class="text-center py-4 grey--text">
              暂无趋势分析数据
            </div>
          </v-card-text>
        </v-card>
      </v-col>
    </v-row>

    <!-- 交易配置 -->
    <v-row>
      <v-col cols="12">
        <v-card>
          <v-card-title>
            <v-icon class="mr-2">mdi-cog</v-icon>
            自动交易配置
          </v-card-title>
          <v-card-text>
            <v-row align="center">
              <v-col cols="12">
                <v-switch
                  v-model="tradeConfig.enabled"
                  label="启用自动生成"
                  @change="saveTradeConfig"
                ></v-switch>
              </v-col>
            </v-row>

            <!-- 品种配置表格 -->
            <div class="text-subtitle-2 mt-2 mb-2">品种配置</div>
            <v-simple-table dense>
              <template v-slot:default>
                <thead>
                  <tr>
                    <th>品种</th>
                    <th>手数</th>
                    <th>止损偏移(点)</th>
                    <th>操作</th>
                  </tr>
                </thead>
                <tbody>
                  <tr v-for="(config, symbol) in tradeConfig.symbol_config" :key="symbol">
                    <td>
                      <strong>{{ symbol }}</strong>
                    </td>
                    <td>
                      <v-text-field
                        v-model.number="config.volume"
                        type="number"
                        step="0.01"
                        min="0.01"
                        dense
                        hide-details
                        style="width: 80px"
                      ></v-text-field>
                    </td>
                    <td>
                      <v-text-field
                        v-model.number="config.sl_offset"
                        type="number"
                        step="0.01"
                        min="0"
                        dense
                        hide-details
                        style="width: 80px"
                      ></v-text-field>
                    </td>
                    <td>
                      <v-btn x-small color="primary" @click="saveTradeConfig">保存</v-btn>
                    </td>
                  </tr>
                </tbody>
              </template>
            </v-simple-table>

            <!-- 添加新品种配置 -->
            <v-row class="mt-3" align="center">
              <v-col cols="4">
                <v-select
                  v-model="newSymbol"
                  :items="availableSymbols"
                  label="选择品种"
                  dense
                  hide-details
                  @change="onSymbolSelect"
                ></v-select>
              </v-col>
              <v-col cols="3">
                <v-text-field
                  v-model.number="newVolume"
                  label="手数"
                  type="number"
                  step="0.01"
                  min="0.01"
                  dense
                  hide-details
                ></v-text-field>
              </v-col>
              <v-col cols="3">
                <v-text-field
                  v-model.number="newSlOffset"
                  label="止损偏移"
                  type="number"
                  step="0.01"
                  min="0"
                  dense
                  hide-details
                ></v-text-field>
              </v-col>
              <v-col cols="2">
                <v-btn small color="success" @click="addSymbolConfig">添加</v-btn>
              </v-col>
            </v-row>

            <div class="text-caption grey--text mt-3">
              <v-icon small>mdi-information</v-icon>
              M1周期接近转折点时自动生成交易指令。止损偏移为固定点数，如GOLD设0.5表示止损在转折点±0.5点。
            </div>
          </v-card-text>
        </v-card>
      </v-col>
    </v-row>

    <!-- K线图表 -->
    <v-row>
      <v-col cols="12">
        <v-card>
          <v-card-title>
            <v-icon class="mr-2">mdi-chart-candlestick</v-icon>
            K线图表 - {{ selectedSymbol }} {{ selectedPeriod }}
          </v-card-title>
          <v-card-text>
            <div v-if="klines.length === 0 && !loading" class="text-center py-10">
              <v-icon size="64" color="grey lighten-1">mdi-chart-box-outline</v-icon>
              <p class="mt-4 grey--text">暂无K线数据，请等待EA推送数据</p>
            </div>
            <div v-else ref="chartContainer" style="height: 400px;"></div>
          </v-card-text>
        </v-card>
      </v-col>
    </v-row>

    <!-- 行情状态 -->
    <v-row>
      <v-col cols="12">
        <v-card>
          <v-card-title>
            <v-icon class="mr-2">mdi-information</v-icon>
            数据状态
          </v-card-title>
          <v-card-text>
            <v-simple-table v-if="marketStatus.store">
              <template v-slot:default>
                <thead>
                  <tr>
                    <th>品种</th>
                    <th>周期</th>
                    <th>K线数量</th>
                    <th>已初始化</th>
                    <th>转折点数量</th>
                  </tr>
                </thead>
                <tbody>
                  <template v-for="(symbolData, symbol) in marketStatus.store">
                    <tr v-for="(periodData, period) in symbolData" :key="`${symbol}-${period}`">
                      <td>{{ symbol }}</td>
                      <td>{{ period }}</td>
                      <td>{{ periodData.count }}</td>
                      <td>
                        <v-chip x-small :color="periodData.initialized ? 'success' : 'warning'">
                          {{ periodData.initialized ? '是' : '否' }}
                        </v-chip>
                      </td>
                      <td>
                        {{ (marketStatus.pivots && marketStatus.pivots[symbol] && marketStatus.pivots[symbol][period]) ? marketStatus.pivots[symbol][period].pivot_count : 0 }}
                      </td>
                    </tr>
                  </template>
                </tbody>
              </template>
            </v-simple-table>
            <div v-else class="text-center py-4 grey--text">
              暂无状态数据
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
import { ref, computed, onMounted, onUnmounted, watch, nextTick } from 'vue'
import { marketAPI } from '@/api/market'
import * as echarts from 'echarts'

export default {
  name: 'Market',
  setup() {
    // 数据
    const selectedSymbol = ref('')
    const selectedPeriod = ref('H1')
    const symbols = ref([])
    const periods = ref([
      { title: '4小时', value: 'H4' },
      { title: '1小时', value: 'H1' },
      { title: '15分钟', value: 'M15' },
      { title: '5分钟', value: 'M5' },
      { title: '1分钟', value: 'M1' }
    ])

    const klines = ref([])
    const allPivots = ref([])
    const marketStatus = ref({})
    const thresholds = ref({})
    const pivotAlerts = ref([])
    const loading = ref(false)
    const showError = ref(false)

    // 持仓数据
    const positions = ref([])
    const closingTicket = ref(null)
    const errorMessage = ref('')

    // 趋势分析相关
    const trendData = ref({})
    const loadingTrend = ref(false)
    const pendingOrders = ref([])

    // 交易配置
    const tradeConfig = ref({
      enabled: true,
      default_volume: 0.01,
      default_sl_offset: 0.05,
      symbol_config: {}
    })

    // 添加新品种
    const newSymbol = ref('')
    const newVolume = ref(0.01)
    const newSlOffset = ref(0.05)

    // 订单确认/放弃状态
    const confirmingOrderId = ref(null)
    const rejectingOrderId = ref(null)

    // WebSocket
    const ws = ref(null)
    const wsConnected = ref(false)
    const chartContainer = ref(null)
    let chartInstance = null

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

    // 可用品种列表（已连接但未配置的）
    const availableSymbols = computed(() => {
      const configured = Object.keys(tradeConfig.value.symbol_config || {})
      return symbols.value.filter(s => !configured.includes(s))
    })

    // 方法
    const loadData = async () => {
      if (!selectedSymbol.value) return
      loading.value = true
      try {
        // 加载K线数据
        const klineData = await marketAPI.getKlines(selectedSymbol.value, selectedPeriod.value, 200)
        klines.value = klineData.data || []

        // 加载转折点数据
        const pivotData = await marketAPI.getPivots(selectedSymbol.value)
        if (pivotData.data) {
          const pivots = []
          for (const period in pivotData.data) {
            pivotData.data[period].forEach(p => {
              pivots.push({ ...p, period })
            })
          }
          allPivots.value = pivots
        }

        // 更新图表
        await nextTick()
        renderChart()

      } catch (err) {
        showError.value = true
        errorMessage.value = `加载数据失败: ${err.message}`
      } finally {
        loading.value = false
      }
    }

    // 交易品种变化时，同时加载K线和趋势
    const onSymbolChange = () => {
      loadData()
      loadTrend()
    }

    // K线周期变化时，加载K线数据
    const onPeriodChange = () => {
      loadData()
    }

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

    const loadSymbols = async () => {
      try {
        const data = await marketAPI.getSymbols()
        symbols.value = data.symbols || []
        // 如果有数据且当前未选择，自动选择第一个
        if (symbols.value.length > 0 && !selectedSymbol.value) {
          selectedSymbol.value = symbols.value[0]
          loadData()
        }
      } catch (err) {
        console.error('加载品种列表失败:', err)
      }
    }

    const connectWebSocket = () => {
      ws.value = marketAPI.createWebSocket(
        // onMessage
        (data) => {
          if (data.type === 'pivot_alert') {
            // 添加新的提醒到列表顶部
            pivotAlerts.value.unshift(data)
            // 只保留最近10条
            if (pivotAlerts.value.length > 10) {
              pivotAlerts.value.pop()
            }
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

    const renderChart = () => {
      if (!chartContainer.value || klines.value.length === 0) return

      if (chartInstance) {
        chartInstance.dispose()
      }

      chartInstance = echarts.init(chartContainer.value)

      const data = klines.value
      const dates = data.map(k => k.timestamp)
      const ohlc = data.map(k => [k.open, k.close, k.low, k.high])

      // 获取当前周期的转折点
      const periodPivots = allPivots.value.filter(p => p.period === selectedPeriod.value)
      const highMarkers = periodPivots
        .filter(p => p.direction === 'high')
        .map(p => ({
          coord: [p.timestamp, p.price],
          name: '高点',
          itemStyle: { color: '#ef5350' }
        }))
      const lowMarkers = periodPivots
        .filter(p => p.direction === 'low')
        .map(p => ({
          coord: [p.timestamp, p.price],
          name: '低点',
          itemStyle: { color: '#26a69a' }
        }))

      const option = {
        tooltip: {
          trigger: 'axis',
          axisPointer: { type: 'cross' }
        },
        legend: {
          data: ['K线', 'MA5', 'MA10', 'MA20']
        },
        grid: {
          left: '10%',
          right: '10%',
          bottom: '15%'
        },
        xAxis: {
          type: 'category',
          data: dates,
          scale: true,
          boundaryGap: false,
          axisLine: { onZero: false },
          splitLine: { show: false },
          min: 'dataMin',
          max: 'dataMax'
        },
        yAxis: {
          scale: true,
          splitArea: { show: true }
        },
        dataZoom: [
          {
            type: 'inside',
            start: 50,
            end: 100
          },
          {
            show: true,
            type: 'slider',
            top: '90%',
            start: 50,
            end: 100
          }
        ],
        series: [
          {
            name: 'K线',
            type: 'candlestick',
            data: ohlc,
            itemStyle: {
              color: '#ef5350',
              color0: '#26a69a',
              borderColor: '#ef5350',
              borderColor0: '#26a69a'
            },
            markPoint: {
              data: [...highMarkers, ...lowMarkers],
              symbolSize: 30,
              label: {
                show: true,
                fontSize: 10
              }
            }
          }
        ]
      }

      chartInstance.setOption(option)
    }

    const getPivotColor = (period) => {
      const colors = {
        'H4': 'red',
        'H1': 'orange',
        'M15': 'yellow darken-2',
        'M5': 'green',
        'M1': 'blue'
      }
      return colors[period] || 'grey'
    }

    const getThresholdLabel = (period) => {
      const threshold = thresholds.value[period]
      if (threshold) {
        return `±${threshold.percent}`
      }
      return period
    }

    const getAlertType = (alert) => {
      // 突破用 error 类型（红色），接近用 warning（黄色）
      if (alert.is_breakthrough) {
        return 'error'
      }
      return 'warning'
    }

    // 趋势分析相关方法
    const loadTrend = async () => {
      if (!selectedSymbol.value) return
      loadingTrend.value = true
      try {
        const data = await marketAPI.getTrend(selectedSymbol.value)
        trendData.value = data
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

    const loadTradeConfig = async () => {
      try {
        const data = await marketAPI.getTradeConfig()
        if (data.config) {
          tradeConfig.value = {
            enabled: data.config.enabled,
            default_volume: data.config.default_volume,
            default_sl_offset: data.config.default_sl_offset,
            symbol_config: data.config.symbol_config || {}
          }
        }
      } catch (err) {
        console.error('加载交易配置失败:', err)
      }
    }

    // 加载持仓数据
    const loadPositions = async () => {
      try {
        const data = await marketAPI.getStatistics(1)
        if (data.statistics && data.statistics.length > 0) {
          const latest = data.statistics[0]
          // 处理持仓数据，添加当前价格
          if (latest.positions && latest.positions.length > 0) {
            positions.value = latest.positions.map(pos => ({
              ...pos,
              symbol: latest.symbol,
              currentPrice: latest.bidPrice // 简化处理
            }))
          } else {
            positions.value = []
          }
        }
      } catch (err) {
        console.error('加载持仓数据失败:', err)
      }
    }

    // 平仓
    const closePosition = async (pos) => {
      closingTicket.value = pos.ticket
      try {
        const data = await marketAPI.closePosition(pos.ticket, pos.symbol)
        if (data.status === 'ok') {
          // 刷新持仓
          await loadPositions()
        } else {
          errorMessage.value = data.message || '平仓失败'
          showError.value = true
        }
      } catch (err) {
        errorMessage.value = `平仓失败: ${err.message}`
        showError.value = true
      } finally {
        closingTicket.value = null
      }
    }

    const saveTradeConfig = async () => {
      try {
        const data = await marketAPI.updateTradeConfig({
          enabled: tradeConfig.value.enabled,
          default_volume: tradeConfig.value.default_volume,
          default_sl_offset: tradeConfig.value.default_sl_offset,
          symbol_config: tradeConfig.value.symbol_config
        })
        if (data.status !== 'ok') {
          errorMessage.value = data.message || '保存配置失败'
          showError.value = true
        }
      } catch (err) {
        errorMessage.value = `保存配置失败: ${err.message}`
        showError.value = true
      }
    }

    const addSymbolConfig = () => {
      if (!newSymbol.value) return
      const symbol = newSymbol.value
      tradeConfig.value.symbol_config[symbol] = {
        volume: newVolume.value || 0.01,
        sl_offset: newSlOffset.value || 0.05
      }
      saveTradeConfig()
      // 清空输入
      newSymbol.value = ''
      newVolume.value = 0.01
      newSlOffset.value = 0.05
    }

    const onSymbolSelect = (symbol) => {
      // 如果该品种已有配置，加载出来
      if (symbol && tradeConfig.value.symbol_config && tradeConfig.value.symbol_config[symbol]) {
        const config = tradeConfig.value.symbol_config[symbol]
        newVolume.value = config.volume || 0.01
        newSlOffset.value = config.sl_offset || 0.05
      } else {
        // 没有配置则使用默认值
        newVolume.value = tradeConfig.value.default_volume || 0.01
        newSlOffset.value = tradeConfig.value.default_sl_offset || 0.05
      }
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

    // 从提醒列表中移除
    const removeAlert = (index) => {
      pivotAlerts.value.splice(index, 1)
    }

    // 确认提醒中的订单（可修改参数）
    const confirmAlertOrder = async (order, alertIndex) => {
      confirmingOrderId.value = order.order_id
      try {
        // 发送修改后的订单数据
        const data = await marketAPI.confirmOrderWithUpdate(order.order_id, {
          mount: order.mount,
          sl: order.sl,
          tp: order.tp
        })
        if (data.status === 'ok') {
          // 移除该提醒
          pivotAlerts.value.splice(alertIndex, 1)
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

    // 放弃提醒中的订单
    const rejectAlertOrder = async (orderId, alertIndex) => {
      rejectingOrderId.value = orderId
      try {
        const data = await marketAPI.rejectOrder(orderId)
        if (data.status === 'ok') {
          // 移除该提醒
          pivotAlerts.value.splice(alertIndex, 1)
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

    const getResonanceColor = (resonance) => {
      if (resonance === 'up') return 'success'
      if (resonance === 'down') return 'error'
      return 'grey'
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
      loadSymbols()
      loadStatus()
      loadThresholds()
      loadPendingOrders()
      loadTradeConfig()
      loadPositions()
      connectWebSocket()

      // 定时刷新状态、品种列表和趋势分析
      const statusInterval = setInterval(() => {
        loadStatus()
        loadSymbols()
        loadPendingOrders()
        loadPositions()
        // 自动刷新趋势分析
        if (selectedSymbol.value) {
          loadTrend()
        }
      }, 10000)

      // 窗口大小变化时重新渲染图表
      window.addEventListener('resize', () => {
        if (chartInstance) {
          chartInstance.resize()
        }
      })

      // 清理
      onUnmounted(() => {
        clearInterval(statusInterval)
        if (ws.value) {
          ws.value.close()
        }
        if (chartInstance) {
          chartInstance.dispose()
        }
      })
    })

    return {
      selectedSymbol,
      selectedPeriod,
      symbols,
      periods,
      klines,
      allPivots,
      highPivots,
      lowPivots,
      marketStatus,
      thresholds,
      pivotAlerts,
      loading,
      showError,
      errorMessage,
      wsConnected,
      chartContainer,
      loadData,
      onSymbolChange,
      onPeriodChange,
      getPivotColor,
      getThresholdLabel,
      getAlertType,
      // 趋势分析相关
      trendData,
      loadingTrend,
      pendingOrders,
      loadTrend,
      confirmOrder,
      rejectOrder,
      getResonanceColor,
      getTrendColor,
      getTrendLabel,
      // 交易配置
      tradeConfig,
      saveTradeConfig,
      availableSymbols,
      newSymbol,
      newVolume,
      newSlOffset,
      addSymbolConfig,
      onSymbolSelect,
      // 提醒订单操作
      confirmingOrderId,
      rejectingOrderId,
      removeAlert,
      confirmAlertOrder,
      rejectAlertOrder,
      // 持仓相关
      positions,
      closingTicket,
      loadPositions,
      closePosition
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
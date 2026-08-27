<template>
  <v-container fluid class="operations-page">
    <section class="operations-hero mb-5">
      <div>
        <div class="section-kicker">EA EXECUTION PIPELINE</div>
        <h1>交易指令</h1>
        <p>{{ selectedAccount?.account_name || '当前账户' }} · 跟踪策略指令从等待领取到 MT5 执行回报</p>
      </div>
      <v-chip :color="selectedAccount?.active ? 'success' : 'warning'" variant="flat">
        <v-icon start>mdi-lan-connect</v-icon>
        {{ selectedAccount?.active ? 'MT5 已连接' : 'MT5 未连接' }}
      </v-chip>
    </section>

    <v-row class="mb-2">
      <v-col cols="12" sm="4">
        <v-card class="metric-card" variant="tonal" color="warning">
          <v-card-text><span>待执行指令</span><strong>{{ pendingTrades.length }}</strong></v-card-text>
        </v-card>
      </v-col>
      <v-col cols="12" sm="4">
        <v-card class="metric-card" variant="tonal" color="success">
          <v-card-text><span>成功回报</span><strong>{{ successfulExecutions }}</strong></v-card-text>
        </v-card>
      </v-col>
      <v-col cols="12" sm="4">
        <v-card class="metric-card" variant="tonal" color="error">
          <v-card-text><span>失败回报</span><strong>{{ failedExecutions }}</strong></v-card-text>
        </v-card>
      </v-col>
    </v-row>

    <v-alert v-if="error" type="error" closable class="mb-4" @click:close="error = ''">{{ error }}</v-alert>
    <v-alert v-if="success" type="success" closable class="mb-4" @click:close="success = ''">{{ success }}</v-alert>

    <v-row>
      <v-col cols="12">
        <v-card class="content-card" elevation="0">
          <v-card-title class="d-flex align-center justify-space-between">
            <div class="d-flex align-center"><v-icon class="mr-2" color="success">mdi-check-decagram-outline</v-icon>EA 执行回报</div>
            <v-btn size="small" variant="tonal" prepend-icon="mdi-refresh" :loading="loadingExecutions" @click="loadExecutions">
              刷新
            </v-btn>
          </v-card-title>
          <v-card-text>
            <v-data-table
              :headers="executionHeaders"
              :items="executionReports"
              :loading="loadingExecutions"
              no-data-text="当前账户暂无 EA 执行回报"
              density="compact"
            >
              <template v-slot:item.success="{ item }">
                <v-chip :color="item.success ? 'success' : 'error'" size="small">
                  {{ item.success ? '成交' : '失败' }}
                </v-chip>
              </template>
              <template v-slot:item.price="{ item }">
                {{ formatPrice(item.requested_price) }} → {{ formatPrice(item.executed_price) }}
              </template>
              <template v-slot:item.slippage="{ item }">
                <span :class="Number(item.slippage) > 0 ? 'text-error' : 'text-success'">
                  {{ formatSlippage(item.slippage) }}
                </span>
              </template>
              <template v-slot:item.reported_at="{ item }">
                {{ formatExecutionTime(item.reported_at) }}
              </template>
              <template v-slot:item.mt5_order="{ item }">
                {{ item.mt5_order || '-' }} / {{ item.mt5_deal || '-' }}
              </template>
              <template v-slot:item.error="{ item }">
                {{ item.error_message || (item.retcode ? `retcode: ${item.retcode}` : '-') }}
              </template>
            </v-data-table>
          </v-card-text>
        </v-card>
      </v-col>
    </v-row>

    <v-row>
      <v-col cols="12">
        <v-card class="content-card queue-card" elevation="0">
          <v-card-title class="d-flex align-center justify-space-between">
            <div class="d-flex align-center"><v-icon class="mr-2" color="warning">mdi-tray-arrow-down</v-icon>待执行指令</div>
            <v-btn
              color="error"
              size="small"
              variant="tonal"
              prepend-icon="mdi-delete-sweep-outline"
              :disabled="!pendingTrades.length"
              :loading="clearing"
              @click="clearAllTrades"
            >
              清空全部
            </v-btn>
          </v-card-title>
          <v-card-text>
            <v-data-table
              :headers="tradeHeaders"
              :items="pendingTrades"
              :loading="loadingTrades"
              no-data-text="暂无待执行指令"
              density="compact"
            >
              <template v-slot:item.direction="{ item }">
                <v-chip
                  :color="item.direction === 'BUY' ? 'success' : 'error'"
                  size="small"
                >
                  {{ item.direction === 'BUY' ? '买入' : '卖出' }}
                </v-chip>
              </template>

              <template v-slot:item.timestamp="{ item }">
                {{ formatTime(item.timestamp) }}
              </template>
            </v-data-table>
          </v-card-text>
        </v-card>
      </v-col>
    </v-row>

  </v-container>
</template>

<script>
import { computed, ref, onMounted, onUnmounted, watch } from 'vue'
import { tradingAPI } from '@/api/trading'
import { useAccountContext } from '@/composables/useAccountContext'

export default {
  name: 'TradeOrders',
  setup() {
    const loadingTrades = ref(false)
    const clearing = ref(false)
    const error = ref('')
    const success = ref('')
    const loadingExecutions = ref(false)
    const executionReports = ref([])
    let refreshTimer = null
    const { selectedAccountId, selectedAccount } = useAccountContext()
    const successfulExecutions = computed(() => executionReports.value.filter(item => item.success).length)
    const failedExecutions = computed(() => executionReports.value.filter(item => !item.success).length)

    const tradeHeaders = [
      { title: '品种', key: 'symbol', width: '20%' },
      { title: '方向', key: 'direction', width: '15%' },
      { title: '手数', key: 'volume', width: '15%' },
      { title: '价格', key: 'price', width: '20%' },
      { title: '时间', key: 'timestamp', width: '30%' },
    ]
    const executionHeaders = [
      { title: '回报时间', key: 'reported_at' },
      { title: '品种', key: 'symbol' },
      { title: '结果', key: 'success' },
      { title: '请求价 → 成交价', key: 'price' },
      { title: '滑点', key: 'slippage' },
      { title: 'MT5 订单/成交', key: 'mt5_order' },
      { title: '失败原因', key: 'error' },
    ]

    const pendingTrades = ref([])

    const loadPendingTrades = async () => {
      try {
        loadingTrades.value = true
        error.value = ''
        const data = await tradingAPI.getPendingTrades(selectedAccountId.value)

        // 将对象格式转换为数组格式
        const tradesObj = data.pending_trades || {}
        const tradesArray = []
        Object.keys(tradesObj).forEach(symbol => {
          tradesObj[symbol].forEach(trade => {
            tradesArray.push({
              ...trade,
              direction: trade.action === 'b' ? 'BUY' : 'SELL',
              volume: trade.mount
            })
          })
        })
        pendingTrades.value = tradesArray
      } catch (err) {
        error.value = `加载指令失败: ${err.message}`
        console.error('Load trades error:', err)
      } finally {
        loadingTrades.value = false
      }
    }

    const clearAllTrades = async () => {
      if (!confirm('确定要清空所有待执行指令吗？')) return

      try {
        clearing.value = true
        error.value = ''
        success.value = ''
        await tradingAPI.clearTrades(selectedAccountId.value)
        success.value = '已清空所有指令！'
        await loadPendingTrades()
      } catch (err) {
        error.value = `清空指令失败: ${err.message}`
        console.error('Clear trades error:', err)
      } finally {
        clearing.value = false
      }
    }

    const formatTime = (timestamp) => {
      if (!timestamp) return ''
      return new Date(timestamp * 1000).toLocaleString('zh-CN', { timeZone: 'Asia/Shanghai' })
    }

    const formatExecutionTime = (timestamp) => {
      if (!timestamp) return '-'
      const date = typeof timestamp === 'number'
        ? new Date(timestamp * 1000)
        : new Date(timestamp)
      return date.toLocaleString('zh-CN', { timeZone: 'Asia/Shanghai' })
    }

    const formatPrice = (value) => value == null ? '-' : Number(value).toLocaleString('zh-CN', {
      minimumFractionDigits: 2,
      maximumFractionDigits: 5,
    })
    const formatSlippage = (value) => {
      if (value == null) return '-'
      const number = Number(value)
      return `${number > 0 ? '+' : ''}${number.toFixed(5)}`
    }

    const loadExecutions = async () => {
      if (!selectedAccountId.value) return
      loadingExecutions.value = true
      try {
        const data = await tradingAPI.getTradeExecutions(selectedAccountId.value)
        executionReports.value = data.reports || []
      } catch (err) {
        console.error('加载 EA 执行结果失败:', err)
      } finally {
        loadingExecutions.value = false
      }
    }

    onMounted(() => {
      if (selectedAccountId.value) {
        loadPendingTrades()
        loadExecutions()
      }
      refreshTimer = setInterval(() => {
        if (!selectedAccountId.value) return
        loadPendingTrades()
        loadExecutions()
      }, 10000)
    })

    watch(selectedAccountId, (value) => {
      pendingTrades.value = []
      executionReports.value = []
      if (value) {
        loadPendingTrades()
        loadExecutions()
      }
    })

    onUnmounted(() => clearInterval(refreshTimer))

    return {
      loadingTrades,
      clearing,
      error,
      success,
      tradeHeaders,
      executionHeaders,
      pendingTrades,
      executionReports,
      selectedAccount,
      successfulExecutions,
      failedExecutions,
      loadingExecutions,
      loadPendingTrades,
      loadExecutions,
      clearAllTrades,
      formatTime,
      formatExecutionTime,
      formatPrice,
      formatSlippage,
    }
  },
}
</script>

<style scoped>
.operations-page { max-width: 1600px; padding: 28px; }
.operations-hero { display:flex; align-items:center; justify-content:space-between; gap:24px; padding:26px 30px; color:#f4fbf8; border-radius:22px; background:radial-gradient(circle at 86% 18%,rgba(247,186,67,.28),transparent 28%),linear-gradient(125deg,#123c35 0%,#176b56 58%,#0d8f70 100%); box-shadow:0 18px 40px rgba(20,82,68,.18); }
.operations-hero h1 { margin:2px 0 6px; font-size:clamp(1.8rem,3vw,2.7rem); line-height:1.05; }
.operations-hero p { margin:0; color:rgba(244,251,248,.76); }
.section-kicker { color:#f4c96b; font-size:.72rem; font-weight:800; letter-spacing:.16em; }
.metric-card .v-card-text { display:flex; align-items:baseline; justify-content:space-between; gap:12px; }
.metric-card strong { font-size:1.9rem; }
.content-card { border:1px solid #dce7e0; border-radius:18px; overflow:hidden; background:linear-gradient(155deg,#fff,#f8fbf9); }
.queue-card { border-color:rgba(183,137,24,.24); }
@media (max-width:700px) { .operations-page{padding:16px}.operations-hero{align-items:stretch;flex-direction:column;padding:22px}.operations-hero .v-chip{align-self:flex-start} }
</style>

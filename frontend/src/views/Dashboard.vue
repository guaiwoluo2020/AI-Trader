<template>
  <v-container fluid>
    <v-row>
      <v-col cols="12">
        <h1 class="mb-4">仪表盘</h1>
      </v-col>
    </v-row>

    <!-- 状态卡片 -->
    <v-row>
      <v-col cols="12" sm="6" md="3">
        <v-card>
          <v-card-title class="d-flex align-center">
            <v-icon class="me-2">mdi-connection</v-icon>
            MT5 连接状态
          </v-card-title>
          <v-card-text>
            <v-chip
              :color="mt5Status.connected ? 'success' : 'error'"
              variant="flat"
            >
              {{ mt5Status.connected ? '已连接' : '未连接' }}
            </v-chip>
            <div v-if="mt5Status.binding" class="text-body-2 text-medium-emphasis mt-3">
              {{ mt5Status.binding.mt5_login || '账号待上报' }}
              <span v-if="mt5Status.binding.mt5_server">
                · {{ mt5Status.binding.mt5_server }}
              </span>
            </div>
          </v-card-text>
        </v-card>
      </v-col>

      <v-col cols="12" sm="6" md="3">
        <v-card>
          <v-card-title class="d-flex align-center">
            <v-icon class="me-2">mdi-format-list-bulleted</v-icon>
            待执行指令
          </v-card-title>
          <v-card-text>
            <div class="text-h4">{{ pendingTradesCount }}</div>
          </v-card-text>
        </v-card>
      </v-col>

      <v-col cols="12" sm="6" md="3">
        <v-card>
          <v-card-title class="d-flex align-center">
            <v-icon class="me-2">mdi-chart-line</v-icon>
            统计记录
          </v-card-title>
          <v-card-text>
            <div class="text-h4">{{ statisticsCount }}</div>
          </v-card-text>
        </v-card>
      </v-col>

      <v-col cols="12" sm="6" md="3">
        <v-card>
          <v-card-title class="d-flex align-center">
            <v-icon class="me-2">mdi-currency-usd</v-icon>
            活跃品种
          </v-card-title>
          <v-card-text>
            <div class="text-h4">{{ activeSymbols.length }}</div>
          </v-card-text>
        </v-card>
      </v-col>
    </v-row>

    <v-row>
      <v-col cols="12">
        <v-card>
          <v-card-title class="d-flex align-center">
            <v-icon class="me-2">mdi-chart-timeline-variant</v-icon>
            行情接入明细
          </v-card-title>
          <v-card-text>
            <v-alert
              v-if="activeSymbols.length === 0"
              type="info"
              variant="tonal"
            >
              暂未收到 EA 上送的行情数据
            </v-alert>
            <v-list v-else lines="two">
              <v-list-item
                v-for="item in activeSymbols"
                :key="item.symbol"
                :title="item.symbol"
              >
                <template #prepend>
                  <v-avatar color="primary" variant="tonal">
                    <v-icon>mdi-chart-candlestick</v-icon>
                  </v-avatar>
                </template>
                <template #subtitle>
                  <div class="period-list mt-2">
                    <v-chip
                      v-for="period in item.periods"
                      :key="period.name"
                      color="primary"
                      size="small"
                      variant="tonal"
                    >
                      {{ period.name }} · {{ period.count }} 根
                    </v-chip>
                  </div>
                </template>
              </v-list-item>
            </v-list>
          </v-card-text>
        </v-card>
      </v-col>
    </v-row>

    <!-- 错误信息 -->
    <v-row v-if="error">
      <v-col cols="12">
        <v-alert type="error" dismissible>
          {{ error }}
        </v-alert>
      </v-col>
    </v-row>
  </v-container>
</template>

<script>
import { onMounted, onUnmounted, ref } from 'vue'
import { marketAPI } from '@/api/market'
import { mt5API, tradingAPI } from '@/api/trading'
import {
  countPendingTrades,
  normalizeActiveSymbols,
} from '@/utils/dashboard-view-data'

export default {
  name: 'Dashboard',
  setup() {
    const mt5Status = ref({ connected: false, binding: null })
    const pendingTradesCount = ref(0)
    const statisticsCount = ref(0)
    const activeSymbols = ref([])
    const error = ref('')
    let refreshTimer = null

    const loadData = async () => {
      try {
        error.value = ''

        const [connection, pendingTrades, statistics, marketStatus] = await Promise.all([
          mt5API.status(),
          tradingAPI.getPendingTrades(),
          tradingAPI.getStatistics(),
          marketAPI.getStatus(),
        ])

        mt5Status.value = connection
        pendingTradesCount.value = countPendingTrades(pendingTrades)
        statisticsCount.value = (statistics.statistics || []).length
        activeSymbols.value = normalizeActiveSymbols(marketStatus)

      } catch (err) {
        error.value = `加载数据失败: ${err.message}`
        console.error('Dashboard error:', err)
      }
    }

    onMounted(() => {
      loadData()
      // 每30秒自动刷新
      refreshTimer = setInterval(loadData, 30000)
    })

    onUnmounted(() => {
      clearInterval(refreshTimer)
    })

    return {
      mt5Status,
      pendingTradesCount,
      statisticsCount,
      activeSymbols,
      error,
      loadData,
    }
  },
}
</script>

<style scoped>
.period-list {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}
</style>

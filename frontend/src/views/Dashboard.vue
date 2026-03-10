<template>
  <v-container fluid>
    <v-row>
      <v-col cols="12">
        <h1 class="mb-4">仪表板</h1>
      </v-col>
    </v-row>

    <!-- 状态卡片 -->
    <v-row>
      <v-col cols="12" sm="6" md="3">
        <v-card>
          <v-card-title class="d-flex align-center">
            <v-icon class="me-2">mdi-heart</v-icon>
            服务状态
          </v-card-title>
          <v-card-text>
            <v-chip
              :color="status.ok ? 'success' : 'error'"
              variant="flat"
            >
              {{ status.ok ? '正常' : '异常' }}
            </v-chip>
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
            <div class="text-h4">{{ activeSymbolsCount }}</div>
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
import { ref, onMounted } from 'vue'
import { tradingAPI } from '@/api/trading'

export default {
  name: 'Dashboard',
  setup() {
    const status = ref({ ok: false })
    const pendingTradesCount = ref(0)
    const statisticsCount = ref(0)
    const activeSymbolsCount = ref(0)
    const error = ref('')

    const loadData = async () => {
      try {
        error.value = ''

        // 获取服务状态
        const statusData = await tradingAPI.getStatus()
        status.value = { ok: statusData.status === 'ok' }

        // 获取待执行指令数量
        const pendingTrades = await tradingAPI.getPendingTrades()
        pendingTradesCount.value = pendingTrades.length || 0

        // 获取统计数据数量
        const statistics = await tradingAPI.getStatistics()
        statisticsCount.value = (statistics.statistics || []).length

        // 计算活跃品种数量
        const symbols = new Set()
        if (pendingTrades && pendingTrades.length > 0) {
          pendingTrades.forEach(trade => {
            if (trade.symbol) symbols.add(trade.symbol)
          })
        }
        activeSymbolsCount.value = symbols.size

      } catch (err) {
        error.value = `加载数据失败: ${err.message}`
        console.error('Dashboard error:', err)
      }
    }

    onMounted(() => {
      loadData()
      // 每30秒自动刷新
      setInterval(loadData, 30000)
    })

    return {
      status,
      pendingTradesCount,
      statisticsCount,
      activeSymbolsCount,
      error,
      loadData,
    }
  },
}
</script>
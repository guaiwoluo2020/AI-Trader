<template>
  <v-container fluid>
    <v-row>
      <v-col cols="12">
        <h1 class="mb-4">统计数据分析</h1>
      </v-col>
    </v-row>

    <!-- 汇总统计 -->
    <v-row>
      <v-col cols="12" md="3">
        <v-card>
          <v-card-title class="d-flex align-center">
            <v-icon class="me-2">mdi-counter</v-icon>
            总记录数
          </v-card-title>
          <v-card-text>
            <div class="text-h4">{{ totalRecords }}</div>
          </v-card-text>
        </v-card>
      </v-col>

      <v-col cols="12" md="3">
        <v-card>
          <v-card-title class="d-flex align-center">
            <v-icon class="me-2">mdi-trending-up</v-icon>
            平均价格
          </v-card-title>
          <v-card-text>
            <div class="text-h4">{{ averagePrice.toFixed(5) }}</div>
          </v-card-text>
        </v-card>
      </v-col>

      <v-col cols="12" md="3">
        <v-card>
          <v-card-title class="d-flex align-center">
            <v-icon class="me-2">mdi-chart-line</v-icon>
            最高价格
          </v-card-title>
          <v-card-text>
            <div class="text-h4">{{ maxPrice.toFixed(5) }}</div>
          </v-card-text>
        </v-card>
      </v-col>

      <v-col cols="12" md="3">
        <v-card>
          <v-card-title class="d-flex align-center">
            <v-icon class="me-2">mdi-chart-line-variant</v-icon>
            最低价格
          </v-card-title>
          <v-card-text>
            <div class="text-h4">{{ minPrice.toFixed(5) }}</div>
          </v-card-text>
        </v-card>
      </v-col>
    </v-row>

    <!-- 图表 -->
    <v-row>
      <v-col cols="12">
        <v-card>
          <v-card-title>价格趋势图</v-card-title>
          <v-card-text>
            <div ref="chartContainer" style="width: 100%; height: 400px;"></div>
          </v-card-text>
        </v-card>
      </v-col>
    </v-row>

    <!-- 数据表格 -->
    <v-row>
      <v-col cols="12">
        <v-card>
          <v-card-title>详细数据</v-card-title>
          <v-card-text>
            <v-data-table
              :headers="tableHeaders"
              :items="statistics"
              :loading="loading"
              :items-per-page="itemsPerPage"
              no-data-text="暂无统计数据"
              density="compact"
            >
              <template v-slot:item.bidPrice="{ item }">
                {{ item.bidPrice.toFixed(2) }}
              </template>

              <template v-slot:item.askPrice="{ item }">
                {{ item.askPrice.toFixed(2) }}
              </template>

              <template v-slot:item.balance="{ item }">
                {{ item.balance.toFixed(2) }}
              </template>
            </v-data-table>
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
import { ref, onMounted, nextTick } from 'vue'
import * as echarts from 'echarts'
import { tradingAPI } from '@/api/trading'

export default {
  name: 'Statistics',
  setup() {
    const chartContainer = ref(null)
    const chart = ref(null)
    const loading = ref(false)
    const error = ref('')
    const statistics = ref([])
    const itemsPerPage = ref(10)

    const totalRecords = ref(0)
    const averagePrice = ref(0)
    const maxPrice = ref(0)
    const minPrice = ref(0)

    const tableHeaders = [
      { title: '时间', key: 'timestamp', width: '20%' },
      { title: '品种', key: 'symbol', width: '15%' },
      { title: '买价', key: 'bidPrice', width: '15%' },
      { title: '卖价', key: 'askPrice', width: '15%' },
      { title: 'Tick数', key: 'tickCount', width: '15%' },
      { title: '余额', key: 'balance', width: '20%' },
    ]

    const loadStatistics = async () => {
      try {
        loading.value = true
        error.value = ''
        const data = await tradingAPI.getStatistics()
        statistics.value = data.statistics || []

        // 计算统计信息
        if (statistics.value.length > 0) {
          totalRecords.value = statistics.value.length
          const prices = statistics.value.map(item => (item.bidPrice + item.askPrice) / 2)
          averagePrice.value = prices.reduce((a, b) => a + b, 0) / prices.length
          maxPrice.value = Math.max(...prices)
          minPrice.value = Math.min(...prices)
        } else {
          totalRecords.value = 0
          averagePrice.value = 0
          maxPrice.value = 0
          minPrice.value = 0
        }

        // 更新图表
        updateChart()
      } catch (err) {
        error.value = `加载统计数据失败: ${err.message}`
        console.error('Load statistics error:', err)
      } finally {
        loading.value = false
      }
    }

    const updateChart = async () => {
      await nextTick()
      if (!chartContainer.value) return

      if (chart.value) {
        chart.value.dispose()
      }

      chart.value = echarts.init(chartContainer.value)

      const option = {
        title: {
          text: '价格趋势'
        },
        tooltip: {
          trigger: 'axis'
        },
        xAxis: {
          type: 'category',
          data: statistics.value.map(item => item.timestamp)
        },
        yAxis: {
          type: 'value',
          name: '价格'
        },
        series: [{
          name: '买价',
          type: 'line',
          data: statistics.value.map(item => item.bidPrice),
          smooth: true,
          lineStyle: {
            color: '#1976D2'
          }
        }, {
          name: '卖价',
          type: 'line',
          data: statistics.value.map(item => item.askPrice),
          smooth: true,
          lineStyle: {
            color: '#4CAF50'
          }
        }]
      }

      chart.value.setOption(option)
    }

    const formatTime = (timestamp) => {
      if (!timestamp) return ''
      return new Date(timestamp * 1000).toLocaleString('zh-CN')
    }

    onMounted(() => {
      loadStatistics()
      // 每30秒自动刷新
      setInterval(loadStatistics, 30000)
    })

    return {
      chartContainer,
      loading,
      error,
      statistics,
      itemsPerPage,
      totalRecords,
      averagePrice,
      maxPrice,
      minPrice,
      tableHeaders,
      loadStatistics,
      formatTime,
    }
  },
}
</script>
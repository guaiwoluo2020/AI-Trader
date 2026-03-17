<template>
  <v-container fluid>
    <!-- 页面标题 -->
    <v-row class="mb-4">
      <v-col cols="12">
        <h2 class="text-h4">
          <v-icon large class="mr-2">mdi-newspaper-variant-outline</v-icon>
          财经日历与新闻
        </h2>
      </v-col>
    </v-row>

    <!-- 状态卡片 -->
    <v-row class="mb-4">
      <v-col cols="12" md="3">
        <v-card outlined>
          <v-card-text class="d-flex align-center">
            <v-icon large color="primary" class="mr-3">mdi-calendar-check</v-icon>
            <div>
              <div class="text-caption text-grey">日历天数</div>
              <div class="text-h5">{{ status.store_status?.calendar_dates || 0 }}</div>
            </div>
          </v-card-text>
        </v-card>
      </v-col>
      <v-col cols="12" md="3">
        <v-card outlined>
          <v-card-text class="d-flex align-center">
            <v-icon large color="warning" class="mr-3">mdi-alert-circle</v-icon>
            <div>
              <div class="text-caption text-grey">重要事件</div>
              <div class="text-h5">{{ status.store_status?.calendar_events || 0 }}</div>
            </div>
          </v-card-text>
        </v-card>
      </v-col>
      <v-col cols="12" md="3">
        <v-card outlined>
          <v-card-text class="d-flex align-center">
            <v-icon large color="info" class="mr-3">mdi-lightning-bolt</v-icon>
            <div>
              <div class="text-caption text-grey">快讯数量</div>
              <div class="text-h5">{{ status.store_status?.flash_news_count || 0 }}</div>
            </div>
          </v-card-text>
        </v-card>
      </v-col>
      <v-col cols="12" md="3">
        <v-card outlined>
          <v-card-text class="d-flex align-center">
            <v-icon large :color="status.running ? 'success' : 'error'" class="mr-3">
              {{ status.running ? 'mdi-check-circle' : 'mdi-close-circle' }}
            </v-icon>
            <div>
              <div class="text-caption text-grey">监控状态</div>
              <div class="text-h5">{{ status.running ? '运行中' : '已停止' }}</div>
            </div>
          </v-card-text>
        </v-card>
      </v-col>
    </v-row>

    <!-- 标签页 -->
    <v-card>
      <v-tabs v-model="activeTab" background-color="primary" dark>
        <v-tab>
          <v-icon class="mr-2">mdi-calendar</v-icon>
          财经日历
        </v-tab>
        <v-tab>
          <v-icon class="mr-2">mdi-lightning-bolt</v-icon>
          实时快讯
        </v-tab>
        <v-tab>
          <v-icon class="mr-2">mdi-chart-timeline-variant</v-icon>
          品种影响
        </v-tab>
      </v-tabs>

      <!-- 财经日历 -->
      <v-tab-item>
        <v-card-text>
          <!-- 筛选栏 -->
          <v-row class="mb-4">
            <v-col cols="12" md="4">
              <v-select
                v-model="selectedImportance"
                :items="importanceOptions"
                label="重要性筛选"
                item-title="text"
                item-value="value"
                outlined
                dense
                hide-details
                clearable
              ></v-select>
            </v-col>
            <v-col cols="12" md="4">
              <v-select
                v-model="selectedCurrency"
                :items="currencyOptions"
                label="货币筛选"
                item-title="text"
                item-value="value"
                outlined
                dense
                hide-details
                clearable
              ></v-select>
            </v-col>
            <v-col cols="12" md="4">
              <v-btn color="primary" @click="fetchCalendar" :loading="loading">
                <v-icon class="mr-2">mdi-refresh</v-icon>
                刷新
              </v-btn>
            </v-col>
          </v-row>

          <!-- 事件列表 -->
          <v-data-table
            :headers="calendarHeaders"
            :items="filteredCalendar"
            :loading="loading"
            item-key="id"
            class="elevation-1"
            :items-per-page="20"
          >
            <!-- 重要性 -->
            <template v-slot:item.importance="{ item }">
              <v-chip
                :color="getImportanceColor(item.importance)"
                small
                dark
              >
                {{ getImportanceText(item.importance) }}
              </v-chip>
            </template>

            <!-- 发布时间 -->
            <template v-slot:item.publish_time="{ item }">
              <div>
                <div class="font-weight-medium">{{ formatDate(item.publish_time) }}</div>
                <div class="text-caption text-grey">{{ formatTime(item.publish_time) }}</div>
              </div>
            </template>

            <!-- 影响品种 -->
            <template v-slot:item.symbols="{ item }">
              <v-chip
                v-for="symbol in item.symbols"
                :key="symbol"
                :color="getSymbolColor(symbol)"
                small
                class="mr-1"
              >
                {{ symbol }}
              </v-chip>
            </template>

            <!-- 数值 -->
            <template v-slot:item.values="{ item }">
              <div class="text-caption">
                <div>预期: <span class="font-weight-medium">{{ item.forecast || '--' }}</span></div>
                <div>前值: <span class="text-grey">{{ item.previous || '--' }}</span></div>
                <div v-if="item.actual" class="success--text">
                  实际: {{ item.actual }}
                </div>
              </div>
            </template>

            <!-- 结果 -->
            <template v-slot:item.result="{ item }">
              <v-chip
                v-if="item.result"
                :color="getResultColor(item.result)"
                small
                dark
              >
                {{ getResultText(item.result) }}
              </v-chip>
              <span v-else class="text-grey">待发布</span>
            </template>
          </v-data-table>
        </v-card-text>
      </v-tab-item>

      <!-- 实时快讯 -->
      <v-tab-item>
        <v-card-text>
          <v-btn color="primary" class="mb-4" @click="fetchFlashNews" :loading="loading">
            <v-icon class="mr-2">mdi-refresh</v-icon>
            刷新快讯
          </v-btn>

          <v-timeline v-if="flashNews.length > 0" dense>
            <v-timeline-item
              v-for="news in flashNews"
              :key="news.id"
              :color="news.importance >= 2 ? 'error' : 'info'"
              small
            >
              <v-card outlined class="mb-2">
                <v-card-text>
                  <div class="d-flex justify-space-between align-start">
                    <div class="flex-grow-1">
                      <!-- 讲话者标签 -->
                      <v-chip
                        v-if="news.speaker"
                        color="primary"
                        small
                        class="mr-2 mb-2"
                      >
                        <v-icon small class="mr-1">mdi-account</v-icon>
                        {{ news.speaker }}
                        <span v-if="news.speaker_title" class="ml-1">({{ news.speaker_title }})</span>
                      </v-chip>

                      <!-- 内容 -->
                      <div class="text-body-1 mb-2">{{ news.content }}</div>

                      <!-- 影响分析 -->
                      <div v-if="news.impact && Object.keys(news.impact).length > 0" class="mt-2">
                        <div class="text-caption text-grey mb-1">影响分析:</div>
                        <v-chip
                          v-for="(impact, symbol) in news.impact"
                          :key="symbol"
                          :color="getImpactColor(impact.direction)"
                          small
                          class="mr-1 mb-1"
                        >
                          {{ symbol }}: {{ impact.direction }}
                          <span v-if="impact.reason" class="ml-1">- {{ impact.reason }}</span>
                        </v-chip>
                      </div>
                    </div>

                    <div class="text-caption text-grey ml-4">
                      {{ formatDateTime(news.time) }}
                    </div>
                  </div>
                </v-card-text>
              </v-card>
            </v-timeline-item>
          </v-timeline>

          <v-alert v-else type="info" text>
            暂无快讯数据
          </v-alert>
        </v-card-text>
      </v-tab-item>

      <!-- 品种影响 -->
      <v-tab-item>
        <v-card-text>
          <v-row class="mb-4">
            <v-col cols="12" md="6">
              <v-select
                v-model="selectedSymbol"
                :items="symbolOptions"
                label="选择品种"
                outlined
                @change="fetchSymbolImpact"
              ></v-select>
            </v-col>
          </v-row>

          <v-row v-if="symbolEvents.length > 0">
            <v-col
              v-for="event in symbolEvents"
              :key="event.id"
              cols="12"
              md="6"
            >
              <v-card outlined class="mb-3">
                <v-card-text>
                  <div class="d-flex justify-space-between align-start mb-2">
                    <div>
                      <v-chip
                        :color="getImportanceColor(event.importance)"
                        small
                        dark
                        class="mr-2"
                      >
                        {{ getImportanceText(event.importance) }}
                      </v-chip>
                      <span class="font-weight-medium">{{ event.name }}</span>
                    </div>
                    <v-chip small>{{ event.currency }}</v-chip>
                  </div>

                  <div class="text-caption text-grey mb-2">
                    {{ formatDateTime(event.publish_time) }}
                  </div>

                  <v-row>
                    <v-col cols="4">
                      <div class="text-caption text-grey">预期</div>
                      <div class="font-weight-medium">{{ event.forecast || '--' }}</div>
                    </v-col>
                    <v-col cols="4">
                      <div class="text-caption text-grey">前值</div>
                      <div class="font-weight-medium">{{ event.previous || '--' }}</div>
                    </v-col>
                    <v-col cols="4">
                      <div class="text-caption text-grey">实际</div>
                      <div class="font-weight-medium success--text">{{ event.actual || '待发布' }}</div>
                    </v-col>
                  </v-row>
                </v-card-text>
              </v-card>
            </v-col>
          </v-row>

          <v-alert v-else-if="selectedSymbol" type="info" text>
            该品种暂无即将发布的重要事件
          </v-alert>
        </v-card-text>
      </v-tab-item>
    </v-card>

    <!-- 新闻提醒弹窗 -->
    <v-snackbar
      v-model="snackbar.show"
      :color="snackbar.color"
      :timeout="5000"
      top
      right
    >
      <v-icon class="mr-2">{{ snackbar.icon }}</v-icon>
      {{ snackbar.message }}
      <template v-slot:action>
        <v-btn text @click="snackbar.show = false">关闭</v-btn>
      </template>
    </v-snackbar>
  </v-container>
</template>

<script>
import { ref, computed, onMounted, onUnmounted } from 'vue'
import axios from 'axios'

export default {
  name: 'News',
  setup() {
    const activeTab = ref(0)
    const loading = ref(false)
    const status = ref({
      running: false,
      ws_clients: 0,
      store_status: {
        calendar_dates: 0,
        total_events: 0,
        flash_news_count: 0
      },
      scheduled_events: 0
    })

    const calendar = ref([])
    const flashNews = ref([])
    const symbolEvents = ref([])

    const selectedImportance = ref(null)
    const selectedCurrency = ref(null)
    const selectedSymbol = ref('GOLD')

    const ws = ref(null)
    const snackbar = ref({
      show: false,
      color: 'info',
      icon: 'mdi-bell',
      message: ''
    })

    const importanceOptions = [
      { text: '高影响', value: 3 },
      { text: '中等影响', value: 2 },
      { text: '低影响', value: 1 }
    ]

    // 货币选项（动态生成）
    const currencyOptions = computed(() => {
      const currencies = new Set()
      calendar.value.forEach(e => {
        if (e.currency) currencies.add(e.currency)
      })
      const currencyNames = {
        'USD': '美元 (USD)',
        'EUR': '欧元 (EUR)',
        'GBP': '英镑 (GBP)',
        'JPY': '日元 (JPY)',
        'AUD': '澳元 (AUD)',
        'CAD': '加元 (CAD)',
        'CHF': '瑞郎 (CHF)',
        'NZD': '纽元 (NZD)'
      }
      return Array.from(currencies).sort().map(c => ({
        text: currencyNames[c] || c,
        value: c
      }))
    })

    const symbolOptions = [
      { text: '黄金 (GOLD)', value: 'GOLD' },
      { text: '原油 (OIL)', value: 'OIL' },
      { text: '比特币 (BTC)', value: 'BTC' },
      { text: '标普500 (SPX)', value: 'SPX' },
      { text: '美日 (USDJPY)', value: 'USDJPY' }
    ]

    const calendarHeaders = [
      { text: '重要性', value: 'importance', width: 100 },
      { text: '事件', value: 'name', width: 200 },
      { text: '货币', value: 'currency', width: 80 },
      { text: '发布时间', value: 'publish_time', width: 150 },
      { text: '数值', value: 'values', width: 120 },
      { text: '结果', value: 'result', width: 100 },
      { text: '影响品种', value: 'symbols', width: 200 }
    ]

    const filteredCalendar = computed(() => {
      let items = calendar.value

      if (selectedImportance.value) {
        items = items.filter(e => e.importance === selectedImportance.value)
      }

      if (selectedCurrency.value) {
        items = items.filter(e => e.currency === selectedCurrency.value)
      }

      return items
    })

    // 获取状态
    const fetchStatus = async () => {
      try {
        const response = await axios.get('/api/news/status')
        status.value = response.data.data
      } catch (error) {
        console.error('获取状态失败:', error)
      }
    }

    // 获取财经日历
    const fetchCalendar = async () => {
      loading.value = true
      try {
        const response = await axios.get('/api/news/upcoming', {
          params: { hours: 168 } // 未来7天
        })
        calendar.value = response.data.data || []
      } catch (error) {
        console.error('获取日历失败:', error)
      } finally {
        loading.value = false
      }
    }

    // 获取快讯
    const fetchFlashNews = async () => {
      loading.value = true
      try {
        const response = await axios.get('/api/news/flash', {
          params: { count: 30 }
        })
        flashNews.value = response.data.data || []
      } catch (error) {
        console.error('获取快讯失败:', error)
      } finally {
        loading.value = false
      }
    }

    // 获取品种影响
    const fetchSymbolImpact = async () => {
      if (!selectedSymbol.value) return

      loading.value = true
      try {
        const response = await axios.get(`/api/news/impact/${selectedSymbol.value}`)
        symbolEvents.value = response.data.data || []
      } catch (error) {
        console.error('获取品种影响失败:', error)
      } finally {
        loading.value = false
      }
    }

    // WebSocket连接
    const connectWebSocket = () => {
      const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
      const wsUrl = `${protocol}//${window.location.host}/api/news/ws`

      ws.value = new WebSocket(wsUrl)

      ws.value.onopen = () => {
        console.log('新闻WebSocket已连接')
      }

      ws.value.onmessage = (event) => {
        try {
          const data = JSON.parse(event.data)
          handleWebSocketMessage(data)
        } catch (error) {
          console.error('解析WebSocket消息失败:', error)
        }
      }

      ws.value.onerror = (error) => {
        console.error('WebSocket错误:', error)
      }

      ws.value.onclose = () => {
        console.log('新闻WebSocket已断开，5秒后重连...')
        setTimeout(connectWebSocket, 5000)
      }
    }

    // 处理WebSocket消息
    const handleWebSocketMessage = (data) => {
      switch (data.type) {
        case 'event_reminder':
          showNotification('warning', 'mdi-clock-alert', data.message)
          fetchCalendar()
          break

        case 'event_result':
          showNotification('success', 'mdi-check-circle', data.message)
          fetchCalendar()
          break

        case 'flash_news':
          showNotification('info', 'mdi-lightning-bolt', data.news?.content?.substring(0, 50) + '...')
          fetchFlashNews()
          break

        case 'calendar_update':
          fetchCalendar()
          break
      }
    }

    // 显示通知
    const showNotification = (color, icon, message) => {
      snackbar.value = {
        show: true,
        color,
        icon,
        message
      }
    }

    // 格式化函数
    const formatDate = (dateStr) => {
      if (!dateStr) return '--'
      const date = new Date(dateStr)
      return date.toLocaleDateString('zh-CN', { month: 'short', day: 'numeric' })
    }

    const formatTime = (dateStr) => {
      if (!dateStr) return '--'
      const date = new Date(dateStr)
      return date.toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' })
    }

    const formatDateTime = (dateStr) => {
      if (!dateStr) return '--'
      const date = new Date(dateStr)
      return date.toLocaleString('zh-CN', {
        month: 'short',
        day: 'numeric',
        hour: '2-digit',
        minute: '2-digit'
      })
    }

    const getImportanceColor = (importance) => {
      switch (importance) {
        case 3: return 'error'
        case 2: return 'warning'
        case 1: return 'info'
        default: return 'grey'
      }
    }

    const getImportanceText = (importance) => {
      switch (importance) {
        case 3: return '高'
        case 2: return '中'
        case 1: return '低'
        default: return '--'
      }
    }

    const getSymbolColor = (symbol) => {
      const colors = {
        'GOLD': 'amber',
        'OIL': 'black',
        'BTC': 'orange',
        'SPX': 'blue',
        'USDJPY': 'red'
      }
      return colors[symbol] || 'grey'
    }

    const getResultColor = (result) => {
      switch (result) {
        case 'better': return 'success'
        case 'worse': return 'error'
        case 'in_line': return 'info'
        default: return 'grey'
      }
    }

    const getResultText = (result) => {
      switch (result) {
        case 'better': return '好于预期'
        case 'worse': return '差于预期'
        case 'in_line': return '符合预期'
        default: return '--'
      }
    }

    const getImpactColor = (direction) => {
      switch (direction) {
        case '利好': return 'success'
        case '利空': return 'error'
        case '中性': return 'info'
        default: return 'grey'
      }
    }

    onMounted(() => {
      fetchStatus()
      fetchCalendar()
      fetchFlashNews()
      fetchSymbolImpact()
      connectWebSocket()
    })

    onUnmounted(() => {
      if (ws.value) {
        ws.value.close()
      }
    })

    return {
      activeTab,
      loading,
      status,
      calendar,
      flashNews,
      symbolEvents,
      selectedImportance,
      selectedCurrency,
      selectedSymbol,
      importanceOptions,
      currencyOptions,
      symbolOptions,
      calendarHeaders,
      filteredCalendar,
      snackbar,
      fetchStatus,
      fetchCalendar,
      fetchFlashNews,
      fetchSymbolImpact,
      formatDate,
      formatTime,
      formatDateTime,
      getImportanceColor,
      getImportanceText,
      getSymbolColor,
      getResultColor,
      getResultText,
      getImpactColor
    }
  }
}
</script>

<style scoped>
.v-timeline-item {
  padding-bottom: 0;
}
</style>
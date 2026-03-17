<template>
  <v-container fluid>
    <v-row>
      <v-col cols="12">
        <h1 class="mb-4">系统运行日志</h1>
      </v-col>
    </v-row>

    <v-row>
      <v-col cols="12">
        <v-card>
          <v-card-title>
            <v-icon class="mr-2">mdi-text-box-outline</v-icon>
            实时日志
            <v-spacer></v-spacer>
            <v-btn icon small class="mr-2" @click="loadLogs" :loading="loading">
              <v-icon small>mdi-refresh</v-icon>
            </v-btn>
            <v-btn color="error" small outlined @click="confirmClear">
              <v-icon left small>mdi-delete</v-icon>
              清空
            </v-btn>
          </v-card-title>
          <v-card-text>
            <!-- 过滤器 -->
            <v-row class="mb-2">
              <v-col cols="4">
                <v-select
                  v-model="filterEventTypes"
                  :items="eventTypes"
                  label="事件类型"
                  dense
                  hide-details
                  clearable
                  multiple
                  chips
                  small-chips
                  deletable-chips
                  @change="loadLogs"
                ></v-select>
              </v-col>
              <v-col cols="4">
                <v-text-field
                  v-model="filterSymbol"
                  label="品种"
                  dense
                  hide-details
                  clearable
                  @change="loadLogs"
                ></v-text-field>
              </v-col>
              <v-col cols="4">
                <v-chip :color="wsConnected ? 'success' : 'error'" small>
                  WebSocket: {{ wsConnected ? '已连接' : '未连接' }}
                </v-chip>
              </v-col>
            </v-row>

            <!-- 日志列表 -->
            <div class="log-container" ref="logContainer">
              <div v-if="logs.length === 0" class="text-center grey--text py-8">
                <v-icon large>mdi-text-box-remove-outline</v-icon>
                <div class="mt-2">暂无日志</div>
              </div>
              <div v-else>
                <div
                  v-for="(log, index) in logs"
                  :key="index"
                  class="log-entry"
                  :class="'log-' + log.event_type"
                >
                  <span class="log-time">{{ formatTime(log.timestamp) }}</span>
                  <v-chip
                    x-small
                    :color="getEventColor(log.event_type)"
                    class="mx-2"
                  >
                    {{ log.event_name }}
                  </v-chip>
                  <span v-if="log.symbol" class="log-symbol">[{{ log.symbol }}]</span>
                  <span class="log-message">{{ log.message }}</span>
                </div>
              </div>
            </div>
          </v-card-text>
        </v-card>
      </v-col>
    </v-row>

    <!-- 清空确认对话框 -->
    <v-dialog v-model="clearDialog" max-width="400">
      <v-card>
        <v-card-title>确认清空</v-card-title>
        <v-card-text>确定要清空所有日志吗？此操作不可撤销。</v-card-text>
        <v-card-actions>
          <v-spacer></v-spacer>
          <v-btn text @click="clearDialog = false">取消</v-btn>
          <v-btn color="error" @click="clearLogs">确认清空</v-btn>
        </v-card-actions>
      </v-card>
    </v-dialog>
  </v-container>
</template>

<script>
import { ref, onMounted, onUnmounted, nextTick } from 'vue'
import { marketAPI } from '@/api/market'

export default {
  name: 'SystemLog',
  setup() {
    const logs = ref([])
    const loading = ref(false)
    const wsConnected = ref(false)
    const clearDialog = ref(false)
    const filterEventTypes = ref([])
    const filterSymbol = ref(null)
    const logContainer = ref(null)
    let ws = null

    const eventTypes = [
      // 大模型相关
      { text: '大模型分析开始', value: 'llm_analysis_start' },
      { text: '大模型分析完成', value: 'llm_analysis_complete' },
      { text: '大模型分析错误', value: 'llm_analysis_error' },
      // EA数据推送
      { text: 'EA推送统计数据', value: 'ea_statistics' },
      { text: 'EA推送全量K线', value: 'ea_kline_full' },
      { text: 'EA推送增量K线', value: 'ea_kline_incremental' },
      { text: 'K线数据过期', value: 'ea_kline_stale' },
      { text: 'EA请求交易指令', value: 'ea_trade_request' },
      // MT5财经日历推送
      { text: 'MT5财经日历上报', value: 'mt5_calendar_update' },
      { text: 'MT5事件结果上报', value: 'mt5_event_result' },
      // 转折点相关
      { text: '转折点检测完成', value: 'pivot_detected' },
      { text: '转折点提醒', value: 'pivot_alert' },
      // 交易指令
      { text: '交易指令生成', value: 'order_generated' },
      { text: '交易指令确认', value: 'order_confirmed' },
      { text: '交易指令拒绝', value: 'order_rejected' },
      { text: '平仓指令', value: 'close_position' },
      // 持仓相关
      { text: '持仓数据更新', value: 'position_update' },
      // 新闻爬虫相关
      { text: '新闻爬虫启动', value: 'news_crawler_start' },
      { text: '财经日历获取', value: 'news_calendar_fetch' },
      { text: '财经日历更新', value: 'news_calendar_update' },
      { text: '财经日历获取失败', value: 'news_calendar_fetch_error' },
      { text: '快讯获取', value: 'news_flash_fetch' },
      { text: '快讯获取失败', value: 'news_flash_fetch_error' },
      { text: '事件调度创建', value: 'news_event_scheduled' },
      { text: '事件发布前提醒', value: 'news_event_reminder' },
      { text: '事件结果获取', value: 'news_event_result' },
      { text: '影响分析完成', value: 'news_impact_analysis' },
      { text: '新闻WebSocket推送', value: 'news_ws_broadcast' },
      // 系统事件
      { text: '系统启动', value: 'system_startup' },
      { text: '系统关闭', value: 'system_shutdown' },
      { text: 'WebSocket连接', value: 'websocket_connect' },
      { text: 'WebSocket断开', value: 'websocket_disconnect' },
    ]

    const loadLogs = async () => {
      loading.value = true
      try {
        const data = await marketAPI.getSystemLogs(100, filterEventTypes.value, filterSymbol.value)
        if (data.status === 'ok') {
          logs.value = data.logs
        }
      } catch (err) {
        console.error('加载日志失败:', err)
      } finally {
        loading.value = false
      }
    }

    const connectWebSocket = () => {
      ws = new WebSocket('ws://localhost:8000/ws/market')

      ws.onopen = () => {
        wsConnected.value = true
        console.log('[SystemLog] WebSocket已连接')
      }

      ws.onmessage = (event) => {
        try {
          const data = JSON.parse(event.data)
          if (data.type === 'system_log') {
            // 新日志推送到列表顶部
            logs.value.unshift(data.data)
            // 保持最多200条
            if (logs.value.length > 200) {
              logs.value = logs.value.slice(0, 200)
            }
            // 滚动到顶部
            nextTick(() => {
              if (logContainer.value) {
                logContainer.value.scrollTop = 0
              }
            })
          }
        } catch (e) {
          // 忽略非JSON消息
        }
      }

      ws.onerror = (error) => {
        console.error('[SystemLog] WebSocket错误:', error)
      }

      ws.onclose = () => {
        wsConnected.value = false
        console.log('[SystemLog] WebSocket已断开')
        // 5秒后重连
        setTimeout(connectWebSocket, 5000)
      }
    }

    const confirmClear = () => {
      clearDialog.value = true
    }

    const clearLogs = async () => {
      try {
        await marketAPI.clearSystemLogs()
        logs.value = []
        clearDialog.value = false
      } catch (err) {
        console.error('清空日志失败:', err)
      }
    }

    const formatTime = (timestamp) => {
      if (!timestamp) return ''
      const date = new Date(timestamp)
      return date.toLocaleTimeString('zh-CN', {
        hour: '2-digit',
        minute: '2-digit',
        second: '2-digit'
      })
    }

    const getEventColor = (eventType) => {
      const colors = {
        // 大模型相关
        'llm_analysis_start': 'info',
        'llm_analysis_complete': 'success',
        'llm_analysis_error': 'error',
        // EA数据推送
        'ea_statistics': 'grey',
        'ea_kline_full': 'primary',
        'ea_kline_incremental': 'primary',
        'ea_kline_stale': 'warning',
        'ea_trade_request': 'success',
        // MT5财经日历推送
        'mt5_calendar_update': 'primary',
        'mt5_event_result': 'success',
        // 转折点相关
        'pivot_detected': 'warning',
        'pivot_alert': 'warning',
        // 交易指令
        'order_generated': 'success',
        'order_confirmed': 'success',
        'order_rejected': 'error',
        'close_position': 'error',
        // 持仓相关
        'position_update': 'info',
        // 新闻爬虫相关
        'news_crawler_start': 'success',
        'news_calendar_fetch': 'info',
        'news_calendar_update': 'success',
        'news_calendar_fetch_error': 'error',
        'news_flash_fetch': 'info',
        'news_flash_fetch_error': 'error',
        'news_event_scheduled': 'warning',
        'news_event_reminder': 'warning',
        'news_event_result': 'success',
        'news_impact_analysis': 'primary',
        'news_ws_broadcast': 'info',
        // 系统事件
        'system_startup': 'success',
        'system_shutdown': 'error',
        'websocket_connect': 'success',
        'websocket_disconnect': 'warning',
      }
      return colors[eventType] || 'grey'
    }

    onMounted(() => {
      loadLogs()
      connectWebSocket()
    })

    onUnmounted(() => {
      if (ws) {
        ws.close()
      }
    })

    return {
      logs,
      loading,
      wsConnected,
      clearDialog,
      filterEventTypes,
      filterSymbol,
      logContainer,
      eventTypes,
      loadLogs,
      confirmClear,
      clearLogs,
      formatTime,
      getEventColor
    }
  }
}
</script>

<style scoped>
.log-container {
  max-height: 600px;
  overflow-y: auto;
  background-color: #1e1e1e;
  border-radius: 4px;
  padding: 12px;
  font-family: 'Monaco', 'Menlo', 'Ubuntu Mono', monospace;
  font-size: 13px;
}

.log-entry {
  padding: 6px 0;
  border-bottom: 1px solid #333;
  color: #e0e0e0;
}

.log-entry:last-child {
  border-bottom: none;
}

.log-time {
  color: #888;
  margin-right: 8px;
}

.log-symbol {
  color: #64b5f6;
  margin-right: 8px;
}

.log-message {
  color: #e0e0e0;
}

.log-llm_analysis_start {
  border-left: 3px solid #2196f3;
  padding-left: 8px;
}

.log-llm_analysis_complete {
  border-left: 3px solid #4caf50;
  padding-left: 8px;
}

.log-llm_analysis_error {
  border-left: 3px solid #f44336;
  padding-left: 8px;
}

.log-ea_kline_full {
  border-left: 3px solid #9c27b0;
  padding-left: 8px;
}

.log-ea_kline_incremental {
  border-left: 3px solid #673ab7;
  padding-left: 8px;
}

.log-ea_kline_stale {
  border-left: 3px solid #ff9800;
  padding-left: 8px;
}

.log-ea_statistics {
  border-left: 3px solid #607d8b;
  padding-left: 8px;
}

.log-pivot_alert {
  border-left: 3px solid #ff9800;
  padding-left: 8px;
}

.log-order_generated {
  border-left: 3px solid #4caf50;
  padding-left: 8px;
}

.log-order_confirmed {
  border-left: 3px solid #2e7d32;
  padding-left: 8px;
}

.log-order_rejected {
  border-left: 3px solid #f44336;
  padding-left: 8px;
}

.log-close_position {
  border-left: 3px solid #e91e63;
  padding-left: 8px;
}

.log-position_update {
  border-left: 3px solid #00bcd4;
  padding-left: 8px;
}

/* 新闻爬虫相关样式 */
.log-news_crawler_start {
  border-left: 3px solid #4caf50;
  padding-left: 8px;
}

.log-news_calendar_fetch {
  border-left: 3px solid #2196f3;
  padding-left: 8px;
}

.log-news_calendar_update {
  border-left: 3px solid #4caf50;
  padding-left: 8px;
}

.log-news_calendar_fetch_error {
  border-left: 3px solid #f44336;
  padding-left: 8px;
}

.log-news_flash_fetch {
  border-left: 3px solid #00bcd4;
  padding-left: 8px;
}

.log-news_flash_fetch_error {
  border-left: 3px solid #ff5722;
  padding-left: 8px;
}

.log-news_event_scheduled {
  border-left: 3px solid #ff9800;
  padding-left: 8px;
}

.log-news_event_reminder {
  border-left: 3px solid #ffc107;
  padding-left: 8px;
}

.log-news_event_result {
  border-left: 3px solid #8bc34a;
  padding-left: 8px;
}

.log-news_impact_analysis {
  border-left: 3px solid #9c27b0;
  padding-left: 8px;
}
</style>
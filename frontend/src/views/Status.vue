<template>
  <v-container fluid>
    <v-row>
      <v-col cols="12">
        <h1 class="mb-4">服务状态监控</h1>
      </v-col>
    </v-row>

    <!-- 健康状态 -->
    <v-row>
      <v-col cols="12" md="6">
        <v-card>
          <v-card-title class="d-flex align-center">
            <v-icon class="me-2" :color="healthStatus.ok ? 'success' : 'error'">
              mdi-heart
            </v-icon>
            服务健康状态
          </v-card-title>
          <v-card-text>
            <v-chip
              :color="healthStatus.ok ? 'success' : 'error'"
              variant="flat"
              size="large"
            >
              {{ healthStatus.ok ? '服务正常' : '服务异常' }}
            </v-chip>
            <div class="mt-2 text-caption">
              最后检查: {{ lastCheckTime }}
            </div>
          </v-card-text>
        </v-card>
      </v-col>

      <v-col cols="12" md="6">
        <v-card>
          <v-card-title class="d-flex align-center">
            <v-icon class="me-2">mdi-information</v-icon>
            系统信息
          </v-card-title>
          <v-card-text>
            <div class="d-flex flex-column ga-2">
              <div><strong>版本:</strong> {{ systemInfo.version || '未知' }}</div>
              <div><strong>运行时间:</strong> {{ formatUptime(systemInfo.uptime) }}</div>
              <div><strong>内存使用:</strong> {{ formatMemory(systemInfo.memory) }}</div>
            </div>
          </v-card-text>
        </v-card>
      </v-col>
    </v-row>

    <!-- 详细指标 -->
    <v-row>
      <v-col cols="12">
        <v-card>
          <v-card-title>详细服务指标</v-card-title>
          <v-card-text>
            <v-row>
              <v-col cols="12" sm="6" md="3">
                <v-card variant="outlined">
                  <v-card-text class="text-center">
                    <div class="text-h4">{{ serviceMetrics.pendingTrades }}</div>
                    <div class="text-caption">待执行指令</div>
                  </v-card-text>
                </v-card>
              </v-col>

              <v-col cols="12" sm="6" md="3">
                <v-card variant="outlined">
                  <v-card-text class="text-center">
                    <div class="text-h4">{{ serviceMetrics.totalTrades }}</div>
                    <div class="text-caption">总交易次数</div>
                  </v-card-text>
                </v-card>
              </v-col>

              <v-col cols="12" sm="6" md="3">
                <v-card variant="outlined">
                  <v-card-text class="text-center">
                    <div class="text-h4">{{ serviceMetrics.activeSymbols }}</div>
                    <div class="text-caption">活跃品种</div>
                  </v-card-text>
                </v-card>
              </v-col>

              <v-col cols="12" sm="6" md="3">
                <v-card variant="outlined">
                  <v-card-text class="text-center">
                    <div class="text-h4">{{ serviceMetrics.successRate }}%</div>
                    <div class="text-caption">成功率</div>
                  </v-card-text>
                </v-card>
              </v-col>
            </v-row>
          </v-card-text>
        </v-card>
      </v-col>
    </v-row>

    <!-- 连接状态 -->
    <v-row>
      <v-col cols="12">
        <v-card>
          <v-card-title>连接状态</v-card-title>
          <v-card-text>
            <v-row>
              <v-col cols="12" sm="6">
                <v-card variant="outlined" class="pa-3">
                  <div class="d-flex align-center">
                    <v-icon
                      :color="connectionStatus.backend ? 'success' : 'error'"
                      class="me-2"
                    >
                      mdi-server
                    </v-icon>
                    <div>
                      <div class="font-weight-bold">后端服务</div>
                      <div class="text-caption">
                        {{ connectionStatus.backend ? '已连接' : '未连接' }}
                      </div>
                    </div>
                  </div>
                </v-card>
              </v-col>

              <v-col cols="12" sm="6">
                <v-card variant="outlined" class="pa-3">
                  <div class="d-flex align-center">
                    <v-icon
                      :color="connectionStatus.mt5 ? 'success' : 'warning'"
                      class="me-2"
                    >
                      mdi-chart-line
                    </v-icon>
                    <div>
                      <div class="font-weight-bold">MT5 连接</div>
                      <div class="text-caption">
                        {{ connectionStatus.mt5 ? '已连接' : '未连接' }}
                      </div>
                    </div>
                  </div>
                </v-card>
              </v-col>
            </v-row>
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
  name: 'Status',
  setup() {
    const healthStatus = ref({ ok: false })
    const systemInfo = ref({})
    const serviceMetrics = ref({
      pendingTrades: 0,
      totalTrades: 0,
      activeSymbols: 0,
      successRate: 0,
    })
    const connectionStatus = ref({
      backend: false,
      mt5: false,
    })
    const lastCheckTime = ref('')
    const error = ref('')

    const checkStatus = async () => {
      try {
        error.value = ''

        // 检查健康状态
        const health = await tradingAPI.health()
        healthStatus.value = health

        // 获取详细状态
        const status = await tradingAPI.getStatus()
        systemInfo.value = status.system || {}

        // 适配实际API响应结构
        serviceMetrics.value = {
          pendingTrades: status.pending_instructions || 0,
          totalTrades: status.statistics_records || 0,
          activeSymbols: status.symbols?.length || 0,
          successRate: status.success_rate || 0,
        }

        // 检查连接状态
        connectionStatus.value = {
          backend: health.ok,
          mt5: status.mt5_connected || false,
        }

        lastCheckTime.value = new Date().toLocaleString('zh-CN')

      } catch (err) {
        error.value = `检查状态失败: ${err.message}`
        healthStatus.value = { ok: false }
        connectionStatus.value = { backend: false, mt5: false }
        console.error('Status check error:', err)
      }
    }

    const formatUptime = (seconds) => {
      if (!seconds) return '未知'
      const hours = Math.floor(seconds / 3600)
      const minutes = Math.floor((seconds % 3600) / 60)
      return `${hours}小时 ${minutes}分钟`
    }

    const formatMemory = (bytes) => {
      if (!bytes) return '未知'
      const mb = (bytes / 1024 / 1024).toFixed(1)
      return `${mb} MB`
    }

    onMounted(() => {
      checkStatus()
      // 每5秒自动检查
      setInterval(checkStatus, 5000)
    })

    return {
      healthStatus,
      systemInfo,
      serviceMetrics,
      connectionStatus,
      lastCheckTime,
      error,
      checkStatus,
      formatUptime,
      formatMemory,
    }
  },
}
</script>
<template>
  <v-container fluid>
    <v-row>
      <v-col cols="12">
        <h1 class="mb-4">仓位管理</h1>
      </v-col>
    </v-row>

    <!-- 标签页 -->
    <v-card>
      <v-tabs v-model="activeTab" background-color="primary" dark>
        <v-tab>
          <v-icon class="mr-2">mdi-chart-box</v-icon>
          当前持仓
        </v-tab>
        <v-tab>
          <v-icon class="mr-2">mdi-history</v-icon>
          历史交易
        </v-tab>
      </v-tabs>

      <!-- 当前持仓 -->
      <v-tab-item>
        <!-- 汇总卡片 -->
        <v-row class="pa-4">
          <v-col cols="12" md="3">
            <v-card outlined>
              <v-card-text class="text-center">
                <div class="text-h4">{{ summary.total_count }}</div>
                <div class="text-caption grey--text">总持仓数</div>
              </v-card-text>
            </v-card>
          </v-col>
          <v-col cols="12" md="3">
            <v-card outlined>
              <v-card-text class="text-center">
                <div class="text-h4" :class="summary.total_profit >= 0 ? 'success--text' : 'error--text'">
                  {{ summary.total_profit >= 0 ? '+' : '' }}{{ summary.total_profit.toFixed(2) }}
                </div>
                <div class="text-caption grey--text">总盈亏</div>
              </v-card-text>
            </v-card>
          </v-col>
          <v-col cols="12" md="3">
            <v-card outlined>
              <v-card-text class="text-center">
                <div class="text-h4 success--text">{{ summary.buy_count }}</div>
                <div class="text-caption grey--text">买单数量</div>
              </v-card-text>
            </v-card>
          </v-col>
          <v-col cols="12" md="3">
            <v-card outlined>
              <v-card-text class="text-center">
                <div class="text-h4 error--text">{{ summary.sell_count }}</div>
                <div class="text-caption grey--text">卖单数量</div>
              </v-card-text>
            </v-card>
          </v-col>
        </v-row>

        <!-- 持仓列表 -->
        <v-card-text>
          <v-btn color="primary" small class="mb-3" @click="loadPositions" :loading="loading">
            <v-icon start small>mdi-refresh</v-icon>
            刷新
          </v-btn>

          <v-table v-if="positions.length > 0">
            <template v-slot:default>
              <thead>
                <tr>
                  <th>订单号</th>
                  <th>品种</th>
                  <th>方向</th>
                  <th>手数</th>
                  <th>开仓价</th>
                  <th>当前盈亏</th>
                  <th>止损距离</th>
                  <th>止盈距离</th>
                  <th>更新时间</th>
                  <th>操作</th>
                </tr>
              </thead>
              <tbody>
                <tr v-for="pos in positions" :key="pos.ticket">
                  <td>{{ pos.ticket }}</td>
                  <td><strong>{{ pos.symbol }}</strong></td>
                  <td>
                    <v-chip size="x-small" :color="pos.type === 'BUY' ? 'success' : 'error'">
                      {{ pos.type === 'BUY' ? '买入' : '卖出' }}
                    </v-chip>
                  </td>
                  <td>{{ pos.volume }}</td>
                  <td>{{ pos.price_open }}</td>
                  <td :class="pos.profit >= 0 ? 'success--text' : 'error--text'">
                    {{ pos.profit >= 0 ? '+' : '' }}{{ pos.profit.toFixed(2) }}
                  </td>
                  <td>{{ pos.distance_sl || '-' }}</td>
                  <td>{{ pos.distance_tp || '-' }}</td>
                  <td>{{ formatTime(pos.updated_at) }}</td>
                  <td>
                    <v-btn size="x-small" color="error" outlined @click="closePosition(pos)">
                      平仓
                    </v-btn>
                  </td>
                </tr>
              </tbody>
            </template>
          </v-table>
          <div v-else class="text-center grey--text py-8">
            <v-icon large>mdi-folder-open-outline</v-icon>
            <div class="mt-2">暂无持仓</div>
          </div>
        </v-card-text>
      </v-tab-item>

      <!-- 历史交易 -->
      <v-tab-item>
        <v-card-text>
          <v-btn color="primary" small class="mb-3" @click="loadTradeHistory" :loading="historyLoading">
            <v-icon start small>mdi-refresh</v-icon>
            刷新
          </v-btn>

          <!-- 统计卡片 -->
          <v-row class="mb-4">
            <v-col cols="12" md="2">
              <v-card outlined>
                <v-card-text class="text-center">
                  <div class="text-h5">{{ historyStats.total_count || 0 }}</div>
                  <div class="text-caption grey--text">总成交数</div>
                </v-card-text>
              </v-card>
            </v-col>
            <v-col cols="12" md="2">
              <v-card outlined>
                <v-card-text class="text-center">
                  <div class="text-h5" :class="(historyStats.net_profit || 0) >= 0 ? 'success--text' : 'error--text'">
                    {{ (historyStats.net_profit || 0) >= 0 ? '+' : '' }}{{ (historyStats.net_profit || 0).toFixed(2) }}
                  </div>
                  <div class="text-caption grey--text">净盈亏</div>
                </v-card-text>
              </v-card>
            </v-col>
            <v-col cols="6" md="1">
              <v-card outlined>
                <v-card-text class="text-center">
                  <div class="text-h6">{{ historyStats.manual_count || 0 }}</div>
                  <div class="text-caption grey--text">手动</div>
                </v-card-text>
              </v-card>
            </v-col>
            <v-col cols="6" md="1">
              <v-card outlined>
                <v-card-text class="text-center">
                  <div class="text-h6 primary--text">{{ historyStats.auto_count || 0 }}</div>
                  <div class="text-caption grey--text">自动</div>
                </v-card-text>
              </v-card>
            </v-col>
            <v-col cols="6" md="1">
              <v-card outlined>
                <v-card-text class="text-center">
                  <div class="text-h6 warning--text">{{ historyStats.sl_tp_count || 0 }}</div>
                  <div class="text-caption grey--text">止损/止盈</div>
                </v-card-text>
              </v-card>
            </v-col>
            <v-col cols="6" md="1">
              <v-card outlined>
                <v-card-text class="text-center">
                  <div class="text-h6 error--text">{{ historyStats.so_count || 0 }}</div>
                  <div class="text-caption grey--text">强制平仓</div>
                </v-card-text>
              </v-card>
            </v-col>
            <v-col cols="6" md="2">
              <v-card outlined>
                <v-card-text class="text-center">
                  <div class="text-h6">{{ (historyStats.total_commission || 0).toFixed(2) }}</div>
                  <div class="text-caption grey--text">手续费</div>
                </v-card-text>
              </v-card>
            </v-col>
            <v-col cols="6" md="2">
              <v-card outlined>
                <v-card-text class="text-center">
                  <div class="text-h6">{{ (historyStats.total_swap || 0).toFixed(2) }}</div>
                  <div class="text-caption grey--text">库存费</div>
                </v-card-text>
              </v-card>
            </v-col>
          </v-row>

          <!-- 品种分布 -->
          <v-row class="mb-4" v-if="historyStats.symbols && Object.keys(historyStats.symbols).length > 0">
            <v-col cols="12">
              <div class="text-subtitle-1 font-weight-bold mb-2">品种分布</div>
              <v-chip
                v-for="(data, symbol) in historyStats.symbols"
                :key="symbol"
                class="mr-2 mb-2"
                :color="data.profit >= 0 ? 'success' : 'error'"
                outlined
              >
                {{ symbol }}: {{ data.count }}单, 盈亏 {{ data.profit >= 0 ? '+' : '' }}{{ data.profit.toFixed(2) }}
              </v-chip>
            </v-col>
          </v-row>

          <!-- 自动单分类 -->
          <v-row class="mb-4" v-if="historyStats.auto_categories && Object.keys(historyStats.auto_categories).length > 0">
            <v-col cols="12">
              <div class="text-subtitle-1 font-weight-bold mb-2">自动单分类</div>
              <v-data-table
                :headers="categoryHeaders"
                :items="categoryItems"
                dense
                hide-default-footer
                class="elevation-1"
              >
                <template v-slot:item.profit="{ item }">
                  <span :class="item.profit >= 0 ? 'success--text' : 'error--text'">
                    {{ item.profit >= 0 ? '+' : '' }}{{ item.profit.toFixed(2) }}
                  </span>
                </template>
                <template v-slot:item.percentage="{ item }">
                  <v-progress-linear
                    :value="item.percentage"
                    color="primary"
                    height="20"
                  >
                    <template v-slot:default>
                      {{ item.percentage }}%
                    </template>
                  </v-progress-linear>
                </template>
              </v-data-table>
            </v-col>
          </v-row>

          <!-- 成交列表 -->
          <div class="text-subtitle-1 font-weight-bold mb-2">成交记录</div>
          <v-table v-if="tradeDeals.length > 0" fixed-header height="400">
            <template v-slot:default>
              <thead>
                <tr>
                  <th>订单号</th>
                  <th>品种</th>
                  <th>方向</th>
                  <th>类型</th>
                  <th>手数</th>
                  <th>价格</th>
                  <th>盈亏</th>
                  <th>手续费</th>
                  <th>时间</th>
                  <th>备注</th>
                </tr>
              </thead>
              <tbody>
                <tr v-for="deal in tradeDeals" :key="deal.ticket">
                  <td>{{ deal.ticket }}</td>
                  <td><strong>{{ deal.symbol }}</strong></td>
                  <td>
                    <v-chip size="x-small" :color="deal.type === 0 ? 'success' : 'error'">
                      {{ deal.type_text }}
                    </v-chip>
                  </td>
                  <td>
                    <v-chip size="x-small" outlined :color="deal.entry === 1 ? 'warning' : 'info'">
                      {{ deal.entry_text }}
                    </v-chip>
                  </td>
                  <td>{{ deal.volume }}</td>
                  <td>{{ deal.price }}</td>
                  <td :class="deal.profit >= 0 ? 'success--text' : 'error--text'">
                    {{ deal.profit >= 0 ? '+' : '' }}{{ deal.profit.toFixed(2) }}
                  </td>
                  <td>{{ deal.commission.toFixed(2) }}</td>
                  <td>{{ deal.time }}</td>
                  <td>
                    <v-chip v-if="deal.order_source === '自动'" size="x-small" color="primary">
                      {{ deal.comment }}
                    </v-chip>
                    <v-chip v-else-if="deal.order_source === '止损触发'" size="x-small" color="error" outlined>
                      {{ deal.comment }}
                    </v-chip>
                    <v-chip v-else-if="deal.order_source === '止盈触发'" size="x-small" color="success" outlined>
                      {{ deal.comment }}
                    </v-chip>
                    <v-chip v-else-if="deal.order_source === '强制平仓'" size="x-small" color="error" dark>
                      {{ deal.comment }}
                    </v-chip>
                    <span v-else class="grey--text">{{ deal.order_source }}</span>
                  </td>
                </tr>
              </tbody>
            </template>
          </v-table>
          <div v-else class="text-center grey--text py-8">
            <v-icon large>mdi-history</v-icon>
            <div class="mt-2">暂无历史交易数据</div>
          </div>
        </v-card-text>
      </v-tab-item>
    </v-card>

    <!-- 平仓确认对话框 -->
    <v-dialog v-model="closeDialog" max-width="400">
      <v-card>
        <v-card-title>确认平仓</v-card-title>
        <v-card-text>
          <div v-if="selectedPosition">
            <div>订单号: {{ selectedPosition.ticket }}</div>
            <div>品种: {{ selectedPosition.symbol }}</div>
            <div>手数: {{ selectedPosition.volume }}</div>
            <div>盈亏: <span :class="selectedPosition.profit >= 0 ? 'success--text' : 'error--text'">{{ selectedPosition.profit }}</span></div>
          </div>
        </v-card-text>
        <v-card-actions>
          <v-spacer></v-spacer>
          <v-btn text @click="closeDialog = false">取消</v-btn>
          <v-btn color="error" @click="confirmClosePosition" :loading="closing">确认平仓</v-btn>
        </v-card-actions>
      </v-card>
    </v-dialog>

    <!-- 提示 -->
    <v-snackbar v-model="showSnackbar" :color="snackbarColor" timeout="3000">
      {{ snackbarMessage }}
    </v-snackbar>
  </v-container>
</template>

<script>
import { ref, computed, onMounted, onUnmounted } from 'vue'
import { marketAPI } from '@/api/market'

export default {
  name: 'Positions',
  setup() {
    const activeTab = ref(0)
    const positions = ref([])
    const summary = ref({
      total_count: 0,
      total_profit: 0,
      buy_count: 0,
      sell_count: 0
    })
    const loading = ref(false)
    const closeDialog = ref(false)
    const selectedPosition = ref(null)
    const closing = ref(false)
    const showSnackbar = ref(false)
    const snackbarMessage = ref('')
    const snackbarColor = ref('success')

    // 交易历史
    const tradeDeals = ref([])
    const historyStats = ref({})
    const historyLoading = ref(false)

    let refreshInterval = null

    const categoryHeaders = [
      { text: '分类', value: 'category', width: 150 },
      { text: '数量', value: 'count', width: 80 },
      { text: '占比', value: 'percentage', width: 150 },
      { text: '盈亏', value: 'profit', width: 100 }
    ]

    const categoryItems = computed(() => {
      if (!historyStats.value.auto_categories) return []
      return Object.entries(historyStats.value.auto_categories).map(([category, data]) => ({
        category,
        count: data.count,
        percentage: data.percentage,
        profit: data.profit
      }))
    })

    const loadPositions = async () => {
      loading.value = true
      try {
        const data = await marketAPI.getPositionsSummary()
        if (data.status === 'ok') {
          positions.value = data.positions || []
          summary.value = {
            total_count: data.total_count || 0,
            total_profit: data.total_profit || 0,
            buy_count: data.buy_count || 0,
            sell_count: data.sell_count || 0
          }
        }
      } catch (err) {
        console.error('加载持仓失败:', err)
      } finally {
        loading.value = false
      }
    }

    const loadTradeHistory = async () => {
      historyLoading.value = true
      try {
        const data = await marketAPI.getTradeHistory()
        if (data.status === 'ok') {
          tradeDeals.value = data.deals || []
          historyStats.value = data.statistics || {}
        }
      } catch (err) {
        console.error('加载交易历史失败:', err)
      } finally {
        historyLoading.value = false
      }
    }

    const closePosition = (pos) => {
      selectedPosition.value = pos
      closeDialog.value = true
    }

    const confirmClosePosition = async () => {
      if (!selectedPosition.value) return

      closing.value = true
      try {
        const data = await marketAPI.closePosition(selectedPosition.value.ticket, selectedPosition.value.symbol)
        if (data.status === 'ok') {
          snackbarMessage.value = '平仓指令已发送'
          snackbarColor.value = 'success'
          showSnackbar.value = true
          closeDialog.value = false
          // 刷新持仓
          setTimeout(loadPositions, 1000)
        } else {
          snackbarMessage.value = data.message || '平仓失败'
          snackbarColor.value = 'error'
          showSnackbar.value = true
        }
      } catch (err) {
        snackbarMessage.value = '平仓失败: ' + err.message
        snackbarColor.value = 'error'
        showSnackbar.value = true
      } finally {
        closing.value = false
      }
    }

    const formatTime = (timestamp) => {
      if (!timestamp) return '-'
      const date = new Date(timestamp)
      return date.toLocaleTimeString('zh-CN', {
        hour: '2-digit',
        minute: '2-digit',
        second: '2-digit'
      })
    }

    onMounted(() => {
      loadPositions()
      loadTradeHistory()
      // 每5秒刷新一次持仓
      refreshInterval = setInterval(loadPositions, 5000)
    })

    onUnmounted(() => {
      if (refreshInterval) {
        clearInterval(refreshInterval)
      }
    })

    return {
      activeTab,
      positions,
      summary,
      loading,
      closeDialog,
      selectedPosition,
      closing,
      showSnackbar,
      snackbarMessage,
      snackbarColor,
      tradeDeals,
      historyStats,
      historyLoading,
      categoryHeaders,
      categoryItems,
      loadPositions,
      loadTradeHistory,
      closePosition,
      confirmClosePosition,
      formatTime
    }
  }
}
</script>
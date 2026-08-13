<template>
  <v-container fluid class="positions-page">
    <section class="positions-hero mb-5">
      <div>
        <div class="section-kicker">LIVE POSITION CONTROL</div>
        <h1>仓位管理</h1>
        <p>{{ selectedAccount?.account_name || '当前账户' }} · 监控实时敞口、浮动盈亏与历史成交</p>
      </div>
      <v-chip :color="selectedAccount?.active ? 'success' : 'warning'" variant="flat">
        <v-icon start>mdi-lan-connect</v-icon>{{ selectedAccount?.active ? 'MT5 已连接' : 'MT5 未连接' }}
      </v-chip>
    </section>

    <v-row class="mb-2">
      <v-col cols="6" md="3"><v-card class="metric-card" color="primary" variant="tonal"><v-card-text><span>当前持仓</span><strong>{{ summary.total_count }}</strong></v-card-text></v-card></v-col>
      <v-col cols="6" md="3"><v-card class="metric-card" :color="summary.total_profit >= 0 ? 'success' : 'error'" variant="tonal"><v-card-text><span>浮动盈亏</span><strong>{{ signedMoney(summary.total_profit) }}</strong></v-card-text></v-card></v-col>
      <v-col cols="6" md="3"><v-card class="metric-card" color="success" variant="tonal"><v-card-text><span>买入仓位</span><strong>{{ summary.buy_count }}</strong></v-card-text></v-card></v-col>
      <v-col cols="6" md="3"><v-card class="metric-card" color="error" variant="tonal"><v-card-text><span>卖出仓位</span><strong>{{ summary.sell_count }}</strong></v-card-text></v-card></v-col>
    </v-row>

    <v-card class="content-card" elevation="0">
      <v-tabs v-model="activeTab" color="primary" class="page-tabs">
        <v-tab value="positions"><v-icon start>mdi-chart-box-outline</v-icon>当前持仓</v-tab>
        <v-tab value="history"><v-icon start>mdi-history</v-icon>历史交易</v-tab>
      </v-tabs>

      <v-window v-model="activeTab">
        <v-window-item value="positions">
          <div class="section-toolbar">
            <div><strong>实时持仓明细</strong><div class="text-caption text-medium-emphasis">每 5 秒自动同步一次 MT5 仓位</div></div>
            <v-btn color="primary" variant="tonal" prepend-icon="mdi-refresh" :loading="loading" @click="loadPositions">刷新</v-btn>
          </div>
          <v-table v-if="positions.length" class="data-table">
            <thead><tr><th>订单号</th><th>品种</th><th>方向</th><th>手数</th><th>开仓价</th><th>当前盈亏</th><th>止损价</th><th>止盈价</th><th>保护状态</th><th>更新时间</th><th>操作</th></tr></thead>
            <tbody>
              <tr v-for="pos in positions" :key="pos.ticket">
                <td class="text-medium-emphasis">#{{ pos.ticket }}</td><td><strong>{{ pos.symbol }}</strong></td>
                <td><v-chip size="small" :color="pos.type === 'BUY' ? 'success' : 'error'" variant="tonal">{{ pos.type === 'BUY' ? '买入' : '卖出' }}</v-chip></td>
                <td>{{ pos.volume }}</td><td>{{ formatPrice(pos.price_open) }}</td>
                <td><strong :class="Number(pos.profit) >= 0 ? 'text-success' : 'text-error'">{{ signedMoney(pos.profit) }}</strong></td>
                <td><strong :class="Number(pos.sl) ? 'text-error' : 'text-medium-emphasis'">{{ formatOptionalPrice(pos.sl) }}</strong><small v-if="pos.distance_sl" class="distance-note">距 {{ pos.distance_sl }}</small></td>
                <td><strong :class="Number(pos.tp) ? 'text-success' : 'text-medium-emphasis'">{{ formatOptionalPrice(pos.tp) }}</strong><small v-if="pos.distance_tp" class="distance-note">距 {{ pos.distance_tp }}</small></td>
                <td><v-chip size="small" :color="protectionColor(pos)" variant="tonal">{{ protectionLabel(pos) }}</v-chip></td>
                <td>{{ formatTime(pos.updated_at) }}</td>
                <td class="action-cell">
                  <v-btn size="small" color="primary" variant="tonal" prepend-icon="mdi-timeline-clock-outline" @click="openManagementTimeline(pos)">轨迹</v-btn>
                  <v-btn size="small" color="error" variant="tonal" @click="closePosition(pos)">平仓</v-btn>
                </td>
              </tr>
            </tbody>
          </v-table>
          <div v-else class="empty-state"><v-icon size="52">mdi-chart-box-outline</v-icon><h3>当前没有持仓</h3><p>新仓位上报后会自动显示在这里。</p></div>
        </v-window-item>

        <v-window-item value="history">
          <div class="section-toolbar">
            <div><strong>账户成交档案</strong><div class="text-caption text-medium-emphasis">复盘成交来源、费用与最终盈亏</div></div>
            <v-btn color="primary" variant="tonal" prepend-icon="mdi-refresh" :loading="historyLoading" @click="loadTradeHistory">刷新</v-btn>
          </div>
          <v-row class="px-4 pb-2">
            <v-col cols="6" md="3"><div class="history-stat"><span>总成交</span><strong>{{ historyStats.total_count || 0 }}</strong></div></v-col>
            <v-col cols="6" md="3"><div class="history-stat"><span>净盈亏</span><strong :class="Number(historyStats.net_profit || 0) >= 0 ? 'text-success' : 'text-error'">{{ signedMoney(historyStats.net_profit) }}</strong></div></v-col>
            <v-col cols="6" md="3"><div class="history-stat"><span>自动 / 手动</span><strong>{{ historyStats.auto_count || 0 }} / {{ historyStats.manual_count || 0 }}</strong></div></v-col>
            <v-col cols="6" md="3"><div class="history-stat"><span>手续费 / 库存费</span><strong>{{ formatMoney(historyStats.total_commission) }} / {{ formatMoney(historyStats.total_swap) }}</strong></div></v-col>
          </v-row>

          <div v-if="historyStats.symbols && Object.keys(historyStats.symbols).length" class="distribution-panel mx-4 mb-4">
            <span class="distribution-title">品种分布</span>
            <v-chip v-for="(data, symbol) in historyStats.symbols" :key="symbol" :color="data.profit >= 0 ? 'success' : 'error'" variant="tonal">{{ symbol }} · {{ data.count }} 单 · {{ signedMoney(data.profit) }}</v-chip>
          </div>

          <v-data-table v-if="historyStats.auto_categories && Object.keys(historyStats.auto_categories).length" :headers="categoryHeaders" :items="categoryItems" density="compact" hide-default-footer class="mx-4 mb-4 category-table">
            <template #item.profit="{ item }"><span :class="item.profit >= 0 ? 'text-success' : 'text-error'">{{ signedMoney(item.profit) }}</span></template>
            <template #item.percentage="{ item }"><v-progress-linear :model-value="item.percentage" color="primary" height="18" rounded><strong>{{ item.percentage }}%</strong></v-progress-linear></template>
          </v-data-table>

          <v-table v-if="tradeDeals.length" class="data-table" fixed-header height="420">
            <thead><tr><th>订单号</th><th>品种</th><th>方向</th><th>类型</th><th>手数</th><th>价格</th><th>盈亏</th><th>手续费</th><th>时间</th><th>来源</th></tr></thead>
            <tbody><tr v-for="deal in tradeDeals" :key="deal.ticket">
              <td class="text-medium-emphasis">#{{ deal.ticket }}</td><td><strong>{{ deal.symbol }}</strong></td>
              <td><v-chip size="small" :color="deal.type === 0 ? 'success' : 'error'" variant="tonal">{{ deal.type_text }}</v-chip></td>
              <td>{{ deal.entry_text }}</td><td>{{ deal.volume }}</td><td>{{ formatPrice(deal.price) }}</td>
              <td><strong :class="Number(deal.profit) >= 0 ? 'text-success' : 'text-error'">{{ signedMoney(deal.profit) }}</strong></td>
              <td>{{ formatMoney(deal.commission) }}</td><td>{{ deal.time }}</td>
              <td><v-chip size="small" variant="outlined" :color="sourceColor(deal.order_source)">{{ deal.comment || deal.order_source || '-' }}</v-chip></td>
            </tr></tbody>
          </v-table>
          <div v-else class="empty-state"><v-icon size="52">mdi-history</v-icon><h3>暂无历史交易</h3><p>MT5 上报成交记录后会显示在这里。</p></div>
        </v-window-item>
      </v-window>
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

    <v-dialog v-model="managementDialog" max-width="760">
      <v-card class="timeline-card">
        <v-card-title class="d-flex align-center justify-space-between">
          <span>持仓管理轨迹</span>
          <v-btn icon="mdi-close" variant="text" @click="managementDialog = false"></v-btn>
        </v-card-title>
        <v-card-text>
          <div v-if="managementPosition" class="timeline-summary">
            <v-chip color="primary" variant="tonal">#{{ managementPosition.ticket }}</v-chip>
            <v-chip variant="tonal">{{ managementPosition.symbol }}</v-chip>
            <v-chip :color="managementPosition.type === 'BUY' ? 'success' : 'error'" variant="tonal">
              {{ managementPosition.type === 'BUY' ? '买入' : '卖出' }}
            </v-chip>
            <v-chip variant="tonal">开仓 {{ formatPrice(managementPosition.price_open) }}</v-chip>
            <v-chip color="error" variant="tonal">止损 {{ formatOptionalPrice(managementPosition.sl) }}</v-chip>
            <v-chip color="success" variant="tonal">止盈 {{ formatOptionalPrice(managementPosition.tp) }}</v-chip>
          </div>

          <v-skeleton-loader v-if="managementLoading" type="list-item-three-line@3"></v-skeleton-loader>
          <v-timeline v-else-if="managementEvents.length" side="end" density="compact" class="management-timeline">
            <v-timeline-item
              v-for="event in managementEvents"
              :key="event.event_id || `${event.event_time}-${event.message}`"
              :dot-color="eventColor(event.rule_type)"
              size="small"
            >
              <div class="timeline-item">
                <div class="d-flex align-center justify-space-between ga-3">
                  <strong>{{ eventTypeLabel(event.rule_type) }}</strong>
                  <span class="text-caption text-medium-emphasis">{{ formatDateTime(event.event_time) }}</span>
                </div>
                <p>{{ event.message || '-' }}</p>
                <div class="timeline-metrics">
                  <span>价格 {{ formatPrice(event.price) }}</span>
                  <span>止损 {{ formatPrice(event.stop_loss) }}</span>
                  <span>止盈 {{ formatOptionalPrice(event.take_profit) }}</span>
                  <span v-if="event.payload?.candidate_stop_loss">候选止损 {{ formatPrice(event.payload.candidate_stop_loss) }}</span>
                  <span v-if="event.payload?.stop_rule">止损规则 {{ ruleLabel(event.payload.stop_rule.type) }}</span>
                  <span v-if="event.payload?.take_profit_rule">止盈规则 {{ ruleLabel(event.payload.take_profit_rule.type) }}</span>
                  <span v-if="event.payload?.close_volume">平仓手数 {{ Number(event.payload.close_volume).toFixed(2) }}</span>
                  <span v-if="event.payload?.profit_r">浮盈 {{ Number(event.payload.profit_r).toFixed(2) }}R</span>
                </div>
              </div>
            </v-timeline-item>
          </v-timeline>
          <div v-else class="empty-state compact">
            <v-icon size="42">mdi-timeline-clock-outline</v-icon>
            <h3>暂无管理轨迹</h3>
            <p>当保本、移动止损、分批止盈等规则触发后，会在这里记录。</p>
          </div>
        </v-card-text>
      </v-card>
    </v-dialog>

    <!-- 提示 -->
    <v-snackbar v-model="showSnackbar" :color="snackbarColor" timeout="3000">
      {{ snackbarMessage }}
    </v-snackbar>
  </v-container>
</template>

<script>
import { ref, computed, onMounted, onUnmounted, watch } from 'vue'
import { marketAPI } from '@/api/market'
import { useAccountContext } from '@/composables/useAccountContext'

export default {
  name: 'Positions',
  setup() {
    const activeTab = ref('positions')
    const positions = ref([])
    const summary = ref({
      total_count: 0,
      total_profit: 0,
      buy_count: 0,
      sell_count: 0
    })
    const loading = ref(false)
    const closeDialog = ref(false)
    const managementDialog = ref(false)
    const managementLoading = ref(false)
    const managementPosition = ref(null)
    const managementEvents = ref([])
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
    const { selectedAccountId, selectedAccount } = useAccountContext()

    const categoryHeaders = [
      { title: '自动单分类', key: 'category', width: 180 },
      { title: '数量', key: 'count', width: 100 },
      { title: '占比', key: 'percentage', width: 220 },
      { title: '盈亏', key: 'profit', width: 120 }
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
        const data = await marketAPI.getPositionsSummary(null, selectedAccountId.value)
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
        const data = await marketAPI.getTradeHistory(selectedAccountId.value)
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

    const openManagementTimeline = async (pos) => {
      managementPosition.value = pos
      managementEvents.value = []
      managementDialog.value = true
      managementLoading.value = true
      try {
        const data = await marketAPI.getPositionManagementEvents(
          pos.symbol,
          pos.ticket,
          selectedAccountId.value
        )
        managementEvents.value = data.events || []
      } catch (err) {
        snackbarMessage.value = '加载持仓轨迹失败: ' + (err.response?.data?.detail || err.message)
        snackbarColor.value = 'error'
        showSnackbar.value = true
      } finally {
        managementLoading.value = false
      }
    }

    const confirmClosePosition = async () => {
      if (!selectedPosition.value) return

      closing.value = true
      try {
        const data = await marketAPI.closePosition(
          selectedPosition.value.ticket,
          selectedPosition.value.symbol,
          selectedAccountId.value
        )
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
    const formatDateTime = (timestamp) => {
      if (!timestamp) return '-'
      return new Date(timestamp).toLocaleString('zh-CN', {
        month: '2-digit',
        day: '2-digit',
        hour: '2-digit',
        minute: '2-digit',
        second: '2-digit'
      })
    }

    const formatMoney = (value) => Number(value || 0).toFixed(2)
    const signedMoney = (value) => {
      const number = Number(value || 0)
      return `${number >= 0 ? '+' : ''}${number.toFixed(2)}`
    }
    const formatPrice = (value) => Number(value || 0).toLocaleString('zh-CN', {
      minimumFractionDigits: 2,
      maximumFractionDigits: 5
    })
    const formatOptionalPrice = (value) => {
      const number = Number(value || 0)
      if (!number) return '-'
      return number.toLocaleString('zh-CN', {
        minimumFractionDigits: 2,
        maximumFractionDigits: 5
      })
    }
    const protectionLabel = (pos) => {
      const hasSl = Number(pos.sl || 0) > 0
      const hasTp = Number(pos.tp || 0) > 0
      if (hasSl && hasTp) return 'SL/TP 已设置'
      if (hasSl) return '仅止损'
      if (hasTp) return '仅止盈'
      return '未保护'
    }
    const protectionColor = (pos) => {
      const hasSl = Number(pos.sl || 0) > 0
      const hasTp = Number(pos.tp || 0) > 0
      if (hasSl && hasTp) return 'success'
      if (hasSl || hasTp) return 'warning'
      return 'error'
    }
    const sourceColor = (source) => ({
      '自动': 'primary',
      '止损触发': 'error',
      '止盈触发': 'success',
      '强制平仓': 'error'
    })[source] || 'grey'
    const eventTypeLabel = (type) => ({
      break_even: '保本止损',
      trailing_stop: '移动止损',
      pivot_trailing: '转折跟进',
      partial_take_profit: '分批止盈',
      stop_loss_update: '止损更新',
      initial_plan: '初始保护',
      reverse_signal: '反向退出',
      max_holding_bars: '时间退出'
    })[type] || type || '持仓管理'
    const eventColor = (type) => ({
      partial_take_profit: 'success',
      stop_loss_update: 'primary',
      initial_plan: 'indigo',
      trailing_stop: 'primary',
      break_even: 'teal',
      pivot_trailing: 'orange',
      reverse_signal: 'error',
      max_holding_bars: 'warning'
    })[type] || 'grey'
    const ruleLabel = (type) => ({
      signal: '信号建议',
      pivot: '转折点',
      atr: 'ATR',
      fixed_points: '固定点数',
      fixed_percent: '固定比例',
      risk_reward: '盈亏比',
      none: '不设固定止盈',
    })[type] || type || '-'

    onMounted(() => {
      if (selectedAccountId.value) {
        loadPositions()
        loadTradeHistory()
      }
      // 每5秒刷新一次持仓
      refreshInterval = setInterval(loadPositions, 5000)
    })

    watch(selectedAccountId, (value) => {
      positions.value = []
      tradeDeals.value = []
      historyStats.value = {}
      if (value) {
        loadPositions()
        loadTradeHistory()
      }
    })

    onUnmounted(() => {
      if (refreshInterval) {
        clearInterval(refreshInterval)
      }
    })

    return {
      activeTab,
      selectedAccount,
      positions,
      summary,
      loading,
      closeDialog,
      managementDialog,
      managementLoading,
      managementPosition,
      managementEvents,
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
      openManagementTimeline,
      confirmClosePosition,
      formatTime,
      formatDateTime,
      formatMoney,
      signedMoney,
      formatPrice,
      formatOptionalPrice,
      protectionLabel,
      protectionColor,
      sourceColor,
      eventTypeLabel,
      eventColor,
      ruleLabel
    }
  }
}
</script>

<style scoped>
.positions-page { max-width:1600px; padding:28px; }
.positions-hero { display:flex; align-items:center; justify-content:space-between; gap:24px; padding:26px 30px; color:#f4fbf8; border-radius:22px; background:radial-gradient(circle at 86% 18%,rgba(247,186,67,.28),transparent 28%),linear-gradient(125deg,#123c35 0%,#176b56 58%,#0d8f70 100%); box-shadow:0 18px 40px rgba(20,82,68,.18); }
.positions-hero h1 { margin:2px 0 6px; font-size:clamp(1.8rem,3vw,2.7rem); line-height:1.05; }
.positions-hero p { margin:0; color:rgba(244,251,248,.76); }
.section-kicker { color:#f4c96b; font-size:.72rem; font-weight:800; letter-spacing:.16em; }
.metric-card .v-card-text { display:flex; align-items:baseline; justify-content:space-between; gap:12px; }
.metric-card strong { font-size:clamp(1.35rem,2vw,1.9rem); }
.content-card { border:1px solid #dce7e0; border-radius:18px; overflow:hidden; background:linear-gradient(155deg,#fff,#f8fbf9); }
.page-tabs { padding:8px 14px 0; border-bottom:1px solid #e2ebe5; }
.section-toolbar { display:flex; align-items:center; justify-content:space-between; gap:16px; padding:20px 24px; }
.data-table :deep(th) { color:#536b62; font-size:.75rem; font-weight:800; letter-spacing:.03em; background:#f2f7f4!important; }
.history-stat { display:flex; flex-direction:column; gap:4px; min-height:86px; padding:16px; border-radius:14px; background:#eef5f1; }
.history-stat span { color:#718078; font-size:.75rem; }
.history-stat strong { font-size:1.25rem; }
.distribution-panel { display:flex; flex-wrap:wrap; align-items:center; gap:10px; padding:16px; border-radius:14px; background:#fbf6e9; }
.distribution-title { margin-right:6px; color:#6e5d28; font-weight:800; }
.category-table { border:1px solid #e2ebe5; border-radius:14px; overflow:hidden; }
.action-cell { display:flex; flex-wrap:wrap; gap:8px; }
.timeline-card { border-radius:22px!important; }
.timeline-summary { display:flex; flex-wrap:wrap; gap:8px; margin-bottom:18px; }
.management-timeline { margin-left:-18px; }
.timeline-item { padding:12px 14px; border:1px solid #dde9e2; border-radius:14px; background:linear-gradient(145deg,#fff,#f7fbf8); }
.timeline-item p { margin:6px 0 10px; color:#385148; }
.timeline-metrics { display:flex; flex-wrap:wrap; gap:8px; }
.timeline-metrics span { padding:4px 8px; border-radius:999px; color:#536b62; background:#eef5f1; font-size:.76rem; }
.empty-state { padding:64px 20px; text-align:center; color:#718078; }
.empty-state.compact { padding:34px 12px; }
.empty-state h3 { margin:12px 0 4px; color:#385148; }
.empty-state p { margin:0; }
@media (max-width:700px) { .positions-page{padding:16px}.positions-hero{align-items:stretch;flex-direction:column;padding:22px}.positions-hero .v-chip{align-self:flex-start}.section-toolbar{align-items:flex-start;flex-direction:column}.section-toolbar .v-btn{width:100%} }
</style>

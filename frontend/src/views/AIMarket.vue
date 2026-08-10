<template>
  <v-container fluid class="ai-market-page">
    <section class="ai-hero mb-5">
      <div>
        <div class="section-kicker">AI MARKET INTELLIGENCE</div>
        <h1>AI 行情</h1>
        <p>{{ selectedAccount?.account_name || '当前账户' }} · 看见每一次 AI 判断，以及它为什么尚未形成交易信号</p>
      </div>
      <div class="hero-status">
        <v-chip :color="selectedAccount?.active ? 'success' : 'warning'" variant="flat">
          <v-icon start>mdi-lan-connect</v-icon>{{ selectedAccount?.active ? 'MT5 已连接' : 'MT5 未连接' }}
        </v-chip>
        <v-btn color="white" variant="tonal" prepend-icon="mdi-refresh" :loading="loading" @click="loadData">刷新分析</v-btn>
      </div>
    </section>

    <v-row class="mb-2">
      <v-col cols="6" md="3"><v-card class="metric-card" color="primary" variant="tonal"><v-card-text><span>我的分析</span><strong>{{ summary.own_count || 0 }}</strong></v-card-text></v-card></v-col>
      <v-col cols="6" md="3"><v-card class="metric-card" color="success" variant="tonal"><v-card-text><span>已接近信号</span><strong>{{ summary.actionable_count || 0 }}</strong></v-card-text></v-card></v-col>
      <v-col cols="6" md="3"><v-card class="metric-card" color="info" variant="tonal"><v-card-text><span>共享分析</span><strong>{{ summary.shared_count || 0 }}</strong></v-card-text></v-card></v-col>
      <v-col cols="6" md="3"><v-card class="metric-card" color="warning" variant="tonal"><v-card-text class="time-metric"><span>最近分析</span><strong>{{ shortTime(summary.last_analysis_time) }}</strong></v-card-text></v-card></v-col>
    </v-row>

    <v-card class="filter-card mb-4" elevation="0">
      <v-card-text class="filters">
        <v-select v-model="filters.symbol" :items="symbolOptions" label="品种" clearable hide-details density="compact" variant="outlined" />
        <v-select v-model="filters.direction" :items="directionOptions" label="AI 方向" clearable hide-details density="compact" variant="outlined" />
        <v-select v-model="filters.status" :items="statusOptions" label="分析状态" clearable hide-details density="compact" variant="outlined" />
      </v-card-text>
    </v-card>

    <v-card class="content-card" elevation="0">
      <v-tabs v-model="activeTab" color="primary" class="page-tabs">
        <v-tab value="own"><v-icon start>mdi-account-brain-outline</v-icon>我的 AI 分析 <v-chip size="x-small" class="ml-2">{{ filteredOwn.length }}</v-chip></v-tab>
        <v-tab value="shared"><v-icon start>mdi-account-group-outline</v-icon>共享 AI 分析 <v-chip size="x-small" class="ml-2">{{ filteredShared.length }}</v-chip></v-tab>
      </v-tabs>

      <v-window v-model="activeTab">
        <v-window-item value="own">
          <div v-if="!access.access_granted && !filteredOwn.length" class="access-state">
            <v-icon size="58" color="primary">mdi-brain</v-icon>
            <h2>尚未开通 AI 行情分析</h2>
            <p>开通后可运行自主 AI 分析；也可以在策略中引用平台共享 AI 数据，无需开通即可参与决策。</p>
            <v-btn color="primary" :loading="requestingAccess" @click="requestAccess">{{ access.status === 'pending' ? '申请审批中' : '申请开通' }}</v-btn>
          </div>
          <v-alert v-if="access.access_granted && !access.service_configured" type="warning" variant="tonal" class="ma-5">
            平台大模型服务尚未完成配置，已保存的分析仍可查看，但暂时不会产生新的分析。
          </v-alert>
          <div v-if="filteredOwn.length" class="analysis-grid pa-5">
            <article v-for="card in filteredOwn" :key="card.card_id" class="analysis-card" :class="`status-${card.status}`">
              <div class="card-head">
                <div><div class="symbol-line"><strong>{{ card.symbol }}</strong><v-chip size="x-small" variant="outlined">{{ card.period }}</v-chip><v-chip v-if="card.derived_from_shared" size="x-small" color="info" variant="tonal">共享派生</v-chip></div><span>{{ card.strategy_name }}</span></div>
                <v-chip :color="directionMeta(card.direction).color" size="small" variant="tonal"><v-icon start>{{ directionMeta(card.direction).icon }}</v-icon>{{ directionMeta(card.direction).label }}</v-chip>
              </div>
              <div class="confidence-row"><span>AI 置信度</span><strong>{{ card.confidence }}%</strong></div>
              <v-progress-linear :model-value="card.confidence" :color="confidenceColor(card.confidence, card.min_confidence)" height="8" rounded />
              <div class="price-grid">
                <div><span>当前价</span><strong>{{ price(card.current_price) }}</strong></div><div><span>建议入场</span><strong>{{ price(card.entry_price) }}</strong></div><div><span>止损</span><strong>{{ price(card.stop_loss) }}</strong></div><div><span>止盈</span><strong>{{ price(card.take_profit) }}</strong></div>
              </div>
              <div class="status-box"><v-chip :color="statusMeta(card.status).color" size="small" variant="flat">{{ statusMeta(card.status).label }}</v-chip><p>{{ card.status_reason }}</p></div>
              <div class="card-footer"><span>{{ card.derived_from_shared ? `${card.source_owner_username} · ${card.source_symbol}` : (card.model || '平台默认模型') }} · {{ formatTime(card.analyzed_at) }}</span><v-btn size="small" variant="text" @click="openDetail(card, false)">查看分析</v-btn></div>
            </article>
          </div>
          <div v-if="access.access_granted && !filteredOwn.length" class="empty-state"><v-icon size="56">mdi-brain</v-icon><h2>暂无 AI 分析</h2><p>启用包含自主分析或共享引用信号源的策略后，结果会显示在这里。</p></div>
        </v-window-item>

        <v-window-item value="shared">
          <v-alert type="info" variant="tonal" class="ma-5 mb-0">这里展示平台共享数据；只有你在自己的策略中明确引用并配置阈值后，它才会作为你的信号源参与决策。</v-alert>
          <div v-if="filteredShared.length" class="analysis-grid pa-5">
            <article v-for="card in filteredShared" :key="card.card_id" class="analysis-card shared-card">
              <div class="card-head">
                <div><div class="symbol-line"><strong>{{ card.symbol }}</strong><v-chip size="x-small" variant="outlined">{{ card.period }}</v-chip></div><span>{{ card.owner_username }} · {{ card.strategy_name }}</span></div>
                <v-chip :color="directionMeta(card.direction).color" size="small" variant="tonal">{{ directionMeta(card.direction).label }}</v-chip>
              </div>
              <div class="shared-meta"><v-chip size="x-small" color="info" variant="tonal">{{ card.model }}</v-chip><v-chip size="x-small" variant="outlined">{{ lifecycleLabel(card.strategy_lifecycle) }}</v-chip><v-chip v-if="card.symbol_similarity != null" size="x-small" variant="outlined">品种相似 {{ Math.round(card.symbol_similarity * 100) }}%</v-chip></div>
              <div class="confidence-row"><span>AI 置信度</span><strong>{{ card.confidence }}%</strong></div>
              <v-progress-linear :model-value="card.confidence" color="info" height="8" rounded />
              <div class="price-grid compact"><div><span>建议入场</span><strong>{{ price(card.entry_price) }}</strong></div><div><span>止损 / 止盈</span><strong>{{ price(card.stop_loss) }} / {{ price(card.take_profit) }}</strong></div></div>
              <div class="status-box shared"><v-chip color="info" size="small" variant="flat">共享参考</v-chip><p>{{ card.status_reason }}</p></div>
              <div class="card-footer"><span>更新于 {{ formatTime(card.analyzed_at) }}</span><v-btn size="small" variant="text" @click="openDetail(card, true)">查看详情</v-btn></div>
            </article>
          </div>
          <div v-else class="empty-state"><v-icon size="56">mdi-account-group-outline</v-icon><h2>暂无共享 AI 分析</h2><p>其他用户主动共享的最新运行结果会显示在这里。</p></div>
        </v-window-item>
      </v-window>
    </v-card>

    <v-dialog v-model="detailDialog" max-width="860" scrollable>
      <v-card v-if="detailCard">
        <v-card-title class="d-flex align-center"><v-icon class="mr-2">mdi-brain</v-icon>{{ detailCard.symbol }} · {{ detailCard.period }} AI 分析<v-spacer /><v-btn icon="mdi-close" variant="text" @click="detailDialog = false" /></v-card-title>
        <v-card-text class="detail-body">
          <v-alert :type="detailShared ? 'info' : 'success'" variant="tonal" class="mb-4">{{ detailCard.status_reason }}</v-alert>
          <h3>周期判断</h3><p>{{ detailCard.trend?.reason || '模型未返回周期判断说明' }}</p>
          <h3>整体判断</h3><p>{{ detailCard.overall_trend?.summary || '模型未返回整体行情总结' }}</p>
          <h3>关键点位</h3><div class="level-row"><span>压力位：{{ levels(detailCard.key_levels?.resistance) }}</span><span>支撑位：{{ levels(detailCard.key_levels?.support) }}</span></div>
          <h3>运行参数</h3><p>{{ detailCard.model || '平台默认模型' }} · {{ detailCard.period }} · {{ detailCard.signal_params?.kline_count || detailCard.kline_count || '-' }} 根 K 线</p>
          <v-expansion-panels v-if="detailShared" variant="accordion">
            <v-expansion-panel title="查看共享提示词"><v-expansion-panel-text><div class="prompt-label">系统提示词</div><pre>{{ detailCard.system_prompt || '未单独配置' }}</pre><div class="prompt-label">分析提示词</div><pre>{{ detailCard.analysis_prompt_template || '未单独配置' }}</pre></v-expansion-panel-text></v-expansion-panel>
          </v-expansion-panels>
        </v-card-text>
        <v-card-actions v-if="detailShared" class="pa-4"><v-spacer /><v-btn color="primary" prepend-icon="mdi-tune-variant" @click="goToStrategySettings">前往策略配置引用</v-btn></v-card-actions>
      </v-card>
    </v-dialog>

    <v-snackbar v-model="showMessage" :color="messageColor">{{ message }}</v-snackbar>
  </v-container>
</template>

<script setup>
import { computed, onMounted, onUnmounted, reactive, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { marketAPI } from '@/api/market'
import { useAccountContext } from '@/composables/useAccountContext'

const router = useRouter()
const { selectedAccountId, selectedAccount } = useAccountContext()
const loading = ref(false)
const requestingAccess = ref(false)
const activeTab = ref('own')
const ownCards = ref([])
const sharedCards = ref([])
const summary = ref({})
const access = ref({})
const filters = reactive({ symbol: null, direction: null, status: null })
const detailDialog = ref(false)
const detailCard = ref(null)
const detailShared = ref(false)
const showMessage = ref(false)
const message = ref('')
const messageColor = ref('success')
let refreshTimer = null

const directionOptions = [{ title: '上涨', value: 'up' }, { title: '震荡', value: 'sideways' }, { title: '下降', value: 'down' }]
const statusOptions = [
  { title: '观察中', value: 'observing' }, { title: '等待价格', value: 'waiting_price' },
  { title: '进入触发区', value: 'ready_to_signal' }, { title: '已形成信号', value: 'signal_formed' },
  { title: '已生成决策', value: 'decision_created' }, { title: '等待分析', value: 'waiting_analysis' },
  { title: '策略未运行', value: 'strategy_inactive' }, { title: '分析已过期', value: 'expired' },
]
const symbolOptions = computed(() => [...new Set([...ownCards.value, ...sharedCards.value].map(item => item.symbol).filter(Boolean))].sort())
const applyFilters = cards => cards.filter(card => (!filters.symbol || card.symbol === filters.symbol) && (!filters.direction || card.direction === filters.direction) && (!filters.status || card.status === filters.status))
const filteredOwn = computed(() => applyFilters(ownCards.value))
const filteredShared = computed(() => applyFilters(sharedCards.value))

async function loadData() {
  if (!selectedAccountId.value) return
  loading.value = true
  try {
    const data = await marketAPI.getAIMarketView(selectedAccountId.value)
    ownCards.value = data.own || []
    sharedCards.value = data.shared || []
    summary.value = data.summary || {}
    access.value = data.access || {}
  } catch (error) {
    messageColor.value = 'error'; message.value = error.response?.data?.detail || '加载 AI 行情失败'; showMessage.value = true
  } finally { loading.value = false }
}
async function requestAccess() {
  if (access.value.status === 'pending') return
  requestingAccess.value = true
  try { const data = await marketAPI.requestLLMAccess(); access.value = data.access || {}; messageColor.value = 'success'; message.value = data.message; showMessage.value = true }
  catch (error) { messageColor.value = 'error'; message.value = error.response?.data?.detail || '申请失败'; showMessage.value = true }
  finally { requestingAccess.value = false }
}
const directionMeta = direction => ({ up: { label: '上涨', color: 'success', icon: 'mdi-trending-up' }, down: { label: '下降', color: 'error', icon: 'mdi-trending-down' }, sideways: { label: '震荡', color: 'warning', icon: 'mdi-trending-neutral' } }[direction] || { label: '待分析', color: 'grey', icon: 'mdi-help-circle-outline' })
const statusMeta = status => ({ observing: { label: '观察中', color: 'info' }, waiting_price: { label: '等待价格', color: 'warning' }, ready_to_signal: { label: '进入触发区', color: 'success' }, signal_formed: { label: '已形成信号', color: 'success' }, decision_created: { label: '已生成决策', color: 'primary' }, waiting_analysis: { label: '等待分析', color: 'grey' }, strategy_inactive: { label: '策略未运行', color: 'grey' }, expired: { label: '分析已过期', color: 'error' } }[status] || { label: status || '未知', color: 'grey' })
const lifecycleLabel = status => ({ draft: '草稿', backtesting: '回测中', backtest_passed: '回测通过', paper_trading: '模拟盘验证', production: '实盘', retired: '已停用' }[status] || status)
const confidenceColor = (value, minimum) => Number(value) >= Number(minimum) ? 'success' : 'warning'
const price = value => Number(value) > 0 ? Number(value).toLocaleString('zh-CN', { minimumFractionDigits: 2, maximumFractionDigits: 5 }) : '--'
const formatTime = value => { if (!value) return '尚未分析'; const date = typeof value === 'number' ? new Date(value * 1000) : new Date(value); return Number.isNaN(date.getTime()) ? String(value) : date.toLocaleString('zh-CN') }
const shortTime = value => value ? formatTime(value).replace(/^.*?\s/, '') : '--'
const levels = values => Array.isArray(values) && values.length ? values.map(price).join('、') : '暂无'
function openDetail(card, shared) { detailCard.value = card; detailShared.value = shared; detailDialog.value = true }
function goToStrategySettings() { detailDialog.value = false; router.push('/strategy-settings') }

onMounted(() => { loadData(); refreshTimer = setInterval(loadData, 30000) })
watch(selectedAccountId, () => { ownCards.value = []; loadData() })
onUnmounted(() => clearInterval(refreshTimer))
</script>

<style scoped>
.ai-market-page{max-width:1600px;padding:28px}.ai-hero{display:flex;align-items:center;justify-content:space-between;gap:24px;padding:26px 30px;color:#f4fbf8;border-radius:22px;background:radial-gradient(circle at 86% 18%,rgba(247,186,67,.28),transparent 28%),linear-gradient(125deg,#123c35 0%,#176b56 58%,#0d8f70 100%);box-shadow:0 18px 40px rgba(20,82,68,.18)}.ai-hero h1{margin:2px 0 6px;font-size:clamp(1.8rem,3vw,2.7rem);line-height:1.05}.ai-hero p{margin:0;color:rgba(244,251,248,.76)}.section-kicker{color:#f4c96b;font-size:.72rem;font-weight:800;letter-spacing:.16em}.hero-status{display:flex;align-items:center;gap:10px}.metric-card .v-card-text{display:flex;align-items:baseline;justify-content:space-between;gap:12px}.metric-card strong{font-size:1.8rem}.time-metric strong{font-size:1rem}.filter-card,.content-card{border:1px solid #dce7e0;border-radius:18px;background:linear-gradient(155deg,#fff,#f8fbf9)}.filters{display:grid;grid-template-columns:repeat(3,minmax(180px,1fr));gap:14px}.content-card{overflow:hidden}.page-tabs{padding:8px 14px 0;border-bottom:1px solid #e2ebe5}.analysis-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(350px,1fr));gap:18px}.analysis-card{display:flex;flex-direction:column;gap:16px;padding:20px;border:1px solid #dce7e0;border-radius:18px;background:#fff;box-shadow:0 10px 24px rgba(34,78,64,.06)}.analysis-card.status-ready_to_signal,.analysis-card.status-signal_formed,.analysis-card.status-decision_created{border-color:#64a98f;box-shadow:0 12px 28px rgba(29,130,98,.13)}.shared-card{border-color:#cfe0e7}.card-head,.card-footer,.confidence-row{display:flex;align-items:center;justify-content:space-between;gap:12px}.symbol-line{display:flex;align-items:center;gap:8px}.symbol-line strong{font-size:1.25rem}.card-head span,.card-footer span{color:#718078;font-size:.76rem}.confidence-row strong{font-size:1.25rem}.price-grid{display:grid;grid-template-columns:repeat(4,1fr);gap:8px}.price-grid.compact{grid-template-columns:1fr 1.5fr}.price-grid div{display:flex;flex-direction:column;padding:10px;border-radius:10px;background:#f1f6f3}.price-grid span{color:#718078;font-size:.68rem}.price-grid strong{margin-top:2px;font-size:.85rem}.status-box{display:flex;align-items:flex-start;gap:10px;padding:12px;border-radius:12px;background:#f0f6f3}.status-box.shared{background:#eff6f8}.status-box p{margin:2px 0 0;color:#52635e;font-size:.8rem;line-height:1.45}.shared-meta{display:flex;flex-wrap:wrap;gap:7px}.access-state,.empty-state{padding:72px 20px;text-align:center;color:#607269}.access-state h2,.empty-state h2{margin:14px 0 6px;color:#29493e}.access-state p,.empty-state p{margin:0 auto 20px;max-width:600px}.detail-body h3{margin:18px 0 6px;color:#29493e}.detail-body p{color:#52635e}.level-row{display:flex;flex-direction:column;gap:7px;padding:14px;border-radius:12px;background:#f1f6f3}.prompt-label{margin:12px 0 5px;font-weight:800}.detail-body pre{overflow:auto;max-height:230px;padding:14px;white-space:pre-wrap;border-radius:10px;background:#132b25;color:#dcece6;font-size:.75rem}
@media(max-width:800px){.ai-market-page{padding:16px}.ai-hero{align-items:stretch;flex-direction:column;padding:22px}.hero-status{align-items:stretch;flex-direction:column}.hero-status .v-chip{align-self:flex-start}.filters{grid-template-columns:1fr}.analysis-grid{grid-template-columns:1fr;padding:16px!important}.price-grid{grid-template-columns:repeat(2,1fr)}.card-footer{align-items:flex-start;flex-direction:column}}
</style>

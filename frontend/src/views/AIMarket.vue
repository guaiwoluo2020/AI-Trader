<template>
  <v-container fluid class="ai-market-page">
    <section class="ai-hero mb-5">
      <div>
        <div class="section-kicker">AI MARKET INTELLIGENCE</div>
        <h1>AI 行情</h1>
        <p>每张卡片对应一个 AI 信号源，只展示行情分析本身；策略如何采用该分析，请在策略执行中心查看。</p>
      </div>
      <div class="hero-status">
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
                <div><div class="symbol-line"><strong>{{ card.symbol }}</strong><v-chip size="x-small" variant="outlined">{{ card.period }}</v-chip></div><span>{{ card.source_name }}</span></div>
                <v-chip :color="directionMeta(card.direction).color" size="small" variant="tonal"><v-icon start>{{ directionMeta(card.direction).icon }}</v-icon>{{ directionMeta(card.direction).label }}</v-chip>
              </div>
              <div class="confidence-row"><span>AI 置信度</span><strong>{{ card.confidence }}%</strong></div>
              <v-progress-linear :model-value="card.confidence" :color="directionMeta(card.direction).color" height="8" rounded />
              <div class="price-grid">
                <div><span>当前价</span><strong>{{ price(card.current_price) }}</strong></div><div><span>建议入场</span><strong>{{ price(card.entry_price) }}</strong></div><div><span>止损</span><strong>{{ price(card.stop_loss) }}</strong></div><div><span>止盈</span><strong>{{ price(card.take_profit) }}</strong></div>
              </div>
              <div v-if="card.suggestion" class="trade-suggestion" :class="`is-${card.suggestion.direction}`">
                <div><span>交易建议</span><strong><v-icon size="16" :color="suggestionMeta(card.suggestion.direction).color">{{ suggestionMeta(card.suggestion.direction).icon }}</v-icon>{{ suggestionMeta(card.suggestion.direction).label }}</strong></div>
                <p>{{ card.suggestion.reason || '模型未提供建议理由' }}</p>
              </div>
              <div class="status-box"><v-chip :color="statusMeta(card.status).color" size="small" variant="flat">{{ statusMeta(card.status).label }}</v-chip><p>{{ card.status_reason }}</p></div>
              <div class="linked-strategies"><span>关联策略</span><v-btn v-for="strategy in card.linked_strategies" :key="strategy.strategy_id" size="x-small" variant="tonal" color="primary" append-icon="mdi-arrow-right" @click="goToStrategyExecution(strategy)">{{ strategy.strategy_name || strategy.strategy_id }}</v-btn><span v-if="!card.linked_strategies?.length" class="text-medium-emphasis">暂未关联</span></div>
              <div class="card-footer"><span>{{ card.market_data_account?.account_name || '未绑定行情账户' }} · {{ card.model || '平台默认模型' }} · {{ formatTime(card.analyzed_at) }}</span><div><v-btn size="small" variant="text" @click="openConfig(card)">查看配置</v-btn><v-btn size="small" variant="text" @click="openSuggestions(card)">交易建议</v-btn><v-btn size="small" variant="text" @click="openHistory(card)">查看历史</v-btn><v-btn size="small" variant="text" @click="openDetail(card, false)">查看分析</v-btn></div></div>
            </article>
          </div>
          <div v-if="access.access_granted && !filteredOwn.length" class="empty-state"><v-icon size="56">mdi-brain</v-icon><h2>暂无 AI 分析</h2><p>请先创建并启用 AI 信号源。自主分析无需绑定策略，产生结果后会显示在这里。</p><v-btn color="primary" prepend-icon="mdi-access-point-plus" @click="router.push('/ai-signal-sources')">创建 AI 信号源</v-btn></div>
        </v-window-item>

        <v-window-item value="shared">
          <v-alert type="info" variant="tonal" class="ma-5 mb-0">这里展示平台全部共享 AI 数据，并根据当前账户上报品种推荐适用关系；只有明确引用后，它才会参与你的策略决策。</v-alert>
          <div v-if="filteredShared.length" class="analysis-grid pa-5">
            <article v-for="card in filteredShared" :key="card.card_id" class="analysis-card shared-card">
              <div class="card-head">
                <div><div class="symbol-line"><strong>{{ card.symbol }}</strong><v-chip size="x-small" variant="outlined">{{ card.period }}</v-chip></div><span>{{ card.owner_username }} · {{ card.strategy_name }}</span></div>
                <v-chip :color="directionMeta(card.direction).color" size="small" variant="tonal">{{ directionMeta(card.direction).label }}</v-chip>
              </div>
              <div class="shared-meta"><v-chip size="x-small" color="info" variant="tonal">{{ card.model }}</v-chip><v-chip size="x-small" variant="outlined">{{ lifecycleLabel(card.strategy_lifecycle) }}</v-chip><v-chip v-if="card.symbol_similarity != null" size="x-small" variant="outlined">品种相似 {{ Math.round(card.symbol_similarity * 100) }}%</v-chip></div>
              <div class="recommendation-row">
                <template v-if="card.recommended_symbols?.length"><span>推荐适用</span><v-chip v-for="target in card.recommended_symbols" :key="target" size="x-small" color="success" variant="tonal">{{ target }}</v-chip></template>
                <template v-else-if="card.similar_symbols?.length"><span>相似候选</span><v-chip v-for="target in card.similar_symbols" :key="target" size="x-small" color="warning" variant="tonal">{{ target }}</v-chip></template>
                <span v-else class="unmatched-note">当前账户上报品种暂未匹配，仍可查看分析</span>
              </div>
              <div class="confidence-row"><span>AI 置信度</span><strong>{{ card.confidence }}%</strong></div>
              <v-progress-linear :model-value="card.confidence" color="info" height="8" rounded />
              <div class="price-grid compact"><div><span>建议入场</span><strong>{{ price(card.entry_price) }}</strong></div><div><span>止损 / 止盈</span><strong>{{ price(card.stop_loss) }} / {{ price(card.take_profit) }}</strong></div></div>
              <div v-if="card.suggestion" class="trade-suggestion" :class="`is-${card.suggestion.direction}`"><div><span>交易建议</span><strong><v-icon size="16" :color="suggestionMeta(card.suggestion.direction).color">{{ suggestionMeta(card.suggestion.direction).icon }}</v-icon>{{ suggestionMeta(card.suggestion.direction).label }}</strong></div><p>{{ card.suggestion.reason || '模型未提供建议理由' }}</p></div>
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
          <div class="analysis-time"><v-icon size="16">mdi-clock-outline</v-icon><span>分析生成时间：{{ formatTime(detailCard.analyzed_at) }}</span></div>
          <v-alert :type="detailShared ? 'info' : 'success'" variant="tonal" class="mb-4">{{ detailCard.status_reason }}</v-alert>
          <h3>周期判断</h3><p>{{ detailCard.trend?.reason || '模型未返回周期判断说明' }}</p>
          <h3>整体判断</h3><p>{{ detailCard.overall_trend?.summary || '模型未返回整体行情总结' }}</p>
          <h3>关键点位</h3><div class="level-row"><span>压力位：{{ levels(detailCard.key_levels?.resistance) }}</span><span>支撑位：{{ levels(detailCard.key_levels?.support) }}</span></div>
          <template v-if="detailCard.suggestion"><h3>交易建议</h3><div class="trade-suggestion detail" :class="`is-${detailCard.suggestion.direction}`"><div><span>方向</span><strong><v-icon size="16" :color="suggestionMeta(detailCard.suggestion.direction).color">{{ suggestionMeta(detailCard.suggestion.direction).icon }}</v-icon>{{ suggestionMeta(detailCard.suggestion.direction).label }}</strong></div><div class="suggestion-levels"><span>入场 {{ price(detailCard.suggestion.entry_price) }}</span><span>止损 {{ price(detailCard.suggestion.stop_loss) }}</span><span>止盈 {{ price(detailCard.suggestion.take_profit) }}</span></div><p>{{ detailCard.suggestion.reason || '模型未提供建议理由' }}</p></div></template>
          <h3>运行参数</h3><p>{{ detailCard.model || '平台默认模型' }} · {{ detailCard.period }} · {{ detailCard.signal_params?.kline_count || detailCard.kline_count || '-' }} 根 K 线</p>
          <template v-if="!detailShared"><h3>关联策略</h3><div class="linked-strategies detail-links"><v-btn v-for="strategy in detailCard.linked_strategies" :key="strategy.strategy_id" size="small" variant="tonal" color="primary" append-icon="mdi-arrow-right" @click="goToStrategyExecution(strategy)">{{ strategy.strategy_name || strategy.strategy_id }}</v-btn><span v-if="!detailCard.linked_strategies?.length" class="text-medium-emphasis">暂未关联策略</span></div></template>
          <v-alert v-if="detailShared" type="info" variant="tonal">共享结果可以作为信号源使用；模型提示词和私有参数受共享者保护，不对外展示。</v-alert>
        </v-card-text>
        <v-card-actions v-if="detailShared" class="pa-4"><v-spacer /><v-btn color="primary" prepend-icon="mdi-tune-variant" @click="goToStrategySettings">前往策略配置引用</v-btn></v-card-actions>
      </v-card>
    </v-dialog>

    <v-dialog v-model="historyDialog" max-width="900" scrollable>
      <v-card><v-card-title>最近 5 次 AI 调用</v-card-title><v-card-text>
        <v-progress-linear v-if="historyLoading" indeterminate color="primary" />
        <v-alert v-else-if="!historyItems.length" type="info" variant="tonal">尚无可追溯的调用记录，新版本开始的调用会在这里展示。</v-alert>
        <v-list v-else lines="two"><v-list-item v-for="item in historyItems" :key="item.call_id">
          <template #prepend><v-icon :color="item.status === 'completed' ? 'success' : 'error'">{{ item.status === 'completed' ? 'mdi-check-circle-outline' : 'mdi-alert-circle-outline' }}</v-icon></template>
          <v-list-item-title>{{ formatTime(item.created_at) }} · {{ item.status === 'completed' ? '分析完成' : '分析失败' }} · {{ item.duration_ms ? `${(item.duration_ms / 1000).toFixed(1)} 秒` : '执行中' }}</v-list-item-title>
          <v-list-item-subtitle>{{ historySummary(item) }}</v-list-item-subtitle>
        </v-list-item></v-list></v-card-text><v-card-actions><v-spacer /><v-btn @click="historyDialog = false">关闭</v-btn></v-card-actions></v-card>
    </v-dialog>

    <v-dialog v-model="suggestionsDialog" max-width="960" scrollable>
      <v-card>
        <v-card-title class="d-flex align-center"><v-icon class="mr-2">mdi-format-list-bulleted-square</v-icon>{{ suggestionsCard?.source_name || 'AI 信号源' }} · 最近 10 条交易建议<v-spacer /><v-btn icon="mdi-close" variant="text" @click="suggestionsDialog = false" /></v-card-title>
        <v-card-text>
          <v-progress-linear v-if="suggestionsLoading" indeterminate color="primary" />
          <v-alert v-else-if="!suggestionItems.length" type="info" variant="tonal">尚无已保存的交易建议。模型生成符合价格约束的建议后会出现在这里。</v-alert>
          <v-list v-else lines="three" class="suggestion-history-list">
            <v-list-item v-for="item in suggestionItems" :key="item.suggestion_id">
              <template #prepend><v-icon :color="suggestionMeta(item.direction).color">{{ suggestionMeta(item.direction).icon }}</v-icon></template>
              <v-list-item-title class="d-flex align-center flex-wrap ga-2"><strong>{{ suggestionMeta(item.direction).label }}</strong><v-chip size="x-small" variant="outlined">{{ item.period }}</v-chip><v-chip v-if="Number(item.suggestion_count) > 1" size="x-small" color="primary" variant="tonal">连续给出 {{ item.suggestion_count }} 次</v-chip><span class="text-medium-emphasis text-caption">{{ formatTime(item.last_seen_at) }}</span></v-list-item-title>
              <v-list-item-subtitle class="suggestion-history-detail"><span>入场 {{ price(item.entry_price) }}</span><span>止损 {{ price(item.stop_loss) }}</span><span>止盈 {{ price(item.take_profit) }}</span><span>置信度 {{ item.confidence }}%</span><p>{{ item.reason || '模型未提供建议理由' }}</p></v-list-item-subtitle>
            </v-list-item>
          </v-list>
        </v-card-text>
        <v-card-actions><v-spacer /><v-btn @click="suggestionsDialog = false">关闭</v-btn></v-card-actions>
      </v-card>
    </v-dialog>

    <v-dialog v-model="configDialog" max-width="900" scrollable>
      <v-card v-if="configCard">
        <v-card-title class="d-flex align-center"><v-icon class="mr-2">mdi-tune-variant</v-icon>{{ configCard.source_name }} · 信号源配置<v-spacer /><v-btn icon="mdi-close" variant="text" @click="configDialog = false" /></v-card-title>
        <v-card-text class="detail-body source-config">
          <h3>分析范围</h3>
          <div class="config-grid"><div><span>主品种</span><strong>{{ configCard.symbol }}</strong></div><div><span>主周期</span><strong>{{ configCard.period }}</strong></div><div><span>运行模型</span><strong>{{ configCard.model || '平台默认模型' }}</strong></div><div><span>调用间隔</span><strong>每 {{ configCard.analysis_interval_minutes || '-' }} 分钟</strong></div><div><span>K 线数量</span><strong>{{ configCard.kline_count || '-' }} 根</strong></div><div><span>运行数据共享</span><strong>{{ configCard.share_runtime_data ? '已共享' : '未共享' }}</strong></div></div>
          <h3>参考行情</h3>
          <div v-if="configCard.reference_market_data?.length" class="reference-config-list"><span v-for="(reference, index) in configCard.reference_market_data" :key="`${reference.symbol}-${reference.period}-${index}`">{{ reference.symbol }} · {{ reference.period }} · {{ reference.kline_count }} 根 · {{ referenceRoleLabel(reference.role) }}</span></div>
          <p v-else class="text-medium-emphasis">未配置参考行情。</p>
          <h3>专属提示词</h3>
          <div class="prompt-preview"><span>System Prompt</span><pre>{{ configCard.system_prompt || '未保存 System Prompt' }}</pre><span>分析模板</span><pre>{{ configCard.analysis_prompt_template || '未保存分析模板' }}</pre></div>
        </v-card-text>
      </v-card>
    </v-dialog>

    <v-snackbar v-model="showMessage" :color="messageColor">{{ message }}</v-snackbar>
  </v-container>
</template>

<script setup>
import { computed, onMounted, onUnmounted, reactive, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { marketAPI } from '@/api/market'

const router = useRouter()
const loading = ref(false)
const requestingAccess = ref(false)
const activeTab = ref('own')
const ownCards = ref([])
const sharedCards = ref([])
const reportedSymbols = ref([])
const summary = ref({})
const access = ref({})
const filters = reactive({ symbol: null, direction: null, status: null })
const detailDialog = ref(false)
const detailCard = ref(null)
const detailShared = ref(false)
const configDialog = ref(false)
const configCard = ref(null)
const historyDialog = ref(false)
const historyLoading = ref(false)
const historyItems = ref([])
const suggestionsDialog = ref(false)
const suggestionsLoading = ref(false)
const suggestionsCard = ref(null)
const suggestionItems = ref([])
const showMessage = ref(false)
const message = ref('')
const messageColor = ref('success')
let refreshTimer = null

const directionOptions = [{ title: '上涨', value: 'up' }, { title: '震荡', value: 'sideways' }, { title: '下降', value: 'down' }]
const statusOptions = [
  { title: '观察中', value: 'observing' }, { title: '等待价格', value: 'waiting_price' },
  { title: '进入触发区', value: 'ready_to_signal' }, { title: '已形成信号', value: 'signal_formed' },
  { title: '有交易建议', value: 'analysis_ready' }, { title: '等待分析', value: 'waiting_analysis' },
  { title: '信号源未启用', value: 'source_disabled' }, { title: '分析已过期', value: 'expired' },
]
const symbolOptions = computed(() => [...new Set([...reportedSymbols.value, ...ownCards.value.map(item => item.symbol), ...sharedCards.value.map(item => item.symbol)].filter(Boolean))].sort())
const cardMatchesSymbol = (card, symbol) => !symbol || card.symbol === symbol || (card.recommended_symbols || []).includes(symbol) || (card.similar_symbols || []).includes(symbol)
const applyFilters = cards => cards.filter(card => cardMatchesSymbol(card, filters.symbol) && (!filters.direction || card.direction === filters.direction) && (!filters.status || card.status === filters.status))
const filteredOwn = computed(() => applyFilters(ownCards.value))
const filteredShared = computed(() => applyFilters(sharedCards.value))

async function loadData() {
  loading.value = true
  try {
    const data = await marketAPI.getAIMarketView()
    ownCards.value = data.own || []
    sharedCards.value = data.shared || []
    reportedSymbols.value = data.account?.reported_symbols || []
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
const suggestionMeta = direction => ({ buy: { label: '建议买入', color: 'success', icon: 'mdi-arrow-up-bold' }, sell: { label: '建议卖出', color: 'error', icon: 'mdi-arrow-down-bold' } }[String(direction || '').toLowerCase()] || { label: '待定', color: 'grey', icon: 'mdi-help-circle-outline' })
const statusMeta = status => ({ observing: { label: '观察中', color: 'info' }, analysis_ready: { label: '有交易建议', color: 'success' }, waiting_analysis: { label: '等待分析', color: 'grey' }, source_disabled: { label: '信号源未启用', color: 'grey' }, expired: { label: '分析已过期', color: 'error' } }[status] || { label: status || '未知', color: 'grey' })
const lifecycleLabel = status => ({ draft: '草稿', backtesting: '回测中', backtest_passed: '回测通过', paper_trading: '模拟盘验证', production: '实盘', retired: '已停用' }[status] || status)
const price = value => Number(value) > 0 ? Number(value).toLocaleString('zh-CN', { minimumFractionDigits: 2, maximumFractionDigits: 5 }) : '--'
const formatTime = value => { if (!value) return '尚未分析'; const date = typeof value === 'number' ? new Date(value * 1000) : new Date(value); return Number.isNaN(date.getTime()) ? String(value) : date.toLocaleString('zh-CN') }
const shortTime = value => value ? formatTime(value).replace(/^.*?\s/, '') : '--'
const levels = values => Array.isArray(values) && values.length ? values.map(price).join('、') : '暂无'
function openDetail(card, shared) { detailCard.value = card; detailShared.value = shared; detailDialog.value = true }
function openConfig(card) { configCard.value = card; configDialog.value = true }
const referenceRoleLabel = role => ({ higher_timeframe: '高周期确认', lower_timeframe: '低周期确认', related_symbol: '关联品种', market_context: '市场上下文' }[role] || '市场上下文')
async function openHistory(card) {
  historyDialog.value = true; historyLoading.value = true; historyItems.value = []
  try { historyItems.value = (await marketAPI.getAIMarketHistory(card.signal_source_id)).items || [] }
  catch (error) { messageColor.value = 'error'; message.value = error.response?.data?.detail || '加载调用历史失败'; showMessage.value = true }
  finally { historyLoading.value = false }
}
async function openSuggestions(card) {
  suggestionsCard.value = card; suggestionsDialog.value = true; suggestionsLoading.value = true; suggestionItems.value = []
  try { suggestionItems.value = (await marketAPI.getAIMarketSuggestions(card.signal_source_id)).items || [] }
  catch (error) { messageColor.value = 'error'; message.value = error.response?.data?.detail || '加载交易建议失败'; showMessage.value = true }
  finally { suggestionsLoading.value = false }
}
const historySummary = item => {
  if (item.status !== 'completed') return item.error_message || '模型调用未完成'
  const result = item.result || {}; const trend = result.overall_trend || {}
  return trend.summary || `模型 ${item.model_id || '默认模型'} 已返回有效分析结果`
}
function goToStrategySettings() { detailDialog.value = false; router.push('/strategy-settings') }
function goToStrategyExecution(strategy) {
  detailDialog.value = false
  router.push({
    path: '/market',
    query: { strategy_id: strategy.strategy_id },
  })
}

onMounted(() => { loadData(); refreshTimer = setInterval(loadData, 30000) })
onUnmounted(() => clearInterval(refreshTimer))
</script>

<style scoped>
.ai-market-page{max-width:1600px;padding:28px}.ai-hero{display:flex;align-items:center;justify-content:space-between;gap:24px;padding:26px 30px;color:#f4fbf8;border-radius:22px;background:radial-gradient(circle at 86% 18%,rgba(247,186,67,.28),transparent 28%),linear-gradient(125deg,#123c35 0%,#176b56 58%,#0d8f70 100%);box-shadow:0 18px 40px rgba(20,82,68,.18)}.ai-hero h1{margin:2px 0 6px;font-size:clamp(1.8rem,3vw,2.7rem);line-height:1.05}.ai-hero p{margin:0;color:rgba(244,251,248,.76)}.section-kicker{color:#f4c96b;font-size:.72rem;font-weight:800;letter-spacing:.16em}.hero-status{display:flex;align-items:center;gap:10px}.metric-card .v-card-text{display:flex;align-items:baseline;justify-content:space-between;gap:12px}.metric-card strong{font-size:1.8rem}.time-metric strong{font-size:1rem}.filter-card,.content-card{border:1px solid #dce7e0;border-radius:18px;background:linear-gradient(155deg,#fff,#f8fbf9)}.filters{display:grid;grid-template-columns:repeat(3,minmax(180px,1fr));gap:14px}.content-card{overflow:hidden}.page-tabs{padding:8px 14px 0;border-bottom:1px solid #e2ebe5}.analysis-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(350px,1fr));gap:18px}.analysis-card{display:flex;flex-direction:column;gap:16px;padding:20px;border:1px solid #dce7e0;border-radius:18px;background:#fff;box-shadow:0 10px 24px rgba(34,78,64,.06)}.analysis-card.status-analysis_ready{border-color:#64a98f;box-shadow:0 12px 28px rgba(29,130,98,.13)}.shared-card{border-color:#cfe0e7}.card-head,.card-footer,.confidence-row{display:flex;align-items:center;justify-content:space-between;gap:12px}.symbol-line,.linked-strategies{display:flex;align-items:center;flex-wrap:wrap;gap:8px}.linked-strategies{font-size:.75rem;color:#52635e}.linked-strategies>span:first-child{font-weight:700}.detail-links{margin-top:8px}.symbol-line strong{font-size:1.25rem}.card-head span,.card-footer span{color:#718078;font-size:.76rem}.confidence-row strong{font-size:1.25rem}.price-grid{display:grid;grid-template-columns:repeat(4,1fr);gap:8px}.price-grid.compact{grid-template-columns:1fr 1.5fr}.price-grid div{display:flex;flex-direction:column;padding:10px;border-radius:10px;background:#f1f6f3}.price-grid span{color:#718078;font-size:.68rem}.price-grid strong{margin-top:2px;font-size:.85rem}.status-box{display:flex;align-items:flex-start;gap:10px;padding:12px;border-radius:12px;background:#f0f6f3}.status-box.shared{background:#eff6f8}.status-box p{margin:2px 0 0;color:#52635e;font-size:.8rem;line-height:1.45}.shared-meta,.recommendation-row{display:flex;align-items:center;flex-wrap:wrap;gap:7px}.recommendation-row{padding:10px 12px;border-radius:11px;background:#f4f8f5;color:#52635e;font-size:.75rem}.recommendation-row>span:first-child{font-weight:700}.unmatched-note{font-weight:400!important;color:#7a8882}.access-state,.empty-state{padding:72px 20px;text-align:center;color:#607269}.access-state h2,.empty-state h2{margin:14px 0 6px;color:#29493e}.access-state p,.empty-state p{margin:0 auto 20px;max-width:600px}.detail-body h3{margin:18px 0 6px;color:#29493e}.detail-body p{color:#52635e}.level-row{display:flex;flex-direction:column;gap:7px;padding:14px;border-radius:12px;background:#f1f6f3}.prompt-label{margin:12px 0 5px;font-weight:800}.detail-body pre{overflow:auto;max-height:230px;padding:14px;white-space:pre-wrap;border-radius:10px;background:#132b25;color:#dcece6;font-size:.75rem}
.analysis-time{display:flex;align-items:center;gap:6px;margin:0 0 14px;color:#587066;font-size:.82rem}.trade-suggestion{padding:12px;border:1px solid #dce7e0;border-left:4px solid #78918a;border-radius:8px;background:#f8fbf9}.trade-suggestion.is-buy{border-left-color:#2f9b70;background:#f2faf6}.trade-suggestion.is-sell{border-left-color:#d75b5b;background:#fff6f5}.trade-suggestion>div{display:flex;align-items:center;justify-content:space-between;gap:10px}.trade-suggestion span{color:#718078;font-size:.72rem}.trade-suggestion strong{display:flex;align-items:center;gap:4px;font-size:.86rem}.trade-suggestion p{margin:8px 0 0;color:#52635e;font-size:.8rem;line-height:1.45}.trade-suggestion.detail{display:flex;flex-direction:column;gap:10px}.suggestion-levels{display:flex!important;justify-content:flex-start!important;flex-wrap:wrap;gap:8px}.suggestion-levels span{padding:5px 8px;border-radius:5px;background:rgba(255,255,255,.7);font-size:.76rem}
.config-grid{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:8px}.config-grid div{display:flex;flex-direction:column;gap:3px;padding:10px;border:1px solid #dce7e0;border-radius:6px;background:#f8fbf9}.config-grid span,.prompt-preview>span{color:#718078;font-size:.72rem}.config-grid strong{color:#29493e;font-size:.85rem}.reference-config-list{display:flex;flex-wrap:wrap;gap:8px}.reference-config-list span{padding:7px 9px;border:1px solid #dce7e0;border-radius:5px;background:#f8fbf9;color:#52635e;font-size:.78rem}.prompt-preview{display:flex;flex-direction:column;gap:7px}.prompt-preview pre{max-height:220px;margin:0 0 8px;padding:12px;overflow:auto;white-space:pre-wrap;border-radius:6px;background:#132b25;color:#dcece6;font-size:.75rem;line-height:1.45}
.suggestion-history-list :deep(.v-list-item){border-bottom:1px solid #e4ece7}.suggestion-history-detail{display:flex!important;flex-wrap:wrap;gap:8px;margin-top:6px!important}.suggestion-history-detail span{padding:3px 7px;border-radius:4px;background:#f1f6f3;color:#52635e;font-size:.75rem}.suggestion-history-detail p{width:100%;margin:3px 0 0;color:#52635e;white-space:normal}
@media(max-width:800px){.ai-market-page{padding:16px}.ai-hero{align-items:stretch;flex-direction:column;padding:22px}.hero-status{align-items:stretch;flex-direction:column}.hero-status .v-chip{align-self:flex-start}.filters{grid-template-columns:1fr}.analysis-grid{grid-template-columns:1fr;padding:16px!important}.price-grid,.config-grid{grid-template-columns:repeat(2,1fr)}.card-footer{align-items:flex-start;flex-direction:column}}
</style>

<template>
  <div class="event-page">
    <section class="event-hero">
      <div>
        <span class="eyebrow">{{ isAdmin ? 'PLATFORM OPERATIONS' : 'ACCOUNT EVENTS' }}</span>
        <h1>{{ isAdmin ? '平台运行中心' : '账户运行记录' }}</h1>
        <p>{{ isAdmin ? '跨用户追踪异常、交易、EA 接入和 AI 调用。' : '查看当前账户从信号、风控到 MT5 成交的完整运行轨迹。' }}</p>
      </div>
      <div class="hero-actions">
        <v-chip :color="liveConnected ? 'success' : 'warning'" variant="flat">
          <v-icon start size="16">{{ liveConnected ? 'mdi-access-point' : 'mdi-access-point-off' }}</v-icon>
          {{ liveConnected ? '实时事件已连接' : '实时事件重连中' }}
        </v-chip>
        <v-btn icon="mdi-refresh" variant="tonal" :loading="loading" @click="loadAll" />
        <v-btn v-if="isAdmin" color="warning" variant="tonal" prepend-icon="mdi-broom" @click="purgeDialog = true">日志保留</v-btn>
      </div>
    </section>

    <section class="metric-grid">
      <article><span>24H 事件</span><strong>{{ summary.total || 0 }}</strong><small>平台与账户活动</small></article>
      <article class="danger"><span>错误</span><strong>{{ summary.errors || 0 }}</strong><small>需要优先处理</small></article>
      <article class="warning"><span>告警</span><strong>{{ summary.warnings || 0 }}</strong><small>风控与接入异常</small></article>
      <article class="trade"><span>交易链路</span><strong>{{ summary.trading || 0 }}</strong><small>交易与风控事件</small></article>
    </section>

    <v-card class="event-workbench" elevation="0">
      <div class="view-tabs">
        <button v-for="view in views" :key="view.value" :class="{ active: activeView === view.value }" @click="selectView(view.value)">
          <v-icon size="17">{{ view.icon }}</v-icon>{{ view.title }}
        </button>
      </div>

      <div class="filter-grid">
        <v-select v-if="isAdmin" v-model="filters.user_id" :items="facets.users" item-title="title" item-value="value" label="用户" clearable hide-details density="compact" />
        <v-select v-model="filters.account_id" :items="accountItems" item-title="title" item-value="value" label="交易账户" clearable hide-details density="compact" />
        <v-select v-model="filters.level" :items="levelOptions" label="级别" clearable hide-details density="compact" />
        <v-select v-model="filters.symbol" :items="facets.symbols" label="品种" clearable hide-details density="compact" />
        <v-select v-model="timeRange" :items="timeRanges" label="时间范围" hide-details density="compact" />
        <v-text-field v-model="filters.search" label="搜索消息、订单或关联ID" prepend-inner-icon="mdi-magnify" clearable hide-details density="compact" @keyup.enter="applyFilters" />
        <v-btn color="primary" height="40" @click="applyFilters">查询</v-btn>
      </div>

      <div class="table-meta">
        <span>共 {{ total }} 条事件</span>
        <span>审计事件不可删除，详情中的密钥与敏感字段应由生产者脱敏</span>
      </div>

      <div class="event-table-wrap">
        <table class="event-table">
          <thead><tr><th>时间</th><th>级别</th><th>模块</th><th v-if="isAdmin">用户 / 账户</th><th>事件</th><th>品种</th><th>状态</th><th></th></tr></thead>
          <tbody>
            <tr v-for="event in logs" :key="event.event_id" @click="openDetail(event)">
              <td class="event-time">{{ formatTime(event.timestamp) }}</td>
              <td><span class="level-dot" :class="event.level"></span>{{ levelLabel(event.level) }}</td>
              <td><v-chip size="x-small" variant="tonal" :color="categoryMeta(event.category).color">{{ categoryMeta(event.category).label }}</v-chip></td>
              <td v-if="isAdmin"><strong>{{ event.username || '平台' }}</strong><small>{{ event.account_name || '平台事件' }}{{ event.mt5_login ? ` · ${event.mt5_login}` : '' }}</small></td>
              <td><strong>{{ event.event_name }}</strong><small>{{ event.message || '无补充说明' }}</small></td>
              <td>{{ event.symbol || '--' }}</td>
              <td>{{ event.status || '--' }}</td>
              <td><v-icon size="18">mdi-chevron-right</v-icon></td>
            </tr>
            <tr v-if="!loading && !logs.length"><td :colspan="isAdmin ? 8 : 7" class="empty-state"><v-icon>mdi-text-box-search-outline</v-icon><span>当前筛选条件下没有事件</span></td></tr>
          </tbody>
        </table>
      </div>

      <div class="pagination-bar">
        <span>第 {{ page }} / {{ pageCount }} 页</span>
        <v-pagination v-model="page" :length="pageCount" :total-visible="6" density="compact" @update:model-value="loadLogs" />
      </div>
    </v-card>

    <v-dialog v-model="detailDialog" max-width="780">
      <v-card class="detail-card" v-if="selectedEvent">
        <v-card-title><div><span>{{ selectedEvent.event_id }}</span><h2>{{ selectedEvent.event_name }}</h2></div><v-btn icon="mdi-close" variant="text" @click="detailDialog = false" /></v-card-title>
        <v-card-text>
          <div class="detail-grid">
            <div><span>时间</span><strong>{{ formatFullTime(selectedEvent.timestamp) }}</strong></div>
            <div><span>级别 / 分类</span><strong>{{ levelLabel(selectedEvent.level) }} · {{ categoryMeta(selectedEvent.category).label }}</strong></div>
            <div><span>用户 / 账户</span><strong>{{ selectedEvent.username || '平台' }} · {{ selectedEvent.account_name || '平台事件' }}</strong></div>
            <div><span>关联 ID</span><strong>{{ selectedEvent.correlation_id || '--' }}</strong></div>
            <div><span>业务对象</span><strong>{{ selectedEvent.entity_type || '--' }} {{ selectedEvent.entity_id || '' }}</strong></div>
            <div><span>请求 ID</span><strong>{{ selectedEvent.request_id || '--' }}</strong></div>
          </div>
          <div class="detail-message">{{ selectedEvent.message || '无补充说明' }}</div>
          <h3>结构化详情</h3>
          <pre>{{ JSON.stringify(selectedEvent.detail || {}, null, 2) }}</pre>
        </v-card-text>
      </v-card>
    </v-dialog>

    <v-dialog v-model="purgeDialog" max-width="460">
      <v-card class="detail-card">
        <v-card-title><div><span>RETENTION POLICY</span><h2>清理过期运行日志</h2></div></v-card-title>
        <v-card-text>
          <v-alert type="warning" variant="tonal" class="mb-4">仅删除超过保留期的运行事件，用户操作审计日志不会被删除。</v-alert>
          <v-select v-model="retentionDays" :items="retentionOptions" label="保留最近日志" />
        </v-card-text>
        <v-card-actions><v-spacer/><v-btn variant="text" @click="purgeDialog = false">取消</v-btn><v-btn color="warning" :loading="purging" @click="purgeLogs">确认清理</v-btn></v-card-actions>
      </v-card>
    </v-dialog>

    <v-snackbar v-model="snackbar" :color="messageType">{{ message }}</v-snackbar>
  </div>
</template>

<script setup>
import { computed, onMounted, onUnmounted, reactive, ref, watch } from 'vue'
import { authState, getAuthToken } from '@/auth'
import { marketAPI } from '@/api/market'
import { getApiWebSocketUrl } from '@/api/runtime'
import { useAccountContext } from '@/composables/useAccountContext'

const isAdmin = computed(() => authState.user?.role === 'admin')
const { accountOptions, selectedAccountId, loadAccountContext } = useAccountContext()
const logs = ref([]), total = ref(0), page = ref(1), loading = ref(false)
const summary = ref({}), facets = reactive({ users: [], accounts: [], symbols: [] })
const activeView = ref('anomaly'), timeRange = ref('24h')
const filters = reactive({ user_id: null, account_id: null, level: null, symbol: null, search: '' })
const detailDialog = ref(false), selectedEvent = ref(null)
const purgeDialog = ref(false), purging = ref(false), retentionDays = ref(30)
const snackbar = ref(false), message = ref(''), messageType = ref('success')
const liveConnected = ref(false)
let ws = null, reconnectTimer = null, liveRefreshTimer = null, disposed = false, initialized = false

const views = [
  { title: '异常与告警', value: 'anomaly', icon: 'mdi-alert-decagram-outline' },
  { title: '交易审计', value: 'trading', icon: 'mdi-swap-horizontal-bold' },
  { title: 'EA 接入', value: 'integration', icon: 'mdi-connection' },
  { title: 'AI 调用', value: 'ai', icon: 'mdi-brain' },
  { title: '用户操作', value: 'audit', icon: 'mdi-shield-account-outline' },
  { title: '全部事件', value: 'all', icon: 'mdi-format-list-bulleted' },
]
const levelOptions = [{ title: '信息', value: 'info' }, { title: '告警', value: 'warning' }, { title: '错误', value: 'error' }, { title: '严重', value: 'critical' }]
const timeRanges = [{ title: '最近 1 小时', value: '1h' }, { title: '最近 24 小时', value: '24h' }, { title: '最近 7 天', value: '7d' }, { title: '最近 30 天', value: '30d' }, { title: '全部时间', value: 'all' }]
const retentionOptions = [{ title: '30 天', value: 30 }, { title: '90 天', value: 90 }, { title: '180 天', value: 180 }]
const pageSize = 50
const pageCount = computed(() => Math.max(1, Math.ceil(total.value / pageSize)))
const accountItems = computed(() => isAdmin.value ? facets.accounts.map(item => ({ ...item, title: `${item.title}${item.mt5_login ? ` · ${item.mt5_login}` : ''}` })) : accountOptions.value)

function queryParams(includePaging = true) {
  const params = {}
  if (includePaging) Object.assign(params, { page: page.value, page_size: pageSize })
  Object.entries(filters).forEach(([key, value]) => { if (value !== null && value !== '') params[key] = value })
  const seconds = { '1h': 3600, '24h': 86400, '7d': 604800, '30d': 2592000 }[timeRange.value]
  if (seconds) params.start_at = Math.floor(Date.now() / 1000) - seconds
  if (activeView.value === 'anomaly') params.level = 'warning,error,critical'
  else if (activeView.value !== 'all') params.category = activeView.value
  return params
}

async function loadLogs() {
  loading.value = true
  try {
    const data = await marketAPI.getSystemLogs(queryParams())
    logs.value = data.logs || []; total.value = data.total || 0
    Object.assign(facets, data.facets || {})
  } catch (error) { showError(error, '加载运行日志失败') }
  finally { loading.value = false }
}
async function loadSummary() {
  try { summary.value = (await marketAPI.getSystemLogSummary(queryParams(false))).summary || {} }
  catch (error) { showError(error, '加载日志汇总失败') }
}
async function loadAll() { await Promise.all([loadLogs(), loadSummary()]) }
function applyFilters() { page.value = 1; loadAll(); reconnectLive() }
function selectView(value) { activeView.value = value; applyFilters() }
function openDetail(event) { selectedEvent.value = event; detailDialog.value = true }
function showError(error, fallback) { messageType.value = 'error'; message.value = error.response?.data?.detail || fallback; snackbar.value = true }

async function purgeLogs() {
  purging.value = true
  try {
    const before = Math.floor(Date.now() / 1000) - retentionDays.value * 86400
    const data = await marketAPI.purgeSystemLogs(before)
    messageType.value = 'success'; message.value = data.message; snackbar.value = true; purgeDialog.value = false
    await loadAll()
  } catch (error) { showError(error, '清理日志失败') }
  finally { purging.value = false }
}

function connectLive() {
  if (disposed) return
  ws = new WebSocket(getApiWebSocketUrl('/ws/system-logs'))
  ws.onopen = () => ws.send(JSON.stringify({ type: 'auth', token: getAuthToken(), account_id: isAdmin.value ? null : filters.account_id }))
  ws.onmessage = event => {
    const payload = JSON.parse(event.data)
    if (payload.type === 'connected') liveConnected.value = true
    if (payload.type === 'system_event') {
      clearTimeout(liveRefreshTimer)
      liveRefreshTimer = setTimeout(loadAll, 400)
    }
  }
  ws.onclose = () => { liveConnected.value = false; if (!disposed) reconnectTimer = setTimeout(connectLive, 5000) }
  ws.onerror = () => { liveConnected.value = false }
}
function reconnectLive() { if (ws) ws.close(); else connectLive() }

const categoryMeta = category => ({ trading: { label: '交易', color: 'success' }, risk: { label: '风控', color: 'warning' }, integration: { label: 'EA 接入', color: 'info' }, ai: { label: 'AI', color: 'primary' }, audit: { label: '审计', color: 'deep-orange' }, market: { label: '行情', color: 'cyan' }, system: { label: '系统', color: 'grey' } }[category] || { label: category || '其他', color: 'grey' })
const levelLabel = level => ({ info: '信息', warning: '告警', error: '错误', critical: '严重' }[level] || level)
const formatTime = value => value ? new Date(value).toLocaleString('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', second: '2-digit' }) : '--'
const formatFullTime = value => value ? new Date(value).toLocaleString('zh-CN') : '--'

watch(selectedAccountId, value => { if (initialized && !isAdmin.value) { filters.account_id = value; applyFilters() } })
onMounted(async () => { if (!isAdmin.value) { await loadAccountContext(); filters.account_id = selectedAccountId.value } await loadAll(); initialized = true; connectLive() })
onUnmounted(() => { disposed = true; clearTimeout(reconnectTimer); clearTimeout(liveRefreshTimer); if (ws) ws.close() })
</script>

<style scoped>
.event-page{min-height:100%;padding:28px;background:radial-gradient(circle at 8% 0,#dbeee5 0,transparent 30%),linear-gradient(145deg,#f3f5ef,#edf2ef)}
.event-hero{display:flex;align-items:flex-end;justify-content:space-between;padding:30px 34px;border-radius:24px;color:#f5f7ef;background:linear-gradient(120deg,#173f34,#286b58 64%,#b47b31);box-shadow:0 20px 45px rgba(26,68,56,.18)}
.eyebrow{font-size:.68rem;letter-spacing:.2em;color:#c7ddcf}.event-hero h1{margin:6px 0 4px;font-family:"Avenir Next Condensed","Trebuchet MS",sans-serif;font-size:2.3rem}.event-hero p{margin:0;color:#dce8e1}.hero-actions{display:flex;gap:10px;align-items:center}
.metric-grid{display:grid;grid-template-columns:repeat(4,1fr);gap:14px;margin:18px 0}.metric-grid article{padding:18px 20px;border:1px solid #dce6df;border-radius:17px;background:#fff}.metric-grid span,.metric-grid small{display:block;color:#78857f;font-size:.72rem}.metric-grid strong{display:block;margin:5px 0;color:#244c40;font-size:1.75rem}.metric-grid .danger strong{color:#ad453f}.metric-grid .warning strong{color:#b4772c}.metric-grid .trade strong{color:#28745c}
.event-workbench{overflow:hidden;border:1px solid #d9e3dc;border-radius:22px!important;background:rgba(255,255,255,.94)}.view-tabs{display:flex;gap:5px;padding:14px 18px;border-bottom:1px solid #e4eae6;overflow:auto}.view-tabs button{display:flex;align-items:center;gap:7px;padding:9px 13px;border:0;border-radius:10px;color:#617169;background:transparent;white-space:nowrap;cursor:pointer}.view-tabs button.active{color:#fff;background:#276653}
.filter-grid{display:grid;grid-template-columns:repeat(5,minmax(130px,1fr)) minmax(220px,1.6fr) auto;gap:10px;padding:17px 18px;background:#f7f9f6}.table-meta,.pagination-bar{display:flex;align-items:center;justify-content:space-between;padding:12px 19px;color:#74817b;font-size:.7rem}.event-table-wrap{overflow:auto}.event-table{width:100%;border-collapse:collapse;min-width:950px}.event-table th{padding:11px 14px;text-align:left;color:#7b8881;font-size:.65rem;letter-spacing:.06em;background:#f1f5f2}.event-table td{padding:13px 14px;border-bottom:1px solid #edf0ee;color:#43534c;font-size:.75rem}.event-table tbody tr{cursor:pointer}.event-table tbody tr:hover{background:#f3f8f5}.event-table strong,.event-table small{display:block}.event-table small{margin-top:3px;color:#89938e}.event-time{white-space:nowrap}.level-dot{display:inline-block;width:8px;height:8px;margin-right:7px;border-radius:50%;background:#6e9183}.level-dot.warning{background:#d39435}.level-dot.error,.level-dot.critical{background:#c94d47}.empty-state{text-align:center!important;padding:55px!important}.empty-state span{display:block;margin-top:8px}
.detail-card{border-radius:20px!important}.detail-card .v-card-title{display:flex;justify-content:space-between;padding:23px 25px;color:#fff;background:#214f41}.detail-card .v-card-title span{font-size:.62rem;letter-spacing:.1em;color:#bfd5ca}.detail-card h2{margin:3px 0 0}.detail-grid{display:grid;grid-template-columns:1fr 1fr;gap:12px;margin:8px 0 18px}.detail-grid div{padding:12px;border-radius:10px;background:#f2f5f2}.detail-grid span,.detail-grid strong{display:block}.detail-grid span{color:#829089;font-size:.65rem}.detail-grid strong{margin-top:4px;font-size:.78rem;word-break:break-all}.detail-message{padding:14px;border-left:3px solid #b47b31;background:#fff8ec}.detail-card h3{margin:20px 0 8px;font-size:.82rem}.detail-card pre{overflow:auto;max-height:320px;padding:15px;border-radius:12px;color:#dce8e1;background:#173f34;font-size:.72rem}
@media(max-width:1100px){.filter-grid{grid-template-columns:repeat(3,1fr)}.metric-grid{grid-template-columns:1fr 1fr}}@media(max-width:700px){.event-page{padding:14px}.event-hero{align-items:flex-start;flex-direction:column;gap:18px;padding:24px}.hero-actions{flex-wrap:wrap}.metric-grid{grid-template-columns:1fr 1fr}.filter-grid{grid-template-columns:1fr}.detail-grid{grid-template-columns:1fr}}
</style>

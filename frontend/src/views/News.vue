<template>
  <div class="event-page">
    <section class="event-hero">
      <div>
        <div class="eyebrow">PUBLIC MARKET INTELLIGENCE</div>
        <h1>市场事件</h1>
        <p>统一展示外部数据源灌入的财经日历、关键事件和实时市场快讯。</p>
      </div>
      <div class="live-state" :class="{ online: wsConnected }">
        <span class="live-dot"></span>
        {{ wsConnected ? '快讯实时通道已连接' : '快讯实时通道重连中' }}
      </div>
    </section>

    <v-alert
      v-if="errorMessage"
      type="error"
      variant="tonal"
      closable
      class="mb-5"
      @click:close="errorMessage = ''"
    >
      {{ errorMessage }}
    </v-alert>

    <section class="metric-grid">
      <article class="metric-card calendar">
        <span>财经日历</span>
        <strong>{{ status.calendar_count || 0 }}</strong>
        <small>公共事件总数</small>
      </article>
      <article class="metric-card key-event">
        <span>关键事件</span>
        <strong>{{ status.key_event_count || 0 }}</strong>
        <small>重点市场事件</small>
      </article>
      <article class="metric-card flash">
        <span>市场快讯</span>
        <strong>{{ status.flash_news_count || 0 }}</strong>
        <small>MySQL 已保存</small>
      </article>
    </section>

    <v-card class="event-card" elevation="0">
      <div class="toolbar">
        <v-tabs v-model="activeTab" color="primary" density="comfortable">
          <v-tab value="calendar" prepend-icon="mdi-calendar-month-outline">财经日历</v-tab>
          <v-tab value="key-events" prepend-icon="mdi-star-four-points-outline">关键事件</v-tab>
          <v-tab value="flash" prepend-icon="mdi-flash-outline">市场快讯</v-tab>
        </v-tabs>
        <div class="toolbar-actions">
          <v-text-field
            v-if="activeTab !== 'flash'"
            v-model="selectedDate"
            type="date"
            label="数据日期"
            variant="outlined"
            density="compact"
            hide-details
            @update:model-value="loadDailyData"
          />
          <v-btn
            variant="tonal"
            color="primary"
            prepend-icon="mdi-refresh"
            :loading="loading"
            @click="loadActiveData"
          >
            刷新
          </v-btn>
        </div>
      </div>

      <v-window v-model="activeTab">
        <v-window-item value="calendar">
          <div v-if="calendar.length" class="calendar-list">
            <article v-for="item in calendar" :key="item.id" class="calendar-row">
              <time>{{ formatEventTime(item.event_time_beijing || item.publish_time || item.event_time) }}</time>
              <div class="event-copy">
                <div class="event-title-line">
                  <strong>{{ item.name }}</strong>
                  <v-chip v-if="item.currency" size="x-small" variant="tonal">{{ item.currency }}</v-chip>
                  <v-chip size="x-small" :color="importanceColor(item.importance)" variant="tonal">
                    {{ importanceLabel(item.importance) }}
                  </v-chip>
                </div>
                <span>{{ item.country || '全球' }} · {{ item.source || 'external' }} · 北京时间</span>
              </div>
              <div class="value-strip">
                <span>前值 <b>{{ displayValue(item.previous) }}</b></span>
                <span>预期 <b>{{ displayValue(item.forecast ?? item.consensus) }}</b></span>
                <span>公布 <b class="actual">{{ displayValue(item.actual) }}</b></span>
              </div>
            </article>
          </div>
          <EmptyState v-else icon="mdi-calendar-blank-outline" text="该日期暂无财经日历数据" />
        </v-window-item>

        <v-window-item value="key-events">
          <div v-if="keyEvents.length" class="key-grid">
            <article v-for="item in keyEvents" :key="item.id" class="key-card">
              <div class="key-time">{{ formatEventTime(item.event_time) }}</div>
              <div class="event-title-line">
                <v-chip size="x-small" :color="importanceColor(item.importance)" variant="tonal">
                  {{ importanceLabel(item.importance) }}
                </v-chip>
                <span>{{ item.category || '重要事件' }}</span>
              </div>
              <h3>{{ item.title }}</h3>
              <p>{{ item.summary || item.description || item.content || '暂无补充说明' }}</p>
              <div class="symbol-row">
                <v-chip v-for="symbol in item.symbols || []" :key="symbol" size="x-small" variant="outlined">
                  {{ symbol }}
                </v-chip>
                <small>{{ item.source || 'external' }}</small>
              </div>
            </article>
          </div>
          <EmptyState v-else icon="mdi-star-off-outline" text="该日期暂无关键事件数据" />
        </v-window-item>

        <v-window-item value="flash">
          <div v-if="flashNews.length" class="flash-list">
            <article v-for="item in flashNews" :key="item.id" class="flash-row">
              <div class="flash-time">{{ formatDateTime(item.published_at || item.time) }}</div>
              <div class="flash-body">
                <div class="event-title-line">
                  <v-chip
                    v-if="Number(item.importance) >= 2"
                    size="x-small"
                    color="warning"
                    variant="tonal"
                  >
                    重要
                  </v-chip>
                  <span class="flash-source">{{ item.source || 'external' }}</span>
                </div>
                <h3 v-if="item.title && item.title !== item.content">{{ item.title }}</h3>
                <p>{{ item.content }}</p>
                <div class="symbol-row">
                  <v-chip v-for="symbol in item.symbols || []" :key="symbol" size="x-small" variant="outlined">
                    {{ symbol }}
                  </v-chip>
                  <v-chip v-for="keyword in (item.keywords || []).slice(0, 5)" :key="keyword" size="x-small" variant="text">
                    #{{ keyword }}
                  </v-chip>
                </div>
              </div>
            </article>
          </div>
          <EmptyState v-else icon="mdi-flash-off-outline" text="暂无市场快讯数据" />
        </v-window-item>
      </v-window>
    </v-card>
  </div>
</template>

<script setup>
import { defineComponent, h, onMounted, onUnmounted, ref, watch } from 'vue'
import { marketAPI } from '../api/market'

const EmptyState = defineComponent({
  props: { icon: String, text: String },
  setup(props) {
    return () => h('div', { class: 'empty-state' }, [
      h('span', { class: `mdi ${props.icon}` }),
      h('p', props.text),
    ])
  },
})

function localDateInput(date = new Date()) {
  const local = new Date(date.getTime() - date.getTimezoneOffset() * 60000)
  return local.toISOString().slice(0, 10)
}

const activeTab = ref('calendar')
const selectedDate = ref(localDateInput())
const status = ref({})
const calendar = ref([])
const keyEvents = ref([])
const flashNews = ref([])
const loading = ref(false)
const errorMessage = ref('')
const wsConnected = ref(false)
let socket = null
let reconnectTimer = null
let unmounted = false

async function loadStatus() {
  const response = await marketAPI.getMarketEventStatus()
  status.value = response.data || {}
}

async function loadCalendar() {
  const response = await marketAPI.getMarketCalendar(selectedDate.value)
  calendar.value = response.data || []
}

async function loadKeyEvents() {
  const response = await marketAPI.getMarketKeyEvents(selectedDate.value)
  keyEvents.value = response.data || []
}

async function loadFlashNews() {
  const response = await marketAPI.getMarketFlashNews(100)
  flashNews.value = response.data || []
}

async function runLoad(loaders) {
  loading.value = true
  errorMessage.value = ''
  try {
    await Promise.all(loaders.map(loader => loader()))
  } catch (error) {
    errorMessage.value = error.response?.data?.detail || '加载市场事件数据失败'
  } finally {
    loading.value = false
  }
}

function loadDailyData() {
  return runLoad([
    activeTab.value === 'calendar' ? loadCalendar : loadKeyEvents,
    loadStatus,
  ])
}

function loadActiveData() {
  const loader = activeTab.value === 'calendar'
    ? loadCalendar
    : activeTab.value === 'key-events' ? loadKeyEvents : loadFlashNews
  return runLoad([loader, loadStatus])
}

function mergeRealtimeFlash(items) {
  const byId = new Map(flashNews.value.map(item => [item.id, item]))
  for (const item of items || []) byId.set(item.id, item)
  flashNews.value = [...byId.values()]
    .sort((left, right) => String(right.published_at || '').localeCompare(String(left.published_at || '')))
    .slice(0, 100)
  status.value.flash_news_count = Math.max(
    Number(status.value.flash_news_count || 0),
    flashNews.value.length,
  )
}

function connectRealtime() {
  if (unmounted || socket) return
  socket = marketAPI.createMarketEventWebSocket((message) => {
    if (message.type === 'connected') {
      wsConnected.value = true
      loadStatus().catch(() => {})
    }
    if (message.type === 'market_flash_news_updated') {
      mergeRealtimeFlash(message.items)
      loadStatus().catch(() => {})
    }
  }, () => {
    socket = null
    wsConnected.value = false
    if (!unmounted) reconnectTimer = window.setTimeout(connectRealtime, 5000)
  })
}

function formatEventTime(value) {
  if (!value) return '待定'
  if (/^\d{1,2}:\d{2}/.test(value)) return value.slice(0, 5)
  const parsed = new Date(value)
  if (Number.isNaN(parsed.getTime())) return value
  return parsed.toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' })
}

function formatDateTime(value) {
  if (!value) return '--'
  const parsed = new Date(value)
  if (Number.isNaN(parsed.getTime())) return value
  return parsed.toLocaleString('zh-CN', {
    month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', second: '2-digit',
  })
}

function displayValue(value) {
  return value === null || value === undefined || value === '' ? '--' : value
}

function importanceLabel(value) {
  const level = Number(value || 0)
  if (level >= 3) return '高影响'
  if (level === 2) return '中影响'
  if (level === 1) return '低影响'
  return '未评级'
}

function importanceColor(value) {
  const level = Number(value || 0)
  if (level >= 3) return 'error'
  if (level === 2) return 'warning'
  return 'grey'
}

onMounted(() => {
  runLoad([loadStatus, loadCalendar, loadKeyEvents, loadFlashNews])
  connectRealtime()
})

watch(activeTab, () => {
  loadActiveData()
})

onUnmounted(() => {
  unmounted = true
  window.clearTimeout(reconnectTimer)
  socket?.close()
  socket = null
})
</script>

<style scoped>
.event-page {
  min-height: 100%;
  padding: 28px;
  background:
    radial-gradient(circle at 92% 4%, rgba(211, 154, 57, .17), transparent 28%),
    linear-gradient(145deg, #f4f0e7 0%, #edf3ef 54%, #f8f6ef 100%);
}
.event-hero { display: flex; justify-content: space-between; align-items: end; gap: 24px; padding: 34px 38px; margin-bottom: 20px; color: #f7f0df; border-radius: 24px; background: linear-gradient(120deg, #102f2a, #1f5d4e 72%, #8c652c); box-shadow: 0 20px 44px rgba(20, 61, 51, .2); }
.eyebrow { color: #e4bd72; font-size: .72rem; font-weight: 800; letter-spacing: .17em; }
.event-hero h1 { margin: 5px 0 8px; font-family: Georgia, serif; font-size: clamp(2.2rem, 5vw, 3.8rem); line-height: 1; }
.event-hero p { margin: 0; color: rgba(255,255,255,.72); }
.live-state { display: flex; align-items: center; gap: 9px; padding: 10px 14px; white-space: nowrap; border: 1px solid rgba(255,255,255,.18); border-radius: 99px; background: rgba(0,0,0,.12); font-size: .78rem; }
.live-dot { width: 8px; height: 8px; border-radius: 50%; background: #d17b63; }
.live-state.online .live-dot { background: #79d8a7; box-shadow: 0 0 0 5px rgba(121,216,167,.13); }
.metric-grid { display: grid; grid-template-columns: repeat(3, 1fr); gap: 14px; margin-bottom: 18px; }
.metric-card { padding: 18px 20px; border: 1px solid rgba(22,65,53,.09); border-radius: 16px; background: rgba(255,255,255,.86); }
.metric-card span, .metric-card small { display: block; color: #78837e; }
.metric-card strong { display: block; margin: 3px 0; color: #173f35; font: 700 1.8rem Georgia, serif; }
.metric-card.calendar { border-top: 3px solid #277d66; }
.metric-card.key-event { border-top: 3px solid #c18b36; }
.metric-card.flash { border-top: 3px solid #c45e45; }
.event-card { overflow: hidden; border: 1px solid rgba(22,65,53,.1); border-radius: 20px; background: rgba(255,255,255,.92); }
.toolbar { display: flex; justify-content: space-between; align-items: center; gap: 18px; padding: 8px 18px; border-bottom: 1px solid #e2e8e4; }
.toolbar-actions { display: flex; align-items: center; gap: 10px; min-width: 330px; }
.toolbar-actions :deep(.v-input) { flex: 1; }
.calendar-list, .flash-list { padding: 8px 20px 24px; }
.calendar-row { display: grid; grid-template-columns: 80px minmax(220px, 1fr) auto; align-items: center; gap: 18px; padding: 18px 8px; border-bottom: 1px solid #e7ebe8; }
.calendar-row time { color: #1d6755; font: 700 1.05rem Georgia, serif; }
.event-title-line, .symbol-row { display: flex; align-items: center; flex-wrap: wrap; gap: 7px; }
.event-copy > span, .flash-source { color: #87908c; font-size: .75rem; }
.value-strip { display: flex; gap: 20px; color: #808a85; font-size: .75rem; }
.value-strip span { display: grid; gap: 3px; }
.value-strip b { color: #344c45; font-size: .85rem; }
.value-strip .actual { color: #b24f39; }
.key-grid { display: grid; grid-template-columns: repeat(2, 1fr); gap: 14px; padding: 20px; }
.key-card { position: relative; padding: 20px; border: 1px solid #e0e7e2; border-radius: 16px; background: #fbfcfa; }
.key-time { position: absolute; top: 18px; right: 18px; color: #1d6755; font-weight: 800; }
.key-card h3 { margin: 14px 0 8px; color: #263f38; font-family: Georgia, serif; }
.key-card p { min-height: 42px; color: #697771; font-size: .88rem; line-height: 1.55; }
.symbol-row small { margin-left: auto; color: #929b97; }
.flash-row { display: grid; grid-template-columns: 125px 1fr; gap: 18px; padding: 20px 8px; border-bottom: 1px solid #e7ebe8; }
.flash-time { color: #8d7060; font: 700 .78rem Georgia, serif; }
.flash-body h3 { margin: 7px 0; color: #293f39; }
.flash-body p { margin: 8px 0 12px; color: #3f514b; line-height: 1.65; }
.empty-state { padding: 90px 20px; text-align: center; color: #8b9690; }
.empty-state .mdi { font-size: 3rem; }
.empty-state p { margin-top: 10px; }
@media (max-width: 800px) {
  .event-page { padding: 14px; }
  .event-hero, .toolbar { align-items: flex-start; flex-direction: column; }
  .metric-grid { grid-template-columns: 1fr; }
  .toolbar-actions { width: 100%; min-width: 0; }
  .calendar-row, .flash-row { grid-template-columns: 1fr; gap: 8px; }
  .value-strip { flex-wrap: wrap; }
  .key-grid { grid-template-columns: 1fr; }
}
</style>

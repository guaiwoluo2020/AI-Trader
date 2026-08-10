<template>
  <v-container fluid class="dashboard-page">
    <section class="dashboard-hero mb-5">
      <div>
        <div class="section-kicker">ACCOUNT OPERATIONS OVERVIEW</div>
        <h1>仪表盘</h1>
        <p>
          {{ overview.account.account_name || selectedAccount?.account_name || '当前交易账户' }}
          <span v-if="overview.account.mt5_login"> · {{ overview.account.mt5_login }}</span>
          <span v-if="overview.account.mt5_server"> · {{ overview.account.mt5_server }}</span>
        </p>
        <div class="hero-meta mt-4">
          <v-chip :color="overview.account.connected ? 'success' : 'error'" variant="flat" size="small">
            <v-icon start>mdi-lan-connect</v-icon>{{ overview.account.connected ? 'MT5 已连接' : 'MT5 未连接' }}
          </v-chip>
          <v-chip :color="overview.account.trading_enabled ? 'success' : 'warning'" variant="tonal" size="small">
            {{ overview.account.trading_enabled ? '交易已启用' : '交易已暂停' }}
          </v-chip>
          <v-chip :color="overview.account.auto_trading_enabled ? 'success' : 'grey'" variant="tonal" size="small">
            {{ overview.account.auto_trading_enabled ? '自动交易开启' : '人工确认模式' }}
          </v-chip>
        </div>
      </div>
      <div class="hero-actions">
        <span>更新于 {{ formatTime(overview.generated_at) }}</span>
        <v-btn color="white" variant="tonal" prepend-icon="mdi-refresh" :loading="loading" @click="loadData">刷新</v-btn>
      </div>
    </section>

    <v-alert v-if="error" type="error" closable class="mb-4" @click:close="error = ''">{{ error }}</v-alert>

    <section class="metric-grid mb-5">
      <article class="metric-card metric-card--equity">
        <div><span>账户净值</span><strong>{{ money(overview.financial.equity) }}</strong><small>余额 {{ money(overview.financial.balance) }}</small></div>
        <v-icon>mdi-wallet-outline</v-icon>
      </article>
      <article class="metric-card" :class="profitClass(overview.financial.floating_profit)">
        <div><span>浮动盈亏</span><strong>{{ signedMoney(overview.financial.floating_profit) }}</strong><small>{{ overview.positions.count }} 个当前持仓</small></div>
        <v-icon>mdi-chart-areaspline</v-icon>
      </article>
      <article class="metric-card" :class="profitClass(overview.financial.today_net_profit)">
        <div><span>今日已实现</span><strong>{{ signedMoney(overview.financial.today_net_profit) }}</strong><small>{{ overview.financial.today_trade_count }} 笔平仓成交</small></div>
        <v-icon>mdi-calendar-today-outline</v-icon>
      </article>
      <article class="metric-card" :class="overview.attention.length ? 'metric-card--warning' : 'metric-card--healthy'">
        <div><span>需要关注</span><strong>{{ overview.attention.length }}</strong><small>{{ overview.attention.length ? '项运行事项待处理' : '账户运行正常' }}</small></div>
        <v-icon>{{ overview.attention.length ? 'mdi-alert-circle-outline' : 'mdi-check-decagram-outline' }}</v-icon>
      </article>
    </section>

    <section class="primary-grid mb-5">
      <v-card class="content-card attention-card" elevation="0">
        <v-card-title class="section-head">
          <div><v-icon color="warning">mdi-radar</v-icon><span>需要关注</span></div>
          <v-chip size="small" :color="overview.attention.length ? 'warning' : 'success'" variant="tonal">{{ overview.attention.length }}</v-chip>
        </v-card-title>
        <v-card-text>
          <div v-if="!overview.attention.length" class="healthy-state">
            <v-icon size="48" color="success">mdi-shield-check-outline</v-icon>
            <div><strong>当前运行正常</strong><p>连接、风控、策略和行情暂未发现需要处理的问题。</p></div>
          </div>
          <div v-else class="attention-list">
            <button v-for="item in overview.attention" :key="item.type" type="button" @click="go(item.path)">
              <v-avatar :color="severityMeta(item.severity).color" variant="tonal" size="38"><v-icon size="20">{{ severityMeta(item.severity).icon }}</v-icon></v-avatar>
              <div><strong>{{ item.title }}</strong><span>{{ item.detail }}</span></div>
              <v-icon size="18">mdi-chevron-right</v-icon>
            </button>
          </div>
        </v-card-text>
      </v-card>

      <v-card class="content-card risk-card" elevation="0">
        <v-card-title class="section-head"><div><v-icon color="success">mdi-shield-half-full</v-icon><span>账户与风控</span></div><v-chip :color="overview.risk.circuit_breaker ? 'error' : 'success'" size="small" variant="tonal">{{ overview.risk.circuit_breaker ? '已熔断' : '正常' }}</v-chip></v-card-title>
        <v-card-text>
          <div class="funds-grid">
            <div><span>可用资金</span><strong>{{ money(overview.financial.free_margin) }}</strong></div>
            <div><span>保证金占用</span><strong>{{ money(overview.financial.margin) }}</strong></div>
            <div><span>保证金水平</span><strong>{{ percent(overview.financial.margin_level) }}</strong></div>
          </div>
          <div class="risk-progress">
            <div class="risk-progress__head"><span>今日订单额度</span><strong>{{ overview.risk.daily_order_count || 0 }} / {{ overview.risk.daily_order_limit || 0 }}</strong></div>
            <v-progress-linear :model-value="ratio(overview.risk.daily_order_count, overview.risk.daily_order_limit)" :color="progressColor(ratio(overview.risk.daily_order_count, overview.risk.daily_order_limit))" height="8" rounded />
          </div>
          <div class="risk-progress">
            <div class="risk-progress__head"><span>今日风险额度</span><strong>{{ Number(overview.risk.daily_risk_used || 0).toFixed(2) }}% / {{ Number(overview.risk.daily_risk_limit || 0).toFixed(2) }}%</strong></div>
            <v-progress-linear :model-value="ratio(overview.risk.daily_risk_used, overview.risk.daily_risk_limit)" :color="progressColor(ratio(overview.risk.daily_risk_used, overview.risk.daily_risk_limit))" height="8" rounded />
          </div>
          <p v-if="overview.risk.circuit_breaker_reason" class="risk-reason">{{ overview.risk.circuit_breaker_reason }}</p>
        </v-card-text>
      </v-card>
    </section>

    <v-card class="content-card mb-5" elevation="0">
      <v-card-title class="section-head">
        <div><v-icon color="success">mdi-chart-timeline-variant-shimmer</v-icon><span>策略运行概览</span></div>
        <v-btn variant="text" color="primary" size="small" append-icon="mdi-arrow-right" @click="go('/market')">查看策略执行</v-btn>
      </v-card-title>
      <v-card-text>
        <div v-if="overview.strategies.length" class="strategy-grid">
          <article v-for="strategy in overview.strategies" :key="strategy.strategy_id">
            <div class="strategy-head"><div><strong>{{ strategy.strategy_name }}</strong><span>{{ strategy.symbol }} · {{ lifecycleLabel(strategy.lifecycle_status) }}</span></div><v-chip :color="directionMeta(strategy.direction).color" size="small" variant="tonal"><v-icon start>{{ directionMeta(strategy.direction).icon }}</v-icon>{{ directionMeta(strategy.direction).label }}</v-chip></div>
            <div class="strategy-confidence"><span>最新置信度</span><strong>{{ Number(strategy.confidence || 0).toFixed(0) }}%</strong></div>
            <v-progress-linear :model-value="strategy.confidence" :color="directionMeta(strategy.direction).color" height="7" rounded />
            <div class="strategy-foot"><span>{{ strategy.auto_execute ? '自动执行' : '人工确认' }}</span><span>{{ formatTime(strategy.latest_decision?.created_at || strategy.latest_signal_at) }}</span></div>
          </article>
        </div>
        <div v-else class="empty-state"><v-icon size="46">mdi-access-point-off</v-icon><strong>当前账户尚未部署运行策略</strong><v-btn size="small" color="primary" variant="tonal" @click="go('/accounts')">前往交易账户绑定策略</v-btn></div>
      </v-card-text>
    </v-card>

    <section class="secondary-grid mb-5">
      <v-card class="content-card" elevation="0">
        <v-card-title class="section-head"><div><v-icon color="success">mdi-briefcase-outline</v-icon><span>当前持仓</span></div><v-btn variant="text" color="primary" size="small" @click="go('/positions')">全部持仓</v-btn></v-card-title>
        <v-card-text>
          <div v-if="overview.positions.items.length" class="position-list">
            <article v-for="position in overview.positions.items" :key="position.ticket">
              <div><strong>{{ position.symbol }}</strong><span>#{{ position.ticket }} · {{ position.volume }} 手</span></div>
              <v-chip :color="position.direction === 'buy' ? 'success' : 'error'" size="x-small" variant="tonal">{{ position.direction === 'buy' ? '买入' : '卖出' }}</v-chip>
              <div class="position-price"><span>开仓价</span><strong>{{ price(position.price_open) }}</strong></div>
              <div class="position-profit" :class="profitClass(position.profit)"><span>浮动盈亏</span><strong>{{ signedMoney(position.profit) }}</strong></div>
            </article>
          </div>
          <div v-else class="empty-state compact"><v-icon size="40">mdi-briefcase-check-outline</v-icon><strong>当前没有持仓</strong><span>新仓位成交后会实时显示在这里。</span></div>
        </v-card-text>
      </v-card>

      <v-card class="content-card" elevation="0">
        <v-card-title class="section-head"><div><v-icon color="info">mdi-brain</v-icon><span>最新 AI 机会</span></div><v-btn variant="text" color="primary" size="small" @click="go('/ai-market')">AI 行情</v-btn></v-card-title>
        <v-card-text>
          <div v-if="overview.ai_opportunities.length" class="ai-list">
            <article v-for="item in overview.ai_opportunities" :key="item.card_id">
              <div class="ai-direction" :class="`is-${item.direction}`"><v-icon>{{ directionMeta(item.direction).icon }}</v-icon></div>
              <div><strong>{{ item.symbol }} · {{ item.period }}</strong><span>{{ item.strategy_name }}{{ item.derived_from_shared ? ' · 共享派生' : '' }}</span><small>{{ item.status_reason }}</small></div>
              <div class="ai-score"><strong>{{ item.confidence }}%</strong><span>{{ aiStatusLabel(item.status) }}</span></div>
            </article>
          </div>
          <div v-else class="empty-state compact"><v-icon size="40">mdi-brain</v-icon><strong>暂无 AI 运行结果</strong><span>部署包含 AI 信号源的策略后会在这里呈现。</span></div>
        </v-card-text>
      </v-card>
    </section>

    <v-card class="content-card market-health-card" elevation="0">
      <v-card-title class="section-head"><div><v-icon color="success">mdi-pulse</v-icon><span>行情健康度</span></div><span class="section-note">只关注数据是否及时，不再展示累计记录数</span></v-card-title>
      <v-card-text>
        <div v-if="overview.market_health.length" class="market-health-grid">
          <article v-for="item in overview.market_health" :key="item.symbol">
            <span class="health-dot" :class="`is-${item.status}`"></span>
            <div><strong>{{ item.symbol }}</strong><span>{{ item.periods.join(' · ') || '等待周期数据' }}</span></div>
            <div class="market-time"><v-chip :color="item.is_stale ? 'warning' : 'success'" size="x-small" variant="tonal">{{ item.is_stale ? '数据延迟' : '实时' }}</v-chip><span>{{ formatTime(item.latest_time) }}</span></div>
          </article>
        </div>
        <div v-else class="empty-state compact"><v-icon size="40">mdi-chart-candlestick</v-icon><strong>暂未收到行情数据</strong><span>EA 完成全量 K 线初始化后会显示行情健康状态。</span></div>
      </v-card-text>
    </v-card>
  </v-container>
</template>

<script setup>
import { onMounted, onUnmounted, reactive, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { tradingAPI } from '@/api/trading'
import { useAccountContext } from '@/composables/useAccountContext'

const router = useRouter()
const { selectedAccountId, selectedAccount } = useAccountContext()
const loading = ref(false)
const error = ref('')
const overview = reactive(emptyOverview())
let refreshTimer = null

function emptyOverview() {
  return {
    generated_at: null,
    account: {}, financial: {}, risk: {},
    positions: { count: 0, items: [] }, pending: {},
    attention: [], strategies: [], ai_opportunities: [], market_health: [],
  }
}

async function loadData() {
  if (!selectedAccountId.value) return
  loading.value = true
  error.value = ''
  try {
    Object.assign(overview, emptyOverview(), await tradingAPI.getDashboardOverview(selectedAccountId.value))
  } catch (err) {
    error.value = err.response?.data?.detail || `加载仪表盘失败: ${err.message}`
  } finally {
    loading.value = false
  }
}

const severityMeta = severity => ({
  error: { color: 'error', icon: 'mdi-alert-octagon-outline' },
  warning: { color: 'warning', icon: 'mdi-alert-outline' },
  info: { color: 'info', icon: 'mdi-information-outline' },
}[severity] || { color: 'grey', icon: 'mdi-circle-outline' })
const directionMeta = value => {
  const direction = String(value || '').toLowerCase()
  if (['up', 'buy', 'b'].includes(direction)) return { label: '上涨', color: 'success', icon: 'mdi-trending-up' }
  if (['down', 'sell', 's'].includes(direction)) return { label: '下降', color: 'error', icon: 'mdi-trending-down' }
  return { label: '震荡', color: 'warning', icon: 'mdi-trending-neutral' }
}
const lifecycleLabel = value => ({ draft: '草稿', backtesting: '回测中', backtest_passed: '回测通过', paper_trading: '模拟验证', production: '实盘运行', retired: '已停用' }[value] || value || '未知阶段')
const aiStatusLabel = value => ({ decision_created: '已生成决策', signal_formed: '已形成信号', ready_to_signal: '进入触发区', waiting_price: '等待价格', observing: '观察中', waiting_analysis: '等待分析', expired: '已过期' }[value] || value)
const ratio = (used, limit) => Number(limit) > 0 ? Math.min(100, Number(used || 0) / Number(limit) * 100) : 0
const progressColor = value => value >= 90 ? 'error' : value >= 70 ? 'warning' : 'success'
const profitClass = value => Number(value || 0) > 0 ? 'is-profit' : Number(value || 0) < 0 ? 'is-loss' : ''
const money = value => Number(value || 0).toLocaleString('zh-CN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })
const signedMoney = value => `${Number(value || 0) > 0 ? '+' : ''}${money(value)}`
const percent = value => Number(value || 0) > 0 ? `${Number(value).toFixed(2)}%` : '--'
const price = value => Number(value || 0) > 0 ? Number(value).toLocaleString('zh-CN', { minimumFractionDigits: 2, maximumFractionDigits: 5 }) : '--'
const formatTime = value => {
  if (!value) return '尚无数据'
  const date = typeof value === 'number' ? new Date(value * 1000) : new Date(value)
  return Number.isNaN(date.getTime()) ? String(value) : date.toLocaleString('zh-CN')
}
const go = path => path && router.push(path)

onMounted(() => { loadData(); refreshTimer = setInterval(loadData, 30000) })
watch(selectedAccountId, () => { Object.assign(overview, emptyOverview()); loadData() })
onUnmounted(() => clearInterval(refreshTimer))
</script>

<style scoped>
.dashboard-page{--ink:#17352d;--muted:#71827a;--line:#dce8e2;max-width:1600px;padding:28px}.dashboard-hero{position:relative;display:flex;align-items:center;justify-content:space-between;gap:28px;min-height:205px;padding:34px 38px;overflow:hidden;border-radius:24px;color:#f6fff9;background:radial-gradient(circle at 85% 22%,rgba(245,190,72,.25),transparent 26%),linear-gradient(122deg,#123b31 0%,#176b4d 57%,#0d8d6c 100%);box-shadow:0 18px 45px rgba(25,78,61,.2)}.dashboard-hero:after{position:absolute;right:-75px;bottom:-145px;width:330px;height:330px;border:58px solid rgba(255,255,255,.07);border-radius:50%;content:''}.section-kicker{color:#f1c866;font-size:.7rem;font-weight:800;letter-spacing:.16em}.dashboard-hero h1{margin:5px 0 8px;font-family:Georgia,"Noto Serif SC",serif;font-size:clamp(2rem,4vw,3rem);line-height:1}.dashboard-hero p{margin:0;color:rgba(246,255,249,.76)}.hero-meta{display:flex;flex-wrap:wrap;gap:8px}.hero-actions{z-index:1;display:flex;align-items:flex-end;flex-direction:column;gap:10px}.hero-actions span{color:rgba(246,255,249,.62);font-size:.72rem}.metric-grid{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:14px}.metric-card{position:relative;display:flex;align-items:center;justify-content:space-between;min-height:126px;padding:22px;overflow:hidden;border:1px solid var(--line);border-radius:18px;background:linear-gradient(145deg,#fff,#f8fbf9);box-shadow:0 8px 24px rgba(30,74,60,.05)}.metric-card span,.metric-card small{display:block;color:var(--muted)}.metric-card span{font-size:.78rem}.metric-card strong{display:block;margin:4px 0 2px;color:var(--ink);font-family:Georgia,serif;font-size:1.75rem}.metric-card small{font-size:.7rem}.metric-card>.v-icon{color:#bad4c9;font-size:2rem}.metric-card.is-profit strong,.position-profit.is-profit strong{color:#16855e}.metric-card.is-loss strong,.position-profit.is-loss strong{color:#c84e4e}.metric-card--warning{border-color:#edd39d;background:linear-gradient(145deg,#fffaf0,#fff)}.metric-card--healthy{border-color:#b9dfcc}.primary-grid{display:grid;grid-template-columns:minmax(0,1.55fr) minmax(330px,.75fr);gap:18px}.secondary-grid{display:grid;grid-template-columns:1fr 1fr;gap:18px}.content-card{overflow:hidden;border:1px solid var(--line);border-radius:19px!important;background:linear-gradient(155deg,#fff,#f9fcfa)}.section-head{display:flex;align-items:center;justify-content:space-between;gap:14px;min-height:62px;border-bottom:1px solid #e6eee9}.section-head>div{display:flex;align-items:center;gap:9px;color:var(--ink);font-weight:700}.section-note{color:var(--muted);font-size:.7rem;font-weight:400}.healthy-state{display:flex;align-items:center;gap:16px;min-height:156px;padding:18px;border-radius:14px;background:#eff9f3}.healthy-state strong,.healthy-state p{display:block}.healthy-state p{margin:4px 0 0;color:var(--muted);font-size:.8rem}.attention-list{display:flex;flex-direction:column;gap:7px}.attention-list button{display:grid;grid-template-columns:auto 1fr auto;align-items:center;gap:12px;width:100%;padding:11px 12px;border:0;border-radius:12px;background:#f6f9f7;color:inherit;text-align:left;cursor:pointer;transition:background .16s,transform .16s}.attention-list button:hover{background:#edf6f1;transform:translateX(2px)}.attention-list strong,.attention-list span{display:block}.attention-list strong{color:var(--ink);font-size:.84rem}.attention-list span{margin-top:2px;color:var(--muted);font-size:.72rem}.funds-grid{display:grid;grid-template-columns:repeat(3,1fr);gap:8px}.funds-grid div{padding:11px;border-radius:11px;background:#eff5f2}.funds-grid span,.funds-grid strong{display:block}.funds-grid span{color:var(--muted);font-size:.66rem}.funds-grid strong{margin-top:2px;color:var(--ink);font-size:.88rem}.risk-progress{margin-top:18px}.risk-progress__head{display:flex;justify-content:space-between;margin-bottom:7px;color:var(--muted);font-size:.72rem}.risk-progress__head strong{color:var(--ink)}.risk-reason{margin:14px 0 0;padding:9px;border-radius:9px;background:#fff0ed;color:#a8443e;font-size:.72rem}.strategy-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(270px,1fr));gap:12px}.strategy-grid article{padding:16px;border:1px solid var(--line);border-radius:14px;background:#fff}.strategy-head,.strategy-confidence,.strategy-foot{display:flex;align-items:center;justify-content:space-between;gap:10px}.strategy-head strong,.strategy-head span{display:block}.strategy-head strong{color:var(--ink)}.strategy-head span,.strategy-foot{margin-top:3px;color:var(--muted);font-size:.68rem}.strategy-confidence{margin:15px 0 7px;color:var(--muted);font-size:.72rem}.strategy-confidence strong{color:var(--ink);font-size:1rem}.strategy-foot{margin-top:11px}.position-list,.ai-list{display:flex;flex-direction:column}.position-list article{display:grid;grid-template-columns:minmax(120px,1fr) auto minmax(90px,.6fr) minmax(100px,.65fr);align-items:center;gap:10px;padding:13px 4px;border-bottom:1px solid #e6eee9}.position-list article:last-child,.ai-list article:last-child{border-bottom:0}.position-list strong,.position-list span{display:block}.position-list>article>div:first-child strong{color:var(--ink)}.position-list span{color:var(--muted);font-size:.68rem}.position-price strong,.position-profit strong{font-size:.84rem}.ai-list article{display:grid;grid-template-columns:auto 1fr auto;align-items:center;gap:12px;padding:13px 4px;border-bottom:1px solid #e6eee9}.ai-direction{display:flex;align-items:center;justify-content:center;width:38px;height:38px;border-radius:11px;background:#fff4dc;color:#b37b15}.ai-direction.is-up{background:#e8f7ef;color:#16855e}.ai-direction.is-down{background:#fff0ee;color:#c84e4e}.ai-list strong,.ai-list span,.ai-list small{display:block}.ai-list>article>div:nth-child(2)>strong{color:var(--ink);font-size:.84rem}.ai-list span,.ai-list small{color:var(--muted);font-size:.68rem}.ai-list small{max-width:330px;margin-top:3px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.ai-score{text-align:right}.ai-score strong{color:var(--ink);font-size:1rem}.market-health-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(300px,1fr));gap:9px}.market-health-grid article{display:grid;grid-template-columns:auto 1fr auto;align-items:center;gap:10px;padding:13px;border-radius:12px;background:#f3f8f5}.health-dot{width:9px;height:9px;border-radius:50%;background:#d09239;box-shadow:0 0 0 4px rgba(208,146,57,.12)}.health-dot.is-active{background:#26a66d;box-shadow:0 0 0 4px rgba(38,166,109,.12)}.market-health-grid strong,.market-health-grid span{display:block}.market-health-grid strong{color:var(--ink);font-size:.84rem}.market-health-grid span{color:var(--muted);font-size:.67rem}.market-time{text-align:right}.market-time>span{margin-top:3px}.empty-state{display:flex;min-height:170px;align-items:center;justify-content:center;flex-direction:column;gap:8px;color:#819189;text-align:center}.empty-state strong{color:var(--ink)}.empty-state span{font-size:.72rem}.empty-state.compact{min-height:190px}.empty-state .v-icon{color:#b5c9bf}
@media(max-width:1100px){.metric-grid{grid-template-columns:repeat(2,1fr)}.primary-grid,.secondary-grid{grid-template-columns:1fr}}
@media(max-width:700px){.dashboard-page{padding:16px}.dashboard-hero{align-items:flex-start;flex-direction:column;padding:25px}.hero-actions{align-items:flex-start}.metric-grid{grid-template-columns:1fr}.funds-grid{grid-template-columns:1fr}.position-list article{grid-template-columns:1fr auto}.position-price,.position-profit{text-align:left}.section-note{display:none}.market-health-grid{grid-template-columns:1fr}}
</style>

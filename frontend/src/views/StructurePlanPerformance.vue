<template>
  <div class="performance-page">
    <section class="hero">
      <div><div class="eyebrow">STRUCTURE PLAN ANALYTICS</div><h1>结构计划盈亏分析</h1><p>按品种、SETUP 和北京时间四小时区间查看最近 7 天的已平仓表现。</p></div>
      <v-btn color="white" variant="outlined" prepend-icon="mdi-refresh" :loading="loading" @click="load">刷新</v-btn>
    </section>

    <v-card class="filter-card" elevation="0">
      <v-card-text class="filters">
        <v-select v-model="filters.symbol" :items="symbolOptions" label="品种（可选）" clearable hide-details density="comfortable" variant="outlined" />
        <v-select v-model="filters.setup_type" :items="setupOptions" label="SETUP（可选）" clearable hide-details density="comfortable" variant="outlined" />
        <v-select v-model="filters.days" :items="dayOptions" item-title="label" item-value="value" label="统计范围" hide-details density="comfortable" variant="outlined" />
      </v-card-text>
    </v-card>

    <v-alert v-if="error" type="error" variant="tonal" class="mb-4">{{ error }}</v-alert>
    <section class="metrics">
      <article><span>净盈亏</span><strong :class="tone(summary.pnl)">{{ money(summary.pnl) }}</strong></article>
      <article><span>成交持仓</span><strong>{{ summary.orders || 0 }}</strong></article>
      <article><span>盈利 / 亏损</span><strong>{{ summary.wins || 0 }} / {{ summary.losses || 0 }}</strong></article>
      <article><span>SETUP 数</span><strong>{{ summary.setup_count || 0 }}</strong></article>
    </section>

    <v-card class="chart-card" elevation="0">
      <v-card-title>四小时盈亏曲线 <small>{{ scopeText }}</small></v-card-title>
      <v-card-text><div ref="chartEl" class="chart" /><div v-if="!points.length && !loading" class="empty">当前筛选条件暂无已平仓 STRUCTURE PLAN 数据</div></v-card-text>
    </v-card>

    <v-card class="table-card" elevation="0">
      <v-card-title>SETUP 汇总</v-card-title>
      <v-table density="comfortable"><thead><tr><th>SETUP</th><th>成交</th><th>胜率</th><th>盈利</th><th>亏损</th><th>净盈亏</th></tr></thead><tbody><tr v-for="item in bySetup" :key="item.setup_type"><td><code>{{ item.setup_type }}</code></td><td>{{ item.orders }}</td><td>{{ item.win_rate.toFixed(2) }}%</td><td class="positive">{{ item.wins }}</td><td class="negative">{{ item.losses }}</td><td :class="tone(item.pnl)">{{ money(item.pnl) }}</td></tr><tr v-if="!bySetup.length"><td colspan="6" class="empty">暂无数据</td></tr></tbody></v-table>
    </v-card>
  </div>
</template>

<script setup>
import { computed, nextTick, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'
import * as echarts from 'echarts'
import { marketAPI } from '../api/market'

const loading = ref(false); const error = ref(''); const chartEl = ref(null); let chart = null
const filters = reactive({ symbol: '', setup_type: '', days: 7 }); const data = ref({ summary: {}, points: [], by_setup: [], options: {} })
const dayOptions = [{ label: '最近 7 天', value: 7 }, { label: '最近 14 天', value: 14 }, { label: '最近 30 天', value: 30 }]
const summary = computed(() => data.value.summary || {}); const points = computed(() => data.value.points || []); const bySetup = computed(() => data.value.by_setup || [])
const symbolOptions = computed(() => data.value.options?.symbols || []); const setupOptions = computed(() => data.value.options?.setups || [])
const scopeText = computed(() => `${filters.symbol || '全部品种'} · ${filters.setup_type || '全部 SETUP'} · 最近 ${filters.days} 天`)
const money = value => `${Number(value || 0) >= 0 ? '+' : ''}${Number(value || 0).toFixed(2)}`
const tone = value => Number(value || 0) >= 0 ? 'positive' : 'negative'
const formatTime = value => new Date(Number(value) * 1000).toLocaleString('zh-CN', { timeZone: 'Asia/Shanghai', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' })

async function load() {
  loading.value = true; error.value = ''
  try { data.value = await marketAPI.getStructurePlanPerformance({ symbol: filters.symbol || undefined, setup_type: filters.setup_type || undefined, days: filters.days }); await nextTick(); renderChart() }
  catch (e) { error.value = e?.response?.data?.detail || '结构计划盈亏分析加载失败' }
  finally { loading.value = false }
}
function renderChart() {
  if (!chartEl.value) return
  if (!chart) chart = echarts.init(chartEl.value)
  chart.setOption({ animation: false, tooltip: { trigger: 'axis', formatter: params => { const p = params?.[0]?.data; return p ? `${formatTime(p.time)}<br/>区间盈亏：${money(p.pnl)}<br/>累计盈亏：${money(p.cumulative_pnl)}<br/>成交：${p.orders}` : '' } }, legend: { top: 4, data: ['区间盈亏', '累计盈亏'] }, grid: { left: 58, right: 24, top: 40, bottom: 44 }, xAxis: { type: 'category', data: points.value.map(p => formatTime(p.time)), axisLabel: { hideOverlap: true } }, yAxis: { type: 'value', scale: true }, dataZoom: [{ type: 'inside' }, { type: 'slider', height: 18, bottom: 5 }], series: [{ name: '区间盈亏', type: 'bar', data: points.value.map(p => ({ value: p.pnl, time: p.time, pnl: p.pnl, cumulative_pnl: p.cumulative_pnl, orders: p.orders }),), itemStyle: { color: params => Number(params.value) >= 0 ? '#1b8a67' : '#d35d50' } }, { name: '累计盈亏', type: 'line', smooth: false, symbol: 'circle', symbolSize: 5, data: points.value.map(p => ({ value: p.cumulative_pnl, time: p.time, pnl: p.pnl, cumulative_pnl: p.cumulative_pnl, orders: p.orders })), lineStyle: { color: '#315b89', width: 2 } }] }, true)
}
watch(() => [filters.symbol, filters.setup_type, filters.days], load); onMounted(load); onBeforeUnmount(() => chart?.dispose())
</script>

<style scoped>
.performance-page{max-width:1500px;padding:28px;margin:auto}.hero{display:flex;justify-content:space-between;align-items:center;gap:20px;padding:27px 30px;color:#f4fbf8;border-radius:22px;background:linear-gradient(125deg,#123c35,#0d8f70);box-shadow:0 18px 40px #1452442e}.eyebrow{color:#f4c96b;font-size:.7rem;font-weight:800;letter-spacing:.16em}.hero h1{margin:6px 0;font-size:2rem}.hero p{margin:0;color:#d7eee6}.filter-card,.chart-card,.table-card{margin-top:18px;border:1px solid #dce7e0;border-radius:16px;background:#fff}.filters{display:grid;grid-template-columns:repeat(3,1fr);gap:14px}.metrics{display:grid;grid-template-columns:repeat(4,1fr);gap:14px;margin-top:18px}.metrics article{padding:17px 20px;border:1px solid #dce7e0;border-radius:14px;background:#f8fbf9}.metrics span{display:block;color:#77847e;font-size:.72rem}.metrics strong{display:block;margin-top:7px;color:#31564b;font-size:1.35rem}.chart-card .v-card-title,.table-card .v-card-title{color:#31564b;font-weight:800}.chart-card small{margin-left:12px;color:#84918b;font-size:.7rem;font-weight:400}.chart{height:430px}.empty{padding:80px;text-align:center;color:#8b9891}.positive{color:#16805f!important}.negative{color:#ba5045!important}code{color:#31564b}@media(max-width:700px){.performance-page{padding:15px}.hero{align-items:flex-start;flex-direction:column}.filters,.metrics{grid-template-columns:1fr 1fr}.chart{height:320px}}
</style>

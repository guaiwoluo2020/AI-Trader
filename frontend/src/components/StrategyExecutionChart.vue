<template>
  <section class="execution-chart-shell">
    <div class="chart-head">
      <div><span class="chart-kicker">MARKET EVENTS</span><strong>{{ symbol }} · {{ period }} K线</strong></div>
      <div class="legend"><span><i class="buy" />买入</span><span><i class="sell" />卖出</span><span><i class="tp" />止盈</span><span><i class="sl" />止损</span><span><i class="close" />平仓</span></div>
    </div>
    <div v-if="!bars.length" class="empty"><v-icon icon="mdi-chart-candlestick" />暂无可用K线</div>
    <div v-else ref="chartEl" class="chart" />
  </section>
</template>

<script setup>
import { nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import * as echarts from 'echarts'

const props = defineProps({
  symbol: { type: String, default: '' },
  period: { type: String, default: 'M5' },
  bars: { type: Array, default: () => [] },
  events: { type: Array, default: () => [] },
})
const chartEl = ref(null)
let chart

function timeValue(value) {
  if (value == null || value === '') return 0
  if (typeof value === 'number' || /^\d+$/.test(String(value))) return Number(value) * (Number(value) < 1e12 ? 1000 : 1)
  const parsed = Date.parse(String(value).replace(' ', 'T'))
  return Number.isNaN(parsed) ? 0 : parsed
}
function bucketFor(value) {
  const target = timeValue(value)
  if (!target) return null
  return props.bars.reduce((best, bar) => {
    const distance = Math.abs(timeValue(bar.timestamp || bar.time) - target)
    return !best || distance < best.distance ? { bar, distance } : best
  }, null)?.bar || null
}
function points(type) {
  return props.events.filter(event => event.type === type).map(event => {
    const bar = bucketFor(event.timestamp)
    if (!bar) return null
    return { name: event.reason || type, value: [String(bar.timestamp || bar.time), Number(event.price || 0)], event }
  }).filter(Boolean)
}
function axisTime(value) {
  return new Date(timeValue(value)).toLocaleString('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hour12: false })
}
async function render() {
  if (!props.bars.length) { chart?.dispose(); chart = null; return }
  await nextTick()
  if (!chartEl.value) return
  chart ||= echarts.init(chartEl.value)
  const categories = props.bars.map(bar => String(bar.timestamp || bar.time))
  const marker = (name, type, color, symbol, rotate = 0) => ({
    name, type: 'scatter', data: points(type), symbol, symbolRotate: rotate, symbolSize: 13,
    itemStyle: { color, borderColor: '#fff', borderWidth: 1 },
    label: { show: true, formatter: name.slice(0, 2), color, fontSize: 9, position: rotate ? 'top' : 'bottom' }, z: 5,
  })
  chart.setOption({
    animation: false,
    legend: { top: 4, right: 8, data: ['K线', '买入', '卖出', '止盈', '止损', '平仓'], textStyle: { color: '#687871', fontSize: 10 } },
    tooltip: { trigger: 'axis', axisPointer: { type: 'cross' }, backgroundColor: 'rgba(20,47,41,.94)', borderWidth: 0, textStyle: { color: '#fff', fontSize: 11 } },
    grid: { left: 54, right: 16, top: 36, bottom: 34 },
    xAxis: { type: 'category', data: categories, axisLabel: { color: '#77847e', fontSize: 9, formatter: axisTime, hideOverlap: true }, axisLine: { lineStyle: { color: '#cbd8d1' } } },
    yAxis: { scale: true, splitNumber: 5, axisLabel: { color: '#77847e', fontSize: 9 }, splitLine: { lineStyle: { color: '#e6ede9' } } },
    dataZoom: [{ type: 'inside' }, { type: 'slider', bottom: 2, height: 16, showDetail: false, fillerColor: 'rgba(32,112,88,.12)' }],
    series: [
      { name: 'K线', type: 'candlestick', data: props.bars.map(bar => [Number(bar.open), Number(bar.close), Number(bar.low), Number(bar.high)]), itemStyle: { color: '#1b8a67', color0: '#d35d50', borderColor: '#1b8a67', borderColor0: '#d35d50' } },
      marker('买入', 'buy', '#0f8060', 'triangle'), marker('卖出', 'sell', '#c84f43', 'triangle', 180),
      marker('止盈', 'take_profit', '#c3913d', 'diamond'), marker('止损', 'stop_loss', '#8e4c9e', 'diamond'), marker('平仓', 'close', '#55717c', 'circle'),
    ],
  }, true)
}
function resize() { chart?.resize() }
watch(() => [props.bars, props.events], render, { deep: true, immediate: true })
onMounted(render); window.addEventListener('resize', resize)
onBeforeUnmount(() => { window.removeEventListener('resize', resize); chart?.dispose() })
</script>

<style scoped>
.execution-chart-shell { border-top: 1px solid #e5ecea; background: #fbfdfc; }
.chart-head { display:flex; justify-content:space-between; align-items:center; gap:10px; padding:10px 14px 4px; }
.chart-head strong,.chart-head span { display:block; }.chart-head strong { color:#31564b; font-size:.78rem; }.chart-kicker { color:#b18443; font-size:.58rem; font-weight:800; letter-spacing:.12em; }
.legend { display:flex; flex-wrap:wrap; gap:7px; color:#77847e; font-size:.62rem; }.legend span { display:inline-flex; gap:4px; align-items:center; }.legend i { width:7px; height:7px; border-radius:50%; }.buy{background:#0f8060}.sell{background:#c84f43}.tp{background:#c3913d}.sl{background:#8e4c9e}.close{background:#55717c}
.chart { width:100%; height:330px; }.empty { min-height:130px; display:grid; place-items:center; color:#89958f; font-size:.74rem; }
@media (max-width:700px){.chart-head{align-items:flex-start;flex-direction:column}.chart{height:280px}}
</style>

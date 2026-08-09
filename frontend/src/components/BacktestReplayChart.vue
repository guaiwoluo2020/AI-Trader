<template>
  <section class="replay-shell">
    <header class="replay-header">
      <div>
        <span>MARKET REPLAY</span>
        <strong>{{ props.symbol ? `${props.symbol} · ` : '' }}K线与交易回放</strong>
      </div>
      <div class="replay-legend">
        <i class="buy-dot" />买入
        <i class="sell-dot" />卖出
        <i class="close-dot" />平仓
        <b>{{ Number(progress || 0).toFixed(1) }}%</b>
      </div>
    </header>
    <div v-if="!bars.length" class="replay-empty">
      <v-icon icon="mdi-chart-candlestick" size="34" />
      <span>正在等待回测引擎生成 K 线快照</span>
    </div>
    <div v-else ref="chartEl" class="replay-chart" />
    <footer v-if="bars.length" class="replay-footer">
      <span>已回放 {{ totalBarCount.toLocaleString('zh-CN') }} 根 M1 K线</span>
      <span v-if="isAggregated">图表已聚合为 {{ bars.length.toLocaleString('zh-CN') }} 根蜡烛</span>
      <span>最新 {{ formatPrice(bars[bars.length - 1].close) }}</span>
    </footer>
  </section>
</template>

<script setup>
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import * as echarts from 'echarts'

const props = defineProps({
  ledger: { type: Object, required: true },
  progress: { type: Number, default: 0 },
  symbol: { type: String, default: '' },
})

const chartEl = ref(null)
let chart = null

const bars = computed(() => props.ledger?.replay_bars || [])
const totalBarCount = computed(() => bars.value.reduce(
  (sum, bar) => sum + Number(bar.bar_count || 1), 0
))
const isAggregated = computed(() => totalBarCount.value > bars.value.length)

function formatPrice(value) {
  return Number(value || 0).toLocaleString('zh-CN', { maximumFractionDigits: 5 })
}

function formatAxisTime(value) {
  const date = new Date(Number(value) * 1000)
  return date.toLocaleString('zh-CN', {
    month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit',
    hour12: false,
  })
}

function bucketFor(timestamp) {
  const target = Number(timestamp || 0)
  if (!target || !bars.value.length) return null
  return bars.value.find(bar => (
    target >= Number(bar.time) && target <= Number(bar.end_time || bar.time)
  )) || bars.value.reduce((nearest, bar) => (
    Math.abs(Number(bar.time) - target) < Math.abs(Number(nearest.time) - target)
      ? bar : nearest
  ), bars.value[0])
}

function orderPoints(direction) {
  return (props.ledger?.orders || [])
    .filter(order => order.status === 'filled' && order.direction === direction)
    .map(order => {
      const bucket = bucketFor(order.filled_at || order.requested_at)
      if (!bucket) return null
      return {
        name: `${direction === 'buy' ? '买入' : '卖出'} · ${order.signal_source || '信号'}`,
        value: [String(bucket.time), Number(order.filled_price ?? order.requested_price)],
        order,
      }
    })
    .filter(Boolean)
}

function closePoints() {
  return (props.ledger?.trades || []).map(trade => {
    const bucket = bucketFor(trade.closed_at)
    if (!bucket) return null
    return {
      name: `平仓 · ${trade.exit_reason || ''}`,
      value: [String(bucket.time), Number(trade.exit_price)],
      trade,
    }
  }).filter(Boolean)
}

function alignedEquity() {
  const points = [...(props.ledger?.equity_curve || [])]
    .sort((left, right) => Number(left.time) - Number(right.time))
  let cursor = 0
  let latest = null
  return bars.value.map(bar => {
    const end = Number(bar.end_time || bar.time)
    while (cursor < points.length && Number(points[cursor].time) <= end) {
      latest = points[cursor]
      cursor += 1
    }
    return latest ? Number(latest.equity) : null
  })
}

async function renderChart() {
  if (!bars.value.length) {
    chart?.dispose()
    chart = null
    return
  }
  await nextTick()
  if (!chartEl.value) return
  if (!chart) chart = echarts.init(chartEl.value)
  const categories = bars.value.map(bar => String(bar.time))
  const candleData = bars.value.map(bar => [
    Number(bar.open), Number(bar.close), Number(bar.low), Number(bar.high),
  ])
  chart.setOption({
    animation: false,
    color: ['#16765c', '#c84f43', '#c3913d', '#2e6f91'],
    legend: {
      top: 4, right: 8,
      data: ['K线', '买入', '卖出', '平仓', '账户净值'],
      textStyle: { color: '#64736d', fontSize: 10 },
    },
    tooltip: {
      trigger: 'axis', axisPointer: { type: 'cross' },
      backgroundColor: 'rgba(20, 47, 41, .94)', borderWidth: 0,
      textStyle: { color: '#f8f4e9', fontSize: 11 },
    },
    axisPointer: { link: [{ xAxisIndex: [0, 1] }] },
    grid: [
      { left: 58, right: 22, top: 40, height: '58%' },
      { left: 58, right: 22, top: '76%', height: '12%' },
    ],
    xAxis: [
      {
        type: 'category', data: categories, boundaryGap: true,
        axisLine: { lineStyle: { color: '#cbd8d1' } },
        axisLabel: { show: false }, splitLine: { show: false },
      },
      {
        type: 'category', gridIndex: 1, data: categories, boundaryGap: true,
        axisLine: { lineStyle: { color: '#cbd8d1' } },
        axisLabel: { color: '#77847e', fontSize: 9, formatter: formatAxisTime, hideOverlap: true },
      },
    ],
    yAxis: [
      {
        scale: true, splitNumber: 5,
        axisLabel: { color: '#77847e', fontSize: 9 },
        splitLine: { lineStyle: { color: '#e6ede9' } },
      },
      {
        scale: true, gridIndex: 1, splitNumber: 2,
        axisLabel: { color: '#77847e', fontSize: 9 },
        splitLine: { lineStyle: { color: '#edf1ef' } },
      },
    ],
    dataZoom: [
      { type: 'inside', xAxisIndex: [0, 1], start: 0, end: 100 },
      {
        type: 'slider', xAxisIndex: [0, 1], bottom: 2, height: 17,
        borderColor: '#d8e3dd', fillerColor: 'rgba(32, 112, 88, .12)',
        handleStyle: { color: '#28705e' }, showDetail: false,
      },
    ],
    series: [
      {
        name: 'K线', type: 'candlestick', data: candleData,
        itemStyle: {
          color: '#1b8a67', color0: '#d35d50',
          borderColor: '#1b8a67', borderColor0: '#d35d50',
        },
      },
      {
        name: '买入', type: 'scatter', data: orderPoints('buy'),
        symbol: 'triangle', symbolSize: 14, symbolOffset: [0, 7],
        itemStyle: { color: '#0f8060', borderColor: '#fff', borderWidth: 1 },
        label: { show: true, formatter: '买', position: 'bottom', color: '#0f8060', fontSize: 9 },
        z: 6,
      },
      {
        name: '卖出', type: 'scatter', data: orderPoints('sell'),
        symbol: 'triangle', symbolRotate: 180, symbolSize: 14, symbolOffset: [0, -7],
        itemStyle: { color: '#c84f43', borderColor: '#fff', borderWidth: 1 },
        label: { show: true, formatter: '卖', position: 'top', color: '#c84f43', fontSize: 9 },
        z: 6,
      },
      {
        name: '平仓', type: 'scatter', data: closePoints(),
        symbol: 'diamond', symbolSize: 11,
        itemStyle: { color: '#c3913d', borderColor: '#fff', borderWidth: 1 },
        label: { show: true, formatter: '平', position: 'right', color: '#9c6b22', fontSize: 9 },
        z: 6,
      },
      {
        name: '账户净值', type: 'line', xAxisIndex: 1, yAxisIndex: 1,
        data: alignedEquity(), symbol: 'none', connectNulls: true,
        lineStyle: { width: 1.5, color: '#2e6f91' }, areaStyle: { opacity: .08 },
      },
    ],
  }, true)
}

function resizeChart() { chart?.resize() }

watch([() => props.ledger, () => props.progress], renderChart, { immediate: true })
onMounted(renderChart)
window.addEventListener('resize', resizeChart)
onBeforeUnmount(() => {
  window.removeEventListener('resize', resizeChart)
  chart?.dispose()
})
</script>

<style scoped>
.replay-shell { margin-bottom: 13px; overflow: hidden; border: 1px solid #d8e5de; border-radius: 12px; background: #fff; }
.replay-header { display: flex; align-items: center; justify-content: space-between; gap: 12px; padding: 11px 14px 7px; }
.replay-header span, .replay-header strong { display: block; }
.replay-header span { color: #b18443; font-size: .58rem; font-weight: 800; letter-spacing: .12em; }
.replay-header strong { margin-top: 2px; color: #31564b; font-size: .8rem; }
.replay-legend { display: flex; align-items: center; gap: 5px; color: #77847e; font-size: .62rem; }
.replay-legend i { width: 7px; height: 7px; border-radius: 50%; }
.replay-legend b { margin-left: 6px; color: #28705e; }
.buy-dot { background: #0f8060; }.sell-dot { background: #c84f43; }.close-dot { background: #c3913d; }
.replay-chart { width: 100%; height: 460px; }
.replay-empty { display: grid; place-items: center; min-height: 180px; color: #8a9690; font-size: .7rem; }
.replay-empty span { margin-top: -45px; }
.replay-footer { display: flex; flex-wrap: wrap; justify-content: space-between; gap: 8px; padding: 7px 14px 10px; color: #84908a; font-size: .6rem; }
@media (max-width: 700px) { .replay-header { align-items: flex-start; flex-direction: column; }.replay-chart { height: 360px; }.replay-legend { flex-wrap: wrap; } }
</style>

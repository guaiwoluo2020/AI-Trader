<template>
  <v-container fluid class="maintenance-page">
    <section class="maintenance-hero">
      <div>
        <span class="eyebrow">STORAGE GOVERNANCE</span>
        <h1>数据维护</h1>
        <p>查看流水清理、WAL 回收与 SQLite 空间整理的执行情况。</p>
      </div>
      <div class="hero-actions">
        <v-chip :color="latestMeta.color" variant="flat"><v-icon start>{{ latestMeta.icon }}</v-icon>{{ latestMeta.label }}</v-chip>
        <v-btn color="amber-lighten-2" variant="flat" prepend-icon="mdi-play-circle-outline" :loading="running" @click="runNow">立即执行维护</v-btn>
      </div>
    </section>

    <v-alert v-if="error" type="error" variant="tonal" class="mt-5">{{ error }}</v-alert>

    <section class="metric-grid mt-5">
      <article><span>数据库大小</span><strong>{{ bytes(space.db_size_bytes) }}</strong><small>SQLite 主文件</small><v-icon>mdi-database-outline</v-icon></article>
      <article><span>可回收空间</span><strong>{{ bytes(space.reclaimable_bytes) }}</strong><small>{{ percent(space.free_ratio) }} 空闲页</small><v-icon>mdi-database-minus-outline</v-icon></article>
      <article><span>页面使用</span><strong>{{ number(space.page_count) }}</strong><small>空闲 {{ number(space.free_page_count) }} 页</small><v-icon>mdi-view-grid-outline</v-icon></article>
      <article><span>最近耗时</span><strong>{{ latest?.duration_ms != null ? `${latest.duration_ms} ms` : '--' }}</strong><small>{{ time(latest?.completed_at) }}</small><v-icon>mdi-timer-sand</v-icon></article>
    </section>

    <div class="content-grid mt-5">
      <v-card class="panel" elevation="0">
        <v-card-title class="panel-title"><div><v-icon>mdi-broom</v-icon><span>当前保留策略</span></div><small>每天自动执行</small></v-card-title>
        <v-card-text class="policy-list">
          <article><div><strong>LLM 调用明细</strong><span>调用计量和错误记录</span></div><b>{{ policy.llm_call_days || 3 }} 天</b></article>
          <article><div><strong>模拟盘心跳</strong><span>后台存活状态流水</span></div><b>{{ policy.paper_heartbeat_days || 3 }} 天</b></article>
          <article><div><strong>回测订单与 K 线回放</strong><span>仅清理终态任务明细</span></div><b>{{ policy.backtest_detail_days || 7 }} 天</b></article>
          <article><div><strong>Alpha 信号明细</strong><span>保留研究报告与因子结果</span></div><b>{{ policy.alpha_signal_days || 7 }} 天</b></article>
          <article class="vacuum-policy"><div><strong>SQLite VACUUM</strong><span>每周检查；空闲 ≥ 20% 且可回收 ≥ 20 MB 才执行</span></div><b>条件执行</b></article>
        </v-card-text>
      </v-card>

      <v-card class="panel latest-panel" elevation="0">
        <v-card-title class="panel-title"><div><v-icon>mdi-pulse</v-icon><span>最近执行结果</span></div><small>{{ triggerLabel(latest?.trigger_type) }}</small></v-card-title>
        <v-card-text v-if="latest" class="latest-body">
          <div class="result-line"><span>WAL Checkpoint</span><strong>{{ latest.checkpoint_status || '--' }}</strong></div>
          <div class="result-line"><span>VACUUM</span><v-chip size="small" :color="vacuumMeta(latest.vacuum_status).color" variant="tonal">{{ vacuumMeta(latest.vacuum_status).label }}</v-chip></div>
          <p>{{ latest.vacuum_reason || '本次未进行空间检查' }}</p>
          <div class="cleanup-tags">
            <v-chip v-for="(count, key) in latest.cleanup" :key="key" size="small" variant="outlined">{{ cleanupLabel(key) }} {{ count }} 条</v-chip>
          </div>
          <v-alert v-if="latest.error_message" type="error" variant="tonal">{{ latest.error_message }}</v-alert>
        </v-card-text>
        <v-card-text v-else class="empty-state"><v-icon size="44">mdi-database-clock-outline</v-icon><strong>尚未执行维护</strong><span>服务启动后约一分钟进行首次维护。</span></v-card-text>
      </v-card>
    </div>

    <v-card class="history-panel mt-5" elevation="0">
      <v-card-title class="panel-title"><div><v-icon>mdi-history</v-icon><span>执行历史</span></div><v-btn icon="mdi-refresh" variant="text" :loading="loading" @click="loadData" /></v-card-title>
      <v-table density="comfortable">
        <thead><tr><th>开始时间</th><th>触发方式</th><th>结果</th><th>清理记录</th><th>空间整理</th><th>耗时</th></tr></thead>
        <tbody>
          <tr v-for="item in runs" :key="item.run_id">
            <td>{{ time(item.started_at) }}</td><td>{{ triggerLabel(item.trigger_type) }}</td>
            <td><v-chip size="x-small" :color="runMeta(item.status).color" variant="tonal">{{ runMeta(item.status).label }}</v-chip></td>
            <td>{{ cleanupTotal(item.cleanup) }} 条</td><td>{{ vacuumMeta(item.vacuum_status).label }}</td><td>{{ item.duration_ms || 0 }} ms</td>
          </tr>
          <tr v-if="!runs.length"><td colspan="6" class="text-center text-medium-emphasis py-8">暂无维护记录</td></tr>
        </tbody>
      </v-table>
    </v-card>

    <v-snackbar v-model="snackbar" :color="messageType">{{ message }}</v-snackbar>
  </v-container>
</template>

<script setup>
import { computed, onMounted, reactive, ref } from 'vue'
import { marketAPI } from '@/api/market'

const loading = ref(false), running = ref(false), error = ref('')
const snackbar = ref(false), message = ref(''), messageType = ref('success')
const space = reactive({}), policy = reactive({}), latest = ref(null), runs = ref([])
const latestMeta = computed(() => runMeta(latest.value?.status || 'idle'))

const runMeta = value => ({ completed: { label: '运行正常', color: 'success', icon: 'mdi-check-decagram-outline' }, failed: { label: '执行失败', color: 'error', icon: 'mdi-alert-octagon-outline' }, running: { label: '执行中', color: 'info', icon: 'mdi-progress-clock' }, idle: { label: '等待首次维护', color: 'blue-grey', icon: 'mdi-clock-outline' } }[value] || { label: value, color: 'grey', icon: 'mdi-circle-outline' })
const vacuumMeta = value => ({ executed: { label: '已执行', color: 'success' }, skipped: { label: '条件未达到', color: 'warning' }, not_due: { label: '本周无需检查', color: 'blue-grey' } }[value] || { label: value || '未执行', color: 'grey' })
const cleanupNames = { llm_call_logs: 'LLM 调用', paper_heartbeat_logs: '模拟心跳', backtest_orders: '回测订单', backtest_replay_bars: 'K线回放', alpha_research_signals: 'Alpha 信号' }
const cleanupLabel = key => cleanupNames[key] || key
const cleanupTotal = value => Object.values(value || {}).reduce((sum, item) => sum + Number(item || 0), 0)
const triggerLabel = value => value === 'manual' ? '管理员手动' : value === 'scheduled' ? '系统定时' : '--'
const bytes = value => { const size = Number(value || 0); if (size < 1024) return `${size} B`; if (size < 1048576) return `${(size / 1024).toFixed(1)} KB`; if (size < 1073741824) return `${(size / 1048576).toFixed(1)} MB`; return `${(size / 1073741824).toFixed(2)} GB` }
const percent = value => `${(Number(value || 0) * 100).toFixed(1)}%`
const number = value => Number(value || 0).toLocaleString('zh-CN')
const time = value => value ? new Date(Number(value) * 1000).toLocaleString('zh-CN') : '尚无记录'

async function loadData() {
  loading.value = true; error.value = ''
  try { const data = await marketAPI.getDataMaintenance(); Object.assign(space, data.space || {}); Object.assign(policy, data.policy || {}); latest.value = data.latest; runs.value = data.runs || [] }
  catch (err) { error.value = err.response?.data?.detail || `加载数据维护状态失败: ${err.message}` }
  finally { loading.value = false }
}
async function runNow() {
  if (!confirm('立即执行数据清理、WAL Checkpoint 和本周空间检查吗？')) return
  running.value = true
  try { const data = await marketAPI.runDataMaintenance(); messageType.value = data.status === 'completed' ? 'success' : 'error'; message.value = data.message; snackbar.value = true; await loadData() }
  catch (err) { messageType.value = 'error'; message.value = err.response?.data?.detail || '执行数据维护失败'; snackbar.value = true }
  finally { running.value = false }
}

onMounted(loadData)
</script>

<style scoped>
.maintenance-page{--ink:#17372f;--muted:#72837b;--line:#dce8e2;max-width:1540px;padding:28px}.maintenance-hero{display:flex;align-items:center;justify-content:space-between;gap:28px;padding:32px 36px;overflow:hidden;border-radius:24px;color:#f6fff9;background:radial-gradient(circle at 82% 20%,rgba(239,184,65,.26),transparent 27%),linear-gradient(122deg,#143c33,#1f6955 58%,#158268);box-shadow:0 18px 44px rgba(24,78,62,.18)}.eyebrow{color:#efc86e;font-size:.68rem;font-weight:800;letter-spacing:.17em}.maintenance-hero h1{margin:5px 0 7px;font-family:Georgia,"Noto Serif SC",serif;font-size:clamp(2rem,4vw,3rem);line-height:1}.maintenance-hero p{margin:0;color:rgba(246,255,249,.72)}.hero-actions{z-index:1;display:flex;align-items:center;gap:10px}.metric-grid{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:14px}.metric-grid article{position:relative;min-height:126px;padding:20px;overflow:hidden;border:1px solid var(--line);border-radius:18px;background:linear-gradient(145deg,#fff,#f7fbf9)}.metric-grid span,.metric-grid small{display:block;color:var(--muted)}.metric-grid span{font-size:.75rem}.metric-grid strong{display:block;margin:5px 0;color:var(--ink);font-family:Georgia,serif;font-size:1.65rem}.metric-grid small{font-size:.68rem}.metric-grid .v-icon{position:absolute;right:17px;bottom:15px;color:#b7d2c6;font-size:2rem}.content-grid{display:grid;grid-template-columns:1.15fr .85fr;gap:16px}.panel,.history-panel{overflow:hidden;border:1px solid var(--line);border-radius:19px!important;background:linear-gradient(155deg,#fff,#f9fcfa)}.panel-title{display:flex;align-items:center;justify-content:space-between;min-height:62px;border-bottom:1px solid #e6eee9}.panel-title>div{display:flex;align-items:center;gap:9px;color:var(--ink);font-weight:750}.panel-title small{color:var(--muted);font-size:.68rem}.policy-list{display:flex;flex-direction:column;padding:12px 20px!important}.policy-list article{display:flex;align-items:center;justify-content:space-between;gap:16px;padding:13px 4px;border-bottom:1px solid #e8efeb}.policy-list article:last-child{border-bottom:0}.policy-list strong,.policy-list span{display:block}.policy-list strong{color:var(--ink);font-size:.84rem}.policy-list span{margin-top:3px;color:var(--muted);font-size:.68rem}.policy-list b{color:#1b7259}.vacuum-policy{margin-top:5px;padding:14px!important;border-radius:12px;background:#f7f1e4}.latest-body{padding:21px!important}.result-line{display:flex;align-items:center;justify-content:space-between;gap:12px;padding:11px 0;border-bottom:1px solid #e8efeb}.result-line span{color:var(--muted);font-size:.75rem}.result-line strong{color:var(--ink);font-family:monospace;font-size:.7rem}.latest-body p{padding:12px;border-radius:10px;background:#f2f6f3;color:#5e7068;font-size:.74rem}.cleanup-tags{display:flex;flex-wrap:wrap;gap:7px}.empty-state{display:flex;min-height:260px;align-items:center;justify-content:center;flex-direction:column;gap:8px;color:#829188}.empty-state strong{color:var(--ink)}.empty-state span{font-size:.72rem}@media(max-width:900px){.metric-grid{grid-template-columns:1fr 1fr}.content-grid{grid-template-columns:1fr}.maintenance-hero{align-items:flex-start;flex-direction:column}.hero-actions{flex-wrap:wrap}}@media(max-width:560px){.maintenance-page{padding:15px}.metric-grid{grid-template-columns:1fr}.history-panel{overflow:auto}}
</style>

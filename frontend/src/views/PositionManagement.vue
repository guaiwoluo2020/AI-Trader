<template>
  <div class="policy-page">
    <section class="policy-hero">
      <div>
        <div class="eyebrow">POSITION MANAGEMENT</div>
        <h1>持仓管理方案</h1>
        <p>查看止损、保本、结构保护和分批止盈规则。这里展示的是策略实际绑定的方案，修改前请先确认影响范围。</p>
      </div>
      <v-btn prepend-icon="mdi-refresh" variant="tonal" :loading="loading" @click="loadPolicies">刷新</v-btn>
    </section>

    <v-alert v-if="errorMessage" type="error" variant="tonal" class="mb-5">{{ errorMessage }}</v-alert>
    <v-progress-linear v-if="loading" indeterminate color="primary" class="mb-5" />

    <div v-if="!loading && !policies.length" class="empty-state">
      <v-icon size="48">mdi-shield-off-outline</v-icon>
      <h3>暂无持仓管理方案</h3>
      <p>请先在策略配置中创建或绑定方案。</p>
    </div>

    <section v-for="policy in policies" :key="policy.policy_id" class="policy-card">
      <div class="policy-card-head">
        <div>
          <div class="d-flex align-center ga-2 flex-wrap">
            <h2>{{ policy.name }}</h2>
            <v-chip size="x-small" :color="policy.enabled ? 'success' : 'grey'" variant="tonal">{{ policy.enabled ? '启用' : '停用' }}</v-chip>
            <v-chip size="x-small" variant="outlined">v{{ policy.version }}</v-chip>
          </div>
          <p class="muted">{{ policy.policy_id }} · {{ modeLabel(policy.config?.management_mode) }}</p>
        </div>
        <v-btn size="small" color="primary" variant="tonal" prepend-icon="mdi-format-list-bulleted" @click="openRules(policy)">{{ rules(policy).length }} 条运行规则</v-btn>
      </div>

      <div class="summary-grid">
        <article><span>初始止损</span><strong>{{ initialStopLabel(policy) }}</strong></article>
        <article><span>最小止损</span><strong>{{ policy.config?.min_stop_atr ? `${policy.config.min_stop_atr} ATR` : `${policy.config?.min_stop_percent || 0}%` }}</strong></article>
        <article><span>风险回报下限</span><strong>{{ policy.config?.min_risk_reward || 0 }}R</strong></article>
        <article><span>分批退出</span><strong>{{ policy.config?.management_mode === 'multi_level_exit' ? '结构多层' : (policy.config?.partial_take_profit ? '已配置' : '普通模式') }}</strong></article>
      </div>

      <div class="rule-list">
        <div v-for="(rule, index) in rules(policy)" :key="`${policy.policy_id}-${index}`" class="rule-row">
          <div class="rule-index">{{ index + 1 }}</div>
          <div class="rule-main">
            <strong>{{ ruleLabel(rule) }}</strong>
            <span>{{ ruleDescription(rule) }}</span>
          </div>
          <v-chip size="x-small" :color="rule.enabled === false ? 'grey' : 'primary'" variant="tonal">{{ rule.enabled === false ? '关闭' : '生效' }}</v-chip>
        </div>
      </div>

      <details class="raw-config">
        <summary>查看完整配置 JSON</summary>
        <pre>{{ pretty(policy.config) }}</pre>
      </details>
    </section>

    <v-dialog v-model="dialogOpen" max-width="900">
      <v-card v-if="selectedPolicy">
        <v-card-title class="d-flex align-center justify-space-between">
          <span>{{ selectedPolicy.name }} · 规则与部署</span>
          <v-btn icon="mdi-close" variant="text" @click="dialogOpen = false" />
        </v-card-title>
        <v-card-text>
          <h3 class="dialog-heading">运行规则</h3>
          <div class="dialog-rule-list">
            <article v-for="(rule, index) in rules(selectedPolicy)" :key="`dialog-${index}`">
              <div class="rule-index">{{ index + 1 }}</div>
              <div><strong>{{ ruleLabel(rule) }}</strong><p>{{ ruleDescription(rule) }}</p><code>{{ pretty(rule) }}</code></div>
            </article>
          </div>
          <h3 class="dialog-heading mt-6">部署情况</h3>
          <div v-if="deploymentsFor(selectedPolicy).length" class="deployment-list">
            <article v-for="item in deploymentsFor(selectedPolicy)" :key="item.key">
              <div><strong>{{ item.strategy_name || item.strategy_id }}</strong><span>{{ item.symbol || '全部品种' }} · {{ item.account_name || `账户 #${item.account_id}` }}</span></div>
              <v-chip size="small" :color="item.status === 'active' ? 'success' : 'grey'" variant="tonal">{{ item.status === 'active' ? '运行中' : item.status || '未运行' }}</v-chip>
              <v-chip size="x-small" :color="item.execution_mode === 'live' ? 'error' : 'primary'" variant="outlined">{{ item.execution_mode === 'live' ? '实盘' : '模拟盘' }}</v-chip>
            </article>
          </div>
          <div v-else class="runtime-empty compact">暂无已识别的策略部署</div>
        </v-card-text>
      </v-card>
    </v-dialog>
  </div>
</template>

<script setup>
import { onMounted, ref } from 'vue'
import { marketAPI } from '../api/market'
import { accountAPI } from '../api/trading'

const policies = ref([])
const loading = ref(false)
const errorMessage = ref('')
const allDeployments = ref([])
const selectedPolicy = ref(null)
const dialogOpen = ref(false)

const modeLabel = value => value === 'multi_level_exit' ? '多层结构退出' : '普通持仓管理'
const ruleMap = {
  profit_protection: '盈利保护', break_even: '保本止损', pivot_trailing: 'Pivot 移动止损',
  structure_trailing: '结构保护位移动止损', trailing_stop: 'R 倍数移动止损',
  target_trailing: '目标跟踪止损', partial_take_profit: '分批止盈', reverse_signal: '反向信号退出',
  max_holding_bars: '最长持仓时间'
}
const ruleLabel = rule => ruleMap[rule.type] || rule.type || '未命名规则'
const initialStopLabel = policy => (policy.config?.initial_stop_rules || []).map(item => `${item.type}${item.period ? `(${item.period})` : ''}`).join(' → ') || '未配置'
const rules = policy => policy.config?.management_rules || []
const ruleDescription = rule => {
  if (rule.type === 'profit_protection') return (rule.stages || []).map(item => `${item.activation_r}R → ${item.stop_r}R`).join('；') || '分阶段保护'
  if (rule.type === 'break_even') return `达到 ${rule.activation_r || 0}R，偏移 ${rule.offset_r || 0}R`
  if (rule.type === 'pivot_trailing') return `达到 ${rule.activation_r || 0}R后，使用 ${rule.period || 'M5'} Pivot${rule.buffer?.value ? `，缓冲 ${rule.buffer.value}` : ''}`
  if (rule.type === 'structure_trailing') return `${rule.structure_layer || 'swing'} 保护位，${rule.buffer_value || 0}${rule.buffer_type === 'atr' ? ' ATR' : ''} 缓冲，最小改善 ${rule.min_improvement_atr || 0} ATR`
  if (rule.type === 'trailing_stop') return `达到 ${rule.activation_r || 0}R 后，距离最有利价格 ${rule.distance_r || 0}R`
  if (rule.type === 'target_trailing') return `达到策略目标后跟踪，默认 ${rule.distance_r || 0}R，范围 ${rule.min_distance_r || 0}R–${rule.max_distance_r || 0}R`
  if (rule.type === 'partial_take_profit') return (rule.levels || []).map(item => `${item.trigger_r}R 平 ${item.close_percent}%`).join('；')
  return JSON.stringify(rule)
}
const pretty = value => JSON.stringify(value || {}, null, 2)
const deploymentsFor = policy => allDeployments.value.filter(item => String(item.policy_id || item.position_management_policy_id || '') === String(policy.policy_id))
const openRules = policy => { selectedPolicy.value = policy; dialogOpen.value = true }
const loadPolicies = async () => {
  loading.value = true; errorMessage.value = ''
  try {
    const [data, accountsData, strategiesData] = await Promise.all([marketAPI.getPositionManagementPolicies(), accountAPI.list(), marketAPI.getStrategies(1, 100)])
    policies.value = data.policies || []
    const strategyMap = new Map((strategiesData.strategies || []).map(strategy => [String(strategy.strategy_id), strategy]))
    allDeployments.value = (accountsData.accounts || []).flatMap(account => (account.deployments || []).map(deployment => {
      const strategy = strategyMap.get(String(deployment.strategy_id)) || {}
      return { ...deployment, ...strategy, account_id: account.id, account_name: account.account_name, policy_id: strategy.position_management_policy_id }
    }))
  } catch (error) {
    errorMessage.value = error?.response?.data?.detail || error.message || '持仓管理方案加载失败'
  } finally { loading.value = false }
}
onMounted(loadPolicies)
</script>

<style scoped>
.policy-page { max-width: 1240px; margin: 0 auto; padding: 34px 28px 64px; color: #17352d; }
.policy-hero { display: flex; justify-content: space-between; gap: 24px; align-items: flex-start; margin-bottom: 28px; }
.eyebrow { color: #2b8a68; font-size: 11px; letter-spacing: .16em; font-weight: 700; }
h1 { margin: 6px 0 8px; font-size: 32px; }
.policy-hero p, .muted { margin: 0; color: #71817a; }
.policy-card { background: #fff; border: 1px solid #dbe8e0; border-radius: 18px; padding: 22px; margin-bottom: 20px; box-shadow: 0 8px 24px rgba(33, 78, 60, .06); }
.policy-card-head { display: flex; justify-content: space-between; gap: 16px; align-items: flex-start; margin-bottom: 18px; }
h2 { margin: 0; font-size: 21px; }
.summary-grid { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 10px; margin-bottom: 18px; }
.summary-grid article { background: #f4f8f5; border-radius: 12px; padding: 13px; }
.summary-grid span, .summary-grid strong { display: block; }
.summary-grid span { color: #71817a; font-size: 12px; margin-bottom: 5px; }
.rule-list { border-top: 1px solid #e5eee8; }
.rule-row { display: flex; align-items: center; gap: 12px; padding: 13px 0; border-bottom: 1px solid #edf2ee; }
.rule-index { width: 25px; height: 25px; border-radius: 50%; background: #e5f2eb; color: #257858; display: grid; place-items: center; font-size: 12px; font-weight: 700; }
.rule-main { flex: 1; display: flex; flex-direction: column; gap: 3px; }
.rule-main span { color: #6f7f77; font-size: 13px; }
.raw-config { margin-top: 17px; color: #557068; font-size: 13px; }
.raw-config summary { cursor: pointer; }
pre { overflow: auto; background: #f5f8f6; border-radius: 10px; padding: 14px; margin-top: 10px; font-size: 11px; }
.empty-state { text-align: center; padding: 70px 20px; color: #71817a; }
.dialog-heading { color: #17352d; margin: 8px 0 12px; }
.dialog-rule-list article, .deployment-list article { display: flex; gap: 12px; align-items: flex-start; padding: 12px 0; border-bottom: 1px solid #edf2ee; }
.dialog-rule-list article p, .deployment-list article span { display: block; margin: 4px 0 0; color: #71817a; font-size: 13px; }
.dialog-rule-list article > div:nth-child(2) { flex: 1; }
.dialog-rule-list code { display: block; white-space: pre-wrap; word-break: break-word; color: #557068; font-size: 11px; margin-top: 6px; }
.deployment-list article > div:first-child { flex: 1; }
.runtime-empty { color: #71817a; padding: 20px 0; }
@media (max-width: 760px) { .policy-page { padding: 22px 15px 48px; } .policy-hero, .policy-card-head { flex-direction: column; } .summary-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); } }
</style>

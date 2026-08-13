<template>
  <v-container fluid class="policy-page">
    <div class="page-hero mb-6">
      <div>
        <div class="eyebrow">POSITION CONTROL</div>
        <h1>持仓管理</h1>
        <p>把止损、止盈和持仓后的保护动作组合成可复用方案。</p>
      </div>
      <v-btn color="primary" size="large" prepend-icon="mdi-plus" @click="openCreate">
        新建方案
      </v-btn>
    </div>

    <v-alert v-if="message" :type="messageType" closable class="mb-4" @click:close="message = ''">
      {{ message }}
    </v-alert>

    <v-row v-if="policies.length">
      <v-col v-for="policy in policies" :key="policy.policy_id" cols="12" md="6" xl="4">
        <v-card class="policy-card" elevation="0">
          <v-card-text>
            <div class="d-flex align-start justify-space-between">
              <div>
                <div class="text-h6 font-weight-bold">{{ policy.name }}</div>
                <div class="text-caption text-medium-emphasis">#{{ policy.policy_id }}</div>
              </div>
              <v-chip :color="policy.enabled ? 'success' : 'grey'" size="small">
                {{ policy.enabled ? '启用' : '停用' }}
              </v-chip>
              <v-chip :color="policy.is_shared ? 'teal' : 'grey'" size="small" variant="tonal">
                {{ policy.readonly_reference ? '共享引用' : (policy.is_shared ? '已共享' : '私有') }}
              </v-chip>
            </div>
            <div class="rule-summary mt-5">
              <div><span>初始止损</span><strong>{{ ruleNames(policy.config.initial_stop_rules) }}</strong></div>
              <div><span>初始止盈</span><strong>{{ ruleNames(policy.config.initial_take_profit_rules) }}</strong></div>
              <div><span>持仓动作</span><strong>{{ ruleNames(policy.config.management_rules) || '仅固定保护' }}</strong></div>
            </div>
          </v-card-text>
          <v-card-actions>
            <v-btn variant="text" prepend-icon="mdi-content-copy" @click="copyPolicy(policy)">复制</v-btn>
            <v-btn variant="text" prepend-icon="mdi-pencil-outline" :disabled="policy.readonly_reference" @click="openEdit(policy)">编辑</v-btn>
            <v-spacer />
            <v-btn color="error" variant="text" icon="mdi-delete-outline" :disabled="policy.readonly_reference" @click="remove(policy)" />
          </v-card-actions>
        </v-card>
      </v-col>
    </v-row>
    <v-card v-else class="empty-state" elevation="0">
      <v-icon size="56">mdi-shield-plus-outline</v-icon>
      <h2>先建立第一套持仓管理方案</h2>
      <p>策略需要绑定方案后才能进入回测。</p>
    </v-card>

    <v-dialog v-model="dialog" max-width="920" persistent>
      <v-card>
        <v-card-title class="d-flex align-center">
          {{ form.policy_id ? '编辑持仓管理方案' : '新建持仓管理方案' }}
          <v-spacer />
          <v-btn icon="mdi-close" variant="text" @click="dialog = false" />
        </v-card-title>
        <v-card-text>
          <v-row>
            <v-col cols="12" md="8"><v-text-field v-model="form.name" label="方案名称" /></v-col>
            <v-col cols="12" md="4"><v-switch v-model="form.enabled" color="success" label="启用方案" /></v-col>
            <v-col cols="12" md="4"><v-switch v-model="form.is_shared" color="success" label="共享到平台方案库" /></v-col>
          </v-row>
          <RuleChain v-model="form.config.initial_stop_rules" title="初始止损规则链" kind="stop" />
          <RuleChain v-model="form.config.initial_take_profit_rules" title="初始止盈规则链" kind="take" />
          <div class="section-title mt-6">持仓后管理</div>
          <v-row>
            <v-col cols="12" md="4"><v-switch v-model="management.breakEven" color="success" label="盈利后移动至保本" /></v-col>
            <v-col cols="6" md="2"><v-text-field v-model.number="management.breakEvenR" label="启动盈利" suffix="R" type="number" min="0.1" step="0.1" /></v-col>
            <v-col cols="12" md="4"><v-switch v-model="management.trailing" color="success" label="启用移动止损" /></v-col>
            <v-col cols="6" md="2"><v-text-field v-model.number="management.trailingActivationR" label="移动启动" suffix="R" type="number" min="0.1" step="0.1" /></v-col>
            <v-col cols="6" md="2"><v-text-field v-model.number="management.trailingR" label="移动距离" suffix="R" type="number" min="0.1" step="0.1" /></v-col>
            <v-col cols="12" md="4"><v-switch v-model="management.pivotTrailing" color="success" label="按新转折点跟进" /></v-col>
            <v-col cols="6" md="2"><v-select v-model="management.pivotPeriod" :items="periods" label="转折周期" /></v-col>
            <v-col cols="12" md="3"><v-switch v-model="management.reverse" color="success" label="反向信号退出" /></v-col>
            <v-col cols="12" md="3"><v-switch v-model="management.timeout" color="success" label="最大持仓时间" /></v-col>
            <v-col cols="6" md="2"><v-text-field v-model.number="management.timeoutBars" label="K线数量" type="number" min="1" /></v-col>
            <v-col cols="6" md="2"><v-select v-model="management.timeoutPeriod" :items="periods" label="计时周期" /></v-col>
          </v-row>
          <div class="section-title mt-6">分批止盈</div>
          <v-switch v-model="management.partialTakeProfit" color="success" label="启用分批止盈" />
          <div v-if="management.partialTakeProfit" class="partial-levels">
            <div v-for="(level, index) in management.partialLevels" :key="level.level_id" class="partial-row">
              <span class="rule-index">{{ index + 1 }}</span>
              <v-text-field v-model.number="level.trigger_r" label="触发盈利" suffix="R" type="number" min="0.1" step="0.1" density="compact" />
              <v-text-field v-model.number="level.close_percent" label="平仓比例" suffix="%" type="number" min="1" max="100" density="compact" />
              <v-select v-model="level.move_sl" :items="partialMoveOptions" label="触发后止损" density="compact" />
              <v-btn icon="mdi-delete-outline" color="error" variant="text" :disabled="management.partialLevels.length === 1" @click="removePartialLevel(index)" />
            </div>
            <v-btn variant="tonal" color="primary" prepend-icon="mdi-plus" @click="addPartialLevel">增加止盈层级</v-btn>
          </div>
          <v-row>
            <v-col cols="6" md="3"><v-text-field v-model.number="form.config.min_risk_reward" label="最小盈亏比" type="number" min="0" step="0.1" /></v-col>
            <v-col cols="6" md="3"><v-text-field v-model.number="form.config.min_stop_distance" label="最小止损距离" type="number" min="0" /></v-col>
            <v-col cols="6" md="3"><v-text-field v-model.number="form.config.max_stop_distance" label="最大止损距离（0不限）" type="number" min="0" /></v-col>
          </v-row>
        </v-card-text>
        <v-card-actions class="pa-5"><v-spacer /><v-btn variant="text" @click="dialog = false">取消</v-btn><v-btn color="primary" :loading="saving" @click="save">保存方案</v-btn></v-card-actions>
      </v-card>
    </v-dialog>
  </v-container>
</template>

<script setup>
import { computed, defineComponent, h, onMounted, reactive, ref } from 'vue'
import { marketAPI } from '../api/market'

const periods = ['M1', 'M5', 'M15', 'H1', 'H4']
const labels = { signal: '信号建议', pivot: '转折点', atr: 'ATR', fixed_points: '固定点数', fixed_percent: '固定比例', risk_reward: '盈亏比', none: '不设固定止盈', break_even: '保本', pivot_trailing: '转折跟进', trailing_stop: '移动止损', partial_take_profit: '分批止盈', reverse_signal: '反向退出', max_holding_bars: '时间退出' }
const partialMoveOptions = [
  { title: '不调整', value: 'none' },
  { title: '推到保本', value: 'break_even' },
  { title: '跟随移动止损', value: 'trail' },
]
const policies = ref([])
const dialog = ref(false)
const saving = ref(false)
const message = ref('')
const messageType = ref('success')
const deepClone = value => JSON.parse(JSON.stringify(value))

const defaultConfig = () => ({
  initial_stop_rules: [{ type: 'pivot', period: 'M5', selection: 'nearest', max_age_bars: 100, buffer: { type: 'fixed_points', value: 0 } }, { type: 'fixed_percent', value: 0.003 }],
  initial_take_profit_rules: [{ type: 'risk_reward', value: 2 }],
  management_rules: [], min_risk_reward: 1, min_stop_distance: 0, max_stop_distance: 0,
})
const form = reactive({ policy_id: '', name: '', enabled: true, config: defaultConfig() })
const management = reactive({
  breakEven: true, breakEvenR: 1,
  trailing: true, trailingActivationR: 1, trailingR: 0.8,
  partialTakeProfit: true,
  partialLevels: [
    { level_id: 'tp1', trigger_r: 1, close_percent: 30, move_sl: 'break_even' },
    { level_id: 'tp2', trigger_r: 2, close_percent: 30, move_sl: 'trail' },
  ],
  pivotTrailing: true, pivotPeriod: 'M5',
  reverse: false, timeout: false, timeoutBars: 120, timeoutPeriod: 'M1'
})

const RuleChain = defineComponent({
  props: { modelValue: Array, title: String, kind: String }, emits: ['update:modelValue'],
  setup(props, { emit }) {
    const options = computed(() => props.kind === 'stop'
      ? ['pivot', 'signal', 'atr', 'fixed_points', 'fixed_percent']
      : ['risk_reward', 'pivot', 'signal', 'atr', 'fixed_points', 'fixed_percent', 'none'])
    const update = (index, key, value) => { const next = deepClone(props.modelValue); next[index][key] = value; emit('update:modelValue', next) }
    const remove = index => emit('update:modelValue', props.modelValue.filter((_, i) => i !== index))
    const add = () => emit('update:modelValue', [...props.modelValue, { type: props.kind === 'stop' ? 'fixed_percent' : 'risk_reward', value: props.kind === 'stop' ? 0.003 : 2 }])
    return () => h('div', { class: 'rule-chain mt-5' }, [
      h('div', { class: 'd-flex align-center mb-2' }, [h('div', { class: 'section-title' }, props.title), h('div', { class: 'flex-grow-1' }), h('button', { class: 'add-rule', onClick: add }, '+ 添加兜底规则')]),
      ...props.modelValue.map((rule, index) => h('div', { class: 'rule-row' }, [
        h('span', { class: 'rule-index' }, String(index + 1)),
        h('select', { value: rule.type, onChange: e => update(index, 'type', e.target.value) }, options.value.map(value => h('option', { value }, labels[value]))),
        rule.type === 'pivot' ? h('select', { value: rule.period || 'M5', onChange: e => update(index, 'period', e.target.value) }, periods.map(value => h('option', { value }, value))) : null,
        ['atr', 'fixed_points', 'fixed_percent', 'risk_reward'].includes(rule.type) ? h('input', { type: 'number', min: 0, step: 0.1, value: rule.value, onInput: e => update(index, 'value', Number(e.target.value)) }) : null,
        h('button', { class: 'remove-rule', disabled: props.modelValue.length === 1, onClick: () => remove(index) }, '移除'),
      ])),
    ])
  }
})

function syncManagement(rules = []) {
  const find = type => rules.find(rule => rule.type === type)
  const be = find('break_even'); management.breakEven = Boolean(be); management.breakEvenR = be?.activation_r || 1
  const trail = find('trailing_stop'); management.trailing = Boolean(trail); management.trailingActivationR = trail?.activation_r || 1; management.trailingR = trail?.distance_r || 0.8
  const partial = find('partial_take_profit'); management.partialTakeProfit = Boolean(partial); management.partialLevels = deepClone(partial?.levels?.length ? partial.levels : [{ level_id: 'tp1', trigger_r: 1, close_percent: 30, move_sl: 'break_even' }])
  const pivot = find('pivot_trailing'); management.pivotTrailing = Boolean(pivot); management.pivotPeriod = pivot?.period || 'M5'
  management.reverse = Boolean(find('reverse_signal'))
  const timeout = find('max_holding_bars'); management.timeout = Boolean(timeout); management.timeoutBars = timeout?.bars || 120; management.timeoutPeriod = timeout?.period || 'M1'
}
function buildManagementRules() {
  const rules = []
  if (management.breakEven) rules.push({ type: 'break_even', activation_r: management.breakEvenR, offset_r: 0 })
  if (management.pivotTrailing) rules.push({ type: 'pivot_trailing', period: management.pivotPeriod, buffer: { type: 'fixed_points', value: 0 } })
  if (management.trailing) rules.push({ type: 'trailing_stop', activation_r: management.trailingActivationR, distance_r: management.trailingR })
  if (management.partialTakeProfit) rules.push({ type: 'partial_take_profit', levels: deepClone(management.partialLevels) })
  if (management.reverse) rules.push({ type: 'reverse_signal' })
  if (management.timeout) rules.push({ type: 'max_holding_bars', period: management.timeoutPeriod, bars: management.timeoutBars })
  return rules
}
function addPartialLevel() {
  const next = management.partialLevels.length + 1
  management.partialLevels.push({ level_id: `tp${next}`, trigger_r: next, close_percent: 25, move_sl: 'trail' })
}
function removePartialLevel(index) { management.partialLevels.splice(index, 1) }
async function load() { const data = await marketAPI.getPositionManagementPolicies(); policies.value = data.policies || [] }
function openCreate() { Object.assign(form, { policy_id: '', name: '', enabled: true, is_shared: false, config: defaultConfig() }); syncManagement([{ type: 'break_even', activation_r: 1 }, { type: 'pivot_trailing', period: 'M5' }, { type: 'trailing_stop', activation_r: 1, distance_r: 0.8 }, { type: 'partial_take_profit', levels: [{ level_id: 'tp1', trigger_r: 1, close_percent: 30, move_sl: 'break_even' }, { level_id: 'tp2', trigger_r: 2, close_percent: 30, move_sl: 'trail' }] }]); dialog.value = true }
function openEdit(policy) { Object.assign(form, deepClone(policy)); syncManagement(form.config.management_rules); dialog.value = true }
async function save() { saving.value = true; try { form.config.management_rules = buildManagementRules(); const payload = { name: form.name, enabled: form.enabled, visibility: form.is_shared ? 'shared' : 'private', config: form.config }; if (form.policy_id) await marketAPI.updatePositionManagementPolicy(form.policy_id, payload); else await marketAPI.createPositionManagementPolicy(payload); dialog.value = false; messageType.value = 'success'; message.value = '持仓管理方案已保存'; await load() } catch (error) { messageType.value = 'error'; message.value = error.response?.data?.detail || error.message } finally { saving.value = false } }
async function remove(policy) { if (!confirm(`确定删除“${policy.name}”吗？`)) return; try { await marketAPI.deletePositionManagementPolicy(policy.policy_id); await load() } catch (error) { messageType.value = 'error'; message.value = error.response?.data?.detail || error.message } }
async function copyPolicy(policy) { try { const data = await marketAPI.copyPositionManagementPolicy(policy.policy_id); messageType.value = 'success'; message.value = data.message || '已复制方案'; await load() } catch (error) { messageType.value = 'error'; message.value = error.response?.data?.detail || error.message } }
function ruleNames(rules = []) { return rules.map(rule => labels[rule.type] || rule.type).join(' → ') }
onMounted(load)
</script>

<style scoped>
.policy-page { max-width: 1500px; padding: 32px; }
.page-hero { display:flex; align-items:end; justify-content:space-between; padding:32px; border-radius:24px; color:#17342d; background:linear-gradient(125deg,#dff3e8,#f4edda 72%,#f4d8b5); }
.page-hero h1 { font-family:Georgia,serif; font-size:42px; line-height:1; margin:6px 0 10px; }
.page-hero p { margin:0; color:#52635e; }.eyebrow { font-size:12px; letter-spacing:.22em; font-weight:800; color:#23745c; }
.policy-card { height:100%; border:1px solid #dce5df; border-radius:20px; background:linear-gradient(155deg,#fff,#f8faf7); }
.rule-summary { display:grid; gap:12px; }.rule-summary div { display:flex; flex-direction:column; padding:12px; border-radius:12px; background:#edf4ef; }.rule-summary span { font-size:11px; color:#718078; }.rule-summary strong { margin-top:3px; font-size:13px; }
.empty-state { padding:70px; text-align:center; border:1px dashed #aebdb4; border-radius:22px; color:#607269; }
.section-title { font-size:14px; font-weight:800; color:#26483d; }.rule-chain { padding:18px; border:1px solid #dce7e0; border-radius:16px; background:#f8fbf9; }
.rule-row { display:grid; grid-template-columns:32px minmax(160px,1fr) 110px 110px 62px; gap:10px; align-items:center; margin-top:8px; }.rule-row select,.rule-row input { height:40px; border:1px solid #c9d6ce; border-radius:8px; padding:0 10px; background:white; }.rule-index { width:28px;height:28px;border-radius:50%;display:grid;place-items:center;background:#245d4c;color:white;font-size:12px; }.add-rule,.remove-rule { border:0;background:transparent;color:#23745c;cursor:pointer;font-weight:700; }.remove-rule { color:#b84b43; }
.partial-levels { display:grid; gap:10px; padding:16px; border:1px solid #dce7e0; border-radius:16px; background:#fbfdfb; }
.partial-row { display:grid; grid-template-columns:32px 1fr 1fr 1fr 44px; gap:10px; align-items:center; }
@media (max-width:700px) { .policy-page{padding:16px}.page-hero{align-items:start;gap:20px;flex-direction:column}.rule-row{grid-template-columns:28px 1fr}.rule-row select,.rule-row input,.remove-rule{grid-column:2}.page-hero h1{font-size:34px} }
</style>

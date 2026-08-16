<template>
  <v-container fluid class="page">
    <section class="hero mb-5">
      <div><span>AI SIGNAL SOURCES</span><h1>AI 信号源</h1><p>独立运行、可复用的 AI 分析服务。策略只负责引用并形成交易决策。</p></div>
      <v-btn color="secondary" prepend-icon="mdi-plus" @click="openCreate">新建信号源</v-btn>
    </section>
    <v-alert v-if="message" :type="messageType" variant="tonal" class="mb-4" closable @click:close="message = ''">{{ message }}</v-alert>
    <v-row>
      <v-col v-for="item in sources" :key="item.signal_source_id" cols="12" md="6" xl="4">
        <v-card class="source-card" elevation="0">
          <v-card-text>
            <div class="d-flex justify-space-between ga-2"><div><span class="eyebrow">{{ item.symbol }} · {{ item.period }}</span><h2>{{ item.name }}</h2></div><v-chip :color="item.enabled ? 'success' : 'grey'" size="small">{{ item.enabled ? '运行中' : '已停用' }}</v-chip></div>
            <p>{{ item.config.model || '未选择模型' }} · 每 {{ item.config.analysis_interval_minutes || '-' }} 分钟分析</p>
            <div class="chips"><v-chip size="x-small" variant="tonal">{{ item.config.kline_count || '-' }} 根K线</v-chip><v-chip size="x-small" variant="tonal">置信度 {{ item.config.min_confidence || '-' }}%</v-chip><v-chip v-if="item.share_runtime_data" size="x-small" color="success" variant="tonal">已共享运行数据</v-chip><v-chip v-if="item.locked" size="x-small" color="warning" variant="tonal">已冻结</v-chip></div>
            <p v-if="item.locked" class="lock-note">已被其他用户引用或被已部署策略使用。不可修改、停用或删除，可复制新版本。</p>
          </v-card-text>
          <v-card-actions><v-btn size="small" variant="text" prepend-icon="mdi-content-copy" @click="copy(item)">复制</v-btn><v-spacer/><v-btn size="small" variant="text" :disabled="item.locked" @click="openEdit(item)">编辑</v-btn><v-btn size="small" color="error" variant="text" :disabled="item.locked" @click="remove(item)">删除</v-btn></v-card-actions>
        </v-card>
      </v-col>
    </v-row>
    <div v-if="!loading && !sources.length" class="empty"><v-icon size="52">mdi-brain</v-icon><h2>还没有 AI 信号源</h2><p>先创建一个独立信号源，再在策略中选择它。</p></div>
    <v-dialog v-model="dialog" max-width="820" persistent><v-card>
      <v-card-title>{{ form.signal_source_id ? '编辑 AI 信号源' : '新建 AI 信号源' }}</v-card-title>
      <v-card-text><v-row>
        <v-col cols="12" md="6"><v-text-field v-model="form.name" label="名称"/></v-col><v-col cols="12" md="3"><v-select v-model="form.symbol" :items="options.symbols" label="交易品种" clearable/></v-col><v-col cols="12" md="3"><v-select v-model="form.period" :items="periods" label="分析周期"/></v-col>
        <v-col cols="12" md="6"><v-select v-model="form.config.model" :items="options.models" label="运行模型"/></v-col><v-col cols="12" md="6"><v-select v-model.number="form.config.analysis_interval_minutes" :items="intervalOptions" label="调用间隔（分钟）"/></v-col><v-col cols="12" md="6"><v-text-field v-model.number="form.config.kline_count" label="分析 K 线数量" type="number" min="10" max="500"/></v-col><v-col cols="12" md="6"><v-text-field v-model.number="form.config.min_confidence" label="最低置信度" suffix="%" type="number" min="0" max="100"/></v-col><v-col cols="12"><v-textarea v-model="form.config.system_prompt" label="系统提示词" rows="3" auto-grow/></v-col><v-col cols="12"><v-textarea v-model="form.config.analysis_prompt_template" label="分析提示词模板" rows="6" auto-grow/></v-col><v-col cols="12"><v-switch v-model="form.share_runtime_data" color="success" inset label="共享运行数据" hint="仅共享分析结论和可见参数，不共享提示词与模型配置。" persistent-hint/></v-col>
      </v-row></v-card-text>
      <v-card-actions><v-spacer/><v-btn variant="text" @click="dialog=false">取消</v-btn><v-btn color="primary" :loading="saving" @click="save">保存</v-btn></v-card-actions>
    </v-card></v-dialog>
  </v-container>
</template>

<script setup>
import { computed, onMounted, reactive, ref, watch } from 'vue'
import { marketAPI } from '@/api/market'

const periods = ['M1', 'M5', 'M15', 'H1', 'H4']
const sources = ref([]), loading = ref(false), saving = ref(false), dialog = ref(false), message = ref(''), messageType = ref('success'), loadingOptions = ref(false)
const options = reactive({ models: [], symbols: [], system: '', prompt: '' })
const emptyForm = () => ({ signal_source_id: '', name: '', symbol: '', period: 'M5', enabled: true, share_runtime_data: false, config: { analysis_mode: 'self_analysis', model: '', analysis_interval_minutes: 5, kline_count: 300, min_confidence: 70, system_prompt: '', analysis_prompt_template: '', reference_runtime_ids: [] } })
const form = reactive(emptyForm())
const intervalOptions = computed(() => [1,2,3,5,10,15,30,60,120,240,480,720,1440].filter(value => value >= ({ M1: 1, M5: 5, M15: 15, H1: 60, H4: 240 }[form.period] || 1)).map(value => ({ title: `${value} 分钟`, value })))
const clone = value => JSON.parse(JSON.stringify(value))
async function load () { loading.value = true; try { sources.value = (await marketAPI.getAISignalSources()).items || [] } catch (e) { messageType.value='error'; message.value=e.response?.data?.detail || '加载信号源失败' } finally { loading.value=false } }
async function loadOptions () { loadingOptions.value=true; try { const data=await marketAPI.getLLMSignalOptions(form.symbol || null); options.models=data.models||[]; options.symbols=data.symbols||[]; options.system=data.default_system_prompt||''; options.prompt=data.default_analysis_prompt_template||'' } finally { loadingOptions.value=false } }
function openCreate () { Object.assign(form, emptyForm()); dialog.value=true; loadOptions() }
function openEdit (item) { Object.assign(form, clone(item)); form.config ||= {}; dialog.value=true; loadOptions() }
async function save () { saving.value=true; try { const data=clone(form); const minimum={M1:1,M5:5,M15:15,H1:60,H4:240}[data.period]||1; data.config.analysis_mode='self_analysis'; data.config.analysis_interval_minutes=Math.max(minimum, Number(data.config.analysis_interval_minutes) || 0); const result=data.signal_source_id ? await marketAPI.updateAISignalSource(data.signal_source_id,data) : await marketAPI.createAISignalSource(data); dialog.value=false; messageType.value='success'; message.value=result.source?.locked ? '已保存并冻结' : 'AI 信号源已保存'; await load() } catch(e) { messageType.value='error'; message.value=e.response?.data?.detail || '保存失败' } finally { saving.value=false } }
async function copy (item) { try { await marketAPI.copyAISignalSource(item.signal_source_id); messageType.value='success'; message.value='已创建可修改的新副本'; await load() } catch(e) { messageType.value='error'; message.value=e.response?.data?.detail || '复制失败' } }
async function remove (item) { if (!confirm(`删除“${item.name}”吗？`)) return; try { await marketAPI.deleteAISignalSource(item.signal_source_id); await load() } catch(e) { messageType.value='error'; message.value=e.response?.data?.detail || '删除失败' } }
watch(() => form.period, () => { const minimum={M1:1,M5:5,M15:15,H1:60,H4:240}[form.period]||1; form.config.analysis_interval_minutes=Math.max(Number(form.config.analysis_interval_minutes)||0, minimum) })
onMounted(load)
</script>

<style scoped>
.page{max-width:1500px;padding:28px}.hero{display:flex;align-items:center;justify-content:space-between;gap:24px;padding:28px 30px;border-radius:22px;color:#f7fcfa;background:radial-gradient(circle at 84% 16%,rgba(249,193,78,.3),transparent 26%),linear-gradient(125deg,#153e36,#13735c)}.hero span,.eyebrow{font-size:.72rem;font-weight:800;letter-spacing:.14em;color:#f2c768}.hero h1{margin:4px 0 7px;font-size:2.35rem}.hero p{margin:0;color:rgba(255,255,255,.78)}.source-card{height:100%;border:1px solid #dbe8e1;border-radius:18px;background:linear-gradient(145deg,#fff,#f7fbf8)}.source-card h2{margin:4px 0 8px;font-size:1.25rem;color:#1f4f42}.source-card p{min-height:38px;color:#60736b;font-size:.84rem}.chips{display:flex;flex-wrap:wrap;gap:6px}.lock-note{min-height:0!important;color:#9b6818!important}.empty{padding:90px 20px;text-align:center;color:#60736b}.empty h2{color:#294f42}@media(max-width:700px){.page{padding:16px}.hero{align-items:stretch;flex-direction:column}.hero h1{font-size:1.9rem}}
</style>

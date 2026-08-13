<template>
  <div class="dataset-page">
    <section class="dataset-hero">
      <div>
        <div class="eyebrow">MARKET DATA LAB</div>
        <h1>回测数据集</h1>
        <p>从已绑定的 MT5 经纪商提取真实 M1 行情，为策略回放建立可复现的数据底座。</p>
      </div>
      <v-btn
        variant="outlined"
        color="white"
        prepend-icon="mdi-refresh"
        :loading="loading"
        @click="loadAll"
      >
        刷新数据
      </v-btn>
    </section>

    <v-alert
      v-if="message"
      :type="messageType"
      variant="tonal"
      closable
      class="mb-5"
      @click:close="message = ''"
    >
      {{ message }}
    </v-alert>

    <v-alert
      v-if="!context.accounts.length"
      type="warning"
      variant="tonal"
      class="mb-5"
    >
      尚未绑定 MT5 账户，请先在“连接 MT5”页面安装并启动 EA。
    </v-alert>
    <v-alert
      v-else-if="needsEaUpgrade"
      type="warning"
      variant="tonal"
      class="mb-5"
    >
      当前 EA 版本为 {{ selectedAccount?.ea_version || '未知' }}，历史数据任务需要
      {{ context.required_ea_version }} 或更高版本。请重新下载并替换 EA 文件。
    </v-alert>

    <section class="metric-grid">
      <article class="metric-card">
        <span>我的数据集</span>
        <strong>{{ quota.usage.datasets }} / {{ quota.limits.datasets ?? '∞' }}</strong>
      </article>
      <article class="metric-card ready">
        <span>已经可用</span>
        <strong>{{ readyCount }}</strong>
      </article>
      <article class="metric-card active">
        <span>采集中</span>
        <strong>{{ activeCount }}</strong>
      </article>
      <article class="metric-card quality">
        <span>平均质量分</span>
        <strong>{{ averageQuality }}</strong>
      </article>
    </section>

    <div class="content-grid">
      <v-card class="create-card" elevation="0">
        <v-card-text>
          <div class="section-tag">NEW DATASET</div>
          <h2>建立历史行情任务</h2>
          <p class="section-copy">
            创建后，匹配品种的 MT5 EA 会自动领取并分片上传。时间可精确到分钟，最短采集 2 小时；预热数据仅用于指标初始化。
          </p>

          <v-form @submit.prevent="createDataset">
            <v-select
              v-model="form.accountId"
              :items="accountOptions"
              item-title="title"
              item-value="value"
              label="历史行情来源账户"
              variant="outlined"
              density="comfortable"
              hint="任务只会由所选账户的 EA 领取"
              persistent-hint
              class="mt-5"
            />
            <v-text-field
              v-model.trim="form.datasetName"
              label="数据集名称"
              placeholder="例如：GOLD 2025 下半年"
              variant="outlined"
              density="comfortable"
            />
            <v-combobox
              v-model="form.symbol"
              :items="context.symbols"
              label="MT5 品种"
              placeholder="例如 GOLD_"
              variant="outlined"
              density="comfortable"
              hint="必须与挂载 EA 的图表品种完全一致"
              persistent-hint
            />
            <div class="range-presets">
              <v-btn
                v-for="preset in rangePresets"
                :key="preset.hours"
                size="small"
                variant="tonal"
                color="teal"
                @click="applyRangePreset(preset.hours)"
              >
                {{ preset.label }}
              </v-btn>
            </div>
            <div class="date-grid">
              <v-text-field
                v-model="form.startDate"
                label="回测开始时间"
                type="datetime-local"
                step="60"
                variant="outlined"
                density="comfortable"
                hint="可手动输入，也可点上方快捷区间"
                persistent-hint
              />
              <v-text-field
                v-model="form.endDate"
                label="回测结束时间"
                type="datetime-local"
                step="60"
                variant="outlined"
                density="comfortable"
                :error="rangeTooShort"
                :hint="rangeTooShort ? '结束时间至少要比开始时间晚 2 小时' : '按浏览器本地时间选择，支持同一天内的 2 小时以上区间'"
                persistent-hint
              />
            </div>
            <v-select
              v-model="form.warmupDays"
              :items="warmupOptions"
              label="指标预热期"
              variant="outlined"
              density="comfortable"
            />
            <v-switch
              v-model="form.isShared"
              color="success"
              inset
              hide-details
              class="visibility-switch"
              label="共享给其他用户"
            />
            <p class="visibility-hint">
              {{ form.isShared ? '其他用户可以查看并用于回测，但不能修改或删除。' : '仅你自己可以查看和使用此数据集。' }}
            </p>

            <div class="account-strip">
              <v-icon icon="mdi-server-network" />
              <div>
                <span>数据来源</span>
                <strong>
                  {{ selectedAccount?.mt5_server || '等待 MT5 上报服务器' }}
                  · {{ selectedAccount?.mt5_login || '未连接' }}
                  · {{ selectedAccount?.connected ? '在线' : '离线' }}
                </strong>
              </div>
            </div>

            <v-btn
              type="submit"
              color="primary"
              size="large"
              block
              prepend-icon="mdi-database-plus-outline"
              :loading="creating"
              :disabled="!canCreate"
              class="mt-5"
            >
              创建并等待 EA 下载
            </v-btn>
          </v-form>
        </v-card-text>
      </v-card>

      <v-card class="list-card" elevation="0">
        <v-card-text>
          <div class="list-heading">
            <div>
              <div class="section-tag">DATA CATALOG</div>
              <h2>已有数据集</h2>
            </div>
            <span class="refresh-note">采集期间每 5 秒自动刷新</span>
          </div>

          <div v-if="!datasets.length && !loading" class="empty-state">
            <v-icon icon="mdi-database-off-outline" size="52" />
            <h3>还没有历史数据集</h3>
            <p>从左侧创建第一个任务，保持对应品种的 MT5 EA 在线。</p>
          </div>

          <div v-else class="dataset-list">
            <article
              v-for="dataset in datasets"
              :key="dataset.dataset_id"
              class="dataset-item"
            >
              <div class="dataset-main">
                <div class="dataset-title-row">
                  <div>
                    <h3>{{ dataset.dataset_name }}</h3>
                    <span>
                      {{ dataset.symbol }} · M1 · #{{ dataset.dataset_id }}
                      <template v-if="!dataset.is_owner"> · 创建者 {{ dataset.creator_username }}</template>
                    </span>
                  </div>
                  <div class="dataset-chips">
                    <v-chip
                      :prepend-icon="dataset.visibility === 'shared' ? 'mdi-account-group-outline' : 'mdi-lock-outline'"
                      :color="dataset.visibility === 'shared' ? 'teal' : 'grey'"
                      size="small"
                      variant="tonal"
                    >
                      {{ dataset.visibility === 'shared' ? '共享' : '私有' }}
                    </v-chip>
                    <v-chip
                      :color="statusMeta(dataset.status).color"
                      size="small"
                      variant="tonal"
                    >
                      {{ statusMeta(dataset.status).label }}
                    </v-chip>
                  </div>
                </div>

                <div class="dataset-details">
                  <div>
                    <span>回测区间</span>
                    <strong>{{ formatDateTime(dataset.requested_start) }} 至 {{ formatDateTime(dataset.requested_end) }}</strong>
                  </div>
                  <div>
                    <span>已接收</span>
                    <strong>{{ formatNumber(dataset.received_bars) }} 根</strong>
                  </div>
                  <div>
                    <span>质量分</span>
                    <strong>{{ dataset.status === 'ready' ? dataset.quality_score : '--' }}</strong>
                  </div>
                  <div>
                    <span>格式</span>
                    <strong>{{ dataset.status === 'ready' ? dataset.data_format : '--' }}</strong>
                  </div>
                  <div>
                    <span>回测引用</span>
                    <strong>
                      {{ dataset.template_reference_count }} 模板 · {{ dataset.task_reference_count }} 任务
                    </strong>
                  </div>
                </div>

                <v-progress-linear
                  v-if="dataset.can_manage && isActive(dataset.status)"
                  :model-value="dataset.progress"
                  color="primary"
                  height="8"
                  rounded
                  class="mt-4"
                />
                <div v-if="isActive(dataset.status)" class="progress-caption">
                  <span>{{ statusMeta(dataset.status).hint }}</span>
                  <strong>{{ dataset.progress }}%</strong>
                </div>
                <div v-if="dataset.error_message" class="error-detail">
                  {{ dataset.error_message }}
                </div>
              </div>

              <div class="dataset-actions">
                <v-btn
                  size="small"
                  variant="text"
                  color="secondary"
                  prepend-icon="mdi-content-copy"
                  @click="copyDataset(dataset)"
                >
                  复制
                </v-btn>
                <v-btn
                  v-if="dataset.can_manage"
                  size="small"
                  variant="text"
                  color="primary"
                  :prepend-icon="dataset.visibility === 'shared' ? 'mdi-lock-outline' : 'mdi-account-group-outline'"
                  @click="toggleVisibility(dataset)"
                >
                  {{ dataset.visibility === 'shared' ? '设为私有' : '设为共享' }}
                </v-btn>
                <v-btn
                  v-if="isActive(dataset.status)"
                  size="small"
                  variant="text"
                  color="warning"
                  @click="cancelDataset(dataset)"
                >
                  取消
                </v-btn>
                <v-btn
                  v-if="dataset.can_manage"
                  size="small"
                  variant="text"
                  color="error"
                  prepend-icon="mdi-delete-outline"
                  :disabled="!dataset.can_delete"
                  :title="dataset.is_referenced ? '数据集已被回测引用，不能删除' : '删除数据集'"
                  @click="deleteDataset(dataset)"
                >
                  删除
                </v-btn>
              </div>
            </article>
          </div>
        </v-card-text>
      </v-card>
    </div>
  </div>
</template>

<script setup>
import { computed, onMounted, onUnmounted, reactive, ref } from 'vue'
import { marketAPI } from '../api/market'

const datasets = ref([])
const quota = reactive({ usage: { datasets: 0 }, limits: { datasets: 10 } })
const context = reactive({ account: null, accounts: [], symbols: [], required_ea_version: '2.04' })
const loading = ref(false)
const creating = ref(false)
const message = ref('')
const messageType = ref('success')
let refreshTimer = null

function toDateTimeInput(date) {
  const localTime = new Date(date.getTime() - date.getTimezoneOffset() * 60 * 1000)
  return localTime.toISOString().slice(0, 16)
}

const minimumRangeSeconds = 2 * 60 * 60
const defaultEnd = new Date()
defaultEnd.setMinutes(0, 0, 0)
const defaultStart = new Date(defaultEnd.getTime() - minimumRangeSeconds * 1000)

const form = reactive({
  accountId: null,
  datasetName: '',
  symbol: '',
  startDate: toDateTimeInput(defaultStart),
  endDate: toDateTimeInput(defaultEnd),
  warmupDays: 1,
  isShared: true,
})

const warmupOptions = [
  { title: '1 天（默认，M1 策略）', value: 1 },
  { title: '7 天（短周期）', value: 7 },
  { title: '3 天', value: 3 },
  { title: '30 天', value: 30 },
  { title: '60 天', value: 60 },
  { title: '90 天', value: 90 },
  { title: '不额外预热', value: 0 },
]

const rangePresets = [
  { label: '最近 2 小时', hours: 2 },
  { label: '最近 6 小时', hours: 6 },
  { label: '最近 12 小时', hours: 12 },
  { label: '最近 1 天', hours: 24 },
  { label: '最近 3 天', hours: 72 },
  { label: '最近 7 天', hours: 168 },
]

const statusMap = {
  pending: { label: '等待 EA', color: 'grey', hint: '保持对应品种的 EA 在线' },
  downloading: { label: '下载中', color: 'info', hint: 'EA 正在分批上传经纪商历史行情' },
  validating: { label: '校验中', color: 'warning', hint: '正在合并分片并检查数据质量' },
  ready: { label: '可用', color: 'success', hint: '可以用于策略回测' },
  failed: { label: '失败', color: 'error', hint: '请检查错误信息后重新建立' },
  canceled: { label: '已取消', color: 'grey', hint: '任务已停止' },
}

const readyCount = computed(() => datasets.value.filter(item => item.status === 'ready').length)
const activeCount = computed(() => datasets.value.filter(item => isActive(item.status)).length)
const averageQuality = computed(() => {
  const ready = datasets.value.filter(item => item.status === 'ready')
  if (!ready.length) return '--'
  const average = ready.reduce((sum, item) => sum + Number(item.quality_score || 0), 0) / ready.length
  return average.toFixed(1)
})
const selectedAccount = computed(() => context.accounts.find(
  item => item.account_id === form.accountId
) || null)
const accountOptions = computed(() => context.accounts.map(item => ({
  value: item.account_id,
  title: `${item.account_name} · ${item.mt5_server || '未知服务器'} · ${item.mt5_login || '未知账号'}${item.connected ? ' · 在线' : ' · 离线'}`,
})))

function parseLocalDateTime(value) {
  const timestamp = new Date(value).getTime()
  return Number.isFinite(timestamp) ? Math.floor(timestamp / 1000) : 0
}

function applyRangePreset(hours) {
  const end = new Date()
  end.setSeconds(0, 0)
  const start = new Date(end.getTime() - Number(hours) * 60 * 60 * 1000)
  form.startDate = toDateTimeInput(start)
  form.endDate = toDateTimeInput(end)
}

const requestedStart = computed(() => parseLocalDateTime(form.startDate))
const requestedEnd = computed(() => parseLocalDateTime(form.endDate))
const rangeTooShort = computed(() => Boolean(
  form.startDate &&
  form.endDate &&
  requestedEnd.value - requestedStart.value < minimumRangeSeconds
))

const canCreate = computed(() => Boolean(
  selectedAccount.value &&
  form.datasetName &&
  form.symbol &&
  form.startDate &&
  form.endDate &&
  !rangeTooShort.value
))

const needsEaUpgrade = computed(() => {
  const current = selectedAccount.value?.ea_version
  if (!current) return true
  return compareVersions(current, context.required_ea_version) < 0
})

function compareVersions(left, right) {
  const a = String(left).split('.').map(value => Number(value) || 0)
  const b = String(right).split('.').map(value => Number(value) || 0)
  for (let index = 0; index < Math.max(a.length, b.length); index += 1) {
    if ((a[index] || 0) !== (b[index] || 0)) return (a[index] || 0) - (b[index] || 0)
  }
  return 0
}

function statusMeta(status) {
  return statusMap[status] || statusMap.pending
}

function isActive(status) {
  return ['pending', 'downloading', 'validating'].includes(status)
}

function formatDateTime(timestamp) {
  if (!timestamp) return '--'
  return new Date(timestamp * 1000).toLocaleString('zh-CN', {
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    hour12: false,
  })
}

function formatNumber(value) {
  return Number(value || 0).toLocaleString('zh-CN')
}

async function loadAll() {
  loading.value = true
  try {
    const [contextData, datasetData] = await Promise.all([
      marketAPI.getBacktestDatasetContext(),
      marketAPI.getBacktestDatasets(),
    ])
    context.account = contextData.account || null
    context.accounts = contextData.accounts || []
    context.symbols = contextData.symbols || []
    context.required_ea_version = contextData.required_ea_version || '2.04'
    if (!form.accountId && context.accounts.length) {
      form.accountId = context.accounts[0].account_id
    }
    datasets.value = datasetData.datasets || []
    Object.assign(quota, datasetData.quota || quota)
    if (!form.symbol && context.symbols.length) form.symbol = context.symbols[0]
  } catch (error) {
    messageType.value = 'error'
    message.value = error.response?.data?.detail || '加载历史数据集失败'
  } finally {
    loading.value = false
  }
}

async function createDataset() {
  if (!canCreate.value) return
  creating.value = true
  try {
    const data = await marketAPI.createBacktestDataset({
      dataset_name: form.datasetName,
      account_id: form.accountId,
      symbol: form.symbol,
      requested_start: requestedStart.value,
      requested_end: requestedEnd.value,
      warmup_days: form.warmupDays,
      visibility: form.isShared ? 'shared' : 'private',
    })
    messageType.value = 'success'
    message.value = data.message
    form.datasetName = ''
    await loadAll()
  } catch (error) {
    messageType.value = 'error'
    message.value = error.response?.data?.detail || '创建历史数据集失败'
  } finally {
    creating.value = false
  }
}

async function toggleVisibility(dataset) {
  const visibility = dataset.visibility === 'shared' ? 'private' : 'shared'
  const label = visibility === 'shared' ? '共享' : '私有'
  if (!confirm(`确定将“${dataset.dataset_name}”设为${label}吗？`)) return
  try {
    const data = await marketAPI.updateBacktestDatasetVisibility(dataset.dataset_id, visibility)
    messageType.value = 'success'
    message.value = data.message
    await loadAll()
  } catch (error) {
    messageType.value = 'error'
    message.value = error.response?.data?.detail || '修改数据集可见性失败'
  }
}

async function copyDataset(dataset) {
  try {
    const data = await marketAPI.copyBacktestDataset(dataset.dataset_id)
    messageType.value = 'success'
    message.value = data.message || '已复制数据集'
    await loadAll()
  } catch (error) {
    messageType.value = 'error'
    message.value = error.response?.data?.detail || '复制数据集失败'
  }
}

async function cancelDataset(dataset) {
  if (!confirm(`确定取消“${dataset.dataset_name}”的数据采集吗？`)) return
  try {
    const data = await marketAPI.cancelBacktestDataset(dataset.dataset_id)
    messageType.value = 'success'
    message.value = data.message
    await loadAll()
  } catch (error) {
    messageType.value = 'error'
    message.value = error.response?.data?.detail || '取消任务失败'
  }
}

async function deleteDataset(dataset) {
  if (!confirm(`确定删除“${dataset.dataset_name}”及其全部行情文件吗？`)) return
  try {
    const data = await marketAPI.deleteBacktestDataset(dataset.dataset_id)
    messageType.value = 'success'
    message.value = data.message
    await loadAll()
  } catch (error) {
    messageType.value = 'error'
    message.value = error.response?.data?.detail || '删除数据集失败'
  }
}

onMounted(async () => {
  await loadAll()
  refreshTimer = window.setInterval(() => {
    if (activeCount.value > 0) loadAll()
  }, 5000)
})

onUnmounted(() => {
  if (refreshTimer) window.clearInterval(refreshTimer)
})
</script>

<style scoped>
.dataset-page {
  min-height: 100%;
  padding: 28px;
  background:
    radial-gradient(circle at 86% 4%, rgba(32, 116, 95, 0.13), transparent 28%),
    linear-gradient(145deg, #f3f1e9 0%, #edf2ed 52%, #f7f5ee 100%);
}

.dataset-hero {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 24px;
  padding: 34px 38px;
  margin-bottom: 24px;
  color: #f8f4e8;
  border-radius: 24px;
  background:
    linear-gradient(120deg, rgba(8, 36, 32, 0.95), rgba(24, 84, 67, 0.9)),
    repeating-linear-gradient(90deg, transparent 0 24px, rgba(255,255,255,.03) 24px 25px);
  box-shadow: 0 18px 44px rgba(19, 56, 45, 0.18);
}

.dataset-hero h1 {
  margin: 4px 0 8px;
  font-family: Georgia, 'Times New Roman', serif;
  font-size: clamp(2rem, 4vw, 3.4rem);
  line-height: 1;
}

.dataset-hero p { margin: 0; max-width: 720px; color: rgba(255,255,255,.76); }
.eyebrow, .section-tag { letter-spacing: .16em; font-size: .72rem; font-weight: 800; }
.eyebrow { color: #d3ad64; }
.section-tag { color: #18725d; }

.metric-grid {
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  gap: 14px;
  margin-bottom: 20px;
}

.metric-card {
  padding: 18px 20px;
  border: 1px solid rgba(18, 65, 53, .09);
  border-radius: 16px;
  background: rgba(255, 255, 255, .78);
}

.metric-card span { display: block; color: #68736e; font-size: .82rem; }
.metric-card strong { display: block; margin-top: 5px; color: #173f35; font-size: 1.8rem; }
.metric-card.ready { border-top: 3px solid #2f8a65; }
.metric-card.active { border-top: 3px solid #297ca6; }
.metric-card.quality { border-top: 3px solid #c28a35; }

.content-grid {
  display: grid;
  grid-template-columns: minmax(300px, 390px) 1fr;
  gap: 20px;
  align-items: start;
}

.create-card, .list-card {
  border: 1px solid rgba(18, 65, 53, .1);
  border-radius: 20px;
  background: rgba(255, 255, 255, .9);
}

.create-card { position: sticky; top: 20px; }
.create-card h2, .list-card h2 { margin: 4px 0 8px; color: #183f35; font-family: Georgia, serif; }
.section-copy { color: #68736e; font-size: .9rem; line-height: 1.55; }
.range-presets {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin: 4px 0 14px;
}
.date-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 10px; }
.visibility-switch { margin-top: -4px; }
.visibility-hint { margin: -2px 0 16px 54px; color: #718078; font-size: .76rem; }

.account-strip {
  display: flex;
  gap: 12px;
  align-items: center;
  padding: 13px 15px;
  border-radius: 12px;
  color: #275a4b;
  background: #edf5f0;
}

.account-strip span, .account-strip strong { display: block; }
.account-strip span { font-size: .72rem; color: #718078; }
.account-strip strong { margin-top: 2px; font-size: .84rem; }

.list-heading { display: flex; align-items: end; justify-content: space-between; margin-bottom: 18px; }
.refresh-note { color: #7b8580; font-size: .76rem; }
.dataset-list { display: grid; gap: 14px; }
.dataset-item { display: flex; gap: 12px; padding: 19px; border: 1px solid #e0e7e2; border-radius: 16px; background: #fcfdfb; }
.dataset-main { flex: 1; min-width: 0; }
.dataset-title-row { display: flex; justify-content: space-between; gap: 12px; }
.dataset-title-row h3 { margin: 0; color: #203e36; font-size: 1rem; }
.dataset-title-row span { color: #839089; font-size: .75rem; }
.dataset-chips { display: flex; align-items: flex-start; gap: 6px; flex-wrap: wrap; justify-content: flex-end; }
.dataset-details { display: grid; grid-template-columns: 2fr repeat(4, 1fr); gap: 16px; margin-top: 16px; }
.dataset-details span, .dataset-details strong { display: block; }
.dataset-details span { color: #87918c; font-size: .7rem; }
.dataset-details strong { margin-top: 3px; color: #344f47; font-size: .8rem; }
.progress-caption { display: flex; justify-content: space-between; margin-top: 6px; color: #718078; font-size: .75rem; }
.dataset-actions { display: flex; flex-direction: column; justify-content: center; }
.error-detail { margin-top: 12px; padding: 10px 12px; border-radius: 10px; color: #a33c35; background: #fff0ed; font-size: .8rem; }
.empty-state { padding: 70px 20px; text-align: center; color: #84918b; }
.empty-state h3 { margin: 14px 0 6px; color: #476058; }
.empty-state p { margin: 0; }

@media (max-width: 960px) {
  .dataset-page { padding: 16px; }
  .dataset-hero { align-items: flex-start; flex-direction: column; padding: 26px; }
  .metric-grid { grid-template-columns: repeat(2, 1fr); }
  .content-grid { grid-template-columns: 1fr; }
  .create-card { position: static; }
}

@media (max-width: 600px) {
  .metric-grid { grid-template-columns: 1fr 1fr; }
  .date-grid, .dataset-details { grid-template-columns: 1fr; }
  .dataset-item, .dataset-title-row { flex-direction: column; }
  .dataset-actions { flex-direction: row; justify-content: flex-end; }
}
</style>

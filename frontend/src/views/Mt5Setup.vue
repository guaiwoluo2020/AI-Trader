<template>
  <div class="setup-page">
    <section class="hero">
      <div>
        <div class="eyebrow">MT5 TERMINAL BRIDGE</div>
        <h1>把交易终端接入你的账户</h1>
        <p>
          下载已经编译好的专属 EA 文件，不需要 MetaEditor，也不需要手工填写用户凭证。
        </p>
      </div>
      <div class="hero-status" :class="{ online: status.connected }">
        <span class="status-dot"></span>
        {{ status.connected ? 'MT5 已连接' : '等待 MT5 连接' }}
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

    <div class="setup-grid">
      <main class="install-panel">
        <div class="panel-heading">
          <div>
            <span class="section-label">INSTALLATION</span>
            <h2>四步完成安装</h2>
          </div>
          <v-btn
            color="primary"
            size="large"
            prepend-icon="mdi-download"
            :loading="downloading"
            :disabled="!status.artifact_available"
            @click="downloadEA"
          >
            下载专属 EA
          </v-btn>
        </div>

        <div v-if="!status.artifact_available && loaded" class="artifact-warning">
          服务端尚未发布编译后的 EX5 文件，完成编译后下载按钮会自动可用。
        </div>

        <ol class="steps">
          <li>
            <span class="step-number">01</span>
            <div>
              <h3>下载专属文件</h3>
              <p>
                文件名中包含一个 10 分钟有效、仅可使用一次的激活码。请不要修改文件名。
              </p>
            </div>
          </li>
          <li>
            <span class="step-number">02</span>
            <div>
              <h3>复制到 Experts 目录</h3>
              <p>MT5 选择“文件 → 打开数据文件夹”，放入以下目录：</p>
              <code>MQL5/Experts/AITrader/</code>
            </div>
          </li>
          <li>
            <span class="step-number">03</span>
            <div>
              <h3>允许 WebRequest</h3>
              <p>
                工具 → 选项 → EA 交易，把服务地址加入允许列表。
              </p>
              <div class="copy-row">
                <code>{{ status.server_url || 'http://127.0.0.1:8000' }}</code>
                <v-btn
                  icon="mdi-content-copy"
                  size="small"
                  variant="text"
                  aria-label="复制服务地址"
                  @click="copyServerUrl"
                />
              </div>
            </div>
          </li>
          <li>
            <span class="step-number">04</span>
            <div>
              <h3>挂载并启用自动交易</h3>
              <p>
                刷新导航器，将 EA 拖到任意图表，勾选算法交易并打开顶部“自动交易”。
                首次启动会自动完成账户绑定。
              </p>
            </div>
          </li>
        </ol>

        <div class="security-note">
          <v-icon icon="mdi-shield-check-outline" />
          <span>
            下载不会中断旧 EA。只有新文件首次成功激活后，旧凭证才会失效。
          </span>
        </div>
      </main>

      <aside class="connection-card">
        <span class="section-label">CONNECTION</span>
        <div class="terminal-mark">MT5</div>
        <h2>{{ status.connected ? '终端在线' : '尚未检测到终端' }}</h2>
        <p class="connection-copy">
          {{
            status.connected
              ? 'EA 正在使用此账户与交易服务通信。'
              : '安装并启动专属 EA 后，这里会自动更新。'
          }}
        </p>

        <dl>
          <div>
            <dt>MT5 登录号</dt>
            <dd>{{ binding.mt5_login || '未上报' }}</dd>
          </div>
          <div>
            <dt>交易服务器</dt>
            <dd>{{ binding.mt5_server || '未上报' }}</dd>
          </div>
          <div>
            <dt>EA 版本</dt>
            <dd>{{ binding.ea_version || '未上报' }}</dd>
          </div>
          <div>
            <dt>最后通信</dt>
            <dd>{{ formatTime(binding.last_seen_at) }}</dd>
          </div>
        </dl>

        <v-btn
          variant="outlined"
          block
          prepend-icon="mdi-refresh"
          :loading="refreshing"
          @click="loadStatus"
        >
          刷新状态
        </v-btn>
      </aside>
    </div>
  </div>
</template>

<script setup>
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { mt5API } from '../api/trading'

const status = ref({
  connected: false,
  artifact_available: false,
  server_url: 'http://127.0.0.1:8000',
  binding: null,
})
const loaded = ref(false)
const refreshing = ref(false)
const downloading = ref(false)
const errorMessage = ref('')
let refreshTimer = null

const binding = computed(() => status.value.binding || {})

async function loadStatus() {
  refreshing.value = true
  try {
    status.value = await mt5API.status()
    errorMessage.value = ''
  } catch (error) {
    errorMessage.value = error.response?.data?.detail || '读取 MT5 连接状态失败'
  } finally {
    refreshing.value = false
    loaded.value = true
  }
}

async function downloadEA() {
  downloading.value = true
  errorMessage.value = ''
  try {
    const response = await mt5API.download()
    const filename =
      response.headers['x-ea-filename'] || extractFilename(response.headers['content-disposition'])
    const url = URL.createObjectURL(response.data)
    const anchor = document.createElement('a')
    anchor.href = url
    anchor.download = filename || 'mt5TerminalEA.ex5'
    document.body.appendChild(anchor)
    anchor.click()
    anchor.remove()
    URL.revokeObjectURL(url)
  } catch (error) {
    errorMessage.value = await extractError(error)
    await loadStatus()
  } finally {
    downloading.value = false
  }
}

function extractFilename(contentDisposition = '') {
  const match = contentDisposition.match(/filename="?([^";]+)"?/i)
  return match?.[1]
}

async function extractError(error) {
  const payload = error.response?.data
  if (payload instanceof Blob) {
    try {
      const data = JSON.parse(await payload.text())
      return data.detail || '下载 EA 失败'
    } catch {
      return '下载 EA 失败'
    }
  }
  return payload?.detail || '下载 EA 失败'
}

async function copyServerUrl() {
  try {
    await navigator.clipboard.writeText(status.value.server_url)
  } catch {
    errorMessage.value = '复制失败，请手工选择服务地址'
  }
}

function formatTime(timestamp) {
  if (!timestamp) return '尚无通信'
  return new Date(timestamp * 1000).toLocaleString('zh-CN', { hour12: false })
}

onMounted(() => {
  loadStatus()
  refreshTimer = window.setInterval(loadStatus, 5000)
})

onUnmounted(() => {
  window.clearInterval(refreshTimer)
})
</script>

<style scoped>
.setup-page {
  min-height: 100%;
  padding: 30px;
  color: #182018;
  background:
    radial-gradient(circle at 92% 4%, rgba(201, 151, 45, 0.18), transparent 28rem),
    linear-gradient(145deg, #f5f3e9 0%, #eef2e8 55%, #e7eee7 100%);
}

.hero {
  display: flex;
  align-items: flex-end;
  justify-content: space-between;
  gap: 24px;
  max-width: 1240px;
  margin: 0 auto 26px;
  padding: 38px 42px;
  color: #f4f0df;
  border-radius: 24px;
  background:
    linear-gradient(120deg, rgba(255, 255, 255, 0.05), transparent 40%),
    #18251d;
  box-shadow: 0 24px 60px rgba(25, 45, 32, 0.18);
}

.eyebrow,
.section-label {
  color: #c9972d;
  font-family: "Avenir Next Condensed", "DIN Condensed", sans-serif;
  font-size: 0.75rem;
  font-weight: 800;
  letter-spacing: 0.2em;
}

.hero h1 {
  max-width: 700px;
  margin: 10px 0 8px;
  font-family: "Songti SC", "STSong", serif;
  font-size: clamp(2rem, 4vw, 3.5rem);
  font-weight: 700;
  line-height: 1.1;
}

.hero p {
  max-width: 680px;
  margin: 0;
  color: #bec9bf;
}

.hero-status {
  display: flex;
  flex: 0 0 auto;
  align-items: center;
  gap: 9px;
  padding: 11px 16px;
  color: #c8d0c9;
  border: 1px solid rgba(255, 255, 255, 0.14);
  border-radius: 999px;
}

.status-dot {
  width: 9px;
  height: 9px;
  border-radius: 50%;
  background: #c9972d;
  box-shadow: 0 0 0 5px rgba(201, 151, 45, 0.13);
}

.hero-status.online .status-dot {
  background: #69c887;
  box-shadow: 0 0 0 5px rgba(105, 200, 135, 0.14);
}

.setup-grid {
  display: grid;
  grid-template-columns: minmax(0, 1fr) 340px;
  gap: 22px;
  max-width: 1240px;
  margin: 0 auto;
}

.install-panel,
.connection-card {
  border: 1px solid rgba(42, 65, 48, 0.11);
  border-radius: 22px;
  background: rgba(255, 255, 250, 0.9);
  box-shadow: 0 16px 45px rgba(33, 54, 39, 0.08);
}

.install-panel {
  padding: 30px 34px;
}

.panel-heading {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 24px;
}

.panel-heading h2,
.connection-card h2 {
  margin: 5px 0 0;
  font-family: "Songti SC", "STSong", serif;
}

.artifact-warning {
  margin-top: 20px;
  padding: 12px 15px;
  color: #7a5714;
  border-left: 3px solid #c9972d;
  background: #fff6dc;
}

.steps {
  margin: 30px 0;
  padding: 0;
  list-style: none;
}

.steps li {
  display: grid;
  grid-template-columns: 54px 1fr;
  gap: 16px;
  padding: 21px 0;
  border-top: 1px solid #e1e6de;
}

.step-number {
  color: #9a741f;
  font-family: "Avenir Next Condensed", sans-serif;
  font-size: 1.4rem;
  font-weight: 800;
}

.steps h3 {
  margin: 0 0 6px;
  font-size: 1.05rem;
}

.steps p {
  margin: 0;
  color: #687268;
  line-height: 1.7;
}

code {
  display: inline-block;
  margin-top: 8px;
  color: #254d34;
  font-size: 0.86rem;
  font-weight: 700;
}

.copy-row {
  display: flex;
  align-items: center;
  gap: 4px;
}

.security-note {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 14px 16px;
  color: #315b3e;
  border-radius: 12px;
  background: #eaf3e9;
}

.connection-card {
  align-self: start;
  position: sticky;
  top: 92px;
  padding: 28px;
}

.terminal-mark {
  display: grid;
  width: 74px;
  height: 74px;
  margin: 22px 0;
  place-items: center;
  color: #fff8e6;
  border-radius: 20px;
  background: linear-gradient(145deg, #234630, #15291c);
  box-shadow: 0 12px 24px rgba(24, 52, 33, 0.2);
  font-weight: 900;
  letter-spacing: 0.08em;
}

.connection-copy {
  min-height: 48px;
  color: #6b746b;
  line-height: 1.55;
}

dl {
  margin: 26px 0;
}

dl div {
  display: flex;
  justify-content: space-between;
  gap: 18px;
  padding: 12px 0;
  border-top: 1px solid #e3e8e1;
}

dt {
  color: #788078;
  font-size: 0.84rem;
}

dd {
  margin: 0;
  overflow-wrap: anywhere;
  text-align: right;
  font-size: 0.88rem;
  font-weight: 700;
}

@media (max-width: 900px) {
  .setup-page {
    padding: 16px;
  }

  .hero {
    align-items: flex-start;
    flex-direction: column;
    padding: 28px 24px;
  }

  .setup-grid {
    grid-template-columns: 1fr;
  }

  .connection-card {
    position: static;
  }
}

@media (max-width: 600px) {
  .panel-heading {
    align-items: stretch;
    flex-direction: column;
  }

  .install-panel {
    padding: 24px 20px;
  }
}
</style>

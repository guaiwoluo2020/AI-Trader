<template>
  <div class="alpha-page">
    <section class="alpha-hero">
      <div>
        <span class="eyebrow">ALPHA DISCOVERY LAB</span>
        <h1>Alpha 研究</h1>
        <p>用 pandas-ta 因子组合和 Optuna 参数搜索，快速验证买卖信号的预测能力。</p>
      </div>
      <div class="hero-badge">
        <v-icon size="34">mdi-atom-variant</v-icon>
        <div><strong>毛收益研究</strong><span>暂不计手续费、点差与滑点</span></div>
      </div>
    </section>

    <v-alert v-if="message" :type="messageType" closable class="mb-5" @click:close="message = ''">
      {{ message }}
    </v-alert>

    <div class="research-grid">
      <v-card class="research-form" elevation="0">
        <div class="section-heading">
          <div><span>01</span><h2>建立研究任务</h2></div>
          <v-btn variant="tonal" color="primary" prepend-icon="mdi-bookshelf" @click="factorLibraryDialog = true">浏览因子库</v-btn>
        </div>

        <v-form @submit.prevent="createRun">
          <v-btn-toggle v-model="researchMode" mandatory color="primary" class="research-mode">
            <v-btn value="ai" prepend-icon="mdi-creation-outline">AI 生成候选</v-btn>
            <v-btn value="advanced" prepend-icon="mdi-tune-vertical">高级自定义</v-btn>
          </v-btn-toggle>
          <div class="field-grid">
            <v-text-field v-model="form.researchName" label="研究名称" variant="outlined" />
            <v-select
              v-model="form.datasetId"
              :items="datasetOptions"
              label="历史行情数据集"
              variant="outlined"
              no-data-text="暂无已就绪数据集"
            />
            <v-select v-model="form.timeframe" :items="timeframes" label="分析周期" variant="outlined" />
            <v-text-field
              v-model.number="form.predictionHorizon"
              type="number"
              min="1"
              max="500"
              label="预测未来 K 线数"
              variant="outlined"
            />
          </div>

          <div v-if="researchMode === 'ai'" class="ai-research-panel">
            <div class="subheading">
              <div><h3>描述研究目标</h3><p>描述想寻找的行情规律，不需要手工选择技术指标。</p></div>
              <v-chip color="primary" variant="tonal">Research Agent</v-chip>
            </div>
            <v-textarea
              v-model="form.researchDescription"
              label="例如：寻找 GOLD 在 M5 周期趋势启动后，未来 15 根 K 线仍能延续的买卖信号"
              variant="outlined"
              rows="3"
              counter="500"
              maxlength="500"
            />
            <div class="generate-row">
              <span>AI 会从趋势、动量、波动、量价、统计等类别中提出结构不同的候选。</span>
              <v-btn color="primary" prepend-icon="mdi-auto-fix" :loading="generatingCandidates" :disabled="form.researchDescription.trim().length < 10" @click="generateCandidates">生成 Alpha 候选</v-btn>
            </div>
            <v-select
              v-model.number="form.llmIterationCount"
              :items="llmIterationOptions"
              label="LLM 结构优化轮次"
              variant="outlined"
              class="iteration-select"
              hint="每轮先由 Optuna 搜索参数，再把训练/验证诊断交给大模型调整因子结构；隐藏测试仅在最终执行一次。"
              persistent-hint
            />
            <div v-if="candidates.length" class="candidate-grid">
              <button
                v-for="candidate in candidates"
                :key="candidate.candidate_id"
                type="button"
                class="candidate-card"
                :class="{ selected: selectedCandidateId === candidate.candidate_id }"
                @click="selectCandidate(candidate)"
              >
                <div class="candidate-head"><span>{{ candidate.theme }}</span><v-icon>{{ selectedCandidateId === candidate.candidate_id ? 'mdi-check-circle' : 'mdi-circle-outline' }}</v-icon></div>
                <h4>{{ candidate.name }}</h4>
                <p>{{ candidate.hypothesis }}</p>
                <div class="candidate-factors">
                  <span v-for="factor in candidate.factors" :key="factor.name">
                    {{ factor.display_name }}<small>{{ factor.category_label }}</small>
                  </span>
                </div>
                <div class="candidate-logic"><b>买</b>{{ candidate.buy_logic }}<b>卖</b>{{ candidate.sell_logic }}</div>
              </button>
            </div>
          </div>

          <div v-else>
            <div class="subheading">
              <div><h3>自定义因子组合</h3><p>专业用户可选择 1-5 个因子，由 Optuna 搜索周期和权重。</p></div>
              <v-btn color="primary" variant="tonal" prepend-icon="mdi-plus" @click="addFactor">添加因子</v-btn>
            </div>
            <div class="factor-list">
              <div v-for="(factor, index) in form.factors" :key="factor.key" class="factor-card">
                <div class="factor-index">F{{ index + 1 }}</div>
                <v-autocomplete v-model="factor.name" :items="factorOptions" label="pandas-ta 因子" variant="outlined" density="compact" class="factor-name" />
                <v-text-field v-model.number="factor.lengthMin" type="number" label="周期最小" min="2" max="500" variant="outlined" density="compact" />
                <v-text-field v-model.number="factor.lengthMax" type="number" label="周期最大" min="2" max="500" variant="outlined" density="compact" />
                <v-text-field v-model.number="factor.weightMin" type="number" step="0.1" label="权重最小" variant="outlined" density="compact" />
                <v-text-field v-model.number="factor.weightMax" type="number" step="0.1" label="权重最大" variant="outlined" density="compact" />
                <v-btn icon="mdi-delete-outline" color="error" variant="text" :disabled="form.factors.length === 1" @click="removeFactor(index)" />
              </div>
            </div>
          </div>

          <div class="subheading compact"><div><h3>信号与交易规则</h3><p>选择一个主退出规则，并按需叠加保护规则；最先触发的规则负责退出。</p></div></div>
          <div class="field-grid">
            <v-select v-model="form.exitMode" :items="exitModeOptions" label="主退出规则" variant="outlined" />
            <v-text-field
              v-if="form.exitMode === 'fixed_horizon'"
              v-model.number="form.fixedHorizonBars"
              type="number"
              min="1"
              max="500"
              label="固定持有 K 线数"
              variant="outlined"
            />
            <v-text-field v-model.number="form.confirmationBars" type="number" min="1" max="10" label="信号确认根数" variant="outlined" />
            <v-text-field v-model.number="form.cooldownBars" type="number" min="0" max="500" label="信号冷却 K 线数" variant="outlined" />
          </div>

          <div class="protection-panel">
            <div class="protection-title"><v-icon>mdi-shield-half-full</v-icon><div><h4>可选保护规则</h4><p>填写 0 表示不启用；Alpha 研究仍只统计毛收益。</p></div></div>
            <div class="protection-grid">
              <v-text-field v-model.number="form.stopLossPercent" type="number" min="0" max="50" step="0.1" suffix="%" label="固定止损" variant="outlined" />
              <v-text-field v-model.number="form.takeProfitPercent" type="number" min="0" max="50" step="0.1" suffix="%" label="固定止盈" variant="outlined" />
              <v-text-field v-model.number="form.trailingStopPercent" type="number" min="0" max="50" step="0.1" suffix="%" label="移动止损" variant="outlined" />
              <v-text-field v-model.number="form.maxHoldingBars" type="number" min="0" max="5000" suffix="根" label="最大持有 K 线" variant="outlined" />
            </div>
          </div>

          <div class="threshold-grid">
            <div><label>买入阈值搜索范围</label><div><v-text-field v-model.number="form.buyThresholdMin" type="number" step="0.1" variant="outlined" density="compact" /><span>至</span><v-text-field v-model.number="form.buyThresholdMax" type="number" step="0.1" variant="outlined" density="compact" /></div></div>
            <div><label>卖出阈值搜索范围</label><div><v-text-field v-model.number="form.sellThresholdMin" type="number" step="0.1" variant="outlined" density="compact" /><span>至</span><v-text-field v-model.number="form.sellThresholdMax" type="number" step="0.1" variant="outlined" density="compact" /></div></div>
          </div>

          <div class="run-strip">
            <div><v-icon>mdi-tune-vertical</v-icon><span>每轮 Optuna 次数</span></div>
            <v-slider v-model="form.trialCount" :min="5" :max="200" :step="5" thumb-label color="primary" hide-details />
            <strong>{{ form.trialCount }}</strong>
          </div>
          <div v-if="researchMode === 'ai'" class="budget-note">
            研究预算：最多 {{ form.llmIterationCount }} 轮 × {{ form.trialCount }} 次 = {{ form.llmIterationCount * form.trialCount }} 次试验；连续两轮无显著改善会提前停止。
          </div>

          <v-btn type="submit" color="primary" size="large" block :loading="creating" :disabled="!canCreate">
            开始 Alpha 搜索
          </v-btn>
        </v-form>
      </v-card>

      <aside class="research-aside">
        <v-card class="metric-card" elevation="0">
          <span>研究任务</span><strong>{{ runs.length }}</strong><small>当前用户</small>
        </v-card>
        <v-card class="metric-card accent" elevation="0">
          <span>执行中</span><strong>{{ activeCount }}</strong><small>自动刷新进度</small>
        </v-card>
        <v-card class="guide-card" elevation="0">
          <v-icon>mdi-lightbulb-on-outline</v-icon>
          <h3>如何选择退出模式</h3>
          <p><b>反向信号退出</b>适合持续趋势观点，是默认模式。</p>
          <p><b>固定持有周期</b>适合验证明确预测窗口。</p>
          <p><b>回到观望退出</b>适合阈值区间型信号。</p>
          <p><b>保护规则</b>与主规则并行，止损、止盈、移动止损或最大持有先触发先退出。</p>
        </v-card>
      </aside>
    </div>

    <section class="runs-section">
      <div class="section-heading">
        <div><span>02</span><h2>研究任务</h2></div>
        <v-btn variant="text" prepend-icon="mdi-refresh" :loading="loading" @click="loadAll">刷新</v-btn>
      </div>
      <div v-if="!runs.length && !loading" class="empty-state">
        <v-icon size="52">mdi-chart-bell-curve-cumulative</v-icon><h3>还没有 Alpha 研究任务</h3><p>从上方选择数据集和因子，建立第一组搜索实验。</p>
      </div>
      <div v-else class="run-list">
        <article v-for="run in runs" :key="run.run_id" class="run-card">
          <div class="run-main">
            <div class="run-icon"><v-icon>mdi-function-variant</v-icon></div>
            <div>
              <div class="run-title"><h3>{{ run.research_name }}</h3><v-chip :color="statusMeta(run.status).color" size="small" variant="tonal">{{ statusMeta(run.status).label }}</v-chip></div>
              <p>{{ run.dataset_name || '数据集已删除' }} · {{ run.symbol || '--' }} · {{ run.config.timeframe }} · {{ exitLabel(run.config.exit_mode) }} · {{ run.config.llm_iteration_count || 1 }} 轮</p>
              <div class="factor-tags"><span v-for="factor in run.config.factors" :key="factor.name">{{ factor.name.toUpperCase() }}</span></div>
            </div>
          </div>
          <div class="run-progress">
            <div><span>搜索进度</span><b>{{ Number(run.progress || 0).toFixed(1) }}%</b></div>
            <v-progress-linear :model-value="run.progress" :color="statusMeta(run.status).color" rounded height="8" />
          </div>
          <div class="run-score">
            <span>AlphaScore</span><strong>{{ run.result.best_score ?? '--' }}</strong><small>{{ run.result.trial_count || run.config.trial_count }} Trials</small>
          </div>
          <div class="run-actions">
            <v-btn variant="tonal" color="primary" @click="openDetail(run)">查看结果</v-btn>
            <v-btn v-if="['queued', 'running'].includes(run.status)" variant="text" color="error" @click="cancelRun(run)">终止</v-btn>
          </div>
          <v-alert v-if="run.error_message" type="error" density="compact" variant="tonal" class="run-error">{{ run.error_message }}</v-alert>
        </article>
      </div>
    </section>

    <v-dialog v-model="detailDialog" max-width="1100">
      <v-card v-if="detailRun" class="detail-dialog">
        <v-card-title><div><span>ALPHA REPORT</span><h2>{{ detailRun.research_name }}</h2></div><v-btn icon="mdi-close" variant="text" @click="detailDialog = false" /></v-card-title>
        <v-card-text>
          <div v-if="detailRun.status === 'completed'" class="publish-panel">
            <div>
              <v-chip :color="detailRun.admission?.passed ? 'success' : 'warning'" variant="tonal">
                {{ detailRun.admission?.passed ? '准入检查已通过' : '尚未达到 Alpha 库准入标准' }}
              </v-chip>
              <p>通过覆盖率、IC、五分组单调性和隐藏测试后，才可作为策略信号源。</p>
            </div>
            <div class="publish-actions">
              <v-select v-model="publishVisibility" :items="visibilityOptions" label="发布范围" density="compact" hide-details></v-select>
              <v-btn color="success" prepend-icon="mdi-bookshelf" :loading="publishing" :disabled="!detailRun.admission?.passed || isRunPublished(detailRun.run_id)" @click="publishRun">
                {{ isRunPublished(detailRun.run_id) ? '已进入 Alpha 库' : '发布到 Alpha 库' }}
              </v-btn>
            </div>
          </div>
          <template v-if="detailRun.admission?.checks?.length">
            <h3 class="table-title">Alpha 准入检查</h3>
            <div class="admission-grid">
              <div v-for="check in detailRun.admission.checks" :key="check.key" :class="{ passed: check.passed }">
                <v-icon>{{ check.passed ? 'mdi-check-circle' : 'mdi-alert-circle' }}</v-icon>
                <span>{{ check.label }}</span><b>{{ scoreValue(check.value) }} {{ check.operator }} {{ check.threshold }}</b>
              </div>
            </div>
          </template>
          <h3 class="report-level"><span>Level 2</span> 因子预测力</h3>
          <div class="report-metrics">
            <div><span>AlphaScore</span><strong>{{ detailRun.result.best_score ?? '--' }}</strong></div>
            <div><span>Rank IC</span><strong>{{ metric('rank_ic') }}</strong></div>
            <div><span>IC_IR</span><strong>{{ metric('ic_ir') }}</strong></div>
            <div><span>Rank IC_IR</span><strong>{{ metric('rank_ic_ir') }}</strong></div>
            <div><span>滚动 IC 样本</span><strong>{{ detailRun.result.metrics?.rolling_ic_count ?? '--' }}</strong></div>
            <div><span>方向命中率</span><strong>{{ percentMetric('hit_rate') }}</strong></div>
            <div><span>IC t-stat</span><strong>{{ metric('ic_t_stat') }}</strong></div>
            <div><span>正 Rank IC 比例</span><strong>{{ percentMetric('positive_rank_ic_ratio') }}</strong></div>
            <div><span>五分组单调性</span><strong>{{ percentMetric('quintile_analysis', 'monotonicity') }}</strong></div>
          </div>
          <h3 class="report-level"><span>Level 3</span> 策略表现</h3>
          <div class="report-metrics">
            <div><span>Sharpe（逐笔）</span><strong>{{ metric('sharpe') }}</strong></div>
            <div><span>Sortino（逐笔）</span><strong>{{ metric('sortino') }}</strong></div>
            <div><span>Profit Factor</span><strong>{{ metric('profit_factor') }}</strong></div>
            <div><span>累计毛收益</span><strong>{{ percentMetric('gross_return') }}</strong></div>
            <div><span>最大回撤</span><strong>{{ percentMetric('max_drawdown') }}</strong></div>
            <div><span>策略换手率</span><strong>{{ percentMetric('strategy_turnover') }}</strong></div>
          </div>
          <v-alert type="info" variant="tonal" class="my-4">本报告为因子研究毛收益，不包含手续费、点差、滑点和资金管理；Sharpe 与 Sortino 当前按逐笔毛收益计算且未年化。</v-alert>
          <template v-if="detailRun.result.factor_diagnostics?.length">
            <h3 class="table-title">因子诊断与正交性</h3>
            <v-table density="compact" class="decay-table">
              <thead><tr><th>因子</th><th>覆盖率</th><th>自相关(1)</th><th>Rank IC</th><th>正 IC 比例</th><th>最大相关</th></tr></thead>
              <tbody><tr v-for="item in detailRun.result.factor_diagnostics" :key="item.name"><td>{{ item.name }}</td><td>{{ asPercent(item.coverage) }}</td><td>{{ scoreValue(autocorrelationAt(item, 1)) }}</td><td>{{ scoreValue(item.rank_ic) }}</td><td>{{ asPercent(item.positive_rank_ic_ratio) }}</td><td>{{ scoreValue(item.max_peer_correlation) }}</td></tr></tbody>
            </v-table>
          </template>
          <template v-if="detailRun.result.metrics?.decay?.length">
            <h3 class="table-title">因子衰减 Decay</h3>
            <v-table density="compact" class="decay-table">
              <thead><tr><th>未来 K 线</th><th>IC</th><th>Rank IC</th><th>信号平均收益</th><th>有效样本</th></tr></thead>
              <tbody>
                <tr v-for="item in detailRun.result.metrics.decay" :key="item.horizon">
                  <td>{{ item.horizon }} 根</td><td>{{ scoreValue(item.ic) }}</td><td>{{ scoreValue(item.rank_ic) }}</td>
                  <td :class="item.mean_signal_return >= 0 ? 'positive' : 'negative'">{{ signedPercent(item.mean_signal_return) }}</td><td>{{ item.sample_count }}</td>
                </tr>
              </tbody>
            </v-table>
          </template>
          <v-alert v-if="detailRun.result.stopped_reason" type="success" variant="tonal" class="my-4">
            已选择第 {{ detailRun.result.selected_iteration }} 轮：{{ detailRun.result.stopped_reason }}
          </v-alert>
          <template v-if="detailRun.iterations?.length">
            <h3 class="table-title">LLM × Optuna 迭代轨迹</h3>
            <v-expansion-panels variant="accordion" class="iteration-panels">
              <v-expansion-panel v-for="iteration in detailRun.iterations" :key="iteration.iteration_number">
                <v-expansion-panel-title>
                  <div class="iteration-title">
                    <b>第 {{ iteration.iteration_number }} 轮</b>
                    <span>{{ iteration.candidate.name || 'Alpha 候选' }}</span>
                    <v-chip v-if="detailRun.result.selected_iteration === iteration.iteration_number" color="success" size="x-small">最终采用</v-chip>
                    <small>验证 {{ scoreValue(iteration.metrics.validation?.score ?? iteration.metrics.objective_score) }} · 泛化差距 {{ scoreValue(iteration.metrics.generalization_gap) }}</small>
                  </div>
                </v-expansion-panel-title>
                <v-expansion-panel-text>
                  <p class="iteration-hypothesis">{{ iteration.candidate.hypothesis }}</p>
                  <code class="expression-block">{{ iteration.expression_text }}</code>
                  <div class="split-summary">
                    <span>训练分数 <b>{{ scoreValue(iteration.metrics.train?.score) }}</b></span>
                    <span>验证分数 <b>{{ scoreValue(iteration.metrics.validation?.score) }}</b></span>
                    <span>验证 IC_IR <b>{{ scoreValue(iteration.metrics.validation?.ic_ir) }}</b></span>
                    <span>验证 Sharpe <b>{{ scoreValue(iteration.metrics.validation?.sharpe) }}</b></span>
                    <span>优化目标 <b>{{ scoreValue(iteration.metrics.objective_score) }}</b></span>
                    <span>中位数 <b>{{ scoreValue(iteration.metrics.median_score) }}</b></span>
                  </div>
                  <details v-if="iteration.feedback_prompt" class="prompt-audit">
                    <summary>查看发送给大模型的改进提示词</summary>
                    <pre>{{ iteration.feedback_prompt }}</pre>
                  </details>
                  <v-alert v-if="iteration.feedback_response?.diagnosis" type="info" density="compact" variant="tonal" class="mt-3">
                    大模型诊断：{{ iteration.feedback_response.diagnosis }}
                  </v-alert>
                  <v-alert v-if="iteration.error_message" type="warning" density="compact" variant="tonal">{{ iteration.error_message }}</v-alert>
                </v-expansion-panel-text>
              </v-expansion-panel>
            </v-expansion-panels>
          </template>
          <h3 class="table-title">最佳参数</h3>
          <div class="param-grid"><div v-for="(value, key) in detailRun.best_params" :key="key"><span>{{ key }}</span><b>{{ formatParam(value) }}</b></div></div>
          <h3 class="table-title">最佳候选交易流水</h3>
          <v-table density="compact">
            <thead><tr><th>方向</th><th>入场时间</th><th>入场价</th><th>退出时间</th><th>退出价</th><th>原因</th><th>毛收益</th></tr></thead>
            <tbody><tr v-for="trade in detailRun.trades || []" :key="trade.trade_id"><td :class="trade.direction">{{ trade.direction === 'buy' ? '买入' : '卖出' }}</td><td>{{ formatTime(trade.entry_time) }}</td><td>{{ price(trade.entry_price) }}</td><td>{{ formatTime(trade.exit_time) }}</td><td>{{ price(trade.exit_price) }}</td><td>{{ exitLabel(trade.exit_reason) }}</td><td :class="trade.gross_return >= 0 ? 'positive' : 'negative'">{{ signedPercent(trade.gross_return) }}</td></tr></tbody>
          </v-table>
        </v-card-text>
      </v-card>
    </v-dialog>

    <v-dialog v-model="factorLibraryDialog" max-width="980">
      <v-card class="factor-library-dialog">
        <v-card-title><div><span>FACTOR CATALOG</span><h2>因子库</h2></div><v-btn icon="mdi-close" variant="text" @click="factorLibraryDialog = false" /></v-card-title>
        <v-card-text>
          <v-text-field v-model="factorSearch" prepend-inner-icon="mdi-magnify" label="搜索因子名称或分类" variant="outlined" clearable />
          <div class="factor-groups">
            <section v-for="group in filteredFactorGroups" :key="group.name">
              <div class="factor-group-title"><h3>{{ group.name }}</h3><span>{{ group.items.length }} 个</span></div>
              <div class="library-grid">
                <article v-for="factor in group.items" :key="factor.name">
                  <div><b>{{ factor.display_name }}</b><code>{{ factor.name }}</code></div>
                  <p>{{ factor.description }}</p>
                  <span>{{ factor.category_label }} · 输入 {{ factor.inputs.join(' / ') || '价格序列' }}</span>
                </article>
              </div>
            </section>
          </div>
        </v-card-text>
      </v-card>
    </v-dialog>
  </div>
</template>

<script setup>
import { computed, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'
import { marketAPI } from '@/api/market'

const loading = ref(false)
const creating = ref(false)
const generatingCandidates = ref(false)
const message = ref('')
const messageType = ref('success')
const datasets = ref([])
const factors = ref([])
const exitModes = ref([])
const runs = ref([])
const detailDialog = ref(false)
const detailRun = ref(null)
const alphaLibrary = ref([])
const publishing = ref(false)
const publishVisibility = ref('private')
const visibilityOptions = [{ title: '仅自己使用', value: 'private' }, { title: '共享给平台用户', value: 'shared' }]
const factorLibraryDialog = ref(false)
const factorSearch = ref('')
const researchMode = ref('ai')
const candidates = ref([])
const selectedCandidateId = ref('')
let factorKey = 0
let refreshTimer = null

const newFactor = (name = '') => ({ key: ++factorKey, name, lengthMin: 7, lengthMax: 30, weightMin: 0.2, weightMax: 1 })
const form = reactive({
  researchName: '', datasetId: null, timeframe: 'M5', predictionHorizon: 15,
  researchDescription: '',
  factors: [newFactor('ema')], exitMode: 'reverse_signal', fixedHorizonBars: 15,
  confirmationBars: 1, cooldownBars: 0, buyThresholdMin: 0.3,
  buyThresholdMax: 2, sellThresholdMin: -2, sellThresholdMax: -0.3, trialCount: 50,
  llmIterationCount: 3,
  stopLossPercent: 0, takeProfitPercent: 0, trailingStopPercent: 0, maxHoldingBars: 0,
})

const timeframes = ['M1', 'M5', 'M15', 'H1', 'H4']
const llmIterationOptions = [1, 2, 3, 4, 5].map(value => ({ title: `${value} 轮`, value }))
const datasetOptions = computed(() => datasets.value.map(item => ({ title: `${item.dataset_name} · ${item.symbol} · ${item.received_bars.toLocaleString()} 根`, value: item.dataset_id })))
const factorOptions = computed(() => factors.value.map(item => ({ title: `${item.display_name} (${item.label}) · ${item.category_label}`, value: item.name })))
const exitModeOptions = computed(() => exitModes.value.map(item => ({ title: item.label, value: item.value })))
const activeCount = computed(() => runs.value.filter(item => ['queued', 'running'].includes(item.status)).length)
const selectedCandidate = computed(() => candidates.value.find(item => item.candidate_id === selectedCandidateId.value) || null)
const activeFactors = computed(() => researchMode.value === 'ai' ? (selectedCandidate.value?.factors || []) : form.factors)
const canCreate = computed(() => Boolean(form.datasetId && activeFactors.value.length && activeFactors.value.every(item => item.name)))
const filteredFactorGroups = computed(() => {
  const keyword = factorSearch.value.trim().toLowerCase()
  const visible = factors.value.filter(item => !keyword || [item.name, item.display_name, item.category_label, item.research_theme].some(value => String(value).toLowerCase().includes(keyword)))
  const groups = new Map()
  visible.forEach(item => {
    if (!groups.has(item.research_theme)) groups.set(item.research_theme, [])
    groups.get(item.research_theme).push(item)
  })
  return [...groups.entries()].map(([name, items]) => ({ name, items }))
})

function addFactor() { if (form.factors.length < 5) form.factors.push(newFactor()) }
function removeFactor(index) { if (form.factors.length > 1) form.factors.splice(index, 1) }
function normalizedFactor(item) {
  return {
    name: item.name,
    length_min: item.length_min ?? item.lengthMin,
    length_max: item.length_max ?? item.lengthMax,
    weight_min: item.weight_min ?? item.weightMin,
    weight_max: item.weight_max ?? item.weightMax,
  }
}
function payload() {
  return {
    research_name: form.researchName || selectedCandidate.value?.name,
    research_description: form.researchDescription,
    research_mode: researchMode.value,
    llm_iteration_count: form.llmIterationCount,
    candidate_meta: selectedCandidate.value ? {
      name: selectedCandidate.value.name, theme: selectedCandidate.value.theme,
      hypothesis: selectedCandidate.value.hypothesis,
      buy_logic: selectedCandidate.value.buy_logic, sell_logic: selectedCandidate.value.sell_logic,
    } : null,
    dataset_id: form.datasetId, timeframe: form.timeframe,
    prediction_horizon: form.predictionHorizon, exit_mode: form.exitMode,
    fixed_horizon_bars: form.fixedHorizonBars, confirmation_bars: form.confirmationBars,
    cooldown_bars: form.cooldownBars, buy_threshold_min: form.buyThresholdMin,
    buy_threshold_max: form.buyThresholdMax, sell_threshold_min: form.sellThresholdMin,
    sell_threshold_max: form.sellThresholdMax, trial_count: form.trialCount,
    stop_loss_percent: form.stopLossPercent, take_profit_percent: form.takeProfitPercent,
    trailing_stop_percent: form.trailingStopPercent, max_holding_bars: form.maxHoldingBars,
    factors: activeFactors.value.map(normalizedFactor),
  }
}
async function loadAll() {
  loading.value = true
  try {
    const [context, runData] = await Promise.all([marketAPI.getAlphaResearchContext(), marketAPI.getAlphaResearchRuns()])
    datasets.value = context.datasets || []; factors.value = context.factors || []; exitModes.value = context.exit_modes || []; alphaLibrary.value = context.alpha_library || []
    runs.value = runData.runs || []
    if (!form.datasetId && datasets.value.length) form.datasetId = datasets.value[0].dataset_id
  } catch (error) { showError(error, '加载 Alpha 研究数据失败') }
  finally { loading.value = false }
}
async function generateCandidates() {
  generatingCandidates.value = true
  try {
    const data = await marketAPI.generateAlphaCandidates({
      research_description: form.researchDescription,
      timeframe: form.timeframe,
      prediction_horizon: form.predictionHorizon,
      candidate_count: 3,
    })
    candidates.value = data.candidates || []
    selectedCandidateId.value = candidates.value[0]?.candidate_id || ''
    messageType.value = 'success'; message.value = data.message
  } catch (error) { showError(error, '生成 Alpha 候选失败') }
  finally { generatingCandidates.value = false }
}
function selectCandidate(candidate) { selectedCandidateId.value = candidate.candidate_id }
async function createRun() {
  if (!canCreate.value) return
  creating.value = true
  try { const data = await marketAPI.createAlphaResearchRun(payload()); messageType.value = 'success'; message.value = data.message; form.researchName = ''; await loadAll() }
  catch (error) { showError(error, '创建 Alpha 研究任务失败') }
  finally { creating.value = false }
}
async function cancelRun(run) {
  if (!confirm(`确定终止“${run.research_name}”吗？`)) return
  try { const data = await marketAPI.cancelAlphaResearchRun(run.run_id); messageType.value = 'success'; message.value = data.message; await loadAll() }
  catch (error) { showError(error, '终止任务失败') }
}
async function openDetail(run) {
  try { const data = await marketAPI.getAlphaResearchRun(run.run_id); detailRun.value = data.run; detailDialog.value = true }
  catch (error) { showError(error, '加载研究报告失败') }
}
function isRunPublished(runId) { return alphaLibrary.value.some(item => item.source_run_id === runId) }
async function publishRun() {
  if (!detailRun.value) return
  publishing.value = true
  try {
    const data = await marketAPI.publishAlphaResearchRun(detailRun.value.run_id, publishVisibility.value)
    alphaLibrary.value = [...alphaLibrary.value.filter(item => item.alpha_id !== data.alpha.alpha_id), data.alpha]
    messageType.value = 'success'; message.value = data.message
  } catch (error) { showError(error, '发布 Alpha 失败') }
  finally { publishing.value = false }
}
function showError(error, fallback) { messageType.value = 'error'; message.value = error.response?.data?.detail || fallback }
function statusMeta(status) { return ({ queued: { label: '等待中', color: 'warning' }, running: { label: '搜索中', color: 'info' }, completed: { label: '已完成', color: 'success' }, failed: { label: '失败', color: 'error' }, canceled: { label: '已终止', color: 'grey' } })[status] || { label: status, color: 'grey' } }
function exitLabel(mode) { return ({ reverse_signal: '反向信号退出', fixed_horizon: '固定周期退出', neutral_signal: '回到观望退出', stop_loss: '固定止损', take_profit: '固定止盈', trailing_stop: '移动止损', max_holding: '最大持有周期', end_of_data: '数据结束' })[mode] || mode }
function metric(key) { const value = detailRun.value?.result?.metrics?.[key]; return value == null ? '--' : Number(value).toFixed(4) }
function percentMetric(key, child = null) { let value = detailRun.value?.result?.metrics?.[key]; if (child) value = value?.[child]; return value == null ? '--' : `${(Number(value) * 100).toFixed(2)}%` }
function asPercent(value) { return value == null ? '--' : `${(Number(value) * 100).toFixed(2)}%` }
function autocorrelationAt(item, lag) { return item?.autocorrelation?.find(value => value.lag === lag)?.correlation }
function signedPercent(value) { const number = Number(value || 0) * 100; return `${number >= 0 ? '+' : ''}${number.toFixed(2)}%` }
function formatParam(value) { return typeof value === 'number' ? Number(value).toFixed(4).replace(/\.0+$/, '') : value }
function formatTime(value) { return value ? new Date(value * 1000).toLocaleString('zh-CN') : '--' }
function price(value) { return Number(value || 0).toLocaleString('zh-CN', { minimumFractionDigits: 2, maximumFractionDigits: 5 }) }
function scoreValue(value) { return value == null ? '--' : Number(value).toFixed(4) }

watch(
  () => [form.researchDescription, form.timeframe, form.predictionHorizon],
  () => { candidates.value = []; selectedCandidateId.value = '' },
)
onMounted(() => { loadAll(); refreshTimer = window.setInterval(() => { if (activeCount.value) loadAll() }, 3000) })
onBeforeUnmount(() => window.clearInterval(refreshTimer))
</script>

<style scoped>
.alpha-page { min-height: 100%; padding: 28px; color: #19352f; background: radial-gradient(circle at 85% 0, rgba(220, 151, 55, .17), transparent 27%), linear-gradient(135deg, #f7f2e8, #edf4ef 58%, #f8f5ed); }
.alpha-hero { display: flex; justify-content: space-between; gap: 28px; align-items: flex-end; padding: 30px 34px; margin-bottom: 22px; border-radius: 24px; color: #fff; background: linear-gradient(120deg, #123f37, #1d6a57 65%, #a97832); box-shadow: 0 18px 45px rgba(25, 61, 52, .18); }
.eyebrow { font-size: 11px; letter-spacing: .22em; color: #e6c98e; font-weight: 800; }.alpha-hero h1 { margin: 5px 0 6px; font-family: Georgia, serif; font-size: clamp(30px, 4vw, 46px); }.alpha-hero p { margin: 0; opacity: .82; }
.hero-badge { display: flex; align-items: center; gap: 13px; min-width: 270px; padding: 14px 18px; border: 1px solid rgba(255,255,255,.18); border-radius: 16px; background: rgba(255,255,255,.09); }.hero-badge div { display: flex; flex-direction: column; }.hero-badge span { font-size: 12px; opacity: .72; }
.research-grid { display: grid; grid-template-columns: minmax(0, 1fr) 250px; gap: 20px; }.research-form, .metric-card, .guide-card { border: 1px solid rgba(32, 78, 65, .1); border-radius: 20px; background: rgba(255,255,255,.82); }.research-form { padding: 24px; }
.section-heading { display: flex; align-items: center; justify-content: space-between; margin-bottom: 22px; }.section-heading > div { display: flex; align-items: center; gap: 10px; }.section-heading span { display: grid; place-items: center; width: 30px; height: 30px; border-radius: 9px; color: #fff; background: #1c6755; font-size: 12px; font-weight: 800; }.section-heading h2 { margin: 0; font-family: Georgia, serif; }
.research-mode { width: 100%; margin-bottom: 20px; border: 1px solid #d8e5de; border-radius: 12px; background: #f1f6f3; }.research-mode .v-btn { flex: 1; }
.field-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 2px 14px; }.subheading { display: flex; align-items: center; justify-content: space-between; margin: 8px 0 16px; padding-top: 16px; border-top: 1px solid #e0e8e3; }.subheading.compact { margin-top: 22px; }.subheading h3, .subheading p { margin: 0; }.subheading p { margin-top: 3px; color: #71827c; font-size: 13px; }
.generate-row { display: flex; justify-content: space-between; gap: 18px; align-items: center; margin-top: -8px; }.generate-row span { color: #71817c; font-size: 12px; }.candidate-grid { display: grid; grid-template-columns: repeat(3, 1fr); gap: 11px; margin-top: 18px; }.candidate-card { appearance: none; padding: 16px; text-align: left; color: #223e37; border: 1px solid #d9e4df; border-radius: 15px; background: #fff; cursor: pointer; transition: transform .18s ease, border-color .18s ease, box-shadow .18s ease; }.candidate-card:hover { transform: translateY(-2px); }.candidate-card.selected { border-color: #23715c; box-shadow: 0 0 0 2px rgba(35, 113, 92, .12), 0 10px 22px rgba(26, 76, 63, .1); }.candidate-head { display: flex; justify-content: space-between; align-items: center; color: #24705c; font-size: 11px; font-weight: 800; }.candidate-card h4 { margin: 8px 0 5px; font-size: 16px; }.candidate-card > p { min-height: 54px; margin: 0; color: #667a73; font-size: 12px; line-height: 1.5; }.candidate-factors { display: flex; flex-wrap: wrap; gap: 5px; margin: 11px 0; }.candidate-factors span { display: flex; flex-direction: column; padding: 5px 7px; color: #77551f; border-radius: 7px; background: #f5ead5; font-size: 10px; font-weight: 700; }.candidate-factors small { color: #9b8259; font-size: 8px; }.candidate-logic { display: grid; grid-template-columns: 20px 1fr; gap: 4px 6px; color: #62766f; font-size: 10px; }.candidate-logic b { color: #1f6a56; }
.iteration-select { margin-top: 18px; }.budget-note { margin: -8px 0 18px; color: #6c756f; font-size: 12px; text-align: right; }
.factor-list { display: grid; gap: 10px; }.factor-card { display: grid; grid-template-columns: 40px minmax(150px, 1.5fr) repeat(4, minmax(90px, 1fr)) 40px; gap: 9px; align-items: center; padding: 13px; border-radius: 15px; background: #f2f6f2; }.factor-index { display: grid; place-items: center; height: 36px; color: #1a6552; border-radius: 10px; background: #dcece4; font-weight: 800; }.factor-card :deep(.v-input__details) { display: none; }
.protection-panel { margin: 3px 0 16px; padding: 15px; border: 1px solid #dce6e1; border-radius: 15px; background: #f4f7f5; }.protection-title { display: flex; gap: 10px; align-items: flex-start; margin-bottom: 12px; }.protection-title .v-icon { color: #24705c; }.protection-title h4, .protection-title p { margin: 0; }.protection-title p { color: #778680; font-size: 11px; }.protection-grid { display: grid; grid-template-columns: repeat(4, 1fr); gap: 10px; }.protection-grid :deep(.v-input__details) { display: none; }
.threshold-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 14px; }.threshold-grid label { display: block; margin-bottom: 7px; color: #61736d; font-size: 13px; font-weight: 700; }.threshold-grid > div > div { display: flex; align-items: center; gap: 8px; }.threshold-grid :deep(.v-input__details) { display: none; }.run-strip { display: grid; grid-template-columns: 170px 1fr 45px; gap: 14px; align-items: center; margin: 19px 0; padding: 14px 16px; border-radius: 14px; background: #f3eee2; }.run-strip > div { display: flex; gap: 9px; align-items: center; }.run-strip strong { text-align: right; color: #a36e25; font-size: 20px; }
.research-aside { display: grid; gap: 14px; align-content: start; }.metric-card { display: grid; padding: 20px; }.metric-card strong { font-family: Georgia, serif; font-size: 38px; color: #1b6553; }.metric-card small { color: #83918d; }.metric-card.accent strong { color: #b57725; }.guide-card { padding: 22px; color: #fff; background: linear-gradient(145deg, #1d4b42, #16362f); }.guide-card > .v-icon { color: #e3b863; }.guide-card h3 { margin: 12px 0; }.guide-card p { margin: 9px 0; font-size: 13px; opacity: .8; }
.runs-section { margin-top: 24px; padding: 24px; border: 1px solid rgba(32, 78, 65, .1); border-radius: 22px; background: rgba(255,255,255,.72); }.run-list { display: grid; gap: 12px; }.run-card { display: grid; grid-template-columns: minmax(280px, 1.5fr) minmax(180px, 1fr) 120px auto; gap: 18px; align-items: center; padding: 18px; border: 1px solid #dfe8e3; border-radius: 16px; background: #fff; }.run-main { display: flex; gap: 13px; align-items: flex-start; }.run-icon { display: grid; place-items: center; min-width: 42px; height: 42px; border-radius: 12px; color: #1c6755; background: #e3f0e9; }.run-title { display: flex; gap: 8px; align-items: center; }.run-title h3, .run-main p { margin: 0; }.run-main p { color: #72827c; font-size: 13px; }.factor-tags { display: flex; flex-wrap: wrap; gap: 5px; margin-top: 7px; }.factor-tags span { padding: 3px 7px; border-radius: 5px; color: #806029; background: #f4ead7; font-size: 10px; font-weight: 800; }.run-progress > div { display: flex; justify-content: space-between; margin-bottom: 7px; font-size: 12px; }.run-score { display: flex; flex-direction: column; }.run-score strong { color: #1a6654; font-size: 23px; }.run-score small { color: #84928d; }.run-actions { display: flex; flex-direction: column; gap: 4px; }.run-error { grid-column: 1 / -1; }.empty-state { padding: 48px; text-align: center; color: #71837d; }.empty-state h3 { margin: 10px 0 3px; }
.detail-dialog { border-radius: 22px !important; }.detail-dialog .v-card-title { display: flex; justify-content: space-between; align-items: center; padding: 22px 26px; color: #fff; background: #194e43; }.detail-dialog .v-card-title span { color: #dfbe78; font-size: 10px; letter-spacing: .18em; }.detail-dialog .v-card-title h2 { margin: 2px 0 0; font-family: Georgia, serif; }.detail-dialog .v-card-text { padding: 24px; }.report-metrics { display: grid; grid-template-columns: repeat(6, 1fr); gap: 10px; }.report-metrics > div { display: flex; flex-direction: column; padding: 14px; border-radius: 12px; background: #f0f5f2; }.report-metrics span { color: #788781; font-size: 11px; }.report-metrics strong { margin-top: 4px; color: #195c4d; font-size: 19px; }.param-grid { display: grid; grid-template-columns: repeat(3, 1fr); gap: 8px; }.param-grid > div { display: flex; justify-content: space-between; gap: 8px; padding: 10px 12px; border-radius: 9px; background: #f5f2eb; font-size: 12px; }.table-title { margin: 20px 0 10px; }.buy, .positive { color: #16845f; font-weight: 700; }.sell, .negative { color: #c34e45; font-weight: 700; }
.publish-panel { display: flex; justify-content: space-between; gap: 20px; align-items: center; margin-bottom: 18px; padding: 15px; border: 1px solid #dbe8e1; border-radius: 14px; background: #f3f8f5; }.publish-panel p { margin: 6px 0 0; color: #708079; font-size: 12px; }.publish-actions { display: grid; grid-template-columns: 170px auto; gap: 10px; align-items: center; }.admission-grid { display: grid; grid-template-columns: repeat(3, 1fr); gap: 8px; }.admission-grid > div { display: grid; grid-template-columns: auto 1fr; gap: 2px 8px; padding: 11px; border-radius: 10px; color: #9c4d42; background: #fbefed; }.admission-grid > div.passed { color: #1b6b53; background: #eaf5ef; }.admission-grid b { grid-column: 2; font-size: 11px; }
.report-level { display: flex; align-items: center; gap: 8px; margin: 18px 0 9px; color: #214d42; }.report-level:first-child { margin-top: 0; }.report-level span { padding: 4px 7px; border-radius: 6px; color: #fff; background: #b47b2b; font-size: 10px; letter-spacing: .08em; }.decay-table { border: 1px solid #e0e8e3; border-radius: 12px; }
.iteration-title { display: flex; align-items: center; gap: 10px; width: 100%; }.iteration-title span { color: #315c51; }.iteration-title small { margin-left: auto; color: #788781; }.iteration-hypothesis { color: #546b64; }.expression-block { display: block; padding: 13px; overflow-x: auto; border-radius: 10px; color: #275c4e; background: #edf4ef; white-space: pre-wrap; }.split-summary { display: grid; grid-template-columns: repeat(4, 1fr); gap: 8px; margin: 12px 0; }.split-summary span { padding: 9px; border-radius: 8px; background: #f5f2eb; font-size: 12px; }.prompt-audit { margin-top: 12px; }.prompt-audit summary { color: #216653; cursor: pointer; font-weight: 700; }.prompt-audit pre { max-height: 320px; padding: 12px; overflow: auto; border-radius: 9px; background: #172923; color: #e5eee9; font-size: 11px; white-space: pre-wrap; }
.factor-library-dialog { border-radius: 22px !important; }.factor-library-dialog .v-card-title { display: flex; justify-content: space-between; align-items: center; padding: 22px 26px; color: #fff; background: linear-gradient(120deg, #194e43, #276b59); }.factor-library-dialog .v-card-title span { color: #dfbe78; font-size: 10px; letter-spacing: .18em; }.factor-library-dialog .v-card-title h2 { margin: 2px 0 0; font-family: Georgia, serif; }.factor-library-dialog .v-card-text { max-height: 72vh; padding: 22px; overflow-y: auto; }.factor-groups { display: grid; gap: 22px; }.factor-group-title { display: flex; justify-content: space-between; align-items: center; margin-bottom: 9px; }.factor-group-title h3 { margin: 0; }.factor-group-title span { color: #778780; font-size: 12px; }.library-grid { display: grid; grid-template-columns: repeat(3, 1fr); gap: 9px; }.library-grid article { padding: 12px; border: 1px solid #e0e7e3; border-radius: 11px; background: #f9faf8; }.library-grid article > div { display: flex; justify-content: space-between; gap: 8px; }.library-grid code { color: #9a6b28; font-size: 10px; }.library-grid p { margin: 7px 0; color: #667871; font-size: 11px; }.library-grid article > span { color: #89958f; font-size: 9px; }
@media (max-width: 1100px) { .research-grid { grid-template-columns: 1fr; }.research-aside { grid-template-columns: 1fr 1fr 2fr; }.candidate-grid { grid-template-columns: 1fr 1fr; }.factor-card { grid-template-columns: 40px 1fr 1fr 1fr; }.factor-card .factor-name { grid-column: span 3; }.protection-grid { grid-template-columns: 1fr 1fr; }.run-card { grid-template-columns: 1fr 1fr; }.report-metrics { grid-template-columns: repeat(3, 1fr); } }
@media (max-width: 700px) { .alpha-page { padding: 14px; }.alpha-hero { align-items: flex-start; flex-direction: column; padding: 23px; }.hero-badge { min-width: 0; width: 100%; }.field-grid, .threshold-grid, .candidate-grid, .protection-grid, .library-grid { grid-template-columns: 1fr; }.generate-row { align-items: stretch; flex-direction: column; }.research-aside { grid-template-columns: 1fr 1fr; }.guide-card { grid-column: 1 / -1; }.factor-card { grid-template-columns: 36px 1fr 36px; }.factor-card .factor-name { grid-column: auto; }.factor-card > .v-input:not(.factor-name) { grid-column: span 3; }.run-card { grid-template-columns: 1fr; }.run-actions { flex-direction: row; }.report-metrics, .param-grid, .split-summary { grid-template-columns: repeat(2, 1fr); }.iteration-title { align-items: flex-start; flex-direction: column; }.iteration-title small { margin-left: 0; }.run-strip { grid-template-columns: 1fr 45px; }.run-strip .v-slider { grid-column: 1 / -1; grid-row: 2; } }
</style>

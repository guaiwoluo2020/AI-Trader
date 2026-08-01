<template>
  <div class="backtest-page">
    <section class="workbench-hero">
      <div>
        <div class="eyebrow">STRATEGY REPLAY WORKBENCH</div>
        <h1>回测任务</h1>
        <p>用可复用模板固化交易成本和仓位假设，每次运行自动保存策略快照并生成独立任务。</p>
      </div>
      <v-btn
        color="white"
        variant="outlined"
        prepend-icon="mdi-refresh"
        :loading="loading"
        @click="loadAll"
      >
        刷新
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
      v-if="!context.datasets.length"
      type="info"
      variant="tonal"
      class="mb-5"
    >
      当前没有已就绪的历史数据集。请先前往
      <router-link to="/backtest-datasets">回测数据集</router-link>
      完成数据采集。
    </v-alert>

    <section class="metric-grid">
      <article><span>模板</span><strong>{{ templates.length }}</strong></article>
      <article><span>运行批次</span><strong>{{ batches.length }}</strong></article>
      <article><span>等待任务</span><strong>{{ queuedTasks }}</strong></article>
      <article><span>策略快照</span><strong>{{ snapshotCount }}</strong></article>
    </section>

    <section class="workspace-grid">
      <v-card class="editor-card" elevation="0">
        <v-card-text>
          <div class="section-tag">{{ editingId ? 'EDIT TEMPLATE' : 'NEW TEMPLATE' }}</div>
          <div class="editor-heading">
            <h2>{{ editingId ? '编辑回测模板' : '建立回测模板' }}</h2>
            <v-btn v-if="editingId" size="small" variant="text" @click="resetForm">取消编辑</v-btn>
          </div>

          <v-form @submit.prevent="saveTemplate">
            <v-text-field
              v-model.trim="form.templateName"
              label="模板名称"
              placeholder="例如：GOLD 转折策略标准回测"
              variant="outlined"
              density="comfortable"
              class="mt-4"
            />
            <v-select
              v-model="form.strategyId"
              :items="strategyOptions"
              item-title="label"
              item-value="value"
              label="策略"
              variant="outlined"
              density="comfortable"
              @update:model-value="removeIncompatibleDatasets"
            />
            <v-select
              v-model="form.datasetIds"
              :items="datasetOptions"
              item-title="label"
              item-value="value"
              label="历史数据集"
              hint="每个数据集会生成一个独立回测任务"
              persistent-hint
              multiple
              chips
              closable-chips
              variant="outlined"
              density="comfortable"
            />
            <v-textarea
              v-model.trim="form.description"
              label="说明"
              rows="2"
              variant="outlined"
              density="comfortable"
              class="mt-2"
            />

            <div class="field-grid">
              <v-text-field v-model.number="form.initialCapital" label="初始资金" type="number" min="1" variant="outlined" density="comfortable" />
              <v-select v-model="form.positionSizingMode" :items="positionModes" label="仓位模式" variant="outlined" density="comfortable" />
              <v-text-field v-model.number="form.fixedVolume" label="固定手数" type="number" min="0.01" step="0.01" variant="outlined" density="comfortable" />
              <v-text-field v-model.number="form.riskPercent" label="单笔风险 %" type="number" min="0.01" max="100" step="0.1" variant="outlined" density="comfortable" />
              <v-text-field v-model.number="form.spreadPoints" label="点差（点）" type="number" min="0" variant="outlined" density="comfortable" />
              <v-text-field v-model.number="form.slippagePoints" label="滑点（点）" type="number" min="0" variant="outlined" density="comfortable" />
              <v-text-field v-model.number="form.commissionPerLot" label="每手手续费" type="number" min="0" variant="outlined" density="comfortable" />
              <v-text-field v-model.number="form.maxPositions" label="最大持仓数" type="number" min="1" max="100" variant="outlined" density="comfortable" />
            </div>
            <v-switch
              v-model="form.useStrategyExits"
              color="success"
              inset
              label="使用策略中的止盈止损规则"
              hide-details
            />
            <v-switch
              v-model="form.isShared"
              color="success"
              inset
              label="共享给其他用户"
              hint="其他用户可以使用模板发起自己的回测，但不能修改或删除模板"
              persistent-hint
              @update:model-value="removeIncompatibleDatasets"
            />

            <v-btn
              type="submit"
              color="primary"
              size="large"
              block
              :loading="saving"
              :disabled="!canSave"
              :prepend-icon="editingId ? 'mdi-content-save-outline' : 'mdi-plus-box-outline'"
              class="mt-4"
            >
              {{ editingId ? '保存模板修改' : '创建回测模板' }}
            </v-btn>
          </v-form>
        </v-card-text>
      </v-card>

      <v-card class="catalog-card" elevation="0">
        <v-card-text>
          <div class="section-tag">REUSABLE RECIPES</div>
          <h2>回测模板</h2>

          <div v-if="!templates.length && !loading" class="empty-state">
            <v-icon icon="mdi-file-document-plus-outline" size="48" />
            <h3>还没有模板</h3>
            <p>建立模板后，策略每次修改都可以快速生成新的回测批次。</p>
          </div>

          <div v-else class="template-list">
            <article v-for="template in templates" :key="template.template_id" class="template-card">
              <div class="template-topline">
                <div>
                  <h3>{{ template.template_name }}</h3>
                  <span>
                    {{ template.strategy_name }} · {{ template.strategy_symbol }}
                    <template v-if="!template.is_owner"> · 创建者 {{ template.creator_username }}</template>
                  </span>
                </div>
                <div class="template-chips">
                  <v-chip
                    :prepend-icon="template.visibility === 'shared' ? 'mdi-account-group-outline' : 'mdi-lock-outline'"
                    :color="template.visibility === 'shared' ? 'teal' : 'grey'"
                    variant="tonal"
                    size="small"
                  >
                    {{ template.visibility === 'shared' ? '共享' : '私有' }}
                  </v-chip>
                  <v-chip color="teal" variant="tonal" size="small">
                    {{ template.dataset_ids.length }} 个任务
                  </v-chip>
                </div>
              </div>
              <p v-if="template.description" class="template-description">{{ template.description }}</p>
              <div class="assumption-row">
                <span>资金 <b>{{ money(template.initial_capital) }}</b></span>
                <span>点差 <b>{{ template.spread_points }}</b></span>
                <span>滑点 <b>{{ template.slippage_points }}</b></span>
                <span>手续费 <b>{{ template.commission_per_lot }}</b></span>
              </div>
              <div class="dataset-tags">
                <v-chip
                  v-for="dataset in template.datasets"
                  :key="dataset.dataset_id"
                  size="x-small"
                  :color="dataset.available === false ? 'error' : 'grey'"
                  variant="tonal"
                >
                  {{ dataset.dataset_name || '数据集不可用' }}
                </v-chip>
              </div>
              <div class="template-actions">
                <v-btn v-if="template.can_manage" size="small" variant="text" prepend-icon="mdi-pencil-outline" @click="editTemplate(template)">编辑</v-btn>
                <v-btn v-if="template.can_manage" size="small" variant="text" color="error" prepend-icon="mdi-delete-outline" @click="deleteTemplate(template)">删除</v-btn>
                <v-spacer />
                <v-btn
                  size="small"
                  color="primary"
                  prepend-icon="mdi-play"
                  :loading="runningId === template.template_id"
                  @click="runTemplate(template)"
                >
                  发起回测
                </v-btn>
              </div>
            </article>
          </div>
        </v-card-text>
      </v-card>
    </section>

    <v-card class="batch-card" elevation="0">
      <v-card-text>
        <div class="batch-heading">
          <div>
            <div class="section-tag">IMMUTABLE RUN HISTORY</div>
            <h2>回测批次</h2>
          </div>
          <span>策略与模板参数已在发起时固化</span>
        </div>

        <div v-if="!batches.length && !loading" class="empty-state compact">
          <p>从模板发起第一次回测后，批次与任务会显示在这里。</p>
        </div>
        <div v-else class="batch-list">
          <article v-for="batch in batches" :key="batch.batch_id" class="batch-item">
            <div class="batch-summary">
              <div class="batch-icon"><v-icon icon="mdi-layers-triple-outline" /></div>
              <div class="batch-name">
                <h3>{{ batch.batch_name }}</h3>
                <span>{{ batch.strategy_name }} · 快照 {{ batch.strategy_snapshot_hash }}</span>
              </div>
              <div class="batch-count"><strong>{{ batch.task_count }}</strong><span>任务</span></div>
              <v-chip :color="statusMeta(batch.status).color" variant="tonal" size="small">
                {{ statusMeta(batch.status).label }}
              </v-chip>
              <v-btn size="small" variant="text" @click="toggleBatch(batch)">
                {{ batchDetails[batch.batch_id] ? '收起' : '查看任务' }}
              </v-btn>
            </div>
            <div v-if="batchDetails[batch.batch_id]" class="task-list">
              <div v-for="task in batchDetails[batch.batch_id].tasks" :key="task.task_id" class="task-entry">
                <div class="task-row">
                  <div>
                    <strong>{{ task.dataset.dataset_name }}</strong>
                    <span>{{ task.dataset.symbol }} · {{ formatDate(task.dataset.requested_start) }} 至 {{ formatDate(task.dataset.requested_end) }}</span>
                  </div>
                  <span>{{ formatNumber(task.dataset.received_bars) }} 根K线</span>
                  <v-chip :color="statusMeta(task.status).color" variant="tonal" size="x-small">
                    {{ statusMeta(task.status).label }}
                  </v-chip>
                </div>
                <v-progress-linear
                  v-if="task.status === 'running'"
                  :model-value="task.progress"
                  color="teal"
                  height="5"
                />
                <div v-if="task.status === 'failed'" class="task-error">
                  <v-icon icon="mdi-alert-circle-outline" size="16" />
                  {{ task.error_message || '回测执行失败' }}
                </div>
                <div v-if="task.status === 'completed'" class="result-panel">
                  <div class="result-metrics">
                    <span>净收益<strong :class="Number(task.result.net_profit) >= 0 ? 'positive' : 'negative'">{{ signedMoney(task.result.net_profit) }}</strong></span>
                    <span>收益率<strong>{{ formatPercent(task.result.total_return_pct) }}</strong></span>
                    <span>最大回撤<strong>{{ formatPercent(task.result.max_drawdown_pct) }}</strong></span>
                    <span>胜率<strong>{{ formatPercent(task.result.win_rate_pct) }}</strong></span>
                    <span>交易次数<strong>{{ task.result.trade_count || 0 }}</strong></span>
                    <span>订单数<strong>{{ task.result.order_count || 0 }}</strong></span>
                    <span>最大并发<strong>{{ task.result.max_concurrent_positions || 0 }}</strong></span>
                    <span>LLM 分析<strong>{{ task.result.llm_analysis_count || 0 }}</strong></span>
                    <span>缓存命中<strong>{{ task.result.llm_cache_hits || 0 }}</strong></span>
                  </div>
                  <div class="result-meta">
                    引擎 {{ task.result.engine_version || task.engine_version || '--' }}
                    · Point {{ task.result.point_size ?? '--' }}
                    · Contract {{ formatNumber(task.result.contract_size) }}
                  </div>
                  <div class="signal-source-row">
                    <span>信号来源</span>
                    <v-chip
                      v-for="source in task.result.enabled_signal_sources || []"
                      :key="source"
                      size="x-small"
                      color="teal"
                      variant="tonal"
                    >
                      {{ sourceLabel(source) }}
                      · {{ task.result.signal_source_trade_counts?.[source] || 0 }} 笔
                    </v-chip>
                    <v-spacer />
                    <v-btn
                      size="x-small"
                      variant="tonal"
                      color="primary"
                      prepend-icon="mdi-chart-areaspline"
                      @click="openReport(task)"
                    >
                      查看回测报告
                    </v-btn>
                    <v-btn
                      size="x-small"
                      variant="tonal"
                      color="teal"
                      prepend-icon="mdi-book-open-variant"
                      :loading="ledgerLoadingId === task.task_id"
                      @click="toggleTaskLedger(task)"
                    >
                      {{ taskLedgers[task.task_id] ? '收起模拟账本' : '查看模拟账本' }}
                    </v-btn>
                  </div>
                  <div v-if="taskLedgers[task.task_id]" class="ledger-panel">
                    <div v-if="taskLedgers[task.task_id].account" class="ledger-summary">
                      <span>初始资金<strong>{{ money(taskLedgers[task.task_id].account.initial_balance) }}</strong></span>
                      <span>最终余额<strong>{{ money(taskLedgers[task.task_id].account.balance) }}</strong></span>
                      <span>最终净值<strong>{{ money(taskLedgers[task.task_id].account.equity) }}</strong></span>
                      <span>成交持仓<strong>{{ taskLedgers[task.task_id].positions.length }}</strong></span>
                    </div>
                    <div class="ledger-title">
                      <strong>订单流水</strong>
                      <span>共 {{ taskLedgers[task.task_id].orders.length }} 笔，展示最近 50 笔</span>
                    </div>
                    <div v-if="!taskLedgers[task.task_id].orders.length" class="ledger-empty">本次回测未生成订单</div>
                    <div v-else class="order-list">
                      <div
                        v-for="order in taskLedgers[task.task_id].orders.slice(-50).reverse()"
                        :key="order.order_id"
                        class="order-row"
                      >
                        <span>{{ formatTime(order.requested_at) }}</span>
                        <b :class="order.direction === 'buy' ? 'positive' : 'negative'">
                          {{ order.direction === 'buy' ? '买入' : '卖出' }}
                        </b>
                        <span>{{ sourceLabel(order.signal_source) }}</span>
                        <span>{{ order.filled_volume || order.requested_volume }} 手</span>
                        <span>{{ order.filled_price ?? order.requested_price }}</span>
                        <v-chip :color="orderStatusMeta(order.status).color" variant="tonal" size="x-small">
                          {{ orderStatusMeta(order.status).label }}
                        </v-chip>
                        <span class="order-reason">{{ order.rejection_reason || '--' }}</span>
                      </div>
                    </div>
                  </div>
                  <v-alert
                    v-for="warning in task.result.warnings || []"
                    :key="warning"
                    type="warning"
                    variant="tonal"
                    density="compact"
                    class="mt-2"
                  >{{ warning }}</v-alert>
                </div>
              </div>
            </div>
          </article>
        </div>
      </v-card-text>
    </v-card>

    <v-dialog v-model="reportDialog" max-width="1180" scrollable @after-enter="renderReportChart">
      <v-card v-if="reportTask" class="report-dialog" elevation="0">
        <v-card-title class="report-header">
          <div>
            <div class="section-tag">BACKTEST PERFORMANCE REPORT</div>
            <h2>策略回测报告</h2>
            <span>{{ reportTask.dataset.dataset_name }} · {{ reportTask.dataset.symbol }}</span>
          </div>
          <div class="report-header-actions">
            <v-chip color="teal" variant="tonal" prepend-icon="mdi-check-decagram-outline">
              {{ reportTask.result.engine_version || reportTask.engine_version }}
            </v-chip>
            <v-btn
              color="teal"
              variant="flat"
              prepend-icon="mdi-flask-plus-outline"
              @click="openDeploymentDialog"
            >
              部署到模拟账户
            </v-btn>
            <v-btn icon="mdi-close" variant="text" @click="closeReport" />
          </div>
        </v-card-title>
        <v-divider />
        <v-card-text class="report-body">
          <section class="report-hero-metrics">
            <article>
              <span>净收益</span>
              <strong :class="Number(reportTask.result.net_profit) >= 0 ? 'positive' : 'negative'">
                {{ signedMoney(reportTask.result.net_profit) }}
              </strong>
              <small>{{ formatPercent(reportTask.result.total_return_pct) }}</small>
            </article>
            <article><span>最大回撤</span><strong>{{ formatPercent(reportTask.result.max_drawdown_pct) }}</strong><small>{{ money(reportTask.result.max_drawdown_amount) }}</small></article>
            <article><span>Profit Factor</span><strong>{{ reportValue(reportTask.result.profit_factor) }}</strong><small>总盈利 / 总亏损</small></article>
            <article><span>夏普比率</span><strong>{{ reportValue(reportTask.result.sharpe_ratio) }}</strong><small>UTC 日收益年化</small></article>
          </section>

          <section class="report-section">
            <div class="report-section-title">
              <div><span>CAPITAL & RISK</span><h3>资金与回撤曲线</h3></div>
              <small>净值按左轴，回撤按右轴</small>
            </div>
            <div v-if="reportTask.result.equity_curve?.length" ref="reportChart" class="report-chart" />
            <div v-else class="report-empty">该历史任务没有可展示的资金曲线</div>
          </section>

          <section class="report-detail-grid">
            <article><span>交易次数</span><strong>{{ reportTask.result.trade_count || 0 }}</strong></article>
            <article><span>胜率</span><strong>{{ formatPercent(reportTask.result.win_rate_pct) }}</strong></article>
            <article><span>单笔期望</span><strong>{{ signedMoney(reportTask.result.expectancy) }}</strong></article>
            <article><span>平均盈亏比</span><strong>{{ reportValue(reportTask.result.payoff_ratio) }}</strong></article>
            <article><span>恢复因子</span><strong>{{ reportValue(reportTask.result.recovery_factor) }}</strong></article>
            <article><span>平均盈利</span><strong class="positive">{{ signedMoney(reportTask.result.average_win) }}</strong></article>
            <article><span>平均亏损</span><strong class="negative">-{{ money(reportTask.result.average_loss) }}</strong></article>
            <article><span>最大单笔盈利</span><strong class="positive">{{ signedMoney(reportTask.result.largest_win) }}</strong></article>
            <article><span>最大单笔亏损</span><strong class="negative">-{{ money(reportTask.result.largest_loss) }}</strong></article>
            <article><span>最大连续盈利</span><strong>{{ reportTask.result.max_consecutive_wins ?? '--' }} 笔</strong></article>
            <article><span>最大连续亏损</span><strong>{{ reportTask.result.max_consecutive_losses ?? '--' }} 笔</strong></article>
            <article><span>平均持仓</span><strong>{{ durationLabel(reportTask.result.average_holding_minutes) }}</strong></article>
            <article><span>手续费合计</span><strong>{{ money(reportTask.result.total_commission) }}</strong></article>
            <article><span>订单 / 成交</span><strong>{{ reportTask.result.order_count || 0 }} / {{ reportTask.result.trade_count || 0 }}</strong></article>
          </section>

          <section class="report-breakdowns">
            <div class="breakdown-card">
              <h3>信号来源归因</h3>
              <div v-if="!reportTask.result.signal_source_stats?.length" class="report-empty compact">暂无数据</div>
              <div v-for="item in reportTask.result.signal_source_stats || []" :key="item.signal_source" class="breakdown-row">
                <strong>{{ sourceLabel(item.signal_source) }}</strong><span>{{ item.trade_count }} 笔</span><span>胜率 {{ formatPercent(item.win_rate_pct) }}</span><b :class="item.net_profit >= 0 ? 'positive' : 'negative'">{{ signedMoney(item.net_profit) }}</b>
              </div>
            </div>
            <div class="breakdown-card">
              <h3>交易方向归因</h3>
              <div v-if="!reportTask.result.direction_stats?.length" class="report-empty compact">暂无数据</div>
              <div v-for="item in reportTask.result.direction_stats || []" :key="item.direction" class="breakdown-row">
                <strong>{{ directionLabel(item.direction) }}</strong><span>{{ item.trade_count }} 笔</span><span>胜率 {{ formatPercent(item.win_rate_pct) }}</span><b :class="item.net_profit >= 0 ? 'positive' : 'negative'">{{ signedMoney(item.net_profit) }}</b>
              </div>
            </div>
            <div class="breakdown-card">
              <h3>退出原因归因</h3>
              <div v-if="!reportTask.result.exit_reason_stats?.length" class="report-empty compact">暂无数据</div>
              <div v-for="item in reportTask.result.exit_reason_stats || []" :key="item.exit_reason" class="breakdown-row">
                <strong>{{ exitReasonLabel(item.exit_reason) }}</strong><span>{{ item.trade_count }} 笔</span><span>胜率 {{ formatPercent(item.win_rate_pct) }}</span><b :class="item.net_profit >= 0 ? 'positive' : 'negative'">{{ signedMoney(item.net_profit) }}</b>
              </div>
            </div>
          </section>

          <section class="report-section">
            <div class="report-section-title"><div><span>MONTHLY ATTRIBUTION</span><h3>月度表现</h3></div></div>
            <div v-if="!reportTask.result.monthly_stats?.length" class="report-empty">暂无月度成交数据</div>
            <div v-else class="monthly-grid">
              <article v-for="item in reportTask.result.monthly_stats" :key="item.month">
                <span>{{ item.month }}</span>
                <strong :class="item.net_profit >= 0 ? 'positive' : 'negative'">{{ signedMoney(item.net_profit) }}</strong>
                <small>{{ formatPercent(item.return_pct) }} · {{ item.trade_count }} 笔</small>
              </article>
            </div>
          </section>
        </v-card-text>
      </v-card>
    </v-dialog>

    <v-dialog v-model="deploymentDialog" max-width="560">
      <v-card class="deployment-dialog" elevation="0">
        <v-card-title class="deployment-dialog-header">
          <div>
            <div class="section-tag">PAPER VALIDATION</div>
            <h2>部署回测策略快照</h2>
            <span>模拟运行会锁定本次回测使用的策略参数</span>
          </div>
          <v-btn icon="mdi-close" variant="text" @click="deploymentDialog = false" />
        </v-card-title>
        <v-divider />
        <v-card-text class="deployment-dialog-body">
          <v-alert v-if="!paperAccounts.length" type="info" variant="tonal" class="mb-4">
            当前没有可用的模拟账户，请先前往
            <router-link to="/accounts">交易账户</router-link>
            创建一个模拟账户。
          </v-alert>
          <v-select
            v-model="deploymentAccountId"
            :items="paperAccountOptions"
            item-title="label"
            item-value="value"
            label="模拟账户"
            variant="outlined"
            prepend-inner-icon="mdi-wallet-outline"
          />
          <v-text-field
            v-model.number="deploymentDays"
            type="number"
            min="1"
            max="365"
            label="模拟运行期限（天）"
            hint="到期后策略自动停止产生新订单，已有持仓继续按止盈止损撮合"
            persistent-hint
            variant="outlined"
            prepend-inner-icon="mdi-calendar-clock-outline"
          />
          <div class="snapshot-note">
            <v-icon icon="mdi-lock-check-outline" size="20" />
            <div><strong>不可变策略快照</strong><span>后续编辑当前策略不会改变这次模拟运行参数；如版本发生变化，需要重新回测后部署。</span></div>
          </div>
        </v-card-text>
        <v-card-actions class="deployment-dialog-actions">
          <v-btn variant="text" @click="deploymentDialog = false">取消</v-btn>
          <v-btn
            color="teal"
            variant="flat"
            prepend-icon="mdi-rocket-launch-outline"
            :disabled="!deploymentAccountId || deploymentDays < 1 || deploymentDays > 365"
            :loading="deployingBacktest"
            @click="deployBacktest"
          >
            开始模拟运行
          </v-btn>
        </v-card-actions>
      </v-card>
    </v-dialog>
  </div>
</template>

<script setup>
import { computed, nextTick, onBeforeUnmount, reactive, ref } from 'vue'
import * as echarts from 'echarts'
import { marketAPI } from '../api/market'
import { accountAPI } from '../api/trading'

const templates = ref([])
const batches = ref([])
const context = reactive({ strategies: [], datasets: [] })
const batchDetails = reactive({})
const taskLedgers = reactive({})
const loading = ref(false)
const saving = ref(false)
const runningId = ref('')
const editingId = ref('')
const ledgerLoadingId = ref('')
const reportDialog = ref(false)
const reportTask = ref(null)
const reportChart = ref(null)
const deploymentDialog = ref(false)
const deploymentAccountId = ref(null)
const deploymentDays = ref(30)
const deployingBacktest = ref(false)
const paperAccounts = ref([])
let reportChartInstance = null
const message = ref('')
const messageType = ref('success')

const defaults = () => ({
  templateName: '', strategyId: '', datasetIds: [], description: '',
  initialCapital: 100000, positionSizingMode: 'strategy', fixedVolume: 0.01,
  riskPercent: 1, spreadPoints: 0, slippagePoints: 0,
  commissionPerLot: 0, maxPositions: 1, useStrategyExits: true,
  isShared: true,
})
const form = reactive(defaults())

const positionModes = [
  { title: '跟随策略配置', value: 'strategy' },
  { title: '固定手数', value: 'fixed' },
  { title: '按资金风险比例', value: 'risk_percent' },
]
const statusMap = {
  queued: { label: '等待引擎', color: 'blue-grey' },
  running: { label: '执行中', color: 'info' },
  completed: { label: '已完成', color: 'success' },
  failed: { label: '失败', color: 'error' },
  canceled: { label: '已取消', color: 'grey' },
}
const orderStatusMap = {
  pending: { label: '待成交', color: 'info' },
  filled: { label: '已成交', color: 'success' },
  rejected: { label: '已拒绝', color: 'error' },
  canceled: { label: '已取消', color: 'grey' },
}

const strategyOptions = computed(() => context.strategies.map(item => ({
  value: item.strategy_id,
  label: `${item.strategy_name} · ${item.symbol} · ${item.lifecycle_status}`,
})))
const selectedStrategy = computed(() => context.strategies.find(item => item.strategy_id === form.strategyId))
const datasetOptions = computed(() => context.datasets
  .filter(item => (!selectedStrategy.value || item.symbol === selectedStrategy.value.symbol)
    && (!form.isShared || item.visibility === 'shared'))
  .map(item => ({
    value: item.dataset_id,
    label: `${item.dataset_name} · ${item.symbol} · 质量 ${item.quality_score}`,
  })))
const canSave = computed(() => Boolean(
  form.templateName && form.strategyId && form.datasetIds.length && Number(form.initialCapital) > 0
))
const queuedTasks = computed(() => batches.value
  .filter(item => item.status === 'queued')
  .reduce((sum, item) => sum + Number(item.task_count || 0), 0))
const snapshotCount = computed(() => new Set(batches.value.map(item => item.strategy_snapshot_hash)).size)
const paperAccountOptions = computed(() => paperAccounts.value.map(account => ({
  value: account.account_id,
  label: `${account.account_name} · ${money(account.balance)} ${account.currency} · ${account.status === 'active' ? '可用' : '不可用'}`,
})))

function statusMeta(status) { return statusMap[status] || statusMap.queued }
function orderStatusMeta(status) { return orderStatusMap[status] || orderStatusMap.pending }
function money(value) { return Number(value || 0).toLocaleString('zh-CN', { maximumFractionDigits: 2 }) }
function signedMoney(value) {
  const number = Number(value || 0)
  return `${number > 0 ? '+' : ''}${number.toLocaleString('zh-CN', { maximumFractionDigits: 2 })}`
}
function formatPercent(value) { return `${Number(value || 0).toFixed(2)}%` }
function formatNumber(value) { return Number(value || 0).toLocaleString('zh-CN') }
function formatDate(value) { return value ? new Date(value * 1000).toLocaleDateString('zh-CN', { timeZone: 'UTC' }) : '--' }
function formatTime(value) { return value ? new Date(value * 1000).toLocaleString('zh-CN') : '--' }
function reportValue(value) { return value === null || value === undefined ? '--' : Number(value).toFixed(2) }
function durationLabel(value) {
  if (value === null || value === undefined) return '--'
  const minutes = Number(value)
  return minutes >= 60 ? `${(minutes / 60).toFixed(1)} 小时` : `${minutes.toFixed(1)} 分钟`
}
function directionLabel(direction) { return direction === 'buy' ? '买入' : direction === 'sell' ? '卖出' : direction }
function exitReasonLabel(reason) {
  return { take_profit: '止盈', stop_loss: '止损', end_of_test: '回测结束平仓' }[reason] || reason
}
function sourceLabel(source) {
  return { pivot: '转折点', key_level: '关键点位', ai_entry: 'AI 入场' }[source] || source
}

function removeIncompatibleDatasets() {
  const allowed = new Set(datasetOptions.value.map(item => item.value))
  form.datasetIds = form.datasetIds.filter(item => allowed.has(item))
}

function payload() {
  return {
    template_name: form.templateName,
    strategy_id: form.strategyId,
    dataset_ids: form.datasetIds,
    description: form.description,
    initial_capital: form.initialCapital,
    position_sizing_mode: form.positionSizingMode,
    fixed_volume: form.fixedVolume,
    risk_percent: form.riskPercent,
    spread_points: form.spreadPoints,
    slippage_points: form.slippagePoints,
    commission_per_lot: form.commissionPerLot,
    max_positions: form.maxPositions,
    use_strategy_exits: form.useStrategyExits,
    visibility: form.isShared ? 'shared' : 'private',
  }
}

async function loadAll() {
  loading.value = true
  try {
    const [contextData, templateData, batchData] = await Promise.all([
      marketAPI.getBacktestTemplateContext(),
      marketAPI.getBacktestTemplates(),
      marketAPI.getBacktestBatches(),
    ])
    context.strategies = contextData.strategies || []
    context.datasets = contextData.datasets || []
    templates.value = templateData.templates || []
    batches.value = batchData.batches || []
    await Promise.all(Object.keys(batchDetails).map(async (batchId) => {
      const data = await marketAPI.getBacktestBatch(batchId)
      batchDetails[batchId] = data.batch
    }))
  } catch (error) {
    showError(error, '加载回测任务失败')
  } finally {
    loading.value = false
  }
}

async function saveTemplate() {
  if (!canSave.value) return
  saving.value = true
  try {
    const data = editingId.value
      ? await marketAPI.updateBacktestTemplate(editingId.value, payload())
      : await marketAPI.createBacktestTemplate(payload())
    messageType.value = 'success'
    message.value = data.message
    resetForm()
    await loadAll()
  } catch (error) {
    showError(error, '保存回测模板失败')
  } finally {
    saving.value = false
  }
}

function editTemplate(template) {
  editingId.value = template.template_id
  Object.assign(form, {
    templateName: template.template_name,
    strategyId: template.strategy_id,
    datasetIds: [...template.dataset_ids],
    description: template.description,
    initialCapital: template.initial_capital,
    positionSizingMode: template.position_sizing_mode,
    fixedVolume: template.fixed_volume,
    riskPercent: template.risk_percent,
    spreadPoints: template.spread_points,
    slippagePoints: template.slippage_points,
    commissionPerLot: template.commission_per_lot,
    maxPositions: template.max_positions,
    useStrategyExits: template.use_strategy_exits,
    isShared: template.visibility === 'shared',
  })
  window.scrollTo({ top: 0, behavior: 'smooth' })
}

function resetForm() {
  editingId.value = ''
  Object.assign(form, defaults())
}

async function deleteTemplate(template) {
  if (!confirm(`确定删除模板“${template.template_name}”吗？历史批次会继续保留。`)) return
  try {
    const data = await marketAPI.deleteBacktestTemplate(template.template_id)
    messageType.value = 'success'
    message.value = data.message
    if (editingId.value === template.template_id) resetForm()
    await loadAll()
  } catch (error) {
    showError(error, '删除回测模板失败')
  }
}

async function runTemplate(template) {
  if (!confirm(`按模板“${template.template_name}”生成 ${template.dataset_ids.length} 个回测任务吗？`)) return
  runningId.value = template.template_id
  try {
    const data = await marketAPI.runBacktestTemplate(template.template_id)
    messageType.value = 'success'
    message.value = data.message
    batchDetails[data.batch.batch_id] = data.batch
    await loadAll()
  } catch (error) {
    showError(error, '发起回测失败')
  } finally {
    runningId.value = ''
  }
}

async function toggleBatch(batch) {
  if (batchDetails[batch.batch_id]) {
    delete batchDetails[batch.batch_id]
    return
  }
  try {
    const data = await marketAPI.getBacktestBatch(batch.batch_id)
    batchDetails[batch.batch_id] = data.batch
  } catch (error) {
    showError(error, '加载任务明细失败')
  }
}

async function toggleTaskLedger(task) {
  if (taskLedgers[task.task_id]) {
    delete taskLedgers[task.task_id]
    return
  }
  ledgerLoadingId.value = task.task_id
  try {
    const data = await marketAPI.getBacktestTaskLedger(task.task_id)
    taskLedgers[task.task_id] = data.ledger
  } catch (error) {
    showError(error, '加载模拟交易账本失败')
  } finally {
    ledgerLoadingId.value = ''
  }
}

async function openReport(task) {
  reportTask.value = task
  reportDialog.value = true
  await nextTick()
}

function closeReport() {
  reportDialog.value = false
  reportChartInstance?.dispose()
  reportChartInstance = null
  reportTask.value = null
}

async function openDeploymentDialog() {
  try {
    const data = await accountAPI.list()
    paperAccounts.value = (data.accounts || []).filter(account => (
      account.account_type === 'paper' && account.status === 'active' && account.enabled
    ))
    deploymentAccountId.value = paperAccounts.value[0]?.account_id || null
    deploymentDays.value = 30
    deploymentDialog.value = true
  } catch (error) {
    showError(error, '加载模拟账户失败')
  }
}

async function deployBacktest() {
  if (!reportTask.value || !deploymentAccountId.value) return
  deployingBacktest.value = true
  try {
    const data = await accountAPI.deployBacktest(
      deploymentAccountId.value, reportTask.value.task_id, deploymentDays.value
    )
    deploymentDialog.value = false
    messageType.value = 'success'
    message.value = `${data.message}，运行期限 ${deploymentDays.value} 天`
  } catch (error) {
    showError(error, '部署到模拟账户失败')
  } finally {
    deployingBacktest.value = false
  }
}

function renderReportChart() {
  if (!reportChart.value || !reportTask.value?.result?.equity_curve?.length) return
  reportChartInstance?.dispose()
  reportChartInstance = echarts.init(reportChart.value)
  const result = reportTask.value.result
  const drawdowns = new Map((result.drawdown_curve || []).map(item => [item.time, item.drawdown_pct]))
  const times = result.equity_curve.map(item => item.time)
  reportChartInstance.setOption({
    animationDuration: 600,
    color: ['#19745d', '#bd5949'],
    tooltip: { trigger: 'axis' },
    legend: { data: ['账户净值', '回撤'], right: 10, top: 0 },
    grid: { left: 20, right: 22, top: 38, bottom: 10, containLabel: true },
    xAxis: {
      type: 'category', boundaryGap: false,
      data: times.map(value => new Date(value * 1000).toLocaleDateString('zh-CN')),
      axisLabel: { color: '#82908a', hideOverlap: true },
    },
    yAxis: [
      { type: 'value', scale: true, axisLabel: { formatter: value => Number(value).toLocaleString('zh-CN') } },
      { type: 'value', min: 0, axisLabel: { formatter: '{value}%' }, splitLine: { show: false } },
    ],
    series: [
      {
        name: '账户净值', type: 'line', symbol: 'none', smooth: 0.15,
        data: result.equity_curve.map(item => item.equity),
        areaStyle: { opacity: 0.12 }, lineStyle: { width: 2 },
      },
      {
        name: '回撤', type: 'line', yAxisIndex: 1, symbol: 'none', smooth: 0.15,
        data: times.map(value => drawdowns.get(value) ?? 0),
        areaStyle: { opacity: 0.08 }, lineStyle: { width: 1.5 },
      },
    ],
  })
}

function showError(error, fallback) {
  messageType.value = 'error'
  message.value = error.response?.data?.detail || fallback
}

loadAll()
const refreshTimer = window.setInterval(() => {
  if (batches.value.some(batch => ['queued', 'running'].includes(batch.status))) loadAll()
}, 3000)
onBeforeUnmount(() => {
  window.clearInterval(refreshTimer)
  reportChartInstance?.dispose()
})
</script>

<style scoped>
.backtest-page { min-height: 100%; padding: 28px; background: radial-gradient(circle at 8% 2%, rgba(208, 147, 58, .13), transparent 25%), linear-gradient(145deg, #f4f0e7, #edf3ef 55%, #f7f4ec); }
.workbench-hero { display: flex; justify-content: space-between; align-items: center; gap: 24px; margin-bottom: 22px; padding: 34px 38px; border-radius: 24px; color: #faf5e8; background: linear-gradient(120deg, #182f2b, #285f50 66%, #98743c); box-shadow: 0 20px 46px rgba(20, 58, 48, .18); }
.workbench-hero h1 { margin: 5px 0 8px; font: 700 clamp(2rem, 4vw, 3.35rem)/1 Georgia, serif; }
.workbench-hero p { margin: 0; max-width: 760px; color: rgba(255,255,255,.75); }
.eyebrow, .section-tag { color: #d7b36f; font-size: .7rem; font-weight: 800; letter-spacing: .16em; }
.section-tag { color: #24735f; }
.metric-grid { display: grid; grid-template-columns: repeat(4, 1fr); gap: 14px; margin-bottom: 20px; }
.metric-grid article { padding: 17px 20px; border: 1px solid rgba(28,73,61,.1); border-radius: 15px; background: rgba(255,255,255,.82); }
.metric-grid span { display: block; color: #76817c; font-size: .76rem; }
.metric-grid strong { color: #1c4a3e; font-size: 1.75rem; }
.workspace-grid { display: grid; grid-template-columns: minmax(330px, 430px) 1fr; gap: 20px; align-items: start; }
.editor-card, .catalog-card, .batch-card { border: 1px solid rgba(24,67,56,.1); border-radius: 20px; background: rgba(255,255,255,.92); }
.editor-card { position: sticky; top: 20px; }
.editor-heading, .template-topline, .template-actions, .batch-heading, .batch-summary { display: flex; align-items: center; justify-content: space-between; gap: 12px; }
h2 { margin: 4px 0 10px; color: #1d453a; font-family: Georgia, serif; }
.field-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 0 10px; }
.template-list, .batch-list { display: grid; gap: 13px; }
.template-card { padding: 18px; border: 1px solid #dde7e1; border-radius: 15px; background: #fcfdfb; }
.template-card h3, .batch-item h3 { margin: 0; color: #27483f; font-size: .98rem; }
.template-topline span, .batch-name span { color: #85908b; font-size: .73rem; }
.template-chips { display: flex; flex-wrap: wrap; justify-content: flex-end; gap: 6px; }
.template-description { margin: 12px 0; color: #66736d; font-size: .82rem; }
.assumption-row { display: flex; flex-wrap: wrap; gap: 8px 16px; padding: 10px 12px; border-radius: 10px; color: #77837d; background: #f0f5f1; font-size: .72rem; }
.assumption-row b { color: #35564d; }
.dataset-tags { display: flex; flex-wrap: wrap; gap: 5px; margin-top: 11px; }
.template-actions { margin-top: 13px; padding-top: 10px; border-top: 1px solid #edf0ed; }
.batch-card { margin-top: 20px; }
.batch-heading > span { color: #7e8984; font-size: .76rem; }
.batch-item { border: 1px solid #dfe7e2; border-radius: 14px; overflow: hidden; background: #fff; }
.batch-summary { padding: 15px 17px; }
.batch-icon { display: grid; place-items: center; width: 38px; height: 38px; border-radius: 10px; color: #276b58; background: #eaf3ee; }
.batch-name { flex: 1; min-width: 0; }
.batch-count { text-align: center; }
.batch-count strong, .batch-count span { display: block; }
.batch-count strong { color: #294e43; font-size: 1.1rem; }
.batch-count span { color: #8a948f; font-size: .65rem; }
.task-list { border-top: 1px solid #e8eeea; background: #f7faf8; }
.task-entry { border-bottom: 1px solid #e7ede9; }
.task-entry:last-child { border-bottom: 0; }
.task-row { display: grid; grid-template-columns: 1fr auto auto; align-items: center; gap: 18px; padding: 12px 18px; }
.task-row strong, .task-row span { display: block; }
.task-row strong { color: #36584f; font-size: .82rem; }
.task-row span { color: #7e8984; font-size: .7rem; }
.task-error { display: flex; align-items: center; gap: 6px; margin: 0 18px 14px; padding: 9px 11px; border-radius: 9px; color: #9e3f34; background: #fcedea; font-size: .75rem; }
.result-panel { margin: 0 18px 16px; padding: 13px; border: 1px solid #dbe9e1; border-radius: 12px; background: #fff; }
.result-metrics { display: grid; grid-template-columns: repeat(auto-fit, minmax(80px, 1fr)); gap: 8px; }
.result-metrics span { color: #84908a; font-size: .65rem; }
.result-metrics strong { display: block; margin-top: 3px; color: #264f43; font-size: .88rem; }
.result-metrics .positive { color: #147b59; }
.result-metrics .negative { color: #bd493c; }
.result-meta { margin-top: 10px; color: #939d98; font-size: .65rem; }
.signal-source-row { display: flex; align-items: center; flex-wrap: wrap; gap: 6px; margin-top: 10px; }
.signal-source-row > span { margin-right: 3px; color: #7f8b85; font-size: .68rem; }
.ledger-panel { margin-top: 12px; padding: 12px; border-radius: 10px; background: #f4f8f5; }
.ledger-summary { display: grid; grid-template-columns: repeat(4, 1fr); gap: 8px; margin-bottom: 12px; }
.ledger-summary span { padding: 8px; border-radius: 8px; color: #7d8983; background: #fff; font-size: .65rem; }
.ledger-summary strong { display: block; margin-top: 2px; color: #285044; font-size: .82rem; }
.ledger-title { display: flex; justify-content: space-between; margin-bottom: 7px; color: #405f56; font-size: .72rem; }
.ledger-title span, .ledger-empty { color: #89948f; font-size: .66rem; }
.order-list { display: grid; gap: 4px; max-height: 310px; overflow: auto; }
.order-row { display: grid; grid-template-columns: 130px 44px 70px 65px 80px 62px minmax(100px, 1fr); align-items: center; gap: 7px; padding: 7px 8px; border-radius: 7px; background: #fff; color: #66746e; font-size: .65rem; }
.order-row .positive { color: #147b59; }
.order-row .negative { color: #bd493c; }
.order-reason { color: #956057; }
.report-dialog { border-radius: 20px !important; background: #f7f8f4; }
.report-header { display: flex; align-items: center; justify-content: space-between; padding: 22px 26px; }
.report-header h2 { margin: 3px 0; }
.report-header span { color: #7b8782; font-size: .75rem; }
.report-header-actions { display: flex; align-items: center; gap: 8px; }
.report-body { padding: 22px 26px 30px !important; }
.report-hero-metrics { display: grid; grid-template-columns: repeat(4, 1fr); gap: 12px; }
.report-hero-metrics article { padding: 17px; border: 1px solid #dbe5df; border-radius: 13px; background: #fff; }
.report-hero-metrics span, .report-detail-grid span { display: block; color: #82908a; font-size: .68rem; }
.report-hero-metrics strong { display: block; margin: 4px 0 2px; color: #234e41; font: 700 1.35rem Georgia, serif; }
.report-hero-metrics small, .monthly-grid small { color: #8d9893; font-size: .65rem; }
.report-dialog .positive { color: #147b59; }
.report-dialog .negative { color: #bd493c; }
.report-section { margin-top: 18px; padding: 17px; border: 1px solid #dfe7e2; border-radius: 14px; background: #fff; }
.report-section-title { display: flex; align-items: flex-end; justify-content: space-between; margin-bottom: 8px; }
.report-section-title span { color: #b18443; font-size: .6rem; font-weight: 800; letter-spacing: .12em; }
.report-section-title h3, .breakdown-card h3 { margin: 2px 0; color: #31564b; font-size: .88rem; }
.report-section-title small { color: #8b9691; font-size: .64rem; }
.report-chart { width: 100%; height: 330px; }
.report-detail-grid { display: grid; grid-template-columns: repeat(7, 1fr); gap: 8px; margin-top: 13px; }
.report-detail-grid article { padding: 11px; border-radius: 10px; background: #eaf1ed; }
.report-detail-grid strong { display: block; margin-top: 3px; color: #31554b; font-size: .8rem; }
.report-breakdowns { display: grid; grid-template-columns: repeat(3, 1fr); gap: 12px; margin-top: 13px; }
.breakdown-card { padding: 15px; border: 1px solid #dfe7e2; border-radius: 13px; background: #fff; }
.breakdown-row { display: grid; grid-template-columns: 1fr auto auto auto; gap: 10px; align-items: center; padding: 8px 0; border-top: 1px solid #edf1ee; color: #75817c; font-size: .65rem; }
.breakdown-row strong { color: #405e55; }
.monthly-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(120px, 1fr)); gap: 8px; }
.monthly-grid article { padding: 10px; border-radius: 9px; background: #f1f5f2; }
.monthly-grid span, .monthly-grid strong, .monthly-grid small { display: block; }
.monthly-grid span { color: #74817b; font-size: .65rem; }
.monthly-grid strong { margin: 3px 0; font-size: .85rem; }
.report-empty { display: grid; place-items: center; min-height: 120px; color: #929d98; font-size: .72rem; }
.report-empty.compact { min-height: 60px; }
.deployment-dialog { border-radius: 20px !important; background: #f7f8f4; }
.deployment-dialog-header { display: flex; align-items: flex-start; justify-content: space-between; padding: 24px 26px; }
.deployment-dialog-header h2 { margin: 4px 0; }.deployment-dialog-header span { color: #7f8b85; font-size: .72rem; }
.deployment-dialog-body { padding: 22px 26px 12px !important; }
.snapshot-note { display: flex; gap: 11px; margin-top: 4px; padding: 13px; border-radius: 11px; color: #27594c; background: #e7f1ec; }
.snapshot-note strong,.snapshot-note span { display: block; }.snapshot-note strong { font-size: .76rem; }.snapshot-note span { margin-top: 3px; color: #6e7f78; font-size: .68rem; line-height: 1.5; }
.deployment-dialog-actions { justify-content: flex-end; gap: 8px; padding: 14px 26px 24px; }
.empty-state { padding: 65px 20px; color: #85918b; text-align: center; }
.empty-state h3 { margin: 12px 0 5px; color: #4a625b; }
.empty-state p { margin: 0; }
.empty-state.compact { padding: 30px; }
@media (max-width: 1050px) { .workspace-grid { grid-template-columns: 1fr; } .editor-card { position: static; } .result-metrics { grid-template-columns: repeat(4, 1fr); } }
@media (max-width: 1050px) { .report-detail-grid { grid-template-columns: repeat(4, 1fr); } .report-breakdowns { grid-template-columns: 1fr; } }
@media (max-width: 700px) { .backtest-page { padding: 15px; } .workbench-hero { align-items: flex-start; flex-direction: column; padding: 25px; } .metric-grid { grid-template-columns: 1fr 1fr; } .field-grid { grid-template-columns: 1fr; } .batch-summary { align-items: flex-start; flex-wrap: wrap; } .batch-name { min-width: calc(100% - 60px); } .task-row { grid-template-columns: 1fr; gap: 6px; } .ledger-summary, .report-hero-metrics, .report-detail-grid { grid-template-columns: 1fr 1fr; } .order-row { grid-template-columns: 1fr 44px 60px; } .order-row > *:nth-child(n+4):not(:last-child) { display: none; } .report-header { align-items: flex-start; } .report-header-actions .v-chip { display: none; } .report-body { padding: 14px !important; } .report-chart { height: 260px; } }
</style>

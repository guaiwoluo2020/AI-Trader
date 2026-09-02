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
              @update:model-value="applyStrategyDefaults"
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
            <v-select
              v-model="form.replayMode"
              :items="replayModes"
              label="行情回放模式"
              hint="Tick 模式使用本地已采集的 Tick 文件；没有 Tick 文件时使用 K 线。"
              persistent-hint
              variant="outlined"
              density="comfortable"
            />
            <v-select
              v-if="form.replayMode === 'ticks'"
              v-model="form.tickFilePath"
              :items="tickFileOptions"
              item-title="label"
              item-value="value"
              label="Tick 数据文件"
              hint="每分钟最多采样 30 笔。"
              persistent-hint
              variant="outlined"
              density="comfortable"
            />
            <v-alert
              v-if="form.replayMode === 'ticks' && form.tickFilePath"
              type="info"
              variant="tonal"
              density="compact"
              class="mb-3"
            >
              {{ tickCoverageHint }}
            </v-alert>
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
              <v-text-field v-model.number="form.maxPositions" label="回测最大持仓数" type="number" min="1" max="100" hint="覆盖策略中的最大持仓限制" persistent-hint variant="outlined" density="comfortable" />
              <v-text-field v-model.number="form.maxSameDirection" label="回测同向最大持仓" type="number" min="1" :max="form.maxPositions" hint="不能大于回测最大持仓数" persistent-hint variant="outlined" density="comfortable" />
            </div>
            <v-switch
              v-model="form.useStrategyExits"
              color="success"
              inset
              label="使用策略中的止盈止损规则"
              hide-details
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
              <v-chip
                v-if="Number(batch.llm_analysis_count || 0) > 0"
                color="blue-grey"
                variant="tonal"
                size="small"
                prepend-icon="mdi-creation-outline"
              >
                大模型调用 {{ batch.llm_call_count || 0 }} 次
                <template v-if="Number(batch.llm_cache_hits || 0) > 0">
                  · 缓存 {{ batch.llm_cache_hits }} 次
                </template>
              </v-chip>
              <v-chip :color="batch.cancel_requested ? 'warning' : statusMeta(batch.status).color" variant="tonal" size="small">
                {{ batch.cancel_requested ? '正在停止' : statusMeta(batch.status).label }}
              </v-chip>
              <v-btn
                v-if="['queued', 'running'].includes(batch.status) && !batch.cancel_requested"
                size="small"
                variant="tonal"
                color="error"
                prepend-icon="mdi-stop-circle-outline"
                :loading="cancelingId === batch.batch_id"
                @click="stopBatch(batch)"
              >停止批次</v-btn>
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
                  <v-chip :color="task.cancel_requested ? 'warning' : statusMeta(task.status).color" variant="tonal" size="x-small">
                    {{ task.cancel_requested && task.status === 'running' ? '正在停止' : statusMeta(task.status).label }}
                  </v-chip>
                  <v-btn
                    v-if="['queued', 'running'].includes(task.status)"
                    size="x-small"
                    variant="text"
                    color="error"
                    icon="mdi-stop-circle-outline"
                    :disabled="task.cancel_requested"
                    :loading="cancelingId === task.task_id"
                    @click="stopTask(task)"
                  />
                </div>
                <div v-if="task.status === 'running'" class="task-progress">
                  <v-progress-linear
                    :model-value="task.progress"
                    :indeterminate="Number(task.progress || 0) <= 0"
                    color="teal"
                    height="7"
                    rounded
                  />
                  <strong>{{ formatProgress(task.progress) }}</strong>
                  <v-chip
                    v-if="Number(task.llm_analysis_count || 0) > 0"
                    color="blue-grey"
                    variant="tonal"
                    size="x-small"
                    prepend-icon="mdi-creation-outline"
                  >
                    大模型调用 {{ task.llm_call_count || 0 }} 次
                    <template v-if="Number(task.llm_cache_hits || 0) > 0">
                      · 缓存 {{ task.llm_cache_hits }} 次
                    </template>
                  </v-chip>
                  <v-btn
                    size="x-small"
                    variant="tonal"
                    color="teal"
                    prepend-icon="mdi-finance"
                    :loading="ledgerLoadingId === task.task_id"
                    @click="toggleTaskLedger(task)"
                  >{{ taskLedgers[task.task_id] ? '收起运行明细' : '查看运行明细' }}</v-btn>
                </div>
                <div
                  v-if="task.status !== 'completed' && taskLedgers[task.task_id]"
                  class="ledger-panel live-ledger"
                >
                  <div v-if="taskLedgers[task.task_id].account" class="ledger-summary">
                    <span>初始资金<strong>{{ money(taskLedgers[task.task_id].account.initial_balance) }}</strong></span>
                    <span>当前余额<strong>{{ money(taskLedgers[task.task_id].account.balance) }}</strong></span>
                    <span>当前净值<strong>{{ money(taskLedgers[task.task_id].account.equity) }}</strong></span>
                    <span>资金变化<strong :class="currentPnL(taskLedgers[task.task_id]) >= 0 ? 'positive' : 'negative'">{{ signedMoney(currentPnL(taskLedgers[task.task_id])) }}</strong></span>
                    <span>当前持仓<strong>{{ openPositionCount(taskLedgers[task.task_id]) }}</strong></span>
                    <span>已平仓<strong>{{ taskLedgers[task.task_id].trades.length }}</strong></span>
                  </div>
                  <BacktestReplayChart
                    :ledger="taskLedgers[task.task_id]"
                    :progress="Number(task.progress || 0)"
                    :symbol="task.dataset.symbol"
                  />
                  <div class="ledger-title">
                    <strong>实时订单流水</strong>
                    <span>共 {{ orderFlow(taskLedgers[task.task_id]).length }} 条开平仓记录，展示最近 50 条</span>
                  </div>
                  <div v-if="!orderFlow(taskLedgers[task.task_id]).length" class="ledger-empty">当前还没有生成订单</div>
                  <div v-else class="order-list">
                    <div
                      v-for="flow in orderFlow(taskLedgers[task.task_id]).slice(-50).reverse()"
                      :key="flow.flow_id"
                      class="order-row"
                    >
                      <span>{{ formatTime(flow.flow_time) }}</span>
                      <b :class="flow.kind === 'close' ? 'close-action' : flow.direction === 'buy' ? 'positive' : 'negative'">{{ flowDirectionLabel(flow) }}</b>
                      <span>{{ flow.kind === 'close' ? '平仓成交' : sourceLabel(flow.signal_source) }}</span>
                      <span>{{ flow.volume }} 手</span>
                      <span class="order-price">
                        <small>{{ flow.first_label }}</small>
                        <strong>{{ tradePrice(flow.first_value) }}</strong>
                      </span>
                      <span class="order-price stop-loss">
                        <small>{{ flow.second_label }}</small>
                        <strong>{{ tradePrice(flow.second_value) }}</strong>
                      </span>
                      <span class="order-price" :class="flow.kind === 'close' ? Number(flow.third_value) >= 0 ? 'take-profit' : 'stop-loss' : 'take-profit'">
                        <small>{{ flow.third_label }}</small>
                        <strong>{{ flow.kind === 'close' ? signedMoney(flow.third_value) : tradePrice(flow.third_value) }}</strong>
                      </span>
                      <v-chip :color="orderStatusMeta(flow.status).color" variant="tonal" size="x-small">{{ orderStatusMeta(flow.status).label }}</v-chip>
                      <span class="order-reason">{{ flow.reason || '--' }}</span>
                    </div>
                  </div>
                </div>
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
                    <span>实际调用<strong>{{ actualLlmCalls(task.result) }}</strong></span>
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
                    <BacktestReplayChart
                      :ledger="taskLedgers[task.task_id]"
                      :progress="100"
                      :symbol="task.dataset.symbol"
                    />
                    <div class="ledger-title">
                      <strong>订单流水</strong>
                      <span>共 {{ orderFlow(taskLedgers[task.task_id]).length }} 条开平仓记录，展示最近 50 条</span>
                    </div>
                    <div v-if="!orderFlow(taskLedgers[task.task_id]).length" class="ledger-empty">本次回测未生成订单</div>
                    <div v-else class="order-list">
                      <div
                        v-for="flow in orderFlow(taskLedgers[task.task_id]).slice(-50).reverse()"
                        :key="flow.flow_id"
                        class="order-row"
                      >
                        <span>{{ formatTime(flow.flow_time) }}</span>
                        <b :class="flow.kind === 'close' ? 'close-action' : flow.direction === 'buy' ? 'positive' : 'negative'">{{ flowDirectionLabel(flow) }}</b>
                        <span>{{ flow.kind === 'close' ? '平仓成交' : sourceLabel(flow.signal_source) }}</span>
                        <span>{{ flow.volume }} 手</span>
                        <span class="order-price">
                          <small>{{ flow.first_label }}</small>
                          <strong>{{ tradePrice(flow.first_value) }}</strong>
                        </span>
                        <span class="order-price stop-loss">
                          <small>{{ flow.second_label }}</small>
                          <strong>{{ tradePrice(flow.second_value) }}</strong>
                        </span>
                        <span class="order-price" :class="flow.kind === 'close' ? Number(flow.third_value) >= 0 ? 'take-profit' : 'stop-loss' : 'take-profit'">
                          <small>{{ flow.third_label }}</small>
                          <strong>{{ flow.kind === 'close' ? signedMoney(flow.third_value) : tradePrice(flow.third_value) }}</strong>
                        </span>
                        <v-chip :color="orderStatusMeta(flow.status).color" variant="tonal" size="x-small">
                          {{ orderStatusMeta(flow.status).label }}
                        </v-chip>
                        <span class="order-reason">{{ flow.reason || '--' }}</span>
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
                <div v-if="['completed', 'canceled'].includes(task.status)" class="ai-analysis-entry">
                  <div>
                    <v-icon icon="mdi-brain" size="19" />
                    <div>
                      <strong>AI 回测复盘</strong>
                      <span>{{ aiAnalysisHint(task.ai_analysis) }}</span>
                    </div>
                  </div>
                  <v-chip
                    v-if="task.ai_analysis?.status !== 'idle'"
                    :color="aiStatusMeta(task.ai_analysis?.status).color"
                    variant="tonal"
                    size="x-small"
                  >{{ aiStatusMeta(task.ai_analysis?.status).label }}</v-chip>
                  <v-btn
                    size="small"
                    :color="task.ai_analysis?.status === 'completed' ? 'primary' : 'teal'"
                    :variant="task.ai_analysis?.status === 'completed' ? 'tonal' : 'flat'"
                    prepend-icon="mdi-chart-box-outline"
                    :loading="aiAnalysisLoadingId === task.task_id"
                    @click="openAIAnalysis(task)"
                  >{{ task.ai_analysis?.status === 'completed' ? '查看优化建议' : '发送给大模型分析' }}</v-btn>
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

    <v-dialog v-model="aiAnalysisDialog" max-width="1040" scrollable>
      <v-card class="ai-analysis-dialog" elevation="0">
        <v-card-title class="ai-analysis-header">
          <div>
            <div class="section-tag">AI BACKTEST REVIEW</div>
            <h2>AI 回测分析与策略优化建议</h2>
            <span>{{ aiAnalysisTask?.dataset?.dataset_name }} · {{ aiAnalysisTask?.dataset?.symbol }}</span>
          </div>
          <div class="ai-header-actions">
            <v-chip :color="aiStatusMeta(aiAnalysis?.status).color" variant="tonal">
              {{ aiStatusMeta(aiAnalysis?.status).label }}
            </v-chip>
            <v-btn icon="mdi-close" variant="text" @click="aiAnalysisDialog = false" />
          </div>
        </v-card-title>
        <v-divider />
        <v-card-text class="ai-analysis-body">
          <div v-if="['queued', 'running'].includes(aiAnalysis?.status)" class="ai-waiting">
            <v-progress-circular indeterminate color="teal" size="48" width="4" />
            <div><strong>大模型正在复盘回测数据</strong><span>正在分析策略快照、交易样本、资金曲线和信号归因，完成后会自动刷新。</span></div>
          </div>
          <v-alert v-else-if="aiAnalysis?.status === 'failed'" type="error" variant="tonal">
            {{ aiAnalysis.error_message || '回测分析失败，请稍后重试' }}
          </v-alert>
          <template v-else-if="aiAnalysis?.status === 'completed'">
            <section class="ai-summary-card">
              <div><span>EXECUTIVE SUMMARY</span><h3>总体结论</h3></div>
              <p>{{ aiAnalysis.result.executive_summary || '大模型未提供总体结论' }}</p>
              <div class="ai-quality">
                数据可信度：<strong>{{ qualityLabel(aiAnalysis.result.data_quality?.level) }}</strong>
                <span v-for="note in aiAnalysis.result.data_quality?.notes || []" :key="note">{{ note }}</span>
              </div>
            </section>
            <section class="ai-result-section">
              <div class="ai-section-title"><span>DIAGNOSIS</span><h3>问题诊断与数据证据</h3></div>
              <div v-if="!aiAnalysis.result.diagnosis?.length" class="report-empty compact">暂无诊断项</div>
              <div v-else class="diagnosis-grid">
                <article v-for="(item, index) in aiAnalysis.result.diagnosis" :key="index">
                  <v-chip :color="severityColor(item.severity)" size="x-small" variant="tonal">{{ item.area || '策略' }} · {{ severityLabel(item.severity) }}</v-chip>
                  <strong>{{ item.finding }}</strong>
                  <p>{{ item.evidence }}</p>
                </article>
              </div>
            </section>
            <section class="ai-result-section">
              <div class="ai-section-title"><span>OPTIMIZATION</span><h3>参数优化建议</h3></div>
              <div v-if="!aiAnalysis.result.optimization_suggestions?.length" class="report-empty compact">暂无参数建议</div>
              <div v-else class="suggestion-list">
                <article v-for="(item, index) in aiAnalysis.result.optimization_suggestions" :key="index">
                  <div class="suggestion-priority">P{{ item.priority || index + 1 }}</div>
                  <div>
                    <h4>{{ item.target || '策略参数' }}</h4>
                    <div class="value-change"><span>{{ item.current_value ?? '--' }}</span><v-icon icon="mdi-arrow-right" size="16" /><strong>{{ item.suggested_value ?? '--' }}</strong></div>
                    <p>{{ item.reason }}</p>
                    <small>预期方向：{{ item.expected_impact || '--' }}</small>
                    <small>验证方式：{{ item.validation_plan || '--' }}</small>
                  </div>
                </article>
              </div>
            </section>
            <div class="ai-bottom-grid">
              <section class="ai-result-section">
                <div class="ai-section-title"><span>RISK</span><h3>风险提示</h3></div>
                <p v-for="warning in aiAnalysis.result.risk_warnings || []" :key="warning" class="risk-item">{{ warning }}</p>
              </section>
              <section class="ai-result-section">
                <div class="ai-section-title"><span>NEXT RUN</span><h3>下一轮回测计划</h3></div>
                <p v-for="change in aiAnalysis.result.next_backtest_plan?.changes || []" :key="change" class="plan-item">{{ change }}</p>
                <small v-for="criterion in aiAnalysis.result.next_backtest_plan?.acceptance_criteria || []" :key="criterion">验收：{{ criterion }}</small>
              </section>
            </div>
          </template>
          <div v-else class="report-empty">点击分析后，大模型将基于当前回测快照给出优化建议。</div>
          <div class="ai-disclaimer">AI 建议仅用于研究和回测验证，不会自动修改策略，也不构成实盘收益承诺。</div>
        </v-card-text>
        <v-card-actions class="deployment-dialog-actions">
          <span v-if="aiAnalysis?.completed_at" class="ai-completed-at">完成于 {{ formatTime(aiAnalysis.completed_at) }} · {{ aiAnalysis.model }}</span>
          <v-spacer />
          <v-btn variant="text" @click="aiAnalysisDialog = false">关闭</v-btn>
          <v-btn
            v-if="['completed', 'failed'].includes(aiAnalysis?.status)"
            color="teal"
            variant="tonal"
            prepend-icon="mdi-refresh"
            :loading="aiAnalysisLoadingId === aiAnalysisTask?.task_id"
            @click="regenerateAIAnalysis"
          >重新分析</v-btn>
        </v-card-actions>
      </v-card>
    </v-dialog>

    <v-dialog v-model="deploymentDialog" max-width="560">
      <v-card class="deployment-dialog" elevation="0">
        <v-card-title class="deployment-dialog-header">
          <div>
            <div class="section-tag">PAPER VALIDATION</div>
            <h2>部署到模拟账户</h2>
            <span>部署前会校验回测快照与当前策略配置是否一致</span>
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
            <div><strong>一致性校验</strong><span>模拟运行引用当前策略配置；如果当前策略和本次回测快照不一致，系统会拒绝部署并要求重新回测。</span></div>
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
import BacktestReplayChart from '../components/BacktestReplayChart.vue'

const templates = ref([])
const batches = ref([])
const context = reactive({ strategies: [], datasets: [], tickFiles: [] })
const batchDetails = reactive({})
const taskLedgers = reactive({})
const loading = ref(false)
const saving = ref(false)
const runningId = ref('')
const cancelingId = ref('')
const editingId = ref('')
const ledgerLoadingId = ref('')
const aiAnalysisLoadingId = ref('')
const aiAnalysisDialog = ref(false)
const aiAnalysisTask = ref(null)
const aiAnalysis = ref(null)
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
  commissionPerLot: 0, maxPositions: 1, maxSameDirection: 1,
  useStrategyExits: true,
  replayMode: 'bars', tickFilePath: '',
})
const form = reactive(defaults())

const positionModes = [
  { title: '跟随策略配置', value: 'strategy' },
  { title: '固定手数', value: 'fixed' },
  { title: '按资金风险比例', value: 'risk_percent' },
]
const replayModes = [
  { title: 'K线回放', value: 'bars' },
  { title: 'Tick回放', value: 'ticks' },
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
  closed: { label: '已平仓', color: 'teal' },
}
const aiStatusMap = {
  idle: { label: '尚未分析', color: 'blue-grey' },
  queued: { label: '等待分析', color: 'info' },
  running: { label: '分析中', color: 'teal' },
  completed: { label: '分析完成', color: 'success' },
  failed: { label: '分析失败', color: 'error' },
}

const strategyOptions = computed(() => context.strategies.map(item => ({
  value: item.strategy_id,
  label: `${item.strategy_name} · ${item.symbol} · ${item.lifecycle_status}`,
})))
const selectedStrategy = computed(() => context.strategies.find(item => item.strategy_id === form.strategyId))
const datasetOptions = computed(() => context.datasets
  .filter(item => !selectedStrategy.value || item.symbol === selectedStrategy.value.symbol)
  .map(item => ({
    value: item.dataset_id,
    label: `${item.dataset_name} · ${item.symbol} · 质量 ${item.quality_score}`,
  })))
const tickFileOptions = computed(() => (context.tickFiles || [])
  .filter(item => !selectedStrategy.value || item.symbol === selectedStrategy.value.symbol)
  .map(item => ({
    value: item.file_path,
    label: `${item.symbol} · ${item.date} · ${item.source} · ${formatTickCoverage(item)}`,
  })))
const selectedTickFile = computed(() => (context.tickFiles || [])
  .find(item => item.file_path === form.tickFilePath))
const tickCoverageHint = computed(() => {
  const item = selectedTickFile.value
  if (!item) return '请选择 Tick 文件。'
  return `支持时间：${formatTime(Number(item.start_time_ms || 0) / 1000)} ～ ${formatTime(Number(item.end_time_ms || 0) / 1000)}；` +
    `共 ${Number(item.tick_count || 0).toLocaleString()} 笔，超过每分钟 30 笔时已均匀采样。`
})
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
function aiStatusMeta(status) { return aiStatusMap[status] || aiStatusMap.idle }
function aiAnalysisHint(analysis) {
  return {
    queued: '分析任务已排队，稍后自动展示结果',
    running: '正在结合成交、资金曲线和策略参数进行复盘',
    completed: `已于 ${formatTime(analysis?.completed_at)} 生成优化建议`,
    failed: analysis?.error_message || '上次分析失败，可以重新提交',
  }[analysis?.status] || '将回测摘要和采样明细发送给已授权的大模型'
}
function severityColor(value) { return { high: 'error', medium: 'warning', low: 'info' }[value] || 'blue-grey' }
function severityLabel(value) { return { high: '高', medium: '中', low: '低' }[value] || '待评估' }
function qualityLabel(value) { return { high: '高', medium: '中', low: '低' }[value] || '待评估' }
function formatTickCoverage(item) {
  return `${Number(item.tick_count || 0).toLocaleString()} Tick`
}
function money(value) { return Number(value || 0).toLocaleString('zh-CN', { maximumFractionDigits: 2 }) }
function signedMoney(value) {
  const number = Number(value || 0)
  return `${number > 0 ? '+' : ''}${number.toLocaleString('zh-CN', { maximumFractionDigits: 2 })}`
}
function formatPercent(value) { return `${Number(value || 0).toFixed(2)}%` }
function formatProgress(value) {
  const progress = Number(value || 0)
  return `${progress < 1 ? progress.toFixed(2) : progress.toFixed(1)}%`
}
function actualLlmCalls(result) {
  if (result?.llm_call_count !== undefined) return Number(result.llm_call_count || 0)
  return Math.max(
    0,
    Number(result?.llm_analysis_count || 0) - Number(result?.llm_cache_hits || 0),
  )
}
function formatNumber(value) { return Number(value || 0).toLocaleString('zh-CN') }
function formatDate(value) { return value ? new Date(value * 1000).toLocaleDateString('zh-CN', { timeZone: 'UTC' }) : '--' }
function formatTime(value) { return value ? new Date(value * 1000).toLocaleString('zh-CN') : '--' }
function reportValue(value) { return value === null || value === undefined ? '--' : Number(value).toFixed(2) }
function tradePrice(value) {
  if (value === null || value === undefined || value === '') return '--'
  return Number(value).toLocaleString('zh-CN', {
    minimumFractionDigits: 2,
    maximumFractionDigits: 5,
  })
}
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
  return {
    pivot: '转折点', key_level: '关键点位', ai_entry: 'AI 入场',
    moving_average: '均线交叉', alpha_factor: '已验证 Alpha',
  }[source] || source
}
function currentPnL(ledger) {
  if (!ledger?.account) return 0
  return Number(ledger.account.equity || 0) - Number(ledger.account.initial_balance || 0)
}
function openPositionCount(ledger) {
  return (ledger?.positions || []).filter(position => position.status === 'open').length
}

function orderFlow(ledger) {
  const orders = (ledger?.orders || []).map(order => ({
    flow_id: `open-${order.order_id}`,
    flow_time: order.requested_at,
    kind: 'open',
    direction: order.direction,
    signal_source: order.signal_source,
    volume: order.filled_volume || order.requested_volume,
    first_label: order.filled_price == null ? '委托点' : '成交点',
    first_value: order.filled_price ?? order.requested_price,
    second_label: '止损点',
    second_value: order.stop_loss,
    third_label: '止盈点',
    third_value: order.take_profit,
    status: order.status,
    reason: order.rejection_reason,
  }))
  const closes = (ledger?.trades || []).map(trade => ({
    flow_id: `close-${trade.trade_id}`,
    flow_time: trade.closed_at,
    kind: 'close',
    direction: trade.direction,
    volume: trade.volume,
    first_label: '开仓点',
    first_value: trade.entry_price,
    second_label: '平仓点',
    second_value: trade.exit_price,
    third_label: '净盈亏',
    third_value: trade.net_profit,
    status: 'closed',
    reason: exitReasonLabel(trade.exit_reason),
  }))
  return [...orders, ...closes].sort(
    (left, right) => Number(left.flow_time || 0) - Number(right.flow_time || 0),
  )
}

function flowDirectionLabel(flow) {
  if (flow.kind === 'close') return flow.direction === 'buy' ? '平多' : '平空'
  return flow.direction === 'buy' ? '买入' : '卖出'
}

function removeIncompatibleDatasets() {
  const allowed = new Set(datasetOptions.value.map(item => item.value))
  form.datasetIds = form.datasetIds.filter(item => allowed.has(item))
}

function applyStrategyDefaults(strategyId) {
  form.strategyId = strategyId
  const strategy = context.strategies.find(item => item.strategy_id === strategyId)
  if (strategy) {
    form.fixedVolume = Number(strategy.fixed_volume ?? 0.01)
    form.riskPercent = Number(strategy.risk_percent ?? 1)
    form.maxPositions = Number(strategy.max_positions ?? 1)
    form.maxSameDirection = Math.min(
      form.maxPositions,
      Number(strategy.max_same_direction ?? form.maxPositions),
    )
  }
  removeIncompatibleDatasets()
  if (!tickFileOptions.value.some(item => item.value === form.tickFilePath)) {
    form.tickFilePath = ''
  }
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
    max_same_direction: form.maxSameDirection,
    use_strategy_exits: form.useStrategyExits,
    replay_mode: form.replayMode,
    tick_file_path: form.tickFilePath,
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
    context.tickFiles = contextData.tick_files || []
    templates.value = templateData.templates || []
    batches.value = batchData.batches || []
    await Promise.all(Object.keys(batchDetails).map(async (batchId) => {
      const data = await marketAPI.getBacktestBatch(batchId)
      batchDetails[batchId] = data.batch
    }))
    await Promise.all(Object.keys(taskLedgers).map(async (taskId) => {
      const data = await marketAPI.getBacktestTaskLedger(taskId)
      taskLedgers[taskId] = data.ledger
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
    maxSameDirection: template.max_same_direction ?? template.max_positions,
    useStrategyExits: template.use_strategy_exits,
    replayMode: template.replay_mode || 'bars',
    tickFilePath: template.tick_file_path || '',
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

async function stopBatch(batch) {
  if (!confirm(`确定停止批次“${batch.batch_name}”中的等待和执行任务吗？`)) return
  cancelingId.value = batch.batch_id
  try {
    const data = await marketAPI.cancelBacktestBatch(batch.batch_id)
    messageType.value = 'success'
    message.value = data.message
    batchDetails[batch.batch_id] = data.batch
    await loadAll()
  } catch (error) {
    showError(error, '停止回测批次失败')
  } finally {
    cancelingId.value = ''
  }
}

async function stopTask(task) {
  if (!confirm(`确定停止数据集“${task.dataset.dataset_name}”的回测任务吗？`)) return
  cancelingId.value = task.task_id
  try {
    const data = await marketAPI.cancelBacktestTask(task.task_id)
    messageType.value = 'success'
    message.value = data.message
    await loadAll()
  } catch (error) {
    showError(error, '停止回测任务失败')
  } finally {
    cancelingId.value = ''
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

async function openAIAnalysis(task) {
  aiAnalysisLoadingId.value = task.task_id
  aiAnalysisTask.value = task
  try {
    const current = await marketAPI.getBacktestTaskAIAnalysis(task.task_id)
    aiAnalysis.value = current.analysis
    if (current.analysis.status === 'idle' || current.analysis.status === 'failed') {
      const started = await marketAPI.startBacktestTaskAIAnalysis(task.task_id)
      aiAnalysis.value = started.analysis
      task.ai_analysis = started.analysis
    }
    aiAnalysisDialog.value = true
  } catch (error) {
    showError(error, '提交回测 AI 分析失败')
  } finally {
    aiAnalysisLoadingId.value = ''
  }
}

async function regenerateAIAnalysis() {
  const task = aiAnalysisTask.value
  if (!task || !confirm('确定重新发送本次回测数据进行分析吗？新的结果会覆盖当前建议。')) return
  aiAnalysisLoadingId.value = task.task_id
  try {
    const data = await marketAPI.startBacktestTaskAIAnalysis(task.task_id, true)
    aiAnalysis.value = data.analysis
    task.ai_analysis = data.analysis
  } catch (error) {
    showError(error, '重新提交回测 AI 分析失败')
  } finally {
    aiAnalysisLoadingId.value = ''
  }
}

async function refreshAIAnalysis() {
  if (!aiAnalysisDialog.value || !aiAnalysisTask.value || !['queued', 'running'].includes(aiAnalysis.value?.status)) return
  try {
    const data = await marketAPI.getBacktestTaskAIAnalysis(aiAnalysisTask.value.task_id)
    aiAnalysis.value = data.analysis
    aiAnalysisTask.value.ai_analysis = data.analysis
  } catch (error) {
    // Keep the current result visible; the next polling cycle can recover.
  }
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
const aiRefreshTimer = window.setInterval(refreshAIAnalysis, 3000)
onBeforeUnmount(() => {
  window.clearInterval(refreshTimer)
  window.clearInterval(aiRefreshTimer)
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
.task-row { display: grid; grid-template-columns: 1fr auto auto auto; align-items: center; gap: 18px; padding: 12px 18px; }
.task-row strong, .task-row span { display: block; }
.task-row strong { color: #36584f; font-size: .82rem; }
.task-row span { color: #7e8984; font-size: .7rem; }
.task-progress { display: grid; grid-template-columns: 1fr 58px auto auto; align-items: center; gap: 10px; margin: 0 18px 12px; }
.task-progress > strong { color: #28705e; font-size: .74rem; text-align: right; }
.task-error { display: flex; align-items: center; gap: 6px; margin: 0 18px 14px; padding: 9px 11px; border-radius: 9px; color: #9e3f34; background: #fcedea; font-size: .75rem; }
.result-panel { margin: 0 18px 16px; padding: 13px; border: 1px solid #dbe9e1; border-radius: 12px; background: #fff; }
.ai-analysis-entry { display: flex; align-items: center; gap: 10px; margin: 0 18px 16px; padding: 12px 14px; border: 1px solid #cfe0d8; border-radius: 12px; background: linear-gradient(110deg, #edf6f1, #fff9ec); }
.ai-analysis-entry > div:first-child { display: flex; align-items: center; gap: 10px; flex: 1; color: #276653; }
.ai-analysis-entry strong, .ai-analysis-entry span { display: block; }.ai-analysis-entry strong { font-size: .78rem; }.ai-analysis-entry span { margin-top: 2px; color: #788880; font-size: .66rem; }
.result-metrics { display: grid; grid-template-columns: repeat(auto-fit, minmax(80px, 1fr)); gap: 8px; }
.result-metrics span { color: #84908a; font-size: .65rem; }
.result-metrics strong { display: block; margin-top: 3px; color: #264f43; font-size: .88rem; }
.result-metrics .positive { color: #147b59; }
.result-metrics .negative { color: #bd493c; }
.result-meta { margin-top: 10px; color: #939d98; font-size: .65rem; }
.signal-source-row { display: flex; align-items: center; flex-wrap: wrap; gap: 6px; margin-top: 10px; }
.signal-source-row > span { margin-right: 3px; color: #7f8b85; font-size: .68rem; }
.ledger-panel { margin-top: 12px; padding: 12px; border-radius: 10px; background: #f4f8f5; }
.live-ledger { margin: 0 18px 14px; border: 1px solid #d9e9e1; background: #eef7f2; }
.ledger-summary { display: grid; grid-template-columns: repeat(4, 1fr); gap: 8px; margin-bottom: 12px; }
.ledger-summary span { padding: 8px; border-radius: 8px; color: #7d8983; background: #fff; font-size: .65rem; }
.ledger-summary strong { display: block; margin-top: 2px; color: #285044; font-size: .82rem; }
.ledger-title { display: flex; justify-content: space-between; margin-bottom: 7px; color: #405f56; font-size: .72rem; }
.ledger-title span, .ledger-empty { color: #89948f; font-size: .66rem; }
.order-list { display: grid; gap: 4px; max-height: 310px; overflow: auto; }
.order-list { overflow-x: auto; }
.order-row { display: grid; grid-template-columns: 130px 44px 70px 65px 95px 95px 95px 62px minmax(100px, 1fr); align-items: center; gap: 7px; min-width: 850px; padding: 7px 8px; border-radius: 7px; background: #fff; color: #66746e; font-size: .65rem; }
.order-row .positive { color: #147b59; }
.order-row .negative { color: #bd493c; }
.order-row .close-action { color: #28748c; }
.order-price small { display: block; color: #98a49f; font-size: .56rem; line-height: 1.1; }
.order-price strong { display: block; margin-top: 2px; color: #315b4f; font-size: .68rem; }
.order-price.stop-loss strong { color: #bd493c; }
.order-price.take-profit strong { color: #147b59; }
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
.ai-analysis-dialog { border-radius: 20px !important; background: #f4f6f1; }
.ai-analysis-header { display: flex; align-items: center; justify-content: space-between; padding: 23px 27px; }.ai-analysis-header h2 { margin: 3px 0; }.ai-analysis-header span { color: #7c8983; font-size: .72rem; }.ai-header-actions { display: flex; align-items: center; gap: 8px; }
.ai-analysis-body { padding: 22px 27px !important; }.ai-waiting { display: flex; align-items: center; justify-content: center; gap: 20px; min-height: 190px; }.ai-waiting strong,.ai-waiting span { display: block; }.ai-waiting strong { color: #285749; }.ai-waiting span { margin-top: 5px; color: #798780; font-size: .72rem; }
.ai-summary-card,.ai-result-section { padding: 17px; border: 1px solid #dbe5df; border-radius: 14px; background: #fff; }.ai-summary-card { background: linear-gradient(120deg, #193d34, #2d6b59); color: #f5f3e9; }.ai-summary-card span,.ai-section-title span { color: #d6ad67; font-size: .61rem; font-weight: 800; letter-spacing: .12em; }.ai-summary-card h3,.ai-section-title h3 { margin: 3px 0; font-size: .9rem; }.ai-summary-card p { margin: 12px 0; font: 600 1rem/1.7 Georgia, serif; }.ai-quality { color: rgba(255,255,255,.75); font-size: .68rem; }.ai-quality > span { margin-left: 12px; color: rgba(255,255,255,.65); letter-spacing: normal; font-weight: 400; }
.ai-result-section { margin-top: 13px; }.diagnosis-grid { display: grid; grid-template-columns: repeat(2, 1fr); gap: 9px; margin-top: 10px; }.diagnosis-grid article { padding: 12px; border-radius: 10px; background: #f3f6f3; }.diagnosis-grid strong { display: block; margin: 8px 0 4px; color: #355a4f; font-size: .78rem; }.diagnosis-grid p,.suggestion-list p { margin: 0; color: #74817b; font-size: .69rem; line-height: 1.55; }
.suggestion-list { display: grid; gap: 9px; margin-top: 10px; }.suggestion-list article { display: grid; grid-template-columns: 42px 1fr; gap: 12px; padding: 13px; border-radius: 11px; background: #f1f5f2; }.suggestion-priority { display: grid; place-items: center; width: 38px; height: 38px; border-radius: 50%; color: #fff; background: #28705d; font-size: .72rem; font-weight: 800; }.suggestion-list h4 { margin: 0; color: #31584c; }.value-change { display: flex; align-items: center; gap: 7px; margin: 5px 0; color: #89948f; font-size: .69rem; }.value-change strong { color: #1c7359; }.suggestion-list small { display: block; margin-top: 5px; color: #77837d; font-size: .65rem; }
.ai-bottom-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 13px; }.risk-item,.plan-item { margin: 8px 0; padding-left: 12px; border-left: 3px solid #d69b4c; color: #687770; font-size: .7rem; }.ai-bottom-grid small { display: block; margin-top: 6px; color: #6f7e77; font-size: .66rem; }.ai-disclaimer { margin-top: 13px; color: #8a9690; font-size: .65rem; text-align: center; }.ai-completed-at { color: #84908a; font-size: .65rem; }
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
@media (max-width: 700px) { .backtest-page { padding: 15px; } .workbench-hero { align-items: flex-start; flex-direction: column; padding: 25px; } .metric-grid { grid-template-columns: 1fr 1fr; } .field-grid { grid-template-columns: 1fr; } .batch-summary { align-items: flex-start; flex-wrap: wrap; } .batch-name { min-width: calc(100% - 60px); } .task-row { grid-template-columns: 1fr auto auto; gap: 6px; } .task-row > div:first-child { grid-column: 1 / -1; } .task-progress { grid-template-columns: 1fr 52px; } .task-progress .v-btn { grid-column: 1 / -1; } .ledger-summary, .report-hero-metrics, .report-detail-grid { grid-template-columns: 1fr 1fr; } .order-row { grid-template-columns: 1fr 48px 70px 62px 92px 92px 92px 62px minmax(120px, 1fr); } .report-header { align-items: flex-start; } .report-header-actions .v-chip { display: none; } .report-body { padding: 14px !important; } .report-chart { height: 260px; } .ai-analysis-entry { align-items: flex-start; flex-wrap: wrap; }.ai-analysis-entry > div:first-child { min-width: 100%; }.diagnosis-grid,.ai-bottom-grid { grid-template-columns: 1fr; }.ai-analysis-header { align-items: flex-start; }.ai-analysis-body { padding: 15px !important; } }
</style>

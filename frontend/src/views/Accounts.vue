<template>
  <div class="accounts-page">
    <section class="account-hero">
      <div>
        <div class="eyebrow">ACCOUNT LEDGER</div>
        <h1>交易账户</h1>
        <p>MT5 连接、Paper 模拟和未来回测账户共享统一身份，但资金与持仓始终相互隔离。</p>
      </div>
      <v-btn variant="outlined" color="white" prepend-icon="mdi-refresh" :loading="loading" @click="loadAccounts">
        刷新账户
      </v-btn>
    </section>

    <v-alert type="info" variant="tonal" class="mb-5">
      MT5 实盘账户由 EA 上报自动发现，无需手工创建；Paper 账户可以部署自动交易策略，使用 EA 实时行情进行独立模拟撮合，不会向 MT5 下发订单。
    </v-alert>
    <v-alert v-if="message" :type="messageType" variant="tonal" closable class="mb-5" @click:close="message = ''">
      {{ message }}
    </v-alert>

    <section class="metric-grid">
      <article><span>账户总数</span><strong>{{ accounts.length }}</strong></article>
      <article><span>MT5 账户</span><strong>{{ mt5Accounts.length }}</strong></article>
      <article><span>在线终端</span><strong>{{ connectedCount }}</strong></article>
      <article><span>Paper 账户</span><strong>{{ paperAccounts.length }}</strong></article>
    </section>

    <div class="content-grid">
      <section class="account-catalog">
        <div class="section-heading">
          <div>
            <div class="section-tag">ACCOUNT CATALOG</div>
            <h2>我的账户</h2>
          </div>
          <span>资金快照由对应执行端维护</span>
        </div>

        <div v-if="!accounts.length && !loading" class="empty-state">
          <v-icon icon="mdi-wallet-plus-outline" size="50" />
          <h3>还没有交易账户</h3>
          <p>下载并安装 EA 后，系统会按 MT5 登录号和交易服务器自动建立账户；也可以先创建 Paper 账户。</p>
        </div>

        <div v-else class="account-list">
          <article v-for="account in accounts" :key="account.account_id" class="account-card" :class="account.account_type">
            <div class="account-topline">
              <div class="account-identity">
                <div class="account-mark">
                  <v-icon :icon="typeMeta(account.account_type).icon" />
                </div>
                <div>
                  <h3>{{ account.account_name }}</h3>
                  <span>#{{ account.account_id }} · {{ typeMeta(account.account_type).label }}</span>
                </div>
              </div>
              <div class="account-chips">
                <v-chip v-if="account.is_default" size="small" variant="tonal" color="amber-darken-2">默认实盘</v-chip>
                <v-chip v-if="account.status === 'archived'" size="small" variant="tonal" color="grey">已归档</v-chip>
                <v-chip v-else-if="account.account_type === 'mt5'" size="small" variant="flat" :color="account.active ? 'success' : 'error'">
                  {{ account.active ? '活跃账户' : '不活跃账户' }}
                </v-chip>
                <v-chip v-if="account.status !== 'archived'" size="small" variant="tonal" :color="account.trading_enabled ? 'success' : 'warning'">
                  {{ account.trading_enabled ? '交易运行' : '交易暂停' }}
                </v-chip>
                <v-chip size="small" variant="tonal" :color="statusMeta(account).color">
                  {{ statusMeta(account).label }}
                </v-chip>
                <v-chip v-if="account.account_type === 'mt5' && account.market_source" size="small" variant="tonal" :color="marketSourceMeta(account.market_source).color">
                  {{ marketSourceMeta(account.market_source).label }}
                </v-chip>
              </div>
            </div>

            <v-alert
              v-if="account.account_type === 'mt5' && ['reuse', 'blocked'].includes(account.market_source?.mode)"
              :type="account.market_source.mode === 'blocked' ? 'error' : 'info'"
              variant="tonal"
              density="compact"
              class="mb-4"
            >{{ account.market_source.message }}</v-alert>

            <div class="balance-row">
              <div><span>余额</span><strong>{{ money(account.balance, account.currency) }}</strong></div>
              <div><span>净值</span><strong>{{ money(account.equity, account.currency) }}</strong></div>
              <div><span>可用资金</span><strong>{{ money(account.free_margin, account.currency) }}</strong></div>
            </div>

            <dl class="account-details">
              <div><dt>环境</dt><dd>{{ environmentLabel(account.environment) }}</dd></div>
              <div><dt>账户币种</dt><dd>{{ account.currency }}</dd></div>
              <div v-if="account.account_type === 'mt5'"><dt>MT5 登录号</dt><dd>{{ account.mt5_login || '未上报' }}</dd></div>
              <div v-if="account.account_type === 'mt5'"><dt>交易服务器</dt><dd>{{ account.mt5_server || '未上报' }}</dd></div>
              <div><dt>资金更新时间</dt><dd>{{ formatTime(account.financial_updated_at) }}</dd></div>
              <div><dt>连接更新时间</dt><dd>{{ formatTime(account.last_seen_at) }}</dd></div>
              <div><dt>自动下单</dt><dd>{{ account.auto_trading_enabled ? '允许' : '仅推荐' }}</dd></div>
              <div><dt>账户风控</dt><dd>{{ account.max_total_positions }} 持仓 · {{ account.max_single_volume }} 手/单</dd></div>
            </dl>
            <div class="bound-strategies">
              <span>已绑定策略</span>
              <div v-if="account.deployments?.length" class="bound-strategy-list">
                <div v-for="deployment in account.deployments" :key="deployment.deployment_id" class="bound-strategy-item">
                  <v-chip size="x-small" :color="deploymentHealthMeta(deployment).color" variant="tonal">
                    {{ deployment.strategy_name || deployment.strategy_id }}<v-chip v-if="deployment.strategy_offline" size="x-small" color="grey" variant="tonal" class="ml-2">策略已下线</v-chip>
                  </v-chip>
                  <span class="bound-strategy-meta" :class="{ 'text-error': deploymentHealthMeta(deployment).alert }">
                    {{ lifecycleLabel(deployment.lifecycle_status) }} ·
                    {{ deployment.status === 'active' ? '运行中' : deploymentStatusLabel(deployment.status) }} ·
                    {{ deployEnabledLabel(deployment) }}
                  </span>
                  <span v-if="deploymentHealthMeta(deployment).alert" class="bound-strategy-alert" :title="deploymentHealthMeta(deployment).reason">
                    {{ deploymentHealthMeta(deployment).reason }}
                  </span>
                </div>
              </div>
              <strong v-else>尚未绑定策略</strong>
            </div>
            <div class="paper-actions">
              <v-btn color="primary" variant="tonal" prepend-icon="mdi-tune-variant" @click="openAccountManager(account)">
                账户管理
              </v-btn>
              <v-btn v-if="account.account_type === 'mt5'" color="secondary" variant="tonal" prepend-icon="mdi-download" :loading="eaDownloadingId === account.account_id" @click="downloadAccountEA(account)">
                重新下载 EA
              </v-btn>
              <v-btn v-if="account.account_type === 'mt5'" color="primary" variant="tonal" prepend-icon="mdi-link-variant" @click="openStrategyManager(account)">
                管理绑定策略
              </v-btn>
              <v-btn v-if="account.account_type === 'mt5'" color="success" variant="tonal" prepend-icon="mdi-chart-timeline-variant" :loading="runtimeLoadingId === account.account_id" @click="openLiveRuntime(account)">
                打开实盘运行台
              </v-btn>
              <v-btn v-if="account.account_type === 'paper'" color="primary" variant="tonal" prepend-icon="mdi-monitor-dashboard" :loading="runtimeLoadingId === account.account_id" @click="openPaperRuntime(account)">
                打开模拟运行台
              </v-btn>
            </div>
          </article>
        </div>
      </section>

      <v-card class="paper-card" elevation="0">
        <v-card-text>
          <div class="section-tag">MT5 ACCOUNT</div>
          <h2>连接新的 MT5 账户</h2>
          <p>下载 EA 并放入目标 MT5 终端。EA 启动后会自动上报登录号和交易服务器，相同账户重复安装不会重复创建。</p>
          <v-btn block color="secondary" prepend-icon="mdi-download-network-outline" :loading="eaDownloadingId === 'new'" class="mt-4" @click="downloadNewEA">
            下载 MT5 Terminal EA
          </v-btn>
          <v-divider class="my-6" />
          <div class="section-tag">PAPER ACCOUNT</div>
          <h2>建立模拟账户</h2>
          <p>定义独立资金与交易成本。账户只使用实时行情模拟成交，不会产生真实订单。</p>
          <v-form @submit.prevent="createPaper">
            <v-text-field v-model.trim="paperForm.name" label="账户名称" placeholder="例如：GOLD 策略模拟盘" variant="outlined" density="comfortable" class="mt-5" />
            <v-text-field v-model.number="paperForm.initialBalance" label="初始资金" type="number" min="1" variant="outlined" density="comfortable" />
            <v-select v-model="paperForm.currency" :items="currencies" label="账户币种" variant="outlined" density="comfortable" />
            <div class="paper-setting-grid">
              <v-text-field v-model.number="paperForm.leverage" label="杠杆" type="number" min="1" variant="outlined" density="comfortable" />
              <v-text-field v-model.number="paperForm.spreadPoints" label="模拟点差（点）" type="number" min="0" variant="outlined" density="comfortable" />
              <v-text-field v-model.number="paperForm.slippagePoints" label="滑点（点）" type="number" min="0" variant="outlined" density="comfortable" />
              <v-text-field v-model.number="paperForm.commissionPerLot" label="每手手续费" type="number" min="0" variant="outlined" density="comfortable" />
            </div>
            <div class="paper-note">
              <v-icon icon="mdi-flask-outline" />
              <span>市价单在下一条有效 Tick 按 Bid/Ask 成交。</span>
            </div>
            <v-btn type="submit" block size="large" color="primary" prepend-icon="mdi-wallet-plus-outline" :loading="creating" :disabled="!paperForm.name || paperForm.initialBalance <= 0" class="mt-5">
              创建 Paper 账户
            </v-btn>
          </v-form>
        </v-card-text>
      </v-card>
    </div>

    <v-dialog v-model="paperDialog" max-width="1160" scrollable @after-enter="renderEquityChart">
      <v-card v-if="paperDetail" class="runtime-dialog" elevation="0">
        <v-card-title class="runtime-header">
          <div>
            <div class="section-tag">LIVE PAPER EXECUTION</div>
            <h2>{{ paperDetail.account.account_name }}</h2>
            <span>实时模拟运行 · 不发送 MT5 指令</span>
          </div>
          <v-btn icon="mdi-close" variant="text" @click="closePaperRuntime" />
        </v-card-title>
        <v-divider />
        <v-card-text class="runtime-body">
          <section class="runtime-metrics">
            <article><span>余额</span><strong>{{ money(paperDetail.account.balance, paperDetail.account.currency) }}</strong></article>
            <article><span>净值</span><strong>{{ money(paperDetail.account.equity, paperDetail.account.currency) }}</strong></article>
            <article><span>可用资金</span><strong>{{ money(paperDetail.account.free_margin, paperDetail.account.currency) }}</strong></article>
            <article><span>持仓 / 成交</span><strong>{{ paperDetail.positions.length }} / {{ paperDetail.trades.length }}</strong></article>
          </section>

          <section class="deployment-workbench">
            <div>
              <div class="section-tag">STRATEGY DEPLOYMENT</div>
              <h3>策略运行实例</h3>
              <p>部署后仅在该 Paper 账户自动执行；包含 AI、转折点或整数点位信号源的策略可跳过回测直接模拟观察，实盘准入仍会检查验证证据。</p>
            </div>
            <div class="deployment-form">
              <v-select
                v-model="selectedStrategyId"
                :items="strategyOptions"
                item-title="label"
                item-value="value"
                label="选择策略"
                variant="outlined"
                density="compact"
                hide-details
              >
                <template #selection="{ item }">
                  <div class="strategy-select-value">
                    <span>{{ item.raw.strategy_name }}</span>
                    <small>{{ item.raw.symbol }}</small>
                    <v-chip v-if="item.raw.deployment" size="x-small" color="success">已部署</v-chip>
                  </div>
                </template>
                <template #item="{ props, item }">
                  <v-list-item v-bind="props" class="strategy-select-item">
                    <template #title>
                      <div class="strategy-option-title">
                        <strong>{{ item.raw.strategy_name }}</strong>
                        <v-chip v-if="item.raw.deployment" size="x-small" :color="deploymentStatusColor(item.raw.deployment.status)">{{ deploymentStatusLabel(item.raw.deployment.status) }}</v-chip>
                        <v-chip v-else size="x-small" variant="tonal">待部署</v-chip>
                      </div>
                    </template>
                    <template #subtitle>{{ item.raw.symbol }} · {{ item.raw.lifecycleLabel }}{{ item.raw.paper_eligibility_reason ? ` · ${item.raw.paper_eligibility_reason}` : '' }}</template>
                  </v-list-item>
                </template>
              </v-select>
              <v-btn color="primary" :disabled="!selectedStrategyId" :loading="deploying" @click="deploySelectedStrategy">部署策略</v-btn>
            </div>
          </section>
          <section v-if="paperDetail.deployments.length" class="active-deployment-strip">
            <span>当前运行实例</span>
            <div>
              <article v-for="deployment in paperDetail.deployments" :key="deployment.deployment_id">
                <v-icon :color="deploymentStatusColor(deployment.status)" size="16">mdi-play-circle</v-icon>
                <strong>{{ deployment.strategy_name || deployment.strategy_id }}</strong><v-chip v-if="deployment.strategy_offline" size="x-small" color="grey" variant="tonal" class="ml-2">策略已下线</v-chip>
                <small>{{ deployment.symbol }} · Paper</small>
                <v-chip size="x-small" :color="deploymentStatusColor(deployment.status)">{{ deploymentStatusLabel(deployment.status) }}</v-chip>
              </article>
            </div>
          </section>
          <div v-if="!paperDetail.deployments.length" class="runtime-empty compact">还没有部署策略</div>
          <div v-else class="deployment-list">
            <article v-for="deployment in paperDetail.deployments" :key="deployment.deployment_id">
              <div><strong>{{ deployment.strategy_name || deployment.strategy_id }}</strong><span>{{ deployment.symbol }} · Paper 自动执行 · {{ deploymentStatusLabel(deployment.status) }}</span></div>
              <v-switch
                :model-value="deployment.status === 'active'"
                color="success"
                inset
                hide-details
                :loading="deploymentLoadingId === deployment.deployment_id"
                @update:model-value="value => toggleDeployment(deployment, value)"
              />
              <v-btn icon="mdi-stop-circle-outline" size="small" variant="text" color="error" title="结束部署" :loading="deploymentLoadingId === deployment.deployment_id" @click="endDeployment(deployment, paperDetail.account.account_id)" />
            </article>
          </div>

          <section class="runtime-chart-card">
            <div class="runtime-section-title">
              <h3>账户净值</h3>
              <v-btn size="small" variant="tonal" color="primary" :loading="reportLoading" @click="loadPaperReport">生成运行报告</v-btn>
            </div>
            <div v-if="paperDetail.equity_curve.length" ref="equityChart" class="equity-chart" />
            <div v-else class="runtime-empty">收到第一条 EA Tick 后开始记录净值</div>
          </section>

          <section class="strategy-performance-card">
            <div class="runtime-section-title">
              <h3>策略收益贡献</h3>
              <span>按部署实例统计 · 分批平仓合并为一笔完整交易</span>
            </div>
            <div v-if="!paperDetail.strategy_performance?.length" class="runtime-empty compact">该账户暂无策略部署</div>
            <article v-for="item in paperDetail.strategy_performance || []" :key="item.deployment_id" class="strategy-performance-row">
              <header>
                <div><strong>{{ item.strategy_name }}</strong><span>{{ item.symbol }} · 部署于 {{ formatTime(item.deployed_at) }}</span></div>
                <v-chip size="x-small" :color="deploymentStatusColor(item.status)" variant="tonal">{{ deploymentStatusLabel(item.status) }}</v-chip>
              </header>
              <div class="strategy-performance-grid">
                <span>完整交易<b>{{ item.closed_position_count }}</b></span>
                <span>成交订单<b>{{ item.filled_order_count }}</b></span>
                <span>盈利 / 亏损 / 持平<b>{{ item.win_count }} / {{ item.loss_count }} / {{ item.breakeven_count }}</b></span>
                <span>胜率<b>{{ Number(item.win_rate).toFixed(2) }}%</b></span>
                <span>盈利金额<b class="positive">{{ signedMoney(item.gross_profit) }}</b></span>
                <span>亏损金额<b class="negative">-{{ Number(item.gross_loss || 0).toLocaleString('zh-CN', { maximumFractionDigits: 2 }) }}</b></span>
                <span>净盈利<b :class="item.net_profit >= 0 ? 'positive' : 'negative'">{{ signedMoney(item.net_profit) }}</b></span>
                <span>持仓 / 浮盈<b :class="item.unrealized_profit >= 0 ? 'positive' : 'negative'">{{ item.open_position_count }} / {{ signedMoney(item.unrealized_profit) }}</b></span>
                <span>手续费<b>{{ Number(item.commission || 0).toFixed(2) }}</b></span>
                <span>收益因子<b>{{ profitFactorLabel(item) }}</b></span>
                <span>平均盈利 / 亏损<b>{{ Number(item.average_win || 0).toFixed(2) }} / -{{ Number(item.average_loss || 0).toFixed(2) }}</b></span>
                <span>最大回撤 / 连亏<b>{{ Number(item.max_drawdown || 0).toFixed(2) }} / {{ item.max_consecutive_losses }} 次</b></span>
              </div>
            </article>
          </section>

          <section v-if="paperReport" class="paper-report">
            <div class="report-heading">
              <div><div class="section-tag">PERFORMANCE REPORT</div><h3>模拟盘绩效报告</h3></div>
              <v-select v-model="reportStrategyId" :items="reportStrategyOptions" item-title="label" item-value="value" label="报告范围" density="compact" variant="outlined" hide-details @update:model-value="loadPaperReport" />
            </div>
            <div class="report-metrics">
              <article><span>累计收益</span><strong :class="paperReport.summary.net_profit >= 0 ? 'positive' : 'negative'">{{ signedMoney(paperReport.summary.net_profit) }}</strong></article>
              <article><span>持仓胜率</span><strong>{{ paperReport.summary.position_win_rate }}%</strong></article>
              <article><span>持仓收益因子</span><strong>{{ paperReport.summary.position_profit_factor ?? '∞' }}</strong></article>
              <article><span>平均持仓 R</span><strong :class="paperReport.summary.average_position_r >= 0 ? 'positive' : 'negative'">{{ Number(paperReport.summary.average_position_r || 0).toFixed(2) }}R</strong></article>
              <article><span>已平仓 / 成交</span><strong>{{ paperReport.summary.closed_position_count }} / {{ paperReport.summary.deal_count }}</strong></article>
              <article><span>拒单次数</span><strong>{{ paperReport.summary.rejected_order_count }}</strong></article>
            </div>
            <div v-if="paperReport.backtest_benchmark" class="benchmark-panel">
              <div class="benchmark-title">
                <div><span>BACKTEST VS PAPER</span><h4>回测与模拟偏差</h4></div>
                <small>基准任务 {{ paperReport.backtest_benchmark.task_id }}</small>
              </div>
              <div class="benchmark-grid">
                <article><span>收益率</span><strong>{{ paperReport.summary.return_pct }}%</strong><small>回测 {{ paperReport.backtest_benchmark.return_pct }}% · 偏差 {{ signedDelta(paperReport.comparison.return_pct) }}pp</small></article>
                <article><span>胜率</span><strong>{{ paperReport.summary.win_rate }}%</strong><small>回测 {{ paperReport.backtest_benchmark.win_rate }}% · 偏差 {{ signedDelta(paperReport.comparison.win_rate) }}pp</small></article>
                <article><span>最大回撤</span><strong>{{ paperReport.summary.max_drawdown_pct }}%</strong><small>回测 {{ paperReport.backtest_benchmark.max_drawdown_pct }}% · 偏差 {{ signedDelta(paperReport.comparison.max_drawdown_pct) }}pp</small></article>
                <article><span>收益因子</span><strong>{{ paperReport.summary.profit_factor ?? '∞' }}</strong><small>回测 {{ paperReport.backtest_benchmark.profit_factor ?? '∞' }} · 偏差 {{ paperReport.comparison.profit_factor === null ? '--' : signedDelta(paperReport.comparison.profit_factor) }}</small></article>
                <article><span>成交次数</span><strong>{{ paperReport.summary.trade_count }}</strong><small>回测 {{ paperReport.backtest_benchmark.trade_count }} · 偏差 {{ signedDelta(paperReport.comparison.trade_count) }}</small></article>
              </div>
            </div>
            <div class="setup-performance-panel">
              <div class="benchmark-title">
                <div><span>SETUP PERFORMANCE</span><h4>按交易形态评估</h4></div>
                <small>
                  已归因 {{ paperReport.summary.setup_attributed_position_count || 0 }} / {{ paperReport.summary.closed_position_count || 0 }} 个已平仓持仓
                </small>
              </div>
              <div v-if="!paperReport.by_setup?.length" class="setup-empty">
                暂无带 Setup 归因的已平仓持仓。新订单完成平仓后将在这里开始统计。
              </div>
              <div v-else class="setup-performance-table">
                <article class="setup-performance-head">
                  <span>Setup</span><span>样本</span><span>胜率</span><span>平均 R</span><span>收益因子</span><span>净收益</span><span>连续亏损</span>
                </article>
                <article v-for="item in paperReport.by_setup" :key="item.name">
                  <strong>{{ setupLabel(item.name) }}</strong>
                  <span>{{ item.position_count }} 个 <v-chip size="x-small" :color="sampleColor(item.sample_status)" variant="tonal">{{ sampleLabel(item.sample_status) }}</v-chip></span>
                  <span>{{ item.win_rate }}%</span>
                  <span :class="item.average_r >= 0 ? 'positive' : 'negative'">{{ Number(item.average_r).toFixed(2) }}R</span>
                  <span>{{ item.profit_factor ?? '∞' }}</span>
                  <span :class="item.net_profit >= 0 ? 'positive' : 'negative'">{{ signedMoney(item.net_profit) }}</span>
                  <span>{{ item.max_consecutive_losses }}</span>
                </article>
              </div>
              <div v-if="paperReport.by_setup_direction?.length" class="setup-direction-grid">
                <div v-for="item in paperReport.by_setup_direction" :key="item.name">
                  <b>{{ setupDirectionLabel(item.name) }}</b>
                  <span>{{ item.position_count }} 个 · 胜率 {{ item.win_rate }}% · 平均 {{ Number(item.average_r).toFixed(2) }}R</span>
                </div>
              </div>
            </div>
            <div class="report-breakdowns">
              <div><h4>按策略</h4><p v-if="!paperReport.by_strategy.length">暂无成交</p><p v-for="item in paperReport.by_strategy" :key="item.name"><b>{{ strategyName(item.name) }}</b><span>{{ item.trade_count }} 笔 · {{ signedMoney(item.net_profit) }}</span></p></div>
              <div><h4>按品种</h4><p v-if="!paperReport.by_symbol.length">暂无成交</p><p v-for="item in paperReport.by_symbol" :key="item.name"><b>{{ item.name }}</b><span>{{ item.trade_count }} 笔 · 胜率 {{ item.win_rate }}%</span></p></div>
              <div><h4>按平仓原因</h4><p v-if="!paperReport.by_exit_reason.length">暂无成交</p><p v-for="item in paperReport.by_exit_reason" :key="item.name"><b>{{ exitReasonLabel(item.name) }}</b><span>{{ item.trade_count }} 笔 · {{ signedMoney(item.net_profit) }}</span></p></div>
            </div>
          </section>

          <section class="runtime-grid">
            <div class="runtime-table-card">
              <div class="runtime-section-title"><h3>当前持仓</h3><span>{{ paperDetail.positions.length }} 笔</span></div>
              <div v-if="!paperDetail.positions.length" class="runtime-empty compact">暂无持仓</div>
              <div v-for="position in paperDetail.positions" :key="position.position_id" class="paper-position-card">
                <div class="paper-position-head">
                  <div>
                    <b :class="position.direction === 'buy' ? 'positive' : 'negative'">{{ position.direction === 'buy' ? '买入' : '卖出' }}</b>
                    <strong>{{ position.symbol }} · {{ position.remaining_volume || position.volume }} / {{ position.volume }} 手</strong>
                    <span>{{ strategyName(position.strategy_id) }}</span>
                    <span v-if="position.setup_type">{{ setupLabel(position.setup_type) }} · {{ position.setup_profile_name || '默认持仓方案' }}</span>
                  </div>
                  <div class="paper-position-actions">
                    <v-chip size="x-small" :color="Number(position.stop_loss) && Number(position.take_profit) ? 'success' : 'warning'" variant="tonal">{{ paperProtectionLabel(position) }}</v-chip>
                    <v-btn size="x-small" variant="tonal" color="primary" @click="togglePaperPosition(position.position_id)">
                      {{ expandedPaperPositions.has(position.position_id) ? '收起轨迹' : '查看轨迹' }}
                    </v-btn>
                  </div>
                </div>
                <div class="paper-position-metrics">
                  <span>入场 <b>{{ price(position.entry_price) }}</b></span>
                  <span>当前 <b>{{ price(position.current_price) }}</b></span>
                  <span>止损 <b class="negative">{{ price(position.stop_loss) }}</b></span>
                  <span>止盈 <b class="positive">{{ price(position.take_profit) }}</b></span>
                  <span>浮盈 <b :class="position.unrealized_profit >= 0 ? 'positive' : 'negative'">{{ signedMoney(position.unrealized_profit) }}</b></span>
                </div>
                <div v-if="expandedPaperPositions.has(position.position_id)" class="paper-position-events">
                  <div v-if="!position.management_events?.length" class="runtime-empty compact">暂无持仓管理轨迹</div>
                  <div v-for="event in position.management_events || []" :key="event.event_id" class="paper-event-row">
                    <span>{{ formatTime(event.event_time) }}</span>
                    <b>{{ paperEventLabel(event.rule_type) }}</b>
                    <span>{{ event.message }}</span>
                    <small>SL {{ price(event.stop_loss) }} · TP {{ price(event.take_profit) }}</small>
                  </div>
                </div>
              </div>
            </div>
            <div class="runtime-table-card">
              <div class="runtime-section-title"><h3>最近成交</h3><span>最多显示 20 笔</span></div>
              <div v-if="!paperDetail.trades.length" class="runtime-empty compact">暂无成交</div>
              <div v-for="trade in paperDetail.trades.slice(0, 20)" :key="trade.trade_id" class="runtime-row trade-row">
                <span>{{ formatTime(trade.closed_at) }}</span>
                <b>{{ trade.symbol }} · {{ trade.direction === 'buy' ? '买入' : '卖出' }}</b>
                <span>持仓 {{ trade.position_id || '--' }}</span>
                <span>开仓 {{ price(trade.entry_price) }} · 初始止损 {{ price(trade.initial_stop_loss) }} · 初始止盈 {{ price(trade.initial_take_profit) }}</span>
                <span>{{ trade.setup_type ? `${setupLabel(trade.setup_type)} · ` : '' }}{{ trade.execution_reason || exitReasonLabel(trade.exit_reason) }}<template v-if="trade.realized_r"> · {{ Number(trade.realized_r).toFixed(2) }}R</template></span>
                <small class="reject-reason">{{ trade.open_reason || '策略信号触发开仓' }}</small>
                <strong :class="trade.net_profit >= 0 ? 'positive' : 'negative'">{{ signedMoney(trade.net_profit) }}</strong>
              </div>
            </div>
          </section>

          <section class="runtime-table-card orders-card">
            <div class="runtime-section-title"><h3>模拟订单流水</h3><span>最近 30 条 · 包含拒单和取消订单</span></div>
            <div v-if="!paperDetail.orders.length" class="runtime-empty compact">暂无订单</div>
            <div v-for="order in paperDetail.orders.slice(0, 30)" :key="order.order_id" class="runtime-row order-row">
              <span>{{ formatTime(order.requested_at) }}</span>
              <b :class="order.direction === 'buy' ? 'positive' : 'negative'">{{ order.direction === 'buy' ? '买入' : '卖出' }}</b>
              <span>{{ order.symbol }} · {{ order.requested_volume }} 手 · 持仓 {{ order.position_id || '成交后生成' }}</span>
              <span>初始止损 {{ price(order.initial_stop_loss) }} · 初始止盈 {{ price(order.initial_take_profit) }}</span>
              <span v-if="order.setup_type">{{ setupLabel(order.setup_type) }} · {{ order.setup_profile_name || '默认持仓方案' }}</span>
              <span>{{ order.filled_price ?? order.requested_price }}</span>
              <v-chip size="x-small" variant="tonal" :color="orderStatus(order.status).color">{{ orderStatus(order.status).label }}</v-chip>
              <span class="reject-reason">{{ order.open_reason || order.rejection_reason || '--' }}</span>
            </div>
          </section>

          <section class="runtime-table-card orders-card">
            <div class="runtime-section-title"><h3>后台运行日志</h3><span>最近 100 条</span></div>
            <div v-if="!paperDetail.runtime_logs?.length" class="runtime-empty compact">后台维护启动后将在这里记录心跳与撮合事件</div>
            <div v-for="log in paperDetail.runtime_logs || []" :key="log.id" class="runtime-row order-row">
              <span>{{ formatTime(log.created_at) }}</span>
              <b>{{ runtimeEventLabel(log.event_type) }}</b>
              <span>{{ log.message }}</span>
            </div>
          </section>
        </v-card-text>
      </v-card>
    </v-dialog>

    <v-dialog v-model="liveDialog" max-width="1160" scrollable persistent @after-enter="renderLiveEquityChart">
      <v-card v-if="liveDetail" class="runtime-dialog live-runtime-dialog" elevation="0">
        <v-card-title class="runtime-header">
          <div>
            <div class="section-tag">LIVE MT5 MONITORING</div>
            <h2>{{ liveDetail.account.account_name }}</h2>
            <span>只读实时监控 · 由 EA 上报账户、仓位和成交数据</span>
          </div>
          <div class="d-flex align-center ga-2">
            <v-chip size="small" :color="liveDetail.account.connected ? 'success' : 'warning'" variant="tonal">{{ liveDetail.account.connected ? '终端在线' : '终端离线' }}</v-chip>
            <v-btn icon="mdi-close" variant="text" @click="closeLiveRuntime" />
          </div>
        </v-card-title>
        <v-divider />
        <v-card-text class="runtime-body">
          <section class="runtime-metrics">
            <article><span>余额</span><strong>{{ money(liveDetail.account.balance, liveDetail.account.currency) }}</strong></article>
            <article><span>净值</span><strong>{{ money(liveDetail.account.equity, liveDetail.account.currency) }}</strong></article>
            <article><span>可用资金</span><strong>{{ money(liveDetail.account.free_margin, liveDetail.account.currency) }}</strong></article>
            <article><span>持仓 / 最近成交</span><strong>{{ liveDetail.positions.length }} / {{ liveDetail.trades.length }}</strong></article>
          </section>

          <section class="runtime-chart-card">
            <div class="runtime-section-title"><h3>实盘账户净值</h3><span>每 6 秒自动刷新</span></div>
            <div v-if="liveDetail.equity_curve.length" ref="liveEquityChart" class="equity-chart" />
            <div v-else class="runtime-empty">等待 EA 上报第一条账户资金快照</div>
          </section>

          <section class="strategy-performance-card">
            <div class="runtime-section-title">
              <h3>策略收益贡献</h3>
              <span>按部署实例统计 · 手工成交不计入策略</span>
            </div>
            <div v-if="!liveDetail.strategy_performance?.length" class="runtime-empty compact">该账户暂无实盘策略部署</div>
            <article v-for="item in liveDetail.strategy_performance || []" :key="item.deployment_id" class="strategy-performance-row">
              <header>
                <div><strong>{{ item.strategy_name }}</strong><span>{{ item.symbol }} · 部署于 {{ formatTime(item.deployed_at) }}</span></div>
                <v-chip size="x-small" :color="deploymentStatusColor(item.status)" variant="tonal">{{ deploymentStatusLabel(item.status) }}</v-chip>
              </header>
              <div class="strategy-performance-grid">
                <span>完整交易<b>{{ item.closed_position_count }}</b></span>
                <span>成交订单<b>{{ item.filled_order_count }}</b></span>
                <span>盈利 / 亏损 / 持平<b>{{ item.win_count }} / {{ item.loss_count }} / {{ item.breakeven_count }}</b></span>
                <span>胜率<b>{{ Number(item.win_rate).toFixed(2) }}%</b></span>
                <span>盈利金额<b class="positive">{{ signedMoney(item.gross_profit) }}</b></span>
                <span>亏损金额<b class="negative">-{{ Number(item.gross_loss || 0).toLocaleString('zh-CN', { maximumFractionDigits: 2 }) }}</b></span>
                <span>净盈利<b :class="item.net_profit >= 0 ? 'positive' : 'negative'">{{ signedMoney(item.net_profit) }}</b></span>
                <span>持仓 / 浮盈<b :class="item.unrealized_profit >= 0 ? 'positive' : 'negative'">{{ item.open_position_count }} / {{ signedMoney(item.unrealized_profit) }}</b></span>
                <span>手续费<b>{{ Number(item.commission || 0).toFixed(2) }}</b></span>
                <span>收益因子<b>{{ profitFactorLabel(item) }}</b></span>
                <span>平均盈利 / 亏损<b>{{ Number(item.average_win || 0).toFixed(2) }} / -{{ Number(item.average_loss || 0).toFixed(2) }}</b></span>
                <span>最大回撤 / 连亏<b>{{ Number(item.max_drawdown || 0).toFixed(2) }} / {{ item.max_consecutive_losses }} 次</b></span>
              </div>
            </article>
          </section>

          <section class="runtime-grid">
            <div class="runtime-table-card">
              <div class="runtime-section-title"><h3>当前实盘持仓</h3><span>{{ liveDetail.positions.length }} 笔</span></div>
              <div v-if="!liveDetail.positions.length" class="runtime-empty compact">暂无持仓</div>
              <div v-for="position in liveDetail.positions" :key="position.ticket" class="paper-position-card">
                <div class="paper-position-head">
                  <div>
                    <b :class="position.direction === 'buy' ? 'positive' : 'negative'">{{ position.direction === 'buy' ? '买入' : '卖出' }}</b>
                    <strong>{{ position.symbol }} · {{ position.volume }} 手 · #{{ position.ticket }}</strong>
                    <span>{{ position.comment || 'MT5 持仓' }}</span>
                  </div>
                  <div class="paper-position-actions">
                    <v-chip size="x-small" :color="Number(position.sl) || Number(position.tp) ? 'success' : 'warning'" variant="tonal">{{ paperProtectionLabel({ stop_loss: position.sl, take_profit: position.tp }) }}</v-chip>
                    <v-btn size="x-small" variant="tonal" color="primary" @click="toggleLivePosition(position.ticket)">{{ expandedLivePositions.has(position.ticket) ? '收起轨迹' : '查看轨迹' }}</v-btn>
                  </div>
                </div>
                <div class="paper-position-metrics">
                  <span>入场 <b>{{ price(position.price_open) }}</b></span>
                  <span>止损 <b class="negative">{{ price(position.sl) }}</b></span>
                  <span>止盈 <b class="positive">{{ price(position.tp) }}</b></span>
                  <span>距 SL <b>{{ price(position.distance_sl) }}</b></span>
                  <span>浮盈 <b :class="Number(position.profit) >= 0 ? 'positive' : 'negative'">{{ signedMoney(position.profit) }}</b></span>
                </div>
                <div v-if="expandedLivePositions.has(position.ticket)" class="paper-position-events">
                  <div v-if="!position.management_events?.length" class="runtime-empty compact">暂无服务端持仓治理轨迹</div>
                  <div v-for="event in position.management_events || []" :key="event.event_id" class="paper-event-row"><span>{{ formatTime(event.event_time) }}</span><b>{{ paperEventLabel(event.rule_type) }}</b><span>{{ event.message }}</span><small>SL {{ price(event.stop_loss) }} · TP {{ price(event.take_profit) }}</small></div>
                </div>
              </div>
            </div>
            <div class="runtime-table-card">
              <div class="runtime-section-title"><h3>最近 MT5 成交</h3><span>最多 20 笔</span></div>
              <div v-if="!liveDetail.trades.length" class="runtime-empty compact">暂无成交上报</div>
              <div v-for="trade in liveDetail.trades.slice(0, 20)" :key="trade.ticket" class="runtime-row trade-row">
                <span>{{ trade.time || '--' }}</span>
                <b :class="trade.type === 0 ? 'positive' : 'negative'">{{ trade.type_text }} · {{ trade.symbol }}</b>
                <span>Position {{ trade.mt5_position_id || '--' }} · {{ trade.entry_text }} · {{ trade.order_source }}</span>
                <span v-if="trade.strategy_triggered">{{ trade.setup_type ? `${setupLabel(trade.setup_type)} · ` : '' }}{{ trade.execution_reason || '策略成交' }}</span>
                <small v-if="trade.open_reason" class="reject-reason">{{ trade.open_reason }} · 初始 SL {{ price(trade.initial_stop_loss) }} · TP {{ price(trade.initial_take_profit) }}</small>
                <strong :class="Number(trade.profit) >= 0 ? 'positive' : 'negative'">{{ signedMoney(trade.profit) }}</strong>
              </div>
            </div>
          </section>

          <section class="runtime-table-card orders-card">
            <div class="runtime-section-title"><h3>策略下单与执行回报</h3><span>服务端指令在 MT5 的实际成交情况</span></div>
            <div v-if="!liveDetail.execution_reports.length" class="runtime-empty compact">暂无策略指令执行回报</div>
            <div v-for="report in liveDetail.execution_reports" :key="report.id" class="runtime-row order-row">
              <span>{{ formatTime(report.reported_at) }}</span>
              <b :class="['b', 'buy'].includes(report.action) ? 'positive' : 'negative'">{{ ['b', 'buy'].includes(report.action) ? '买入' : '卖出' }}</b>
              <span>{{ report.symbol }} · {{ report.executed_volume || report.requested_volume }} 手 · Position {{ report.mt5_position_id || '--' }}</span>
              <span>{{ price(report.executed_price || report.requested_price) }} · 初始 SL {{ price(report.initial_stop_loss) }} · TP {{ price(report.initial_take_profit) }}</span>
              <v-chip size="x-small" :color="report.success ? 'success' : 'error'" variant="tonal">{{ report.success ? '已成交' : '失败' }}</v-chip>
              <span v-if="report.setup_type">{{ setupLabel(report.setup_type) }} · {{ report.setup_profile_name || '默认持仓方案' }}</span>
              <span class="reject-reason">{{ report.open_reason || report.error_message || `滑点 ${Number(report.slippage || 0).toFixed(5)}` }}</span>
            </div>
          </section>
        </v-card-text>
      </v-card>
    </v-dialog>

    <v-dialog v-model="accountDialog" max-width="680">
      <v-card v-if="managedAccount" class="binding-dialog" elevation="0">
        <v-card-title class="binding-header">
          <div><div class="section-tag">ACCOUNT CONTROL</div><h2>账户管理</h2><span>{{ managedAccount.mt5_login || managedAccount.account_type }} · {{ managedAccount.mt5_server || 'AI Trader' }}</span></div>
          <v-btn icon="mdi-close" variant="text" @click="accountDialog = false" />
        </v-card-title>
        <v-card-text>
          <v-alert type="info" variant="tonal" density="compact" class="mb-4">
            MT5 登录号和交易服务器由 EA 上报，不允许手工修改。暂停交易不会中断行情和资金上报。
          </v-alert>
          <v-text-field v-model.trim="accountForm.accountName" label="账户备注名" variant="outlined" />
          <v-switch v-model="accountForm.tradingEnabled" color="success" inset label="允许该账户产生新交易" />
          <v-switch v-model="accountForm.autoTradingEnabled" color="success" inset label="允许策略自动确认并下单" />
          <div class="paper-setting-grid">
            <v-text-field v-model.number="accountForm.maxTotalPositions" label="最大总持仓" type="number" min="1" max="100" variant="outlined" />
            <v-text-field v-model.number="accountForm.maxSingleVolume" label="单笔最大手数" type="number" min="0.01" step="0.01" variant="outlined" />
            <v-text-field v-model.number="accountForm.dailyLossLimit" label="每日最大亏损（%）" type="number" min="0.1" step="0.1" variant="outlined" />
            <v-text-field v-model.number="accountForm.dailyOrderLimit" label="每日订单上限" type="number" min="1" variant="outlined" />
          </div>
          <div class="paper-actions mt-2">
            <v-btn color="primary" :loading="accountSaving" @click="saveAccountControls">保存账户配置</v-btn>
            <v-btn v-if="managedAccount.status === 'archived'" color="success" variant="tonal" :loading="accountSaving" @click="restoreManagedAccount">恢复账户</v-btn>
            <v-btn v-else color="error" variant="tonal" :disabled="managedAccount.account_type === 'mt5' && managedAccount.connected" :loading="accountSaving" @click="archiveManagedAccount">归档账户</v-btn>
          </div>
        </v-card-text>
      </v-card>
    </v-dialog>

    <v-dialog v-model="strategyDialog" max-width="720">
      <v-card v-if="selectedAccount" class="binding-dialog" elevation="0">
        <v-card-title class="binding-header">
          <div><div class="section-tag">ACCOUNT STRATEGIES</div><h2>{{ selectedAccount.account_name }}</h2><span>#{{ selectedAccount.account_id }} · {{ typeMeta(selectedAccount.account_type).label }}</span></div>
          <v-btn icon="mdi-close" variant="text" @click="strategyDialog = false" />
        </v-card-title>
        <v-card-text>
          <v-alert type="info" variant="tonal" density="compact" class="mb-4">该账户只会执行这里处于“运行中”的策略，其他账户的绑定不会影响本账户。</v-alert>
          <section v-if="selectedAccount.deployments?.length" class="active-deployment-strip live">
            <span>当前运行实例</span>
            <div>
              <article v-for="deployment in selectedAccount.deployments" :key="deployment.deployment_id">
                <v-icon :color="deploymentStatusColor(deployment.status)" size="16">mdi-server-network</v-icon>
                <strong>{{ deployment.strategy_name || deployment.strategy_id }}</strong>
                <small>{{ deployment.symbol }} · MT5 实盘</small>
                <v-chip size="x-small" :color="deploymentStatusColor(deployment.status)">{{ deploymentStatusLabel(deployment.status) }}</v-chip>
              </article>
            </div>
          </section>
          <div class="binding-form">
            <v-select v-model="accountStrategyId" :items="accountStrategyOptions" item-title="label" item-value="value" label="选择可绑定策略" variant="outlined" density="compact" hide-details>
              <template #item="{ props, item }">
                <v-list-item v-bind="props" class="strategy-select-item">
                  <template #title><div class="strategy-option-title"><strong>{{ item.raw.strategy_name }}</strong><v-chip size="x-small" variant="tonal">待绑定</v-chip></div></template>
                  <template #subtitle>{{ item.raw.symbol }} · {{ item.raw.lifecycleLabel }}{{ item.raw.paper_eligibility_reason ? ` · ${item.raw.paper_eligibility_reason}` : '' }}</template>
                </v-list-item>
              </template>
            </v-select>
            <v-btn color="primary" :disabled="!accountStrategyId" :loading="bindingStrategy" @click="bindAccountStrategy">绑定策略</v-btn>
          </div>
          <div v-if="!selectedAccount.deployments?.length" class="runtime-empty compact">当前账户尚未绑定策略</div>
          <div v-else class="binding-list">
            <article v-for="deployment in selectedAccount.deployments" :key="deployment.deployment_id">
              <div><strong>{{ deployment.strategy_name || deployment.strategy_id }}</strong><span>{{ deployment.symbol }} · {{ deployment.execution_mode === 'live' ? 'MT5 实盘' : 'Paper 模拟' }}</span></div>
              <div class="binding-controls">
                <v-switch :model-value="deployment.status === 'active'" color="success" inset hide-details :loading="deploymentLoadingId === deployment.deployment_id" @update:model-value="value => toggleAccountDeployment(deployment, value)" />
                <v-btn icon="mdi-stop-circle-outline" size="small" variant="text" color="error" title="结束部署" :loading="deploymentLoadingId === deployment.deployment_id" @click="endDeployment(deployment, selectedAccount.account_id)" />
                <v-btn v-if="selectedAccount.account_type === 'mt5'" icon="mdi-link-variant-off" size="small" variant="text" color="error" @click="removeAccountDeployment(deployment)" />
              </div>
            </article>
          </div>
        </v-card-text>
      </v-card>
    </v-dialog>
  </div>
</template>

<script setup>
import { computed, nextTick, onBeforeUnmount, reactive, ref } from 'vue'
import * as echarts from 'echarts'
import { accountAPI } from '../api/trading'

const accounts = ref([])
const loading = ref(false)
const creating = ref(false)
const eaDownloadingId = ref(null)
const accountDialog = ref(false)
const accountSaving = ref(false)
const managedAccount = ref(null)
const accountForm = reactive({
  accountName: '', tradingEnabled: true, autoTradingEnabled: true,
  maxTotalPositions: 10, maxSingleVolume: 10,
  dailyLossLimit: 5, dailyOrderLimit: 100,
})
const strategyDialog = ref(false)
const selectedAccount = ref(null)
const accountStrategyId = ref('')
const bindingStrategy = ref(false)
const paperDialog = ref(false)
const paperDetail = ref(null)
const liveDialog = ref(false)
const liveDetail = ref(null)
const paperContext = reactive({ strategies: [] })
const paperContextLoaded = ref(false)
const selectedStrategyId = ref('')
const deploying = ref(false)
const deploymentLoadingId = ref('')
const runtimeLoadingId = ref(null)
const reportLoading = ref(false)
const paperReport = ref(null)
const reportStrategyId = ref('')
const equityChart = ref(null)
const liveEquityChart = ref(null)
const expandedPaperPositions = ref(new Set())
const expandedLivePositions = ref(new Set())
let equityChartInstance = null
let liveEquityChartInstance = null
let liveRefreshTimer = null
const message = ref('')
const messageType = ref('success')
const currencies = ['USD', 'CNY', 'EUR', 'GBP', 'JPY']
const paperForm = reactive({
  name: '', initialBalance: 100000, currency: 'USD', leverage: 100,
  spreadPoints: 0, slippagePoints: 0, commissionPerLot: 0,
})

const mt5Accounts = computed(() => accounts.value.filter(item => item.account_type === 'mt5'))
const paperAccounts = computed(() => accounts.value.filter(item => item.account_type === 'paper'))
const connectedCount = computed(() => mt5Accounts.value.filter(item => item.connected).length)
const strategyOptions = computed(() => paperContext.strategies.filter(
  strategy => strategy.paper_eligible
).map(strategy => {
  const deployment = (paperDetail.value?.deployments || []).find(
    item => item.strategy_id === strategy.strategy_id,
  )
  return {
    value: strategy.strategy_id,
    label: `${deployment ? '运行中 · ' : ''}${strategy.strategy_name} · ${strategy.symbol}`,
    ...strategy,
    deployment,
    lifecycleLabel: lifecycleLabel(strategy.lifecycle_status),
  }
}).sort((left, right) => Number(Boolean(right.deployment)) - Number(Boolean(left.deployment))))
const accountStrategyOptions = computed(() => {
  if (!selectedAccount.value) return []
  const existing = new Set((selectedAccount.value.deployments || []).map(item => item.strategy_id))
  return paperContext.strategies.filter(strategy => {
    const eligible = selectedAccount.value.account_type === 'mt5'
      ? strategy.live_eligible : strategy.paper_eligible
    return eligible && !existing.has(strategy.strategy_id)
  }).map(strategy => ({
    value: strategy.strategy_id,
    label: `${strategy.strategy_name} · ${strategy.symbol}${strategy.paper_eligibility_reason ? ' · 可直接模拟' : ''}`,
    ...strategy,
    lifecycleLabel: lifecycleLabel(strategy.lifecycle_status),
  }))
})
const reportStrategyOptions = computed(() => [
  { value: '', label: '全部策略' },
  ...(paperDetail.value?.deployments || []).map(item => ({
    value: item.strategy_id, label: item.strategy_name || item.strategy_id,
  })),
])

const typeMap = {
  mt5: { label: 'MT5 经纪商账户', icon: 'mdi-server-network' },
  paper: { label: 'Paper 模拟账户', icon: 'mdi-flask-outline' },
  backtest: { label: '回测账户', icon: 'mdi-history' },
}
function typeMeta(type) { return typeMap[type] || typeMap.paper }
function statusMeta(account) {
  if (account.status === 'archived') return { label: '已归档', color: 'grey' }
  if (account.account_type === 'mt5') return account.connected
    ? { label: '终端在线', color: 'success' }
    : { label: '终端离线', color: 'grey' }
  return { label: '模拟引擎就绪', color: 'teal' }
}
function environmentLabel(value) {
  return { live: '实盘', demo: '经纪商模拟', simulated: '系统模拟', unknown: '待识别' }[value] || value
}
function money(value, currency) {
  return `${Number(value || 0).toLocaleString('zh-CN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })} ${currency}`
}
function formatTime(value) {
  return value ? new Date(value * 1000).toLocaleString('zh-CN', {
    hour12: false,
    timeZone: 'Asia/Shanghai',
  }) : '暂无数据'
}
function signedMoney(value) {
  const number = Number(value || 0)
  return `${number > 0 ? '+' : ''}${number.toLocaleString('zh-CN', { maximumFractionDigits: 2 })}`
}
function signedDelta(value) {
  const number = Number(value || 0)
  return `${number > 0 ? '+' : ''}${number.toFixed(2)}`
}
function profitFactorLabel(item) {
  if (!Number(item?.closed_position_count || 0)) return '--'
  if (item.profit_factor == null) return Number(item.gross_profit || 0) > 0 ? '∞' : '--'
  return Number(item.profit_factor).toFixed(2)
}
function price(value) {
  const number = Number(value || 0)
  if (!number) return '-'
  return number.toLocaleString('zh-CN', {
    minimumFractionDigits: 2,
    maximumFractionDigits: 5,
  })
}
function paperProtectionLabel(position) {
  const hasSl = Number(position.stop_loss || 0) > 0
  const hasTp = Number(position.take_profit || 0) > 0
  if (hasSl && hasTp) return 'SL/TP'
  if (hasSl) return '仅止损'
  if (hasTp) return '仅止盈'
  return '未保护'
}
function paperEventLabel(type) {
  return {
    initial_plan: '初始保护',
    break_even: '保本止损',
    trailing_stop: '移动止损',
    pivot_trailing: '转折跟进',
    partial_take_profit: '分批止盈',
    stop_loss_update: '止损更新',
    reverse_signal: '反向退出',
    max_holding_bars: '时间退出',
  }[type] || type || '持仓管理'
}
function togglePaperPosition(positionId) {
  const next = new Set(expandedPaperPositions.value)
  if (next.has(positionId)) next.delete(positionId)
  else next.add(positionId)
  expandedPaperPositions.value = next
}
function toggleLivePosition(ticket) {
  const next = new Set(expandedLivePositions.value)
  if (next.has(ticket)) next.delete(ticket)
  else next.add(ticket)
  expandedLivePositions.value = next
}
function deploymentStatusLabel(status) {
  return { active: '运行中', paused: '已暂停', completed: '已结束', offline: '策略已下线' }[status] || status
}
function deploymentStatusColor(status) {
  return { active: 'success', paused: 'warning', completed: 'grey' }[status] || 'grey'
}
function marketSourceMeta(source) {
  return {
    primary: { label: '行情主源', color: 'success' },
    reuse: { label: '复用行情', color: 'info' },
    blocked: { label: '行情冲突', color: 'error' },
    pending: { label: '待识别行情', color: 'grey' },
  }[source?.mode] || { label: '待识别行情', color: 'grey' }
}
function lifecycleLabel(status) {
  return {
    draft: '草稿',
    backtesting: '回测中',
    backtest_passed: '回测通过',
    paper_trading: '模拟验证',
    production: '实盘可用',
    retired: '已退役',
  }[status] || status || '未知'
}
// 策略是否处于"可用于实盘"的生命周期阶段
function lifecycleUsable(lifecycle) {
  return ['backtest_passed', 'paper_trading', 'production'].includes(lifecycle)
}
function deployEnabledLabel(deployment) {
  return deployment.status === 'active' ? '部署运行中 · 自动执行' : `部署${deploymentStatusLabel(deployment.status)}`
}
// 部署健康度由部署状态、生命周期和账户开关决定。
function deploymentHealthMeta(deployment) {
  const active = deployment.status === 'active'
  const usable = lifecycleUsable(deployment.lifecycle_status)
  if (active && usable) {
    return { color: 'success', alert: false, reason: '' }
  }
  if (!active) {
    return { color: 'warning', alert: true, reason: `部署${deploymentStatusLabel(deployment.status)}，策略停止运行` }
  }
  if (!usable) {
    return { color: 'error', alert: true, reason: `策略阶段「${lifecycleLabel(deployment.lifecycle_status)}」不可用于交易` }
  }
  return { color: 'success', alert: false, reason: '' }
}
function exitReasonLabel(reason) { return { take_profit: '止盈', stop_loss: '止损' }[reason] || reason }
function setupLabel(value) {
  return {
    range_reversal: '箱体反转',
    range_breakout: '箱体突破',
    trend_pullback: '趋势回调',
    trend_breakout: '趋势突破',
    triangle_breakout: '三角突破',
    reversal: '转折入场',
    generic_entry: '通用入场',
  }[value] || value || '通用入场'
}
function setupDirectionLabel(value) {
  const [setup, direction] = String(value || '').split('|')
  return `${setupLabel(setup)} · ${direction === 'buy' ? '买入' : direction === 'sell' ? '卖出' : direction || '未知方向'}`
}
function sampleLabel(value) {
  return { insufficient: '样本不足', preliminary: '初步观察', reliable: '相对可靠' }[value] || value
}
function sampleColor(value) {
  return { insufficient: 'warning', preliminary: 'info', reliable: 'success' }[value] || 'grey'
}
function strategyName(strategyId) {
  return paperContext.strategies.find(item => item.strategy_id === strategyId)?.strategy_name || strategyId
}
function orderStatus(status) {
  return {
    pending: { label: '待成交', color: 'info' },
    filled: { label: '已成交', color: 'success' },
    rejected: { label: '已拒绝', color: 'error' },
    canceled: { label: '已取消', color: 'grey' },
  }[status] || { label: status, color: 'grey' }
}
function runtimeEventLabel(type) {
  return { heartbeat: '运行心跳', execution: '撮合事件' }[type] || type
}

async function loadPaperContext(force = false) {
  if (paperContextLoaded.value && !force) return
  const contextData = await accountAPI.getPaperContext()
  paperContext.strategies = contextData.strategies || []
  paperContextLoaded.value = true
}

async function loadAccounts(includeContext = false) {
  loading.value = true
  try {
    const data = await accountAPI.list()
    accounts.value = data.accounts || []
    if (includeContext) await loadPaperContext()
  } catch (error) {
    messageType.value = 'error'
    message.value = error.response?.data?.detail || '加载交易账户失败'
  } finally {
    loading.value = false
  }
}

async function createPaper() {
  creating.value = true
  try {
    const data = await accountAPI.createPaper({
      account_name: paperForm.name,
      initial_balance: paperForm.initialBalance,
      currency: paperForm.currency,
      leverage: paperForm.leverage,
      spread_points: paperForm.spreadPoints,
      slippage_points: paperForm.slippagePoints,
      commission_per_lot: paperForm.commissionPerLot,
    })
    messageType.value = 'success'
    message.value = data.message
    paperForm.name = ''
    await loadAccounts()
  } catch (error) {
    messageType.value = 'error'
    message.value = error.response?.data?.detail || '创建 Paper 账户失败'
  } finally {
    creating.value = false
  }
}

async function downloadAccountEA(account) {
  eaDownloadingId.value = account.account_id
  await downloadEA(`${account.account_name} 的 EA 已下载；终端启动后将按实际登录账户自动识别`)
}

async function downloadNewEA() {
  eaDownloadingId.value = 'new'
  await downloadEA('EA 已下载；在目标 MT5 终端启动后，账户会自动出现在这里')
}

async function downloadEA(successMessage) {
  try {
    const response = await accountAPI.downloadMt5EA()
    const filename = response.headers['x-ea-filename'] || 'mt5TerminalEA.ex5'
    const url = URL.createObjectURL(response.data)
    const link = document.createElement('a')
    link.href = url
    link.download = filename
    link.click()
    URL.revokeObjectURL(url)
    messageType.value = 'success'
    message.value = successMessage
  } catch (error) {
    messageType.value = 'error'
    message.value = error.response?.data?.detail || '下载 EA 失败'
  } finally {
    eaDownloadingId.value = null
  }
}

function openAccountManager(account) {
  managedAccount.value = account
  Object.assign(accountForm, {
    accountName: account.account_name,
    tradingEnabled: account.trading_enabled,
    autoTradingEnabled: account.auto_trading_enabled,
    maxTotalPositions: account.max_total_positions,
    maxSingleVolume: account.max_single_volume,
    dailyLossLimit: account.daily_loss_limit,
    dailyOrderLimit: account.daily_order_limit,
  })
  accountDialog.value = true
}

async function saveAccountControls() {
  accountSaving.value = true
  try {
    const data = await accountAPI.update(managedAccount.value.account_id, {
      account_name: accountForm.accountName,
      trading_enabled: accountForm.tradingEnabled,
      auto_trading_enabled: accountForm.autoTradingEnabled,
      max_total_positions: accountForm.maxTotalPositions,
      max_single_volume: accountForm.maxSingleVolume,
      daily_loss_limit: accountForm.dailyLossLimit,
      daily_order_limit: accountForm.dailyOrderLimit,
    })
    messageType.value = 'success'
    message.value = data.message
    await loadAccounts()
    managedAccount.value = accounts.value.find(item => item.account_id === data.account.account_id)
  } catch (error) {
    messageType.value = 'error'
    message.value = error.response?.data?.detail || '保存账户配置失败'
  } finally {
    accountSaving.value = false
  }
}

async function archiveManagedAccount() {
  if (!confirm(`确定归档“${managedAccount.value.account_name}”吗？关联策略会暂停运行。`)) return
  accountSaving.value = true
  try {
    const data = await accountAPI.archive(managedAccount.value.account_id)
    messageType.value = 'success'
    message.value = data.message
    accountDialog.value = false
    await loadAccounts()
  } catch (error) {
    messageType.value = 'error'
    message.value = error.response?.data?.detail || '归档账户失败'
  } finally {
    accountSaving.value = false
  }
}

async function restoreManagedAccount() {
  accountSaving.value = true
  try {
    const data = await accountAPI.restore(managedAccount.value.account_id)
    messageType.value = 'success'
    message.value = data.message
    accountDialog.value = false
    await loadAccounts()
  } catch (error) {
    messageType.value = 'error'
    message.value = error.response?.data?.detail || '恢复账户失败'
  } finally {
    accountSaving.value = false
  }
}

function openStrategyManager(account) {
  selectedAccount.value = account
  accountStrategyId.value = ''
  strategyDialog.value = true
  loadPaperContext()
}

async function bindAccountStrategy() {
  const preflight = await accountAPI.deploymentPreflight(selectedAccount.value.account_id, accountStrategyId.value)
  if (preflight.warnings?.length && !confirm(`部署风险提醒：\n\n${preflight.warnings.join('\n\n')}\n\n仍然部署吗？`)) return
  bindingStrategy.value = true
  try {
    const data = await accountAPI.deployStrategy(selectedAccount.value.account_id, accountStrategyId.value)
    messageType.value = 'success'
    message.value = [data.message, ...(data.warnings || [])].join('；')
    accountStrategyId.value = ''
    await refreshSelectedAccount()
  } catch (error) {
    messageType.value = 'error'
    message.value = error.response?.data?.detail || '绑定策略失败'
  } finally {
    bindingStrategy.value = false
  }
}

async function toggleAccountDeployment(deployment, active) {
  deploymentLoadingId.value = deployment.deployment_id
  try {
    await accountAPI.setDeploymentStatus(selectedAccount.value.account_id, deployment.deployment_id, active)
    await refreshSelectedAccount()
  } catch (error) {
    messageType.value = 'error'
    message.value = error.response?.data?.detail || '更新绑定状态失败'
  } finally {
    deploymentLoadingId.value = ''
  }
}

async function removeAccountDeployment(deployment) {
  if (!confirm(`确定从该账户解绑“${deployment.strategy_name || deployment.strategy_id}”吗？`)) return
  try {
    const data = await accountAPI.removeDeployment(selectedAccount.value.account_id, deployment.deployment_id)
    messageType.value = 'success'
    message.value = data.message
    await refreshSelectedAccount()
  } catch (error) {
    messageType.value = 'error'
    message.value = error.response?.data?.detail || '解绑策略失败'
  }
}

async function refreshSelectedAccount() {
  const accountId = selectedAccount.value.account_id
  await loadAccounts()
  selectedAccount.value = accounts.value.find(item => item.account_id === accountId) || null
}

async function openPaperRuntime(account) {
  runtimeLoadingId.value = account.account_id
  try {
    const [data] = await Promise.all([
      accountAPI.getPaperDetail(account.account_id),
      loadPaperContext(),
    ])
    paperDetail.value = data.detail
    expandedPaperPositions.value = new Set()
    selectedStrategyId.value = ''
    paperReport.value = null
    reportStrategyId.value = ''
    paperDialog.value = true
    await nextTick()
  } catch (error) {
    messageType.value = 'error'
    message.value = error.response?.data?.detail || '加载模拟账户失败'
  } finally {
    runtimeLoadingId.value = null
  }
}

async function openLiveRuntime(account) {
  runtimeLoadingId.value = account.account_id
  try {
    const data = await accountAPI.getLiveMonitoring(account.account_id)
    liveDetail.value = data.detail
    expandedLivePositions.value = new Set()
    liveDialog.value = true
    clearInterval(liveRefreshTimer)
    liveRefreshTimer = setInterval(refreshLiveDetail, 6000)
    await nextTick()
  } catch (error) {
    messageType.value = 'error'
    message.value = error.response?.data?.detail || '加载实盘运行台失败'
  } finally {
    runtimeLoadingId.value = null
  }
}

async function refreshLiveDetail() {
  if (!liveDetail.value) return
  try {
    const data = await accountAPI.getLiveMonitoring(liveDetail.value.account.account_id)
    liveDetail.value = data.detail
    await nextTick()
    renderLiveEquityChart()
  } catch (error) {
    messageType.value = 'error'
    message.value = error.response?.data?.detail || '刷新实盘运行数据失败'
  }
}

function closeLiveRuntime() {
  liveDialog.value = false
  clearInterval(liveRefreshTimer)
  liveRefreshTimer = null
  liveEquityChartInstance?.dispose()
  liveEquityChartInstance = null
  liveDetail.value = null
  expandedLivePositions.value = new Set()
}

async function loadPaperReport() {
  if (!paperDetail.value) return
  reportLoading.value = true
  try {
    const data = await accountAPI.getPaperReport(
      paperDetail.value.account.account_id, reportStrategyId.value
    )
    paperReport.value = data.report
  } catch (error) {
    messageType.value = 'error'
    message.value = error.response?.data?.detail || '生成模拟盘报告失败'
  } finally {
    reportLoading.value = false
  }
}

function closePaperRuntime() {
  paperDialog.value = false
  equityChartInstance?.dispose()
  equityChartInstance = null
  paperDetail.value = null
  expandedPaperPositions.value = new Set()
}

async function refreshPaperDetail() {
  const data = await accountAPI.getPaperDetail(paperDetail.value.account.account_id)
  paperDetail.value = data.detail
  await nextTick()
  renderEquityChart()
  await loadAccounts()
}

async function deploySelectedStrategy() {
  const preflight = await accountAPI.deploymentPreflight(paperDetail.value.account.account_id, selectedStrategyId.value)
  if (preflight.warnings?.length && !confirm(`部署风险提醒：\n\n${preflight.warnings.join('\n\n')}\n\n仍然部署吗？`)) return
  deploying.value = true
  try {
    const data = await accountAPI.deployStrategy(
      paperDetail.value.account.account_id, selectedStrategyId.value
    )
    messageType.value = 'success'
    message.value = [data.message, ...(data.warnings || [])].join('；')
    selectedStrategyId.value = ''
    await refreshPaperDetail()
  } catch (error) {
    messageType.value = 'error'
    message.value = error.response?.data?.detail || '部署策略失败'
  } finally {
    deploying.value = false
  }
}

async function toggleDeployment(deployment, active) {
  deploymentLoadingId.value = deployment.deployment_id
  try {
    await accountAPI.setDeploymentStatus(
      paperDetail.value.account.account_id, deployment.deployment_id, active
    )
    await refreshPaperDetail()
  } catch (error) {
    messageType.value = 'error'
    message.value = error.response?.data?.detail || '更新策略运行状态失败'
  } finally {
    deploymentLoadingId.value = ''
  }
}

async function endDeployment(deployment, accountId) {
  if (!confirm(`结束“${deployment.strategy_name || deployment.strategy_id}”在此账户上的部署吗？不会自动平仓，历史记录会保留。`)) return
  deploymentLoadingId.value = deployment.deployment_id
  try {
    const data = await accountAPI.endDeployment(accountId, deployment.deployment_id)
    messageType.value = 'success'
    message.value = data.message
    if (paperDetail.value?.account.account_id === accountId) await refreshPaperDetail()
    if (selectedAccount.value?.account_id === accountId) await refreshSelectedAccount()
  } catch (error) {
    messageType.value = 'error'
    message.value = error.response?.data?.detail || '结束部署失败'
  } finally {
    deploymentLoadingId.value = ''
  }
}

function renderEquityChart() {
  if (!equityChart.value || !paperDetail.value?.equity_curve?.length) return
  equityChartInstance?.dispose()
  equityChartInstance = echarts.init(equityChart.value)
  const curve = paperDetail.value.equity_curve
  equityChartInstance.setOption({
    animationDuration: 500,
    tooltip: { trigger: 'axis' },
    grid: { left: 18, right: 18, top: 18, bottom: 8, containLabel: true },
    xAxis: {
      type: 'category', boundaryGap: false,
      data: curve.map(item => new Date(item.time * 1000).toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' })),
      axisLabel: { color: '#81908a', hideOverlap: true },
    },
    yAxis: { type: 'value', scale: true },
    series: [{
      type: 'line', name: '净值', symbol: 'none', smooth: 0.15,
      data: curve.map(item => item.equity), lineStyle: { color: '#24735f', width: 2 },
      areaStyle: { color: 'rgba(36,115,95,.16)' },
    }],
  })
}

function renderLiveEquityChart() {
  if (!liveEquityChart.value || !liveDetail.value?.equity_curve?.length) return
  liveEquityChartInstance?.dispose()
  liveEquityChartInstance = echarts.init(liveEquityChart.value)
  const curve = liveDetail.value.equity_curve
  liveEquityChartInstance.setOption({
    animationDuration: 300,
    tooltip: { trigger: 'axis' },
    grid: { left: 18, right: 18, top: 18, bottom: 8, containLabel: true },
    xAxis: { type: 'category', boundaryGap: false, data: curve.map(item => new Date(item.time * 1000).toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit', second: '2-digit' })), axisLabel: { color: '#81908a', hideOverlap: true } },
    yAxis: { type: 'value', scale: true },
    series: [{ type: 'line', name: '净值', symbol: 'none', smooth: 0.12, data: curve.map(item => item.equity), lineStyle: { color: '#13795b', width: 2 }, areaStyle: { color: 'rgba(19,121,91,.16)' } }],
  })
}

loadAccounts()
onBeforeUnmount(() => {
  clearInterval(liveRefreshTimer)
  equityChartInstance?.dispose()
  liveEquityChartInstance?.dispose()
})
</script>

<style scoped>
.accounts-page { min-height: 100%; padding: 28px; background: radial-gradient(circle at 92% 5%, rgba(187,134,49,.13), transparent 27%), linear-gradient(145deg, #f4f0e7, #edf3ef 58%, #f8f5ec); }
.account-hero { display: flex; align-items: center; justify-content: space-between; gap: 24px; margin-bottom: 22px; padding: 34px 38px; border-radius: 24px; color: #fbf7ec; background: linear-gradient(118deg, #122f2a, #285f50 70%, #9a7336); box-shadow: 0 20px 46px rgba(20,58,48,.18); }
.account-hero h1 { margin: 5px 0 8px; font: 700 clamp(2rem,4vw,3.35rem)/1 Georgia,serif; }
.account-hero p { margin: 0; max-width: 760px; color: rgba(255,255,255,.76); }
.eyebrow,.section-tag { color: #d9b873; font-size: .7rem; font-weight: 800; letter-spacing: .16em; }
.section-tag { color: #24735f; }
.metric-grid { display: grid; grid-template-columns: repeat(4,1fr); gap: 14px; margin-bottom: 20px; }
.metric-grid article { padding: 17px 20px; border: 1px solid rgba(27,71,59,.1); border-radius: 15px; background: rgba(255,255,255,.82); }
.metric-grid span { display: block; color: #79847f; font-size: .76rem; }
.metric-grid strong { color: #1c4a3e; font-size: 1.75rem; }
.content-grid { display: grid; grid-template-columns: 1fr minmax(300px,380px); gap: 20px; align-items: start; }
.account-catalog,.paper-card { padding: 22px; border: 1px solid rgba(24,67,56,.1); border-radius: 20px; background: rgba(255,255,255,.92); }
.paper-card { position: sticky; top: 20px; padding: 0; }
.paper-card h2,.section-heading h2 { margin: 4px 0 8px; color: #1d453a; font-family: Georgia,serif; }
.paper-card p { color: #6d7973; font-size: .86rem; line-height: 1.55; }
.section-heading,.account-topline,.account-identity,.account-chips { display: flex; align-items: center; justify-content: space-between; gap: 12px; }
.section-heading > span { color: #7f8a85; font-size: .74rem; }
.account-list { display: grid; gap: 14px; }
.account-card { padding: 19px; border: 1px solid #dfe7e2; border-left: 4px solid #33725f; border-radius: 16px; background: #fcfdfb; }
.account-card.paper { border-left-color: #b38237; }
.account-mark { display: grid; place-items: center; width: 42px; height: 42px; border-radius: 12px; color: #276a57; background: #eaf3ee; }
.account-card.paper .account-mark { color: #8a642b; background: #f8f0df; }
.account-identity h3 { margin: 0; color: #284b41; font-size: 1rem; }
.account-identity span { color: #87918d; font-size: .72rem; }
.balance-row { display: grid; grid-template-columns: repeat(3,1fr); gap: 10px; margin: 17px 0; }
.balance-row div { padding: 12px; border-radius: 11px; background: #f0f5f2; }
.balance-row span,.balance-row strong { display: block; }
.balance-row span { color: #81908a; font-size: .68rem; }
.balance-row strong { margin-top: 4px; color: #254b40; font-size: .9rem; }
.account-details { display: grid; grid-template-columns: repeat(3,1fr); gap: 10px 18px; margin: 0; }
.account-details dt { color: #8b9590; font-size: .67rem; }
.account-details dd { margin: 2px 0 0; color: #4a615a; font-size: .76rem; overflow-wrap: anywhere; }
.paper-note { display: flex; align-items: center; gap: 10px; padding: 12px; border-radius: 11px; color: #6d684f; background: #faf3e3; font-size: .76rem; }
.paper-setting-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 0 10px; }
.paper-actions { display: flex; justify-content: flex-end; margin-top: 15px; padding-top: 13px; border-top: 1px solid #e7ece9; }
.bound-strategies { margin-top: 14px; padding: 11px 13px; border-radius: 11px; background: #f5f8f6; }.bound-strategies>span { display:block; margin-bottom:7px; color:#7d8a84; font-size:.65rem; }.bound-strategies>div { display:flex; flex-wrap:wrap; gap:5px; }.bound-strategies>strong { color:#9aa39f; font-size:.7rem; font-weight:500; }.bound-strategy-list { display:flex; flex-direction:column; gap:6px; width:100%; }.bound-strategy-item { display:flex; align-items:center; gap:8px; flex-wrap:wrap; padding:6px 8px; border:1px solid #e0e8e3; border-radius:9px; background:#fff; }.bound-strategy-meta { color:#5f6e68; font-size:.66rem; }.bound-strategy-alert { padding:1px 7px; border-radius:6px; color:#c62828; background:#fdecea; font-size:.62rem; font-weight:600; }.paper-actions { gap:8px; flex-wrap:wrap; }
.binding-dialog { border-radius:18px!important; background:#f5f7f3; }.binding-header { display:flex; align-items:center; justify-content:space-between; padding:22px 24px; }.binding-header h2 { margin:3px 0; }.binding-header span { color:#7d8983; font-size:.7rem; }.binding-form { display:grid; grid-template-columns:1fr auto; gap:9px; }.binding-list { display:grid; gap:8px; margin-top:14px; }.binding-list article { display:flex; align-items:center; justify-content:space-between; padding:12px 14px; border:1px solid #dbe5df; border-radius:11px; background:#fff; }.binding-list strong,.binding-list span { display:block; }.binding-list strong { color:#285044; font-size:.8rem; }.binding-list span { color:#89948f; font-size:.65rem; }.binding-controls { display:flex; align-items:center; gap:5px; }
.runtime-dialog { border-radius: 20px !important; background: #f5f7f3; }
.runtime-header { display: flex; align-items: center; justify-content: space-between; padding: 22px 26px; }
.runtime-header h2 { margin: 3px 0; }
.runtime-header span { color: #7f8b85; font-size: .72rem; }
.runtime-body { padding: 22px 26px 30px !important; }
.runtime-metrics { display: grid; grid-template-columns: repeat(4,1fr); gap: 10px; }
.runtime-metrics article { padding: 15px; border: 1px solid #dce5df; border-radius: 12px; background: #fff; }
.runtime-metrics span,.runtime-metrics strong { display: block; }
.runtime-metrics span { color: #82908a; font-size: .68rem; }.runtime-metrics strong { margin-top: 4px; color: #295145; font-size: 1rem; }
.deployment-workbench { display: grid; grid-template-columns: 1fr minmax(360px,520px); align-items: center; gap: 20px; margin-top: 14px; padding: 16px; border-radius: 13px; color: #f8f4e8; background: linear-gradient(120deg,#183b33,#2d6958); }
.deployment-workbench h3 { margin: 3px 0; }.deployment-workbench p { margin: 0; color: rgba(255,255,255,.68); font-size: .7rem; }
.deployment-form { display: grid; grid-template-columns: 1fr auto; align-items: center; gap: 8px; }
.deployment-form :deep(.v-field) { background: #fff; }
.strategy-select-value,.strategy-option-title { display:flex; align-items:center; gap:7px; min-width:0; }.strategy-select-value span,.strategy-option-title strong { overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }.strategy-select-value small { color:#7a8781; font-size:.66rem; }.strategy-select-item :deep(.v-list-item-subtitle) { margin-top:3px; color:#7d8984; font-size:.7rem; }.active-deployment-strip { display:grid; grid-template-columns:112px 1fr; gap:12px; align-items:start; margin-top:10px; padding:11px 13px; border:1px solid #cfe3d9; border-radius:11px; background:linear-gradient(110deg,#f1faf5,#fff); }.active-deployment-strip>span { padding-top:5px; color:#477164; font-size:.68rem; font-weight:800; letter-spacing:.08em; }.active-deployment-strip>div { display:flex; flex-wrap:wrap; gap:7px; }.active-deployment-strip article { display:flex; align-items:center; gap:6px; padding:6px 8px; border:1px solid #dbeae1; border-radius:8px; background:#fff; }.active-deployment-strip strong { color:#295145; font-size:.73rem; }.active-deployment-strip small { color:#77857e; font-size:.63rem; }.active-deployment-strip.live { margin:0 0 14px; background:linear-gradient(110deg,#eef6f4,#fff); }
.deployment-list { display: grid; gap: 7px; margin-top: 9px; }
.deployment-list article { display: flex; align-items: center; justify-content: space-between; padding: 10px 13px; border: 1px solid #dce6e0; border-radius: 10px; background: #fff; }
.deployment-list strong,.deployment-list span { display: block; }.deployment-list strong { color: #31554b; font-size: .78rem; }.deployment-list span { color: #87928d; font-size: .64rem; }
.runtime-chart-card,.runtime-table-card { margin-top: 13px; padding: 15px; border: 1px solid #dfe7e2; border-radius: 13px; background: #fff; }
.strategy-performance-card { margin-top:13px; padding:15px; border:1px solid #dfe7e2; border-radius:8px; background:#fff; }
.strategy-performance-row { padding:13px 0; border-top:1px solid #e7ede9; }
.strategy-performance-row:first-of-type { border-top:0; }
.strategy-performance-row header { display:flex; align-items:flex-start; justify-content:space-between; gap:12px; margin-bottom:10px; }
.strategy-performance-row header strong,.strategy-performance-row header span { display:block; }
.strategy-performance-row header strong { color:#284f44; font-size:.82rem; }
.strategy-performance-row header span { margin-top:2px; color:#87938d; font-size:.63rem; }
.strategy-performance-grid { display:grid; grid-template-columns:repeat(4,minmax(130px,1fr)); gap:7px; }
.strategy-performance-grid>span { min-width:0; padding:8px 10px; border-radius:7px; background:#f3f7f4; color:#84918b; font-size:.6rem; }
.strategy-performance-grid b { display:block; margin-top:3px; overflow-wrap:anywhere; color:#31554b; font-size:.72rem; }
.paper-report { margin-top: 13px; padding: 18px; border-radius: 15px; background: linear-gradient(135deg,#173d34,#275d50); color: #fff; }
.report-heading { display: flex; align-items: center; justify-content: space-between; gap: 18px; }.report-heading h3 { margin: 3px 0; }.report-heading .v-select { max-width: 280px; }.report-heading :deep(.v-field) { background: #fff; }
.report-metrics { display: grid; grid-template-columns: repeat(6,1fr); gap: 8px; margin-top: 14px; }.report-metrics article { padding: 12px; border-radius: 10px; background: rgba(255,255,255,.1); }.report-metrics span,.report-metrics strong { display:block; }.report-metrics span { color: rgba(255,255,255,.62); font-size:.62rem; }.report-metrics strong { margin-top:4px; font-size:.9rem; }
.report-breakdowns { display:grid; grid-template-columns:repeat(3,1fr); gap:10px; margin-top:10px; }.report-breakdowns>div { padding:12px; border-radius:10px; background:rgba(255,255,255,.07); }.report-breakdowns h4 { margin:0 0 7px; font-size:.72rem; }.report-breakdowns p { display:flex; justify-content:space-between; gap:8px; margin:5px 0; color:rgba(255,255,255,.72); font-size:.62rem; }
.benchmark-panel { margin-top:12px; padding:14px; border-radius:11px; background:rgba(255,255,255,.08); }.benchmark-title { display:flex; align-items:flex-end; justify-content:space-between; gap:12px; }.benchmark-title span { color:#d7b36f; font-size:.58rem; font-weight:800; letter-spacing:.12em; }.benchmark-title h4 { margin:2px 0; }.benchmark-title small { color:rgba(255,255,255,.55); font-size:.6rem; }.benchmark-grid { display:grid; grid-template-columns:repeat(5,1fr); gap:7px; margin-top:10px; }.benchmark-grid article { padding:10px; border-radius:8px; background:rgba(255,255,255,.07); }.benchmark-grid span,.benchmark-grid strong,.benchmark-grid small { display:block; }.benchmark-grid span { color:rgba(255,255,255,.57); font-size:.59rem; }.benchmark-grid strong { margin:3px 0; font-size:.8rem; }.benchmark-grid small { color:rgba(255,255,255,.62); font-size:.56rem; line-height:1.45; }
.setup-performance-panel { margin-top:12px; padding:14px; border-radius:11px; background:rgba(255,255,255,.08); }.setup-empty { margin-top:10px; padding:14px; border:1px dashed rgba(255,255,255,.2); border-radius:8px; color:rgba(255,255,255,.62); font-size:.65rem; text-align:center; }.setup-performance-table { margin-top:10px; overflow-x:auto; }.setup-performance-table article { display:grid; grid-template-columns:minmax(120px,1.4fr) minmax(125px,1.2fr) repeat(5,minmax(72px,.8fr)); gap:8px; align-items:center; min-width:760px; padding:9px 8px; border-top:1px solid rgba(255,255,255,.1); font-size:.63rem; }.setup-performance-table article:first-child { border-top:0; }.setup-performance-table strong { color:#fff; }.setup-performance-table span { color:rgba(255,255,255,.72); }.setup-performance-table .setup-performance-head { color:rgba(255,255,255,.48); font-weight:700; }.setup-direction-grid { display:grid; grid-template-columns:repeat(2,1fr); gap:7px; margin-top:9px; }.setup-direction-grid div { padding:9px 10px; border-radius:8px; background:rgba(255,255,255,.06); }.setup-direction-grid b,.setup-direction-grid span { display:block; }.setup-direction-grid b { font-size:.66rem; }.setup-direction-grid span { margin-top:3px; color:rgba(255,255,255,.6); font-size:.58rem; }
.runtime-section-title { display: flex; align-items: center; justify-content: space-between; margin-bottom: 8px; }.runtime-section-title h3 { margin: 0; color: #31554b; font-size: .84rem; }.runtime-section-title span { color: #89948f; font-size: .65rem; }
.equity-chart { width: 100%; height: 280px; }
.runtime-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 13px; }.runtime-grid .runtime-table-card { margin-top: 13px; }
.runtime-row { display: grid; align-items: center; gap: 8px; padding: 8px 5px; border-top: 1px solid #edf1ee; color: #6f7d77; font-size: .67rem; }.position-row { grid-template-columns: 45px 1fr 1fr auto; }.trade-row { grid-template-columns: 130px 1fr 80px auto; }.order-row { grid-template-columns: 130px 45px 1fr 90px 65px minmax(100px,1fr); }
.paper-position-card { display: grid; gap: 10px; padding: 12px 0; border-top: 1px solid #edf1ee; }
.paper-position-head { display: flex; align-items: flex-start; justify-content: space-between; gap: 12px; }
.paper-position-head strong,.paper-position-head span { display: block; }
.paper-position-head strong { margin-top: 2px; color: #31554b; font-size: .78rem; }
.paper-position-head span { color: #84908a; font-size: .64rem; }
.paper-position-actions { display: flex; align-items: center; gap: 7px; }
.paper-position-metrics { display: grid; grid-template-columns: repeat(5, minmax(88px, 1fr)); gap: 7px; }
.paper-position-metrics span { display: flex; flex-direction: column; padding: 8px; border-radius: 9px; background: #f2f7f4; color: #7d8b85; font-size: .62rem; }
.paper-position-metrics b { margin-top: 2px; font-size: .72rem; }
.paper-position-events { display: grid; gap: 6px; padding: 9px; border-radius: 10px; background: #f8faf7; }
.paper-event-row { display: grid; grid-template-columns: 92px 72px 1fr auto; gap: 8px; align-items: center; color: #6f7d77; font-size: .63rem; }
.paper-event-row b { color: #31554b; }
.paper-event-row small { color: #8c9892; }
.runtime-dialog .positive { color: #147b59; }.runtime-dialog .negative { color: #bd493c; }.reject-reason { color: #9a6258; }
.runtime-empty { display: grid; place-items: center; min-height: 130px; color: #919c97; font-size: .72rem; }.runtime-empty.compact { min-height: 62px; }
.empty-state { padding: 65px 20px; color: #85918b; text-align: center; }
.empty-state h3 { margin: 12px 0 5px; color: #4a625b; }.empty-state p { margin: 0; }
@media(max-width:1000px){.content-grid{grid-template-columns:1fr}.paper-card{position:static}.account-details{grid-template-columns:1fr 1fr}.deployment-workbench{grid-template-columns:1fr}.runtime-grid{grid-template-columns:1fr}.strategy-performance-grid{grid-template-columns:repeat(3,minmax(120px,1fr))}.report-metrics{grid-template-columns:repeat(3,1fr)}.report-breakdowns{grid-template-columns:1fr}.benchmark-grid{grid-template-columns:repeat(2,1fr)}.setup-direction-grid{grid-template-columns:1fr}}
@media(max-width:650px){.accounts-page{padding:15px}.account-hero{align-items:flex-start;flex-direction:column;padding:25px}.metric-grid,.runtime-metrics{grid-template-columns:1fr 1fr}.account-topline{align-items:flex-start;flex-direction:column}.balance-row,.account-details,.paper-setting-grid{grid-template-columns:1fr}.account-chips{flex-wrap:wrap}.runtime-body{padding:14px!important}.deployment-form,.active-deployment-strip{grid-template-columns:1fr}.strategy-performance-grid{grid-template-columns:1fr 1fr}.order-row{grid-template-columns:1fr 45px 70px}.order-row>*:nth-child(n+4):not(:last-child){display:none}.trade-row{grid-template-columns:1fr auto}.trade-row>*:nth-child(2),.trade-row>*:nth-child(3){display:none}}
.deployment-form :deep(.v-field__input),.deployment-form :deep(.v-field__input input),.deployment-form :deep(.v-label){color:#254b40!important}.deployment-form :deep(.v-field__input input::placeholder){color:#71817a!important;opacity:1}.strategy-select-value span{color:#254b40}.strategy-select-value small{color:#6b7b74}.strategy-select-item :deep(.v-list-item-title),.strategy-select-item :deep(.v-list-item-subtitle){color:#254b40!important}.strategy-select-item :deep(.v-list-item-subtitle){color:#6f7e77!important}.deployment-form :deep(.v-field){border-color:#d6e4dc!important}.deployment-form :deep(.v-field--focused){border-color:#80b59f!important}
</style>

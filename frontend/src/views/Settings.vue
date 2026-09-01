<template>
  <v-container fluid>
    <v-row v-if="!isStrategyPage" class="settings-workspace">
      <v-col cols="12">
        <section class="settings-hero" :class="{ 'settings-hero--admin': isAdmin }">
          <div>
            <div class="strategy-eyebrow">{{ isAdmin ? 'ADMIN CONTROL CENTER' : 'PERSONAL CONTROL ROOM' }}</div>
            <h2>{{ isAdmin ? '平台运营与安全配置' : '管理你的交易工作空间' }}</h2>
            <p>{{ isAdmin ? '集中维护用户额度、注册邮件和大模型服务，让平台运行状态清晰可控。' : '查看资源额度、保护账户安全，并管理大模型行情分析权限。' }}</p>
            <div class="settings-hero__identity">
              <v-avatar size="30" color="white" variant="tonal"><v-icon size="18">mdi-account-circle-outline</v-icon></v-avatar>
              <span>{{ currentUser.username }}</span>
              <v-chip size="x-small" :color="isAdmin ? 'warning' : 'success'" variant="flat">{{ roleLabel }}</v-chip>
            </div>
          </div>
          <v-btn v-if="isAdmin" variant="outlined" color="white" prepend-icon="mdi-refresh" :loading="quotaSaving === 'loading'" @click="loadAdminWorkspace">刷新运营数据</v-btn>
          <v-btn v-else to="/strategy-settings" variant="outlined" color="white" prepend-icon="mdi-chart-timeline-variant-shimmer">前往策略管理</v-btn>
        </section>

        <section class="settings-metrics">
          <article>
            <span>{{ isAdmin ? '受管用户' : '历史数据集' }}</span>
            <strong>{{ isAdmin ? quotaUsers.length : `${myQuota.usage.datasets} / ${myQuota.limits.datasets ?? '∞'}` }}</strong>
            <small>{{ isAdmin ? '可在白名单中单独扩容' : '创建的数据集占用额度' }}</small>
            <v-icon>mdi-database-outline</v-icon>
          </article>
          <article>
            <span>{{ isAdmin ? '待审批 AI 申请' : '策略额度' }}</span>
            <strong>{{ isAdmin ? llmAccessRequests.length : `${myQuota.usage.strategies} / ${myQuota.limits.strategies ?? '∞'}` }}</strong>
            <small>{{ isAdmin ? '等待管理员处理' : '私有及复制后的策略均计入' }}</small>
            <v-icon>mdi-chart-timeline-variant</v-icon>
          </article>
          <article>
            <span>{{ isAdmin ? '邮件服务' : '信号源额度' }}</span>
            <strong>{{ isAdmin ? (emailConfig.enabled && emailConfig.password_set ? '在线' : '待配置') : `${myQuota.usage.signal_sources} / ${myQuota.limits.signal_sources ?? '∞'}` }}</strong>
            <small>{{ isAdmin ? '负责注册与登录验证码' : '独立 AI 信号源及策略规则源' }}</small>
            <v-icon>mdi-access-point</v-icon>
          </article>
          <article>
            <span>{{ isAdmin ? '平台 AI 服务' : 'AI 行情分析' }}</span>
            <strong>{{ isAdmin ? (llmConfig.enabled ? '启用' : '停用') : llmAccessLabel }}</strong>
            <small>{{ isAdmin ? '全局大模型服务状态' : llmAccessDescription }}</small>
            <v-icon>mdi-brain</v-icon>
          </article>
        </section>

        <v-tabs v-model="settingsTab" color="primary" class="settings-main-tabs">
          <v-tab value="account"><v-icon start>mdi-account-lock-outline</v-icon>账户与安全</v-tab>
          <v-tab v-if="isAdmin" value="email"><v-icon start>mdi-email-lock-outline</v-icon>邮件服务</v-tab>
          <v-tab v-if="isAdmin" value="instruments"><v-icon start>mdi-swap-horizontal-bold</v-icon>品种映射</v-tab>
          <v-tab v-if="isAdmin" value="quota"><v-icon start>mdi-account-star-outline</v-icon>用户与会员</v-tab>
          <v-tab v-if="isAdmin" value="structure"><v-icon start>mdi-chart-timeline-variant</v-icon>结构分析</v-tab>
          <v-tab value="llm"><v-icon start>mdi-brain</v-icon>{{ isAdmin ? 'AI 服务管理' : 'AI 功能' }}</v-tab>
        </v-tabs>
      </v-col>
    </v-row>

    <v-row v-if="!isStrategyPage && isAdmin && settingsTab === 'structure'">
      <v-col cols="12">
        <v-card class="user-settings-card admin-service-card" elevation="0">
          <v-card-title class="settings-card-title">
            <div><v-icon>mdi-chart-timeline-variant</v-icon><span>系统结构识别</span></div>
            <small>基于分层 Pivot、结构状态机和局部形态实时计算</small>
          </v-card-title>
          <v-card-text>
            <v-divider class="my-5" />
            <div class="llm-section-head compact"><div><h3>系统结构识别参数</h3><p>规则引擎用于 Pivot、趋势线、箱体和突破确认。参数修改后，下次行情请求立即使用。</p></div><v-btn color="primary" :loading="structureEngineSaving" @click="saveStructureEngineConfig">保存参数</v-btn></div>
            <v-row class="mt-2">
              <v-col cols="12" sm="6" md="3"><v-text-field v-model.number="structureEngineConfig.pivot_legs" type="number" min="2" max="12" label="小级别 Pivot 腿数" hint="左右各观察几根K线" persistent-hint density="compact" variant="outlined" /></v-col>
              <v-col cols="12" sm="6" md="3"><v-text-field v-model.number="structureEngineConfig.medium_pivot_legs" type="number" min="3" max="30" label="中级别 Pivot 腿数" density="compact" variant="outlined" /></v-col>
              <v-col cols="12" sm="6" md="3"><v-text-field v-model.number="structureEngineConfig.large_pivot_legs" type="number" min="5" max="60" label="大级别 Pivot 腿数" density="compact" variant="outlined" /></v-col>
              <v-col cols="12" sm="6" md="3"><v-text-field v-model.number="structureEngineConfig.min_reversal_atr" type="number" min="0.1" max="5" step="0.1" label="最小反转幅度（ATR）" density="compact" variant="outlined" /></v-col>
              <v-col cols="12" sm="6" md="3"><v-text-field v-model.number="structureEngineConfig.break_buffer_atr" type="number" min="0" max="2" step="0.05" label="突破缓冲（ATR）" hint="收盘越过结构位的最小距离" persistent-hint density="compact" variant="outlined" /></v-col>
              <v-col cols="12" sm="6" md="3"><v-text-field v-model.number="structureEngineConfig.break_confirm_bars" type="number" min="1" max="10" label="突破收盘确认根数" hint="连续收盘站上/跌破才确认" persistent-hint density="compact" variant="outlined" /></v-col>
              <v-col cols="12" sm="6" md="3"><v-text-field v-model.number="structureEngineConfig.retest_bars" type="number" min="0" max="10" label="反转保持根数" hint="反向突破后继续保持，才切换主结构" persistent-hint density="compact" variant="outlined" /></v-col>
              <v-col cols="12" sm="6" md="3"><v-text-field v-model.number="structureEngineConfig.displacement_atr" type="number" min="0.1" max="5" step="0.1" label="强位移阈值（ATR）" hint="达到后可跳过额外保持确认" persistent-hint density="compact" variant="outlined" /></v-col>
              <v-col cols="12" sm="6" md="3"><v-text-field v-model.number="structureEngineConfig.range_touch_tolerance" type="number" min="0.0001" max="0.05" step="0.0001" label="箱体触碰容差" hint="比例，例如 0.003 = 0.3%" persistent-hint density="compact" variant="outlined" /></v-col>
              <v-col cols="12" sm="6" md="3"><v-text-field v-model.number="structureEngineConfig.range_touch_atr" type="number" min="0.1" max="3" step="0.05" label="边界触碰容差（ATR）" density="compact" variant="outlined" /></v-col>
              <v-col cols="12" sm="6" md="3"><v-text-field v-model.number="structureEngineConfig.range_min_touches" type="number" min="1" max="10" label="箱体最少触碰次数" density="compact" variant="outlined" /></v-col>
              <v-col cols="12" sm="6" md="3"><v-text-field v-model.number="structureEngineConfig.range_min_inside_ratio" type="number" min="0.5" max="1" step="0.05" label="区间内部收盘比例" hint="例如 0.65 = 65%" persistent-hint density="compact" variant="outlined" /></v-col>
              <v-col cols="12" sm="6" md="3"><v-text-field v-model.number="structureEngineConfig.range_min_bars" type="number" min="12" max="200" label="区间最少K线数" density="compact" variant="outlined" /></v-col>
              <v-col cols="12" sm="6" md="3"><v-text-field v-model.number="structureEngineConfig.range_max_atr" type="number" min="1" max="30" step="0.5" label="箱体最大宽度（ATR）" density="compact" variant="outlined" /></v-col>
              <v-col cols="12" sm="6" md="3"><v-text-field v-model.number="structureEngineConfig.min_segment_bars" type="number" min="5" max="100" label="结构段最少K线数" density="compact" variant="outlined" /></v-col>
              <v-col cols="12" sm="6" md="3"><v-text-field v-model.number="structureEngineConfig.trendline_touch_atr" type="number" min="0.1" max="3" step="0.1" label="趋势线触碰容差（ATR）" density="compact" variant="outlined" /></v-col>
              <v-col cols="12" sm="6" md="3"><v-text-field v-model.number="structureEngineConfig.trendline_min_touches" type="number" min="2" max="10" label="趋势线最少触碰次数" density="compact" variant="outlined" /></v-col>
              <v-col cols="12" sm="6" md="3"><v-text-field v-model.number="structureEngineConfig.trendline_min_bars" type="number" min="10" max="200" label="趋势线最少跨度（K线）" density="compact" variant="outlined" /></v-col>
              <v-col cols="12" sm="6" md="3"><v-text-field v-model.number="structureEngineConfig.trend_min_direction_ratio" type="number" min="0.5" max="0.95" step="0.01" label="趋势方向一致率" hint="正反结构中主导方向的最低比例，例如 0.62 = 62%" persistent-hint density="compact" variant="outlined" /></v-col>
              <v-col cols="12" sm="6" md="3"><v-text-field v-model.number="structureEngineConfig.trend_relaxed_direction_ratio" type="number" min="0.5" max="0.9" step="0.01" label="明显位移时一致率" hint="净位移达到阈值时使用的宽松比例，避免轻中度趋势被判成箱体" persistent-hint density="compact" variant="outlined" /></v-col>
              <v-col cols="12" sm="6" md="3"><v-text-field v-model.number="structureEngineConfig.trend_min_efficiency" type="number" min="0.1" max="1" step="0.05" label="趋势方向效率" hint="净位移/结构路径的最低比例" persistent-hint density="compact" variant="outlined" /></v-col>
              <v-col cols="12" sm="6" md="3"><v-text-field v-model.number="structureEngineConfig.trend_min_net_change_atr" type="number" min="0.5" max="10" step="0.5" label="趋势最小净位移（ATR）" hint="主导方向还需达到的整体位移" persistent-hint density="compact" variant="outlined" /></v-col>
            </v-row>
            <div class="llm-section-head compact mt-4"><div><h3>结构交易计划参数</h3><p>行情层统一生成计划；按品种/周期专属配置覆盖默认值，策略仅负责引用和执行筛选。</p></div></div>
            <v-row class="mt-2">
              <v-col cols="12" sm="6" md="3"><v-text-field v-model.number="structureEngineConfig.entry_zone_atr" type="number" min="0" max="3" step="0.05" label="入场区域（ATR）" hint="计划入场价允许的接近范围" persistent-hint density="compact" variant="outlined" /></v-col>
              <v-col cols="12" sm="6" md="3"><v-text-field v-model.number="structureEngineConfig.stop_buffer_atr" type="number" min="0" max="5" step="0.05" label="止损缓冲（ATR）" density="compact" variant="outlined" /></v-col>
              <v-col cols="12" sm="6" md="3"><v-text-field v-model.number="structureEngineConfig.min_real_risk_reward" type="number" min="1" max="10" step="0.1" label="最低真实盈亏比" density="compact" variant="outlined" /></v-col>
              <v-col cols="12" sm="6" md="3"><v-text-field v-model.number="structureEngineConfig.trend_min_real_risk_reward" type="number" min="0.1" max="10" step="0.1" label="趋势回踩最低盈亏比" hint="用于上涨回踩买入和下跌反弹卖出，默认 0.5" persistent-hint density="compact" variant="outlined" /></v-col>
              <v-col cols="12" sm="6" md="3"><v-text-field v-model.number="structureEngineConfig.breakout_target_atr" type="number" min="1" max="10" step="0.5" label="突破目标（ATR）" density="compact" variant="outlined" /></v-col>
              <v-col cols="12" sm="6" md="3"><v-text-field v-model.number="structureEngineConfig.breakout_retest_valid_bars" type="number" min="1" max="50" label="突破回踩有效K线数" density="compact" variant="outlined" /></v-col>
              <v-col cols="12" sm="6" md="3"><v-switch v-model="structureEngineConfig.enable_triangle_prebreakout" color="primary" inset hide-details label="启用三角形提前入场" /></v-col>
              <v-col cols="12" sm="6" md="3"><v-switch v-model="structureEngineConfig.require_location_reclaim" color="primary" inset hide-details label="结构位置要求回收确认" /></v-col>
            </v-row>
            <div class="llm-section-head compact mt-4"><div><h3>品种 / 周期专属覆盖</h3><p>专属参数优先于全局参数；未配置的字段继续使用全局值。</p></div></div>
            <div class="d-flex flex-wrap ga-2 align-center">
              <v-select v-model="structureProfileDraft.symbol" :items="symbols" label="品种" density="compact" variant="outlined" hide-details style="max-width:220px" />
              <v-select v-model="structureProfileDraft.period" :items="['M1','M5','M15','H1','H4']" label="周期" density="compact" variant="outlined" hide-details style="max-width:150px" />
              <v-btn color="secondary" variant="tonal" :loading="structureEngineSaving" @click="saveStructureProfile">保存当前参数为专属配置</v-btn>
            </div>
            <v-chip v-for="item in structureProfiles" :key="`${item.symbol}-${item.period}`" closable size="small" class="mr-2 mt-3" @click:close="removeStructureProfile(item)">{{ item.symbol }} · {{ item.period }}</v-chip>
          </v-card-text>
        </v-card>
      </v-col>
    </v-row>

    <!-- 策略管理 -->
    <v-row v-if="isStrategyPage" class="strategy-workspace">
      <v-col cols="12">
        <template v-if="!selectedStrategy">
          <section class="strategy-hero">
            <div>
              <div class="strategy-eyebrow">STRATEGY WORKSPACE</div>
              <h2>把策略从想法推进到实盘</h2>
              <p>集中管理信号源、风险约束和验证进度，快速找到需要处理的策略。</p>
            </div>
            <div class="d-flex flex-wrap ga-2">
              <v-btn color="primary" size="large" class="strategy-primary-action" @click="openNewStrategyDialog">
                <v-icon start>mdi-plus</v-icon>新建策略
              </v-btn>
            </div>
          </section>

          <v-tabs v-model="strategyWorkspaceTab" color="primary" class="strategy-main-tabs">
            <v-tab value="mine"><v-icon start>mdi-briefcase-outline</v-icon>我的策略</v-tab>
            <v-tab value="shared" @click="loadSharedStrategies"><v-icon start>mdi-bookshelf</v-icon>平台策略库</v-tab>
          </v-tabs>

          <v-window v-model="strategyWorkspaceTab">
            <v-window-item value="mine">
              <div class="strategy-metrics">
                <article><span>全部策略</span><strong>{{ strategyTotal }} / {{ strategyQuota.limits.strategies ?? '∞' }}</strong><v-icon>mdi-layers-triple-outline</v-icon></article>
                <article><span>实盘可用</span><strong>{{ strategyMetrics.production }}</strong><v-icon>mdi-rocket-launch-outline</v-icon></article>
                <article><span>已部署运行</span><strong>{{ strategyMetrics.deployed }}</strong><v-icon>mdi-pulse</v-icon></article>
                <article><span>信号源用量</span><strong>{{ strategyQuota.usage.signal_sources }} / {{ strategyQuota.limits.signal_sources ?? '∞' }}</strong><v-icon>mdi-access-point</v-icon></article>
              </div>

              <v-card class="strategy-list-shell" elevation="0">
                <div class="strategy-toolbar">
                  <v-text-field v-model="strategySearch" label="搜索策略或品种" prepend-inner-icon="mdi-magnify" density="compact" hide-details clearable></v-text-field>
                  <v-select v-model="strategyLifecycleFilter" :items="lifecycleFilterOptions" label="生命周期" density="compact" hide-details></v-select>
                  <v-select v-model="strategyVisibilityFilter" :items="visibilityFilterOptions" label="可见性" density="compact" hide-details></v-select>
                  <v-btn icon="mdi-refresh" variant="text" :loading="strategiesLoading" @click="loadStrategies"></v-btn>
                </div>

                <v-table v-if="filteredStrategies.length" class="strategy-table hidden-sm-and-down">
                  <thead><tr><th>策略</th><th>信号源</th><th>生命周期</th><th>可见性</th><th>更新时间</th><th class="text-right">操作</th></tr></thead>
                  <tbody>
                    <tr v-for="strategy in filteredStrategies" :key="strategy.strategy_id" @click="openStrategyDetail(strategy)">
                      <td><div class="strategy-name-cell"><span class="strategy-symbol">{{ strategy.symbol }}</span><div><strong>{{ strategy.strategy_name }}</strong><small>#{{ strategy.strategy_id }}</small></div></div></td>
                      <td><div class="source-pill-row"><v-chip v-for="source in strategySourceBadges(strategy)" :key="source.key" size="x-small" :color="source.color" variant="tonal">{{ source.label }}</v-chip><span v-if="!signalSourceCount(strategy)" class="text-caption text-medium-emphasis">未配置</span></div></td>
                      <td><v-chip :color="getLifecycleMeta(strategy).color" size="small" variant="tonal">{{ getLifecycleMeta(strategy).label }}</v-chip></td>
                      <td><v-icon size="17" class="mr-1">{{ strategy.is_shared ? 'mdi-earth' : 'mdi-lock-outline' }}</v-icon>{{ strategy.is_shared ? '共享' : '私有' }}</td>
                      <td class="text-caption">{{ formatStrategyTime(strategy.updated_at) }}</td>
                      <td class="text-right"><v-btn icon="mdi-pencil-outline" size="small" variant="text" color="primary" @click.stop="openStrategyDetail(strategy)"></v-btn><v-btn icon="mdi-delete-outline" size="small" variant="text" color="error" @click.stop="deleteStrategy(strategy)"></v-btn></td>
                    </tr>
                  </tbody>
                </v-table>

                <div v-if="filteredStrategies.length" class="strategy-mobile-list hidden-md-and-up">
                  <article v-for="strategy in filteredStrategies" :key="strategy.strategy_id" @click="openStrategyDetail(strategy)">
                    <div class="d-flex justify-space-between align-start"><div class="strategy-name-cell"><span class="strategy-symbol">{{ strategy.symbol }}</span><div><strong>{{ strategy.strategy_name }}</strong><small>#{{ strategy.strategy_id }}</small></div></div><v-icon>mdi-chevron-right</v-icon></div>
                    <div class="source-pill-row mt-3"><v-chip :color="getLifecycleMeta(strategy).color" size="x-small">{{ getLifecycleMeta(strategy).label }}</v-chip><v-chip v-for="source in strategySourceBadges(strategy)" :key="source.key" size="x-small" :color="source.color" variant="tonal">{{ source.label }}</v-chip></div>
                  </article>
                </div>

                <div v-if="!filteredStrategies.length" class="strategy-empty">
                  <v-progress-circular v-if="strategiesLoading" indeterminate color="primary" />
                  <v-icon v-else size="52">{{ strategiesError ? 'mdi-alert-circle-outline' : 'mdi-radar' }}</v-icon>
                  <h3>{{ strategiesLoading ? '正在加载策略' : (strategiesError ? '策略加载失败' : (strategies.length ? '没有符合筛选条件的策略' : '还没有策略')) }}</h3>
                  <p>{{ strategiesLoading ? '正在读取你的策略配置，请稍候。' : (strategiesError || (strategies.length ? '调整筛选条件后再试试。' : '创建第一条策略，开始配置交易信号。')) }}</p>
                  <v-btn v-if="strategiesError" color="primary" variant="tonal" @click="loadStrategies">重试</v-btn>
                  <v-btn v-if="!strategies.length" color="primary" variant="tonal" @click="openNewStrategyDialog">新建策略</v-btn>
                </div>
                <div v-if="strategyTotal > strategyPageSize" class="d-flex justify-center py-4">
                  <v-pagination v-model="strategyPage" :length="strategyPageCount" :total-visible="7" :disabled="strategiesLoading" @update:model-value="loadStrategies" />
                </div>
              </v-card>
            </v-window-item>

            <v-window-item value="shared">
              <div class="shared-library-head"><div><h3>平台策略库</h3><p>直接使用共享策略；一旦被应用，源策略会冻结。需要调整时请复制为自己的私有草稿。</p></div><v-btn icon="mdi-refresh" variant="text" :loading="sharedStrategiesLoading" @click="loadSharedStrategies"></v-btn></div>
              <div v-if="sharedStrategiesLoading" class="strategy-empty"><v-progress-circular indeterminate color="primary"></v-progress-circular></div>
              <div v-else-if="sharedStrategies.length" class="shared-strategy-list">
                <article v-for="item in sharedStrategies" :key="`${item.owner_user_id}-${item.strategy_id}`" class="shared-strategy-card">
                  <div class="shared-strategy-card__head"><div><span class="strategy-symbol">{{ item.symbol }}</span><h3>{{ item.strategy_name }}</h3><p>由 {{ item.owner_username }} 分享</p></div><v-chip :color="getLifecycleColor(item.lifecycle_status)" size="small" variant="tonal">{{ item.lifecycle_label || getLifecycleMeta(item).label }}</v-chip></div>
                  <div class="shared-strategy-card__body"><v-chip size="x-small" variant="outlined">{{ signalSourceCount(item) }} 个信号源</v-chip><v-chip size="x-small" variant="outlined">置信度 {{ item.min_confidence }}%</v-chip><v-chip size="x-small" variant="outlined">{{ getConsistencyLabel(item.consistency_requirement) }}</v-chip></div>
                  <v-select v-if="item.target_symbol_options?.length > 1" v-model="sharedStrategyTargetSymbols[sharedStrategyKey(item)]" :items="item.target_symbol_options" item-title="label" item-value="symbol" label="使用到我的品种" density="compact" variant="outlined" hide-details class="mt-3"></v-select>
                  <p v-if="item.mapping_notice" class="text-caption text-medium-emphasis mt-3 mb-0">{{ item.mapping_notice }}</p>
                  <div class="shared-card-footer"><span>更新于 {{ formatStrategyTime(item.updated_at) }}</span><v-btn :color="isSharedStrategyUsed(item) ? 'success' : 'primary'" size="small" :loading="sharedStrategyCopying === sharedStrategyKey(item)" :disabled="isSharedStrategyUsed(item)" @click="useSharedStrategy(item)"><v-icon start>{{ isSharedStrategyUsed(item) ? 'mdi-check-circle-outline' : 'mdi-link-variant' }}</v-icon>{{ isSharedStrategyUsed(item) ? '已使用' : '使用策略' }}</v-btn></div>
                </article>
              </div>
              <div v-else class="strategy-empty"><v-icon size="52">mdi-bookshelf</v-icon><h3>暂无平台共享策略</h3><p>用户共享的策略会出现在这里。</p></div>
            </v-window-item>
          </v-window>
        </template>

        <template v-else>
          <section class="strategy-detail-head">
            <div class="d-flex align-center ga-3">
              <v-btn icon="mdi-arrow-left" variant="tonal" @click="closeStrategyDetail"></v-btn>
              <div><div class="strategy-eyebrow">{{ selectedStrategy.symbol }} · #{{ selectedStrategy.strategy_id }}</div><h2>{{ selectedStrategy.strategy_name }}</h2></div>
            </div>
            <div class="d-flex align-center flex-wrap ga-2">
              <v-chip :color="getLifecycleMeta(selectedStrategy).color" variant="tonal">{{ getLifecycleMeta(selectedStrategy).label }}</v-chip>
              <v-chip variant="outlined"><v-icon start size="16">{{ selectedStrategy.is_shared ? 'mdi-earth' : 'mdi-lock-outline' }}</v-icon>{{ selectedStrategy.is_shared ? '已共享' : '私有' }}</v-chip>
              <v-btn variant="tonal" color="secondary" :loading="strategySaving === `copy-${selectedStrategy.strategy_id}`" @click="copyStrategy(selectedStrategy)"><v-icon start>mdi-content-copy</v-icon>复制新版本</v-btn>
              <v-btn color="warning" variant="tonal" :loading="paperDeployLoading" @click="openPaperDeployDialog(selectedStrategy)"><v-icon start>mdi-flask-outline</v-icon>部署到模拟</v-btn>
              <v-btn v-if="selectedStrategy.lifecycle_status === 'production'" color="success" variant="tonal" :loading="liveDeployLoading" @click="openLiveDeployDialog(selectedStrategy)"><v-icon start>mdi-rocket-launch-outline</v-icon>部署到实盘</v-btn>
              <v-btn v-if="!selectedStrategy.readonly_reference" color="primary" :loading="strategySaving === selectedStrategy.strategy_id" :disabled="!hasStrategyChanges" @click="saveSelectedStrategy"><v-icon start>mdi-content-save-outline</v-icon>保存修改</v-btn>
            </div>
          </section>

          <v-card class="strategy-detail-shell" elevation="0">
            <v-alert v-if="selectedStrategy.readonly_reference" type="info" variant="tonal" class="ma-4 mb-0">这是平台共享策略的只读引用。你可以使用或停止使用，也可以复制为自己的私有草稿后再调整。</v-alert>
            <v-tabs v-model="strategyDetailTab" color="primary" class="strategy-detail-tabs">
              <v-tab value="overview">概览</v-tab><v-tab value="signals">信号源 <v-chip size="x-small" class="ml-2">{{ signalSourceCount(selectedStrategy) }}</v-chip></v-tab><v-tab value="risk">仓位与风控</v-tab><v-tab value="lifecycle">验证与生命周期</v-tab>
            </v-tabs>
            <v-divider></v-divider>
            <v-window v-model="strategyDetailTab" class="strategy-detail-content">
              <v-window-item value="overview">
                <div class="detail-section-title"><div><h3>策略基础信息</h3><p>这些信息用于识别策略并控制多信号源如何共同决策。</p></div></div>
                <v-row><v-col cols="12" md="6"><v-text-field v-model="selectedStrategy.strategy_name" label="策略名称" :readonly="selectedStrategy.readonly_reference"></v-text-field></v-col><v-col cols="12" md="3"><v-text-field :model-value="selectedStrategy.symbol" label="交易品种" readonly></v-text-field></v-col><v-col cols="12" md="3"><v-text-field v-model.number="selectedStrategy.min_confidence" label="最低置信度" type="number" min="0" max="100" suffix="%" :readonly="selectedStrategy.readonly_reference"></v-text-field></v-col><v-col cols="12" md="6"><v-select v-model="selectedStrategy.consistency_requirement" :items="consistencyOptions" label="一致性要求" :disabled="selectedStrategy.readonly_reference"></v-select></v-col></v-row>
                <div class="strategy-setting-card" :class="{ 'is-active': selectedStrategy.is_shared }"><div><v-icon>mdi-share-variant-outline</v-icon><div><strong>共享到平台策略库</strong><p>其他用户可只读使用；一旦被应用，源策略会冻结，后续请复制新版本。</p></div></div><v-switch v-model="selectedStrategy.is_shared" color="success" hide-details :disabled="selectedStrategy.readonly_reference"></v-switch></div>
              </v-window-item>

              <v-window-item value="signals">
                <div class="detail-section-title"><div><h3>信号源配置</h3><p>关键点位与其他信号互斥；AI、转折点、均线和 Alpha 可以按不同周期组合。</p></div><v-btn v-if="!selectedStrategy.readonly_reference" color="primary" variant="tonal" @click="openSignalSourceDialog(selectedStrategy)"><v-icon start>mdi-plus</v-icon>添加信号源</v-btn></div>
                <div v-if="selectedStrategySignalSources.length" class="signal-source-list">
                  <article v-for="source in selectedStrategySignalSources" :key="source.signal_source_id" class="signal-source-card">
                    <div class="signal-source-card__head"><div class="d-flex align-center flex-wrap ga-2"><v-avatar size="34" color="grey-lighten-4"><v-icon :color="sourceMetaFor(source.source).color" size="19">{{ sourceMetaFor(source.source).icon }}</v-icon></v-avatar><div><strong>{{ sourceMetaFor(source.source).label }}</strong><div class="text-caption text-medium-emphasis">{{ source.source === 'key_level' ? '全周期共用' : source.period }}</div></div></div><div class="d-flex align-center"><v-switch v-model="source.enabled" color="success" density="compact" hide-details :disabled="selectedStrategy.readonly_reference"></v-switch><v-btn v-if="!selectedStrategy.readonly_reference" icon="mdi-pencil-outline" size="small" variant="text" color="primary" @click="openSignalSourceDialog(selectedStrategy, source)"></v-btn><v-btn v-if="!selectedStrategy.readonly_reference" icon="mdi-delete-outline" size="small" variant="text" color="error" @click="removeSignalSource(selectedStrategy, source)"></v-btn></div></div>
                    <div class="signal-source-summary"><v-chip size="x-small" variant="outlined">权重 {{ source.weight }}</v-chip><span class="text-caption text-medium-emphasis">{{ signalSourceSummary(source) }}</span></div>
                  </article>
                </div>
                <div v-else class="strategy-empty compact">
                  <v-icon size="44">mdi-access-point-plus</v-icon>
                  <h3>还没有信号源</h3>
                  <p>添加关键点位、AI 入场、均线交叉或已验证 Alpha。</p>
                  <p v-if="selectedStrategy?.signal_sources?.length" class="text-caption text-error">
                    检测到原始信号源 {{ selectedStrategy.signal_sources.length }} 条，但当前页面无法识别，请刷新或重新保存策略。
                  </p>
                </div>
              </v-window-item>

              <v-window-item value="risk">
                <div class="detail-section-title"><div><h3>仓位与风险约束</h3><p>控制每次交易的规模，以及策略能够同时持有的仓位。</p></div></div>
                <v-row><v-col cols="12" sm="6" md="3"><v-text-field v-model.number="selectedStrategy.fixed_volume" label="固定手数" type="number" step="0.01" min="0.01" :readonly="selectedStrategy.readonly_reference"></v-text-field></v-col><v-col cols="12" sm="6" md="3"><v-text-field v-model.number="selectedStrategy.max_positions" label="最大持仓数" type="number" min="1" :readonly="selectedStrategy.readonly_reference"></v-text-field></v-col><v-col cols="12" sm="6" md="3"><v-text-field v-model.number="selectedStrategy.max_same_direction" label="同向最大持仓" type="number" min="1" :readonly="selectedStrategy.readonly_reference"></v-text-field></v-col><v-col cols="12" sm="6" md="3"><v-text-field v-model.number="selectedStrategy.risk_percent" label="单笔风险比例" type="number" min="0.1" step="0.1" suffix="%" :readonly="selectedStrategy.readonly_reference"></v-text-field></v-col></v-row>
                <div class="detail-section-title mt-5"><div><h3>持仓管理方案</h3><p>策略创建时已完成方案绑定；这里仅展示当前绑定，方案内容请在持仓管理页面维护。</p></div><v-btn to="/position-management" variant="text" color="primary" prepend-icon="mdi-shield-edit-outline">管理方案</v-btn></div>
                <v-text-field
                  :model-value="positionPolicyOptions.find(item => item.value === selectedStrategy.position_management_policy_id)?.title || '未绑定持仓管理方案'"
                  label="当前持仓管理方案"
                  prepend-inner-icon="mdi-shield-check-outline"
                  readonly
                  max-width="620"
                />
              </v-window-item>

              <v-window-item value="lifecycle">
                <div class="lifecycle-banner"><div><span>当前阶段</span><h3>{{ getLifecycleMeta(selectedStrategy).label }}</h3><p>{{ getLifecycleMeta(selectedStrategy).description }}</p></div><div class="d-flex flex-wrap ga-2"><v-btn v-for="action in getLifecycleActions(selectedStrategy)" :key="action.target" :color="action.color" variant="outlined" :disabled="isLifecycleActionDisabled(selectedStrategy, action)" :loading="strategyLifecycleSaving === selectedStrategy.strategy_id" @click="transitionStrategyLifecycle(selectedStrategy, action)"><v-icon start>{{ action.icon }}</v-icon>{{ action.label }}</v-btn></div></div>
                <div v-if="getAdmission(selectedStrategy)" class="admission-panel mt-5"><div class="admission-title"><div><strong>策略准入证据</strong><span>只认可当前参数版本产生的验证结果</span></div><v-chip size="small" :color="getAdmission(selectedStrategy).eligible_for_production ? 'success' : 'warning'" variant="tonal">{{ getAdmission(selectedStrategy).eligible_for_production ? '满足实盘准入' : '验证进行中' }}</v-chip></div><div class="admission-stages"><article v-for="stage in admissionStages(selectedStrategy)" :key="stage.key"><div><v-icon size="18" :color="stage.data.passed ? 'success' : 'grey'">{{ stage.data.passed ? 'mdi-check-decagram' : 'mdi-progress-clock' }}</v-icon><strong>{{ stage.label }}</strong></div><p>{{ stage.data.message }}</p><div v-if="stage.data.checks?.length" class="admission-checks"><v-chip v-for="check in stage.data.checks" :key="check.key" size="x-small" :color="check.passed ? 'success' : 'error'" variant="tonal">{{ check.label }}</v-chip></div></article></div></div>
                <div class="danger-zone"><div><strong>{{ selectedStrategy.readonly_reference ? '移除共享策略引用' : '删除策略' }}</strong><p>{{ selectedStrategy.readonly_reference ? '需先结束该策略在所有账户上的部署；移除不会影响原作者或其他使用者。' : '删除后无法恢复；被回测任务引用时后端会阻止删除。' }}</p></div><v-btn color="error" variant="outlined" @click="deleteStrategy(selectedStrategy)"><v-icon start>mdi-delete-outline</v-icon>{{ selectedStrategy.readonly_reference ? '移除使用' : '删除策略' }}</v-btn></div>
              </v-window-item>
            </v-window>
          </v-card>
        </template>
      </v-col>
    </v-row>

    <v-dialog v-model="paperDeployDialog" max-width="560">
      <v-card>
        <v-card-title class="d-flex align-center justify-space-between">
          <span>部署到模拟账户</span>
          <v-btn icon="mdi-close" variant="text" @click="paperDeployDialog = false"></v-btn>
        </v-card-title>
        <v-card-text>
          <v-alert type="info" variant="tonal" class="mb-4">
            模拟运行会绑定当前策略配置。包含 AI、转折点、整数点位信号源的策略可以跳过回测，直接进入模拟观察；其他策略仍需满足回测准入。
          </v-alert>
          <v-select
            v-model="paperDeployAccountId"
            :items="paperAccountOptions"
            item-title="label"
            item-value="value"
            label="选择 Paper 账户"
            :loading="paperDeployLoading"
            :disabled="paperDeployLoading"
          ></v-select>
          <v-alert v-if="!paperAccounts.length && !paperDeployLoading" type="warning" variant="tonal">
            还没有可用的 Paper 模拟账户，请先到交易账户页面创建。
          </v-alert>
        </v-card-text>
        <v-card-actions>
          <v-spacer></v-spacer>
          <v-btn variant="text" @click="paperDeployDialog = false">取消</v-btn>
          <v-btn color="warning" :disabled="!paperDeployAccountId" :loading="paperDeploySubmitting" @click="deploySelectedStrategyToPaper">
            开始模拟运行
          </v-btn>
        </v-card-actions>
      </v-card>
    </v-dialog>

    <v-dialog v-model="liveDeployDialog" max-width="580">
      <v-card>
        <v-card-title class="d-flex align-center justify-space-between">
          <span>部署到实盘账户</span>
          <v-btn icon="mdi-close" variant="text" @click="liveDeployDialog = false"></v-btn>
        </v-card-title>
        <v-card-text>
          <v-alert type="warning" variant="tonal" class="mb-4">
            实盘部署会让策略参与 MT5 实盘账户的自动交易决策。请确认账户风控、持仓管理方案和 EA 连接状态都已检查。
          </v-alert>
          <v-select
            v-model="liveDeployAccountId"
            :items="liveAccountOptions"
            item-title="label"
            item-value="value"
            label="选择 MT5 实盘账户"
            :loading="liveDeployLoading"
            :disabled="liveDeployLoading"
          ></v-select>
          <v-alert v-if="!liveAccounts.length && !liveDeployLoading" type="warning" variant="tonal">
            还没有可用的 MT5 实盘账户。请先启动 EA，并确保账户已启用交易。
          </v-alert>
        </v-card-text>
        <v-card-actions>
          <v-spacer></v-spacer>
          <v-btn variant="text" @click="liveDeployDialog = false">取消</v-btn>
          <v-btn color="success" :disabled="!liveDeployAccountId" :loading="liveDeploySubmitting" @click="deploySelectedStrategyToLive">
            确认部署实盘
          </v-btn>
        </v-card-actions>
      </v-card>
    </v-dialog>

    <!-- 账户与安全 -->
    <v-row v-if="!isStrategyPage && settingsTab === 'account'">
      <v-col cols="12">
        <v-card class="user-settings-card" elevation="0">
          <v-card-title class="settings-card-title">
            <div><v-icon>mdi-account-lock-outline</v-icon><span>账户与安全</span></div>
            <small>个人资料与登录保护</small>
          </v-card-title>
          <v-card-text>
            <v-row>
              <v-col cols="12" md="6">
                <div class="text-subtitle-2 mb-3">当前账户</div>
                <v-list density="compact" class="account-summary">
                  <v-list-item title="用户名" :subtitle="currentUser.username">
                    <template #prepend>
                      <v-icon>mdi-account-outline</v-icon>
                    </template>
                  </v-list-item>
                  <v-list-item title="登录邮箱" :subtitle="currentUser.email || '未绑定邮箱'">
                    <template #prepend><v-icon>mdi-email-outline</v-icon></template>
                  </v-list-item>
                  <v-list-item title="用户角色">
                    <template #prepend>
                      <v-icon>mdi-shield-account</v-icon>
                    </template>
                    <template #subtitle>
                      <v-chip
                        size="small"
                        :color="currentUser.role === 'admin' ? 'primary' : 'grey'"
                        variant="tonal"
                      >
                        {{ roleLabel }}
                      </v-chip>
                    </template>
                  </v-list-item>
                  <v-list-item title="会员等级">
                    <template #prepend><v-icon>mdi-crown-outline</v-icon></template>
                    <template #subtitle>
                      <v-chip size="small" :color="membershipColor(currentUser.membership_level)" variant="tonal">
                        {{ membershipLabel(currentUser.membership_level) }}
                      </v-chip>
                    </template>
                  </v-list-item>
                  <v-list-item title="实盘交易" :subtitle="currentUser.role === 'admin' || currentUser.live_trading_enabled ? '已授权' : '未授权'">
                    <template #prepend><v-icon>mdi-finance</v-icon></template>
                  </v-list-item>
                </v-list>
              </v-col>

              <v-col cols="12" md="6">
                <div class="text-subtitle-2 mb-3">登录方式</div>
                <v-alert type="success" variant="tonal">
                  所有成员（包括管理员）统一使用注册邮箱接收验证码登录，验证码 3 分钟内有效，无需设置或记忆密码。
                </v-alert>
              </v-col>
            </v-row>
          </v-card-text>
        </v-card>
      </v-col>
    </v-row>

    <!-- 管理员邮件服务配置 -->
    <v-row v-if="!isStrategyPage && isAdmin && settingsTab === 'email'">
      <v-col cols="12">
        <v-card class="user-settings-card admin-service-card" elevation="0">
          <v-card-title class="settings-card-title d-flex align-center justify-space-between flex-wrap ga-2">
            <div><v-icon class="mr-2">mdi-email-lock-outline</v-icon>验证码邮件服务</div>
            <v-chip :color="emailConfig.enabled && emailConfig.password_set ? 'success' : 'warning'" variant="tonal" size="small">
              {{ emailConfig.enabled && emailConfig.password_set ? '已启用' : emailConfig.password_set ? '已停用' : '待配置' }}
            </v-chip>
          </v-card-title>
          <v-card-text>
            <v-alert type="info" variant="tonal" density="compact" class="mb-5">
              用于发送注册及登录验证码。SMTP 密码加密存储且不会回显；留空表示保留现有密码。
            </v-alert>
            <v-row>
              <v-col cols="12" md="4"><v-text-field v-model="emailConfig.smtp_host" label="SMTP 服务器" variant="outlined" density="compact" /></v-col>
              <v-col cols="12" sm="6" md="2"><v-text-field v-model.number="emailConfig.smtp_port" label="端口" type="number" variant="outlined" density="compact" /></v-col>
              <v-col cols="12" sm="6" md="2"><v-switch v-model="emailConfig.use_ssl" color="success" label="SSL 加密" hide-details /></v-col>
              <v-col cols="12" md="4"><v-text-field v-model="emailConfig.sender_name" label="发件人名称" variant="outlined" density="compact" /></v-col>
              <v-col cols="12" md="6"><v-text-field v-model="emailConfig.sender_email" label="发件邮箱" prepend-inner-icon="mdi-email-outline" variant="outlined" density="compact" /></v-col>
              <v-col cols="12" md="6">
                <v-text-field
                  v-model="emailConfig.password"
                  label="SMTP 密码"
                  :placeholder="emailConfig.password_set ? '已加密保存，输入新密码可覆盖' : '请输入 SMTP 密码或客户端安全密码'"
                  :type="showEmailPassword ? 'text' : 'password'"
                  :append-inner-icon="showEmailPassword ? 'mdi-eye-off' : 'mdi-eye'"
                  prepend-inner-icon="mdi-key-outline"
                  variant="outlined"
                  density="compact"
                  @click:append-inner="showEmailPassword = !showEmailPassword"
                />
              </v-col>
            </v-row>
            <div class="d-flex align-center flex-wrap ga-3">
              <v-switch v-model="emailConfig.enabled" color="success" label="允许发送注册验证码" hide-details />
              <v-spacer />
              <v-btn variant="tonal" color="primary" :loading="emailTesting" :disabled="!emailConfig.password_set && !emailConfig.password" @click="testEmailConfig">
                <v-icon start>mdi-email-fast-outline</v-icon>发送测试邮件
              </v-btn>
              <v-btn color="primary" :loading="emailSaving" @click="saveEmailConfig">
                <v-icon start>mdi-content-save-lock-outline</v-icon>保存邮件配置
              </v-btn>
            </div>
          </v-card-text>
        </v-card>
      </v-col>
    </v-row>

    <!-- 管理员交易商品种映射 -->
    <v-row v-if="!isStrategyPage && isAdmin && settingsTab === 'instruments'">
      <v-col cols="12">
        <v-card class="user-settings-card admin-service-card" elevation="0">
          <v-card-title class="settings-card-title"><div><v-icon>mdi-swap-horizontal-bold</v-icon><span>交易商品种关联映射</span></div><small>同一关联组内的品种可用于共享策略与共享 AI 数据</small></v-card-title>
          <v-card-text>
            <v-alert type="info" variant="tonal" density="compact" class="mb-5">系统会从 MT5 服务器自动提取交易商，例如 <code>XMGlobal-MT5 2 → XMGlobal</code>。这里仅维护“交易商 + 原始品种”关系；把不同交易商的黄金品种放入同一个关联组即可共享。未配置时仅允许完全同名品种共享。</v-alert>
            <div class="d-flex align-center justify-space-between mb-3"><div><strong>最近上报价格</strong><p class="text-caption text-medium-emphasis mb-0">每个交易商和品种仅保留最近 5 条、约 1 分钟内的报价，管理员可据此手工判断是否建立关联。</p></div><v-btn size="small" variant="text" prepend-icon="mdi-refresh" @click="loadInstrumentPriceObservations">刷新</v-btn></div>
            <v-table v-if="instrumentPriceObservations.length" density="compact" class="quota-table mb-5"><thead><tr><th>交易商</th><th>品种</th><th>最新买价</th><th>最新卖价</th><th>中间价</th><th>最近 5 次中间价</th><th>关联状态</th><th>操作</th></tr></thead><tbody><tr v-for="item in instrumentPriceObservations" :key="`${item.broker_name}-${item.symbol}`"><td>{{ item.broker_name }}</td><td><strong>{{ item.symbol }}</strong></td><td>{{ formatInstrumentPrice(item.latest?.bid) }}</td><td>{{ formatInstrumentPrice(item.latest?.ask) }}</td><td>{{ formatInstrumentPrice(item.latest?.mid) }}</td><td><span v-for="(price, index) in item.prices" :key="`${item.symbol}-${price.timestamp}-${index}`" class="mr-2">{{ formatInstrumentPrice(price.mid) }}</span></td><td><v-chip size="small" :color="item.mapped ? 'success' : 'grey'" variant="tonal">{{ item.mapped ? `已关联 · ${item.mapping_group}` : '未关联' }}</v-chip></td><td><v-btn v-if="!item.mapped" size="small" color="primary" variant="tonal" @click="useInstrumentPriceObservation(item)">建立关联</v-btn><span v-else class="text-caption text-success">已建立</span></td></tr></tbody></v-table>
            <v-alert v-else type="info" variant="tonal" density="compact" class="mb-5">暂无最近价格数据。EA 上报统计数据后，这里会显示最新报价。</v-alert>
            <div class="d-flex align-center justify-space-between mb-3"><div><strong>已上报行情的交易商与品种</strong><p class="text-caption text-medium-emphasis mb-0">仅统计 EA 实际上传过 K 线的组合，可点击直接建立关联。</p></div><v-btn size="small" variant="text" prepend-icon="mdi-refresh" @click="loadInstrumentObservations">刷新</v-btn></div>
            <v-table v-if="instrumentObservations.length" density="compact" class="quota-table mb-5"><thead><tr><th>交易商</th><th>MT5 服务器</th><th>上报品种</th><th>账户数</th><th>最近上报</th><th></th></tr></thead><tbody><tr v-for="item in instrumentObservations" :key="`${item.broker_server}-${item.symbol}`"><td><strong>{{ item.broker_name || '未识别' }}</strong></td><td>{{ item.broker_server || '--' }}</td><td><v-chip size="small" color="success" variant="tonal">{{ item.symbol }}</v-chip></td><td>{{ item.account_count }}</td><td>{{ formatInvitationTime(item.last_reported_at) }}</td><td class="text-right"><v-btn size="small" color="primary" variant="tonal" @click="useInstrumentObservation(item)">建立关联</v-btn></td></tr></tbody></v-table>
            <v-alert v-else type="info" variant="tonal" density="compact" class="mb-5">暂未收到 EA 上报的 K 线行情。</v-alert>
            <v-row>
              <v-col cols="12" md="4"><v-text-field v-model="instrumentMappingForm.broker_name" label="交易商" placeholder="XMGlobal" density="compact" variant="outlined" /></v-col>
              <v-col cols="12" md="3"><v-text-field v-model="instrumentMappingForm.native_symbol" label="原始品种" placeholder="GOLD_" density="compact" variant="outlined" /></v-col>
              <v-col cols="12" md="3"><v-text-field v-model="instrumentMappingForm.mapping_group" label="关联组" placeholder="XAUUSD" density="compact" variant="outlined" /></v-col>
              <v-col cols="12" md="2"><v-text-field v-model="instrumentMappingForm.display_name" label="展示名称（可选）" density="compact" variant="outlined" /></v-col>
            </v-row>
            <div class="d-flex justify-end mb-5"><v-btn color="primary" :loading="instrumentMappingSaving" @click="saveInstrumentMapping"><v-icon start>mdi-content-save-outline</v-icon>保存映射</v-btn></div>
            <v-table v-if="instrumentMappings.length" density="comfortable" class="quota-table"><thead><tr><th>关联组</th><th>交易商</th><th>原始品种</th><th>展示名称</th><th>状态</th><th class="text-right">操作</th></tr></thead><tbody><tr v-for="item in instrumentMappings" :key="item.mapping_id"><td><v-chip size="small" color="primary" variant="tonal">{{ item.mapping_group }}</v-chip></td><td>{{ item.effective_broker_name }}</td><td><strong>{{ item.native_symbol }}</strong></td><td>{{ item.display_name || '--' }}</td><td><v-chip size="x-small" :color="item.enabled ? 'success' : 'grey'" variant="tonal">{{ item.enabled ? '启用' : '停用' }}</v-chip></td><td class="text-right"><v-btn icon="mdi-delete-outline" size="small" variant="text" color="error" @click="deleteInstrumentMapping(item)"></v-btn></td></tr></tbody></v-table>
            <div v-else class="text-medium-emphasis text-center py-8">尚未配置交易商品种映射。</div>
          </v-card-text>
        </v-card>
      </v-col>
    </v-row>

    <!-- 管理员用户与会员权益 -->
    <v-row v-if="!isStrategyPage && isAdmin && settingsTab === 'quota'">
      <v-col cols="12">
        <v-card class="user-settings-card admin-service-card mb-5" elevation="0">
          <v-card-title class="settings-card-title">
            <div><v-icon>mdi-ticket-account</v-icon><span>私人邀请管理</span></div>
            <small>注册入口不对公众开放</small>
          </v-card-title>
          <v-card-text>
            <v-alert type="info" variant="tonal" density="compact" class="mb-4">
              邀请链接和邀请码使用同一凭证。完整邀请码只在创建成功后显示一次，请及时复制邀请链接。
            </v-alert>
            <div class="invitation-form">
              <v-text-field v-model="invitationForm.label" label="邀请备注" placeholder="例如：测试伙伴张三" variant="outlined" density="compact" hide-details />
              <v-text-field v-model.number="invitationForm.max_uses" label="可注册人数" type="number" min="1" max="1000" variant="outlined" density="compact" hide-details />
              <v-text-field v-model.number="invitationForm.expires_days" label="有效天数" type="number" min="1" max="365" variant="outlined" density="compact" hide-details />
              <v-btn color="primary" height="40" :loading="invitationSaving" @click="createInvitation"><v-icon start>mdi-link-plus</v-icon>生成邀请链接</v-btn>
            </div>
            <div v-if="latestInviteLink" class="invite-result mt-4">
              <div><small>新邀请链接，仅本次显示</small><strong>{{ latestInviteLink }}</strong></div>
              <v-btn color="success" variant="tonal" @click="copyLatestInvite"><v-icon start>mdi-content-copy</v-icon>复制链接</v-btn>
            </div>
            <v-table v-if="invitations.length" density="comfortable" class="quota-table mt-4">
              <thead><tr><th>邀请码</th><th>备注</th><th>使用情况</th><th>有效期至</th><th>状态</th></tr></thead>
              <tbody>
                <tr v-for="item in invitations" :key="item.invitation_id">
                  <td><strong>{{ item.code_prefix }}********</strong></td>
                  <td>{{ item.label || '未备注' }}</td>
                  <td>{{ item.used_count }} / {{ item.max_uses }}</td>
                  <td>{{ formatInvitationTime(item.expires_at) }}</td>
                  <td><v-switch :model-value="item.active" color="success" hide-details :loading="invitationSaving === item.invitation_id" @update:model-value="setInvitationActive(item, $event)" /></td>
                </tr>
              </tbody>
            </v-table>
            <v-empty-state v-else icon="mdi-ticket-outline" title="还没有邀请码" text="创建后即可邀请私人测试成员加入。" />
          </v-card-text>
        </v-card>
        <v-card class="user-settings-card admin-service-card" elevation="0">
          <v-card-title class="settings-card-title d-flex align-center justify-space-between flex-wrap ga-2">
            <div><v-icon class="mr-2">mdi-account-star-outline</v-icon>已注册用户、会员与配额</div>
            <v-chip color="success" variant="tonal" size="small">新用户默认白银会员</v-chip>
          </v-card-title>
          <v-card-text>
            <v-alert type="info" variant="tonal" density="compact" class="mb-4">
              等级决定默认资源额度；留空表示跟随会员等级。实盘必须同时满足黄金/钻石等级和管理员授权，管理员账号不受限制。
            </v-alert>
            <v-alert v-if="quotaLoading" type="info" variant="tonal" density="compact" class="mb-3">正在加载用户与会员数据…</v-alert>
            <v-alert v-else-if="quotaError" type="error" variant="tonal" density="compact" class="mb-3">{{ quotaError }}</v-alert>
            <v-alert v-else-if="!quotaUsers.length" type="warning" variant="tonal" density="compact" class="mb-3">暂无用户数据。</v-alert>
            <v-table v-else density="comfortable" class="quota-table">
              <thead><tr><th>用户</th><th>会员等级</th><th>实盘授权</th><th>当前用量</th><th>数据集上限</th><th>策略上限</th><th>信号源上限</th><th></th></tr></thead>
              <tbody>
                <tr v-for="item in quotaUsers" :key="item.user_id">
                  <td><strong>{{ item.username }}</strong><small>{{ item.email || '未绑定邮箱' }}</small></td>
                  <td>
                    <v-select v-model="item.membershipDraft.membership_level" :items="membershipOptions" :disabled="item.role === 'admin'" density="compact" hide-details style="min-width: 126px" @update:model-value="!liveEligibleLevel($event) && (item.membershipDraft.live_trading_enabled = false)" />
                  </td>
                  <td>
                    <v-switch v-model="item.membershipDraft.live_trading_enabled" color="success" hide-details :disabled="item.role === 'admin' || !liveEligibleLevel(item.membershipDraft.membership_level)" />
                    <small>{{ liveEligibleLevel(item.membershipDraft.membership_level) ? '管理员授权后可实盘' : '黄金会员起开放' }}</small>
                  </td>
                  <td>
                    <v-chip size="x-small" variant="tonal">数据集 {{ item.usage.datasets }}/{{ item.limits.datasets ?? '∞' }}</v-chip>
                    <v-chip size="x-small" variant="tonal" class="ml-1">策略 {{ item.usage.strategies }}/{{ item.limits.strategies ?? '∞' }}</v-chip>
                    <v-chip size="x-small" variant="tonal" class="ml-1">信号 {{ item.usage.signal_sources }}/{{ item.limits.signal_sources ?? '∞' }}</v-chip>
                  </td>
                  <td><v-text-field v-model="item.quotaDraft.max_datasets" :disabled="item.role === 'admin'" placeholder="等级默认" type="number" min="0" max="1000" density="compact" hide-details /></td>
                  <td><v-text-field v-model="item.quotaDraft.max_strategies" :disabled="item.role === 'admin'" placeholder="等级默认" type="number" min="0" max="1000" density="compact" hide-details /></td>
                  <td><v-text-field v-model="item.quotaDraft.max_signal_sources" :disabled="item.role === 'admin'" placeholder="等级默认" type="number" min="0" max="1000" density="compact" hide-details /></td>
                  <td><div class="d-flex ga-1"><v-btn size="small" color="primary" :disabled="item.role === 'admin'" :loading="quotaSaving === item.user_id" @click="saveUserQuota(item)">保存</v-btn><v-btn size="small" variant="tonal" :disabled="item.role === 'admin'" @click="viewAsUser(item)">查看页面</v-btn></div></td>
                </tr>
              </tbody>
            </v-table>
          </v-card-text>
        </v-card>
        <v-card class="user-settings-card admin-service-card mt-5" elevation="0">
          <v-card-title class="settings-card-title d-flex align-center justify-space-between flex-wrap ga-2">
            <div><v-icon class="mr-2">mdi-shield-crown-outline</v-icon>用户策略状态治理</div>
            <v-btn icon="mdi-refresh" variant="text" :loading="adminStrategiesLoading" @click="loadAdminStrategies" />
          </v-card-title>
          <v-card-text>
            <v-alert type="info" variant="tonal" density="compact" class="mb-4">
              管理员只推进策略生命周期；进入“可用于实盘”后，用户侧可将策略部署到账户运行。共享引用策略允许推进到实盘。
            </v-alert>
            <div class="strategy-governance-toolbar">
              <v-text-field v-model="adminStrategySearch" label="搜索用户/策略/品种" prepend-inner-icon="mdi-magnify" density="compact" variant="outlined" hide-details />
              <v-select v-model="adminStrategyLifecycleFilter" :items="lifecycleFilterOptions" label="生命周期" density="compact" variant="outlined" hide-details />
            </div>
            <v-table density="comfortable" class="quota-table admin-strategy-table mt-4">
              <thead><tr><th>用户</th><th>策略</th><th>信号源</th><th>当前状态</th><th>推进到</th><th>备注</th><th></th></tr></thead>
              <tbody>
                <tr v-for="item in filteredAdminStrategies" :key="`${item.user_id}:${item.strategy_id}`">
                  <td>
                    <strong>{{ item.username }}</strong>
                    <small>{{ item.email || '未绑定邮箱' }} · {{ membershipLabel(item.membership_level) }} · 实盘{{ item.live_trading_enabled ? '已授权' : '未授权' }}</small>
                  </td>
                  <td>
                    <strong>{{ item.strategy_name }}</strong>
                    <small>{{ item.symbol }} · {{ item.strategy_id }}<span v-if="item.source_owner_user_id"> · 共享引用</span></small>
                  </td>
                  <td>
                    <v-chip v-for="source in item.signal_sources || []" :key="source.signal_source_id" size="x-small" variant="tonal" class="mr-1 mb-1">
                      {{ signalSourceLabel(source.source) }} {{ source.period }}
                    </v-chip>
                  </td>
                  <td><v-chip :color="getLifecycleColor(item.lifecycle_status)" size="small" variant="tonal">{{ lifecycleLabel(item.lifecycle_status) }}</v-chip></td>
                  <td>
                    <v-select v-model="item.adminTargetStatus" :items="adminLifecycleOptions" density="compact" hide-details style="min-width: 132px" />
                  </td>
                  <td>
                    <v-text-field v-model="item.adminReason" placeholder="可选" density="compact" hide-details style="min-width: 150px" />
                  </td>
                  <td>
                    <div class="d-flex ga-1">
                      <v-btn
                        size="small"
                        variant="tonal"
                        color="info"
                        prepend-icon="mdi-monitor-eye"
                        :loading="adminDeploymentsLoading === `${item.user_id}:${item.strategy_id}`"
                        @click="openAdminDeployments(item)"
                      >部署</v-btn>
                      <v-btn
                        size="small"
                        color="primary"
                        :disabled="item.adminTargetStatus === item.lifecycle_status"
                        :loading="adminStrategySaving === `${item.user_id}:${item.strategy_id}`"
                        @click="adminPromoteStrategy(item)"
                      >推进</v-btn>
                    </div>
                  </td>
                </tr>
              </tbody>
            </v-table>
            <v-empty-state v-if="!adminStrategiesLoading && !filteredAdminStrategies.length" icon="mdi-shield-search" title="没有符合条件的策略" text="调整筛选条件后再试试。" />
          </v-card-text>
        </v-card>
      </v-col>
    </v-row>

    <!-- 管理员: 策略部署详情弹窗 -->
    <v-dialog v-model="adminDeploymentsDialog" max-width="880">
      <v-card class="settings-dialog-card" elevation="0">
        <v-card-title class="settings-card-title d-flex align-center justify-space-between">
          <div>
            <div class="font-weight-bold">
              策略部署 · {{ adminDeploymentsDetail?.strategy?.strategy_name || adminDeploymentsDetail?.strategy?.strategy_id || '' }}
            </div>
            <small class="text-medium-emphasis">
              {{ adminDeploymentsDetail?.strategy?.symbol }} · 生命周期
              <v-chip size="x-small" variant="tonal" class="ml-1" :color="getLifecycleColor(adminDeploymentsDetail?.strategy?.lifecycle_status)">
                {{ lifecycleLabel(adminDeploymentsDetail?.strategy?.lifecycle_status) }}
              </v-chip>
            </small>
          </div>
          <v-btn icon="mdi-close" variant="text" @click="adminDeploymentsDialog = false" />
        </v-card-title>
        <v-card-text>
          <v-alert v-if="adminDeploymentsError" type="error" variant="tonal" density="compact" class="mb-4">{{ adminDeploymentsError }}</v-alert>
          <v-table density="comfortable" class="quota-table mt-2">
            <thead>
              <tr>
                <th>账户</th>
                <th>类型 / 环境</th>
                <th>部署状态</th>
                <th>余额</th>
                <th>净值</th>
                <th>浮动盈亏</th>
                <th>累计盈亏</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="dep in adminDeploymentsDetail?.deployments || []" :key="dep.deployment_id">
                <td>
                  <strong>{{ dep.account_name }}</strong>
                  <small class="d-block text-medium-emphasis">#{{ dep.account_id }}</small>
                </td>
                <td>
                  <v-chip v-if="dep.account_type === 'mt5'" size="x-small" color="success" variant="tonal">MT5 实盘</v-chip>
                  <v-chip v-else size="x-small" color="teal" variant="tonal">Paper 模拟</v-chip>
                  <div class="mt-1"><small>{{ environmentLabel(dep.environment) }}</small></div>
                  <small v-if="dep.account_type === 'mt5'" :class="dep.connected ? 'text-success' : 'text-grey'">{{ dep.connected ? '终端在线' : '终端离线' }}</small>
                </td>
                <td>
                  <v-chip size="x-small" :color="dep.status === 'active' ? 'success' : 'grey'" variant="tonal">
                    {{ deploymentStatusLabelForAdmin(dep.status) }}
                  </v-chip>
                </td>
                <td>{{ money(dep.balance, dep.currency) }}</td>
                <td>{{ money(dep.equity, dep.currency) }}</td>
                <td :class="pnlClass(dep.unrealized_pnl)">{{ signedMoney(dep.unrealized_pnl) }}</td>
                <td :class="pnlClass(dep.total_pnl)">
                  {{ signedMoney(dep.total_pnl) }}
                  <small class="d-block" :class="pnlClass(dep.total_pnl_pct)">{{ signedDelta(dep.total_pnl_pct) }}%</small>
                </td>
              </tr>
              <tr v-if="!adminDeploymentsDetail?.deployments?.length">
                <td colspan="7" class="text-center text-medium-emphasis py-6">该策略当前未部署到任何账户</td>
              </tr>
            </tbody>
          </v-table>
        </v-card-text>
      </v-card>
    </v-dialog>

    <!-- 大模型功能与管理员配置 -->
    <v-row v-if="!isStrategyPage && settingsTab === 'llm'">
      <v-col cols="12">
        <v-card class="user-settings-card" :class="{ 'admin-service-card': isAdmin }" elevation="0">
          <v-card-title class="settings-card-title">
            <div><v-icon>mdi-brain</v-icon><span>{{ isAdmin ? '大模型配置与审批' : '大模型行情分析' }}</span></div>
            <small>{{ isAdmin ? '全局服务与用户开通申请' : '开通后可使用自主 AI 分析' }}</small>
          </v-card-title>
          <v-card-text>
            <v-tabs v-if="isAdmin" v-model="llmWorkspaceTab" color="primary" class="llm-workspace-tabs">
              <v-tab value="providers"><v-icon start>mdi-server-network-outline</v-icon>供应商</v-tab>
              <v-tab value="models"><v-icon start>mdi-database-cog-outline</v-icon>模型目录</v-tab>
              <v-tab value="scenes"><v-icon start>mdi-text-box-edit-outline</v-icon>场景与提示词</v-tab>
              <v-tab value="requests"><v-icon start>mdi-account-clock-outline</v-icon>开通审批 <v-chip size="x-small" class="ml-2">{{ llmAccessRequests.length }}</v-chip></v-tab>
            </v-tabs>
            <v-form v-if="isAdmin && llmWorkspaceTab === 'providers'" ref="llmForm">
              <div class="llm-section-head">
                <div>
                  <div class="font-weight-bold">供应商配置</div>
                  <div class="text-caption text-medium-emphasis">
                    可保存多套 BASE URL/API Key；只有一个供应商会作为平台当前有效配置。
                  </div>
                </div>
                <v-btn variant="tonal" prepend-icon="mdi-plus" @click="newLLMProvider">
                  新增供应商
                </v-btn>
              </div>
              <v-row>
                <v-col cols="12" md="4">
                  <v-text-field
                    v-model="llmConfig.provider_name"
                    label="供应商名称"
                    dense
                    hide-details
                    placeholder="例如 DeepSeek 生产环境"
                  ></v-text-field>
                </v-col>
                <v-col cols="12" md="4">
                  <v-switch
                    v-model="llmConfig.active"
                    color="success"
                    hide-details
                    label="保存后设为当前有效"
                  />
                </v-col>
                <v-col cols="12" md="4">
                  <v-text-field
                    v-model="llmConfig.api_key"
                    label="API Key"
                    :type="showApiKey ? 'text' : 'password'"
                    :append-icon="showApiKey ? 'mdi-eye-off' : 'mdi-eye'"
                    @click:append="showApiKey = !showApiKey"
                    dense
                    hide-details
                    :placeholder="llmConfig.api_key_set ? '已设置（输入可更新）' : '请输入 API Key'"
                  ></v-text-field>
                </v-col>
                <v-col cols="12" md="4">
                  <v-text-field
                    v-model="llmConfig.api_base"
                    label="API Base URL"
                    dense
                    hide-details
                    placeholder="https://api.openai.com/v1"
                  ></v-text-field>
                </v-col>
                <v-col cols="12" md="4">
                  <v-text-field
                    v-model="llmConfig.model"
                    label="供应商默认模型"
                    dense
                    hide-details
                    placeholder="gpt-4o-mini"
                  ></v-text-field>
                </v-col>
              </v-row>
              <v-row class="mt-2">
                <v-col cols="12">
                  <v-btn color="primary" @click="saveLLMConfig" :loading="llmSaving">
                    <v-icon start>mdi-content-save</v-icon>
                    保存供应商配置
                  </v-btn>
                  <v-chip
                    class="ml-3"
                    :color="llmConfig.enabled ? 'success' : 'error'"
                    small
                  >
                    {{ llmConfig.enabled ? '已启用' : '未启用' }}
                  </v-chip>
                </v-col>
              </v-row>
              <div v-if="llmGovernance.providers.length" class="provider-grid mt-4">
                <article
                  v-for="provider in llmGovernance.providers"
                  :key="provider.provider_id"
                  class="provider-card"
                  :class="{ active: provider.active }"
                >
                  <div class="d-flex align-center justify-space-between">
                    <strong>{{ provider.provider_name }}</strong>
                    <v-chip size="x-small" :color="provider.active ? 'success' : 'grey'" variant="tonal">
                      {{ provider.active ? '当前有效' : '未启用' }}
                    </v-chip>
                  </div>
                  <p>{{ provider.api_base }}</p>
                  <p>默认模型：{{ provider.model || '--' }}</p>
                  <p>API Key：{{ provider.api_key_set ? '已设置' : '未设置' }}</p>
                  <div class="d-flex ga-2 mt-3">
                    <v-btn size="small" variant="tonal" @click="selectLLMProvider(provider)">编辑</v-btn>
                    <v-btn
                      size="small"
                      color="success"
                      variant="tonal"
                      :disabled="provider.active"
                      @click="activateLLMProvider(provider)"
                    >设为有效</v-btn>
                  </div>
                </article>
              </div>
            </v-form>

            <div v-if="isAdmin && llmWorkspaceTab === 'models'" class="mt-6 llm-governance-panel">
              <div class="d-flex align-center justify-space-between mb-3">
                <div>
                  <div class="font-weight-bold">模型目录</div>
                  <div class="text-caption text-medium-emphasis">
                    模型来自当前有效供应商的 /models；切换供应商后请重新同步模型。
                  </div>
                </div>
                <v-btn
                  color="primary" variant="tonal" prepend-icon="mdi-sync"
                  :loading="llmModelsSyncing" @click="syncLLMModels"
                >同步模型</v-btn>
              </div>
              <v-chip
                v-for="model in llmGovernance.models" :key="model.model_id"
                class="mr-2 mb-2" :color="model.enabled ? 'success' : 'grey'"
                :variant="model.enabled ? 'flat' : 'tonal'"
                :disabled="!model.available"
                @click="toggleLLMModel(model)"
              >
                {{ model.model_id }}{{ model.available ? '' : '（已离线）' }}
              </v-chip>
              <v-alert v-if="!llmGovernance.models.length" type="info" variant="tonal" density="compact" class="mt-3">
                尚未同步模型列表，请先保存大模型 BASE URL/API Key 后点击“同步模型”。
              </v-alert>
              <v-alert v-else-if="!enabledLLMModelIds.length" type="warning" variant="tonal" density="compact" class="mt-3">
                已同步模型，但还没有启用模型。请点击上方模型标签启用至少一个模型，再为 Alpha 候选生成等场景选择模型。
              </v-alert>
              <v-alert
                v-for="warning in llmGovernance.scene_model_warnings"
                :key="warning.scene_code"
                type="warning"
                variant="tonal"
                density="compact"
                class="mt-3"
              >
                {{ warning.message }}
                <span v-if="warning.invalid_model_ids?.length">
                  失效模型：{{ warning.invalid_model_ids.join('、') }}
                </span>
              </v-alert>

            </div>

            <div v-if="isAdmin && llmWorkspaceTab === 'scenes'" class="mt-6 llm-governance-panel">
              <v-row class="mt-2">
                <v-col cols="12">
                  <div class="llm-section-head compact">
                    <div>
                      <div class="font-weight-bold">场景模型</div>
                      <div class="text-caption text-medium-emphasis">
                        每个调用场景可独立选择模型和默认模型；提示词由系统维护，切换供应商后需要重新确认这些模型。
                      </div>
                    </div>
                  </div>
                </v-col>
                <v-col v-for="scene in llmGovernance.scenes" :key="scene.scene_code" cols="12" md="6">
                  <v-card variant="outlined" class="pa-4 h-100">
                    <div class="d-flex align-center justify-space-between">
                      <div>
                        <strong>{{ scene.display_name }}</strong>
                        <div class="text-caption text-medium-emphasis">
                          {{ scene.frequency_class === 'high' ? '高频 · 需要开通' : '低频 · 使用免费额度' }}
                        </div>
                      </div>
                      <v-switch v-model="scene.enabled" color="success" hide-details density="compact" />
                    </div>
                    <v-select
                      v-model="scene.model_ids" :items="enabledLLMModelIds"
                      label="允许使用的模型" multiple chips closable-chips class="mt-3"
                    />
                    <v-select
                      v-model="scene.default_model_id" :items="scene.model_ids"
                      label="默认模型"
                    />
                    <v-switch
                      v-model="scene.allow_user_selection" color="success" hide-details
                      label="允许用户选择模型"
                    />
                    <v-alert type="info" variant="tonal" density="compact" class="mt-3">
                      提示词由系统按场景维护。AI 信号源可由用户根据当前品种、周期、参考行情和分析目标生成专属候选提示词。
                    </v-alert>
                    <v-btn block variant="tonal" color="primary" class="mt-3" @click="saveLLMScene(scene)">
                      保存场景配置
                    </v-btn>
                  </v-card>
                </v-col>
              </v-row>
            </div>

            <v-alert v-if="!isAdmin" type="info" variant="tonal" class="mt-4">
              回测报告和 Alpha 研究无需申请开通，共享每日 30 次免费大模型调用额度；
              今日剩余 {{ llmFreeQuota.remaining }} / {{ llmFreeQuota.limit }} 次。行情 AI 信号仍需申请开通。
            </v-alert>

            <div v-if="!isAdmin" class="text-caption grey--text mt-3">
              <v-icon small>mdi-information</v-icon>
              管理员配置共享的大模型服务，审批通过的用户将使用此配置进行行情分析。
            </div>

            <div v-if="isAdmin && llmWorkspaceTab === 'requests'" class="mt-6">
              <v-divider class="mb-4"></v-divider>
              <div class="d-flex align-center mb-3">
                <div class="text-h6">开通申请待办</div>
                <v-chip small color="warning" class="ml-2">
                  {{ llmAccessRequests.length }}
                </v-chip>
                <v-btn icon small class="ml-2" :loading="llmRequestsLoading" @click="loadLLMAccessRequests">
                  <v-icon small>mdi-refresh</v-icon>
                </v-btn>
              </div>

              <v-table v-if="llmAccessRequests.length">
                <thead>
                  <tr>
                    <th>申请用户</th>
                    <th>申请时间</th>
                    <th class="text-right">操作</th>
                  </tr>
                </thead>
                <tbody>
                  <tr v-for="request in llmAccessRequests" :key="request.id">
                    <td>{{ request.username }}</td>
                    <td>{{ formatTimestamp(request.requested_at) }}</td>
                    <td class="text-right">
                      <v-btn
                        color="success"
                        size="small"
                        class="mr-2"
                        :loading="llmReviewingId === request.id"
                        @click="reviewLLMRequest(request, 'approved')"
                      >
                        通过
                      </v-btn>
                      <v-btn
                        color="error"
                        variant="outlined"
                        size="small"
                        :loading="llmReviewingId === request.id"
                        @click="reviewLLMRequest(request, 'rejected')"
                      >
                        拒绝
                      </v-btn>
                    </td>
                  </tr>
                </tbody>
              </v-table>
              <div v-else class="text-center grey--text py-5">
                暂无待审批的大模型开通申请
              </div>
            </div>

            <div v-if="!isAdmin" class="py-2">
              <div class="d-flex flex-wrap align-center mb-4">
                <v-chip :color="llmAccessColor" size="small">
                  {{ llmAccessLabel }}
                </v-chip>
                <span class="text-body-2 grey--text ml-3">
                  {{ llmAccessDescription }}
                </span>
              </div>

              <v-alert
                v-if="llmAccess.review_note"
                type="info"
                variant="tonal"
                class="mb-4"
              >
                管理员备注：{{ llmAccess.review_note }}
              </v-alert>

              <v-btn
                v-if="['not_requested', 'rejected'].includes(llmAccess.status)"
                color="primary"
                :loading="llmAccessRequesting"
                @click="requestLLMAccess"
              >
                <v-icon start>mdi-send-check</v-icon>
                {{ llmAccess.status === 'rejected' ? '重新申请开通' : '申请开通' }}
              </v-btn>
            </div>
          </v-card-text>
        </v-card>
      </v-col>
    </v-row>

    <v-dialog v-model="newStrategyDialog" max-width="620">
      <v-card>
        <v-card-title class="new-strategy-title"><v-avatar color="primary" variant="tonal" size="42"><v-icon>mdi-chart-timeline-variant-shimmer</v-icon></v-avatar><div><strong>新建策略</strong><span>先创建基础信息，再进入详情添加信号源。</span></div></v-card-title>
        <v-card-text>
          <v-select v-model="newStrategySymbol" :items="strategySymbolOptions" :loading="strategySymbolsLoading" label="交易品种" prepend-inner-icon="mdi-currency-usd" class="mt-4"></v-select>
          <v-text-field v-model="newStrategyName" label="策略名称" placeholder="例如：GOLD M5 趋势策略" prepend-inner-icon="mdi-tag-outline"></v-text-field>
          <v-select v-model="newStrategyPolicyId" :items="positionPolicyOptions" :loading="positionPoliciesLoading" :disabled="positionPoliciesLoading || !!positionPoliciesError" label="持仓管理方案" prepend-inner-icon="mdi-shield-check-outline" no-data-text="暂无可用持仓管理方案"></v-select>
          <v-alert v-if="positionPoliciesError" type="error" variant="tonal" density="compact" class="mb-2">{{ positionPoliciesError }} <v-btn size="small" variant="text" @click="loadPositionPolicies">重试</v-btn></v-alert>
          <v-alert v-else-if="!positionPoliciesLoading && !positionPolicyOptions.length" type="warning" variant="tonal" density="compact" class="mb-2">暂无启用的持仓管理方案，请先在持仓管理页面创建方案。</v-alert>
          <v-alert type="info" variant="tonal" density="compact">新策略默认为私有草稿，不会立即参与交易。持仓管理方案只需在这里选择一次，创建后在风控选项卡中只展示当前绑定。</v-alert>
        </v-card-text>
        <v-card-actions>
          <v-spacer></v-spacer>
          <v-btn variant="text" @click="newStrategyDialog = false">取消</v-btn>
          <v-btn color="primary" :disabled="!newStrategySymbol || !newStrategyPolicyId" :loading="strategySaving === 'new'" @click="addStrategy"><v-icon start>mdi-plus</v-icon>创建并配置</v-btn>
        </v-card-actions>
      </v-card>
    </v-dialog>

    <v-dialog v-model="signalSourceDialog" max-width="760">
      <v-card>
        <v-card-title>{{ signalSourceEditMode === 'edit' ? '编辑信号源' : '添加信号源' }}</v-card-title>
        <v-card-text>
          <v-progress-linear v-if="signalSourceDialogLoading" indeterminate color="primary" class="mb-4" />
          <v-alert v-if="signalSourceDialogLoading" type="info" variant="tonal" density="compact" class="mb-4">
            正在加载可复用的共享信号源、模型和 Alpha 选项，请稍候…
          </v-alert>
          <v-alert
            v-if="!newSignalSource.source || !newSignalSource.params"
            type="error"
            variant="tonal"
            density="compact"
            class="mb-4"
          >
            当前信号源配置结构异常，请关闭弹窗后刷新页面重试。
          </v-alert>
          <v-alert v-else type="success" variant="tonal" density="compact" class="mb-4">
            当前配置：{{ sourceMetaFor(newSignalSource.source).label }} · {{ newSignalSource.source === 'key_level' ? '全周期' : newSignalSource.period || '未选择周期' }}
          </v-alert>
          <v-alert type="info" variant="tonal" density="compact" class="mb-4">
            关键点位信号与 AI 入场、转折点、均线交叉、已验证 Alpha 互斥：策略中一旦存在关键点位，就不能再添加其他信号；存在其他信号时，也不能添加关键点位。
          </v-alert>
          <v-alert v-if="aiSignalOptionsLoading" type="info" variant="tonal" density="compact" class="mb-4">
            正在加载 AI 模型与共享 Alpha 选项，弹窗可先配置基础参数。
          </v-alert>
          <div class="signal-source-type-grid mb-3">
            <button
              v-for="option in signalSourceTypeOptions"
              :key="option.value"
              type="button"
              class="signal-source-type-card"
              :class="{
                'signal-source-type-card--active': newSignalSource.source === option.value,
                'signal-source-type-card--disabled': option.disabled || signalSourceEditMode === 'edit'
              }"
              :disabled="option.disabled || signalSourceEditMode === 'edit'"
              @click="onNewSignalSourceTypeChange(option.value)"
            >
              <v-icon :color="sourceMetaFor(option.value).color">
                {{ sourceMetaFor(option.value).icon }}
              </v-icon>
              <span>{{ option.title }}</span>
              <small v-if="option.disabled">{{ signalSourceDisabledReason(option.value) }}</small>
            </button>
          </div>
          <v-row dense>
            <v-col v-if="!['key_level', 'alpha_factor'].includes(newSignalSource.source)" cols="12" sm="6">
              <v-select
                v-model="newSignalSource.period"
                :items="availablePeriodsForNewSource"
                label="分析周期"
                :disabled="newSignalSource.source === 'ai_entry' && newSignalSource.params.analysis_mode === 'shared_reference'"
                :no-data-text="'该信号源的所有周期都已添加'"
              ></v-select>
            </v-col>
            <v-col cols="12" sm="6">
              <v-text-field
                v-model.number="newSignalSource.weight"
                label="信号权重"
                type="number"
                min="1"
                max="100"
              ></v-text-field>
            </v-col>
          </v-row>
          <v-alert v-if="newSignalSource.source === 'key_level'" type="info" variant="tonal" density="compact">
            关键点位信号源为全周期共用配置，同一策略只能添加一条。
          </v-alert>

          <template v-if="newSignalSource.source === 'key_level'">
            <v-row dense class="mt-3">
              <v-col cols="12" sm="6">
                <v-select v-model="newSignalSource.params.level_mode" :items="keyLevelModeOptions" label="关键点位来源"></v-select>
              </v-col>
              <v-col v-if="newSignalSource.params.level_mode === 'levels'" cols="12" sm="6">
                <v-text-field v-model="newSignalSource.params.levels_text" label="关键点位数字（逗号分隔）" placeholder="4000, 4050, 4100"></v-text-field>
              </v-col>
              <v-col v-if="newSignalSource.params.level_mode === 'expression'" cols="12" sm="6">
                <v-text-field v-model="newSignalSource.params.expression" label="计算表达式" placeholder="round(price / 100) * 100"></v-text-field>
              </v-col>
              <v-col cols="12" class="text-caption text-medium-emphasis">表达式变量为 price，可使用 floor、ceil、round、abs、min、max。</v-col>
              <v-col cols="12" sm="6">
                <v-text-field v-model.number="newSignalSource.params.order_distance" label="下单距离比例" type="number" step="0.0001" min="0"></v-text-field>
              </v-col>
              <v-col cols="12" sm="6">
                <v-text-field v-model.number="newSignalSource.params.cooldown_seconds" label="信号冷却（秒）" type="number" min="0"></v-text-field>
              </v-col>
              <v-col cols="12">
                <div class="key-level-trigger-grid">
                  <v-switch v-model="newSignalSource.params.upward_approach_sell" label="向上接近下卖单" color="success" density="compact" hide-details></v-switch>
                  <v-switch v-model="newSignalSource.params.downward_approach_buy" label="向下接近下买单" color="success" density="compact" hide-details></v-switch>
                  <v-switch v-model="newSignalSource.params.upward_breakout_buy" label="向上突破下买单" color="success" density="compact" hide-details></v-switch>
                  <v-switch v-model="newSignalSource.params.downward_breakout_sell" label="向下突破下卖单" color="success" density="compact" hide-details></v-switch>
                </div>
                <div class="text-caption text-medium-emphasis mt-2">
                  下单距离沿用比例语义，例如 0.0008 表示价格距离关键位万分之八内可触发；止盈止损统一由持仓管理方案生成。
                </div>
              </v-col>
            </v-row>
          </template>

          <template v-else-if="newSignalSource.source === 'pivot'">
            <v-row dense class="mt-3">
              <v-col cols="12"><v-alert type="info" variant="tonal" density="compact">系统先用左右已收盘 K 线确认局部高低点，再按价格区域合并重复确认。接近低点做多、接近高点做空；突破高点做多、跌破低点做空。百分比均按“价格差 ÷ 转折点价格”计算，最终止盈止损仍会由持仓管理方案校验。</v-alert></v-col>
              <v-col cols="12" sm="6"><v-select v-model="newSignalSource.params.signal_type" :items="pivotSignalTypeOptions" label="触发方式" hint="接近反转、突破跟随，或同时启用两种机会" persistent-hint></v-select></v-col>
              <v-col cols="12" sm="6"><v-text-field v-model.number="newSignalSource.params.confirmation_strength" label="左右确认K线" type="number" min="1" max="20" suffix="根" hint="左右各需要多少根已收盘K线；越大越稳定，但确认越慢" persistent-hint></v-text-field></v-col>
              <v-col cols="12" sm="6"><v-text-field v-model.number="newSignalSource.params.proximity_threshold_percent" label="接近转折点范围" type="number" min="0" max="5" step="0.01" suffix="%" hint="当前价进入该距离才触发接近反转；越大越容易触发" persistent-hint></v-text-field></v-col>
              <v-col cols="12" sm="6"><v-text-field v-model.number="newSignalSource.params.merge_distance_percent" label="重复确认价格范围" type="number" min="0" max="5" step="0.01" suffix="%" hint="同方向转折点价格差在此范围内视为同一区域并累计确认次数" persistent-hint></v-text-field></v-col>
              <v-col cols="12" sm="6"><v-text-field v-model.number="newSignalSource.params.stop_buffer_percent" label="结构止损缓冲" type="number" min="0" max="5" step="0.01" suffix="%" hint="建议止损放在转折点外侧的距离；持仓管理方案会再次约束" persistent-hint></v-text-field></v-col>
              <v-col cols="12" sm="6"><v-text-field v-model.number="newSignalSource.params.risk_reward_ratio" label="建议盈亏比" type="number" min="1" max="10" step="0.1" hint="例如 2 表示建议止盈距离约为止损距离的 2 倍" persistent-hint></v-text-field></v-col>
              <v-col cols="12" sm="6"><v-text-field v-model.number="newSignalSource.params.candidate_limit" label="最近候选转折点" type="number" min="1" max="100" suffix="个" hint="仅从最新的候选中选择" persistent-hint></v-text-field></v-col>
              <v-col cols="12" sm="6"><v-text-field v-model.number="newSignalSource.params.min_confirmation_count" label="最少重复确认" type="number" min="1" max="20" suffix="次" hint="谨慎场景建议设为 2" persistent-hint></v-text-field></v-col>
              <v-col cols="12" sm="6"><v-text-field v-model.number="newSignalSource.params.max_age_bars" label="最长有效期" type="number" min="1" max="5000" suffix="根K线" hint="确认后超过该时间的转折点不再参与交易" persistent-hint></v-text-field></v-col>
              <v-col cols="12" sm="6"><v-text-field v-model.number="newSignalSource.params.recency_half_life_bars" label="评分半衰期" type="number" min="1" max="5000" suffix="根K线" hint="每经过该数量K线，时间评分减半" persistent-hint></v-text-field></v-col>
              <v-col cols="12" sm="6"><v-text-field v-model.number="newSignalSource.params.min_pivot_score" label="最低转折点评分" type="number" min="0" max="100" suffix="分" hint="0 不过滤；谨慎场景建议 80 分" persistent-hint></v-text-field></v-col>
              <v-col cols="12" sm="6"><v-text-field v-model.number="newSignalSource.params.cooldown_seconds" label="同一转折点冷却" type="number" min="0" max="86400" suffix="秒" hint="一次触发后，在该时长内不重复发送同一转折点信号" persistent-hint></v-text-field></v-col>
            </v-row>
          </template>

          <template v-else-if="newSignalSource.source === 'moving_average'">
            <v-row dense class="mt-3">
              <v-col cols="12" sm="6"><v-text-field v-model.number="newSignalSource.params.fast_period" label="快线周期" type="number" min="1" max="500"></v-text-field></v-col>
              <v-col cols="12" sm="6"><v-text-field v-model.number="newSignalSource.params.slow_period" label="慢线周期" type="number" min="2" max="1000"></v-text-field></v-col>
              <v-col cols="12" sm="6"><v-select v-model="newSignalSource.params.ma_type" :items="movingAverageTypeOptions" label="均线类型"></v-select></v-col>
              <v-col cols="12" sm="6"><v-text-field v-model.number="newSignalSource.params.cooldown_seconds" label="信号冷却（秒）" type="number" min="0"></v-text-field></v-col>
              <v-col cols="12" sm="6"><v-text-field v-model.number="newSignalSource.params.min_confidence" label="最低置信度" type="number" min="0" max="100" suffix="%"></v-text-field></v-col>
              <v-col cols="12" class="text-caption text-medium-emphasis">交叉先生成潜在信号；方向一致且置信度达标后才触发入场，触发后失效，反向交叉会刷新方向。</v-col>
            </v-row>
          </template>

          <template v-else-if="newSignalSource.source === 'structure_plan'">
            <v-row dense class="mt-3">
              <v-col cols="12"><v-alert type="info" variant="tonal" density="compact">每根已收盘 K 线按“背景方向 + 当前形态 + 所处位置 + 确认证据”生成或更新一个最相关计划，Tick 只判断触及与回收。覆盖趋势结构位回踩、箱体边界、突破回踩、假突破和流动性扫单；没有可靠机会时会显示具体拦截原因。</v-alert></v-col>
              <v-col cols="12" sm="6"><v-select v-model="newSignalSource.params.allowed_directions" :items="[{title:'买入和卖出',value:['buy','sell']},{title:'仅买入',value:['buy']},{title:'仅卖出',value:['sell']}]" label="允许方向" multiple chips></v-select></v-col>
              <v-col cols="12"><v-alert type="info" variant="tonal" density="compact">结构计划的识别、入场、止损、止盈和有效期已统一移至“系统结构识别 → 结构交易计划参数”，并支持按品种和周期覆盖。策略只筛选允许方向，并严格采用公共计划的有效期。</v-alert></v-col>
            </v-row>
          </template>

          <template v-else-if="newSignalSource.source === 'alpha_factor'">
            <v-row dense class="mt-3">
              <v-col cols="12">
                <v-select
                  v-model="newSignalSource.params.alpha_id"
                  :items="alphaLibraryOptions"
                  label="已验证 Alpha"
                  no-data-text="暂无通过准入检查的 Alpha，请先在 Alpha 研究中发布"
                  @update:model-value="onAlphaSelected"
                ></v-select>
              </v-col>
              <v-col cols="12" sm="6"><v-text-field :model-value="newSignalSource.params.alpha_name || '--'" label="Alpha 版本" readonly></v-text-field></v-col>
              <v-col cols="12" sm="6"><v-text-field :model-value="newSignalSource.period" label="执行周期" readonly></v-text-field></v-col>
              <v-col cols="12" sm="6"><v-text-field v-model.number="newSignalSource.params.min_confidence" label="最低置信度" type="number" min="0" max="100" suffix="%"></v-text-field></v-col>
              <v-col cols="12" sm="6"><v-text-field v-model.number="newSignalSource.params.cooldown_seconds" label="信号冷却（秒）" type="number" min="0"></v-text-field></v-col>
              <v-col cols="12"><v-alert type="info" variant="tonal" density="compact">策略会固定当前 Alpha 版本快照，回测和实盘使用同一个执行器；因子库后续更新不会悄悄改变本策略。</v-alert></v-col>
            </v-row>
          </template>

          <template v-else>
            <v-row dense class="mt-3">
              <v-col cols="12">
                <v-select
                  v-model="newSignalSource.params.ai_signal_source_id"
                  :items="managedAISignalSourceOptions"
                  label="选择 AI 信号源"
                  :loading="aiSignalOptionsLoading"
                  no-data-text="暂无可用于当前品种的 AI 信号源，请先创建或等待其他用户共享"
                  @update:model-value="onManagedAISignalSourceSelected"
                ></v-select>
              </v-col>
              <v-col cols="12" class="d-flex align-center ga-2">
                <v-alert type="info" variant="tonal" density="compact" class="flex-grow-1">选择自己的或适配当前品种的共享 AI 信号源。模型、提示词与运行频率均由信号源统一管理，策略只设置入场门槛。</v-alert>
                <v-btn to="/ai-signal-sources" variant="outlined" color="primary">管理信号源</v-btn>
              </v-col>
              <template v-if="newSignalSource.params.ai_signal_source_id">
                <v-col cols="12" sm="6"><v-text-field v-model.number="newSignalSource.params.min_confidence" label="策略最低置信度" type="number" min="0" max="100" suffix="%"></v-text-field></v-col>
                <v-col cols="12" sm="6"><v-text-field v-model.number="newSignalSource.params.entry_threshold_percent" label="策略入场价接近阈值" type="number" step="0.01" min="0" max="10" suffix="%"></v-text-field></v-col>
              </template>
            </v-row>
            <template v-if="false">
            <v-btn-toggle
              v-model="newSignalSource.params.analysis_mode"
              mandatory
              color="success"
              variant="outlined"
              divided
              class="mt-3"
            >
              <v-btn value="self_analysis">自主 AI 分析</v-btn>
              <v-btn value="shared_reference">引用共享 AI 数据</v-btn>
            </v-btn-toggle>
            <v-alert
              v-if="newSignalSource.params.analysis_mode === 'self_analysis' && !aiSignalOptions.accessGranted"
              type="warning"
              variant="tonal"
              density="compact"
              class="mt-3"
            >
              自主 AI 分析仅对已开通大模型分析的付费用户开放；你也可以切换为“引用共享 AI 数据”，无需开通即可参与策略决策。
            </v-alert>
            <v-alert v-else-if="newSignalSource.params.analysis_mode === 'shared_reference'" type="info" variant="tonal" density="compact" class="mt-3">
              系统不会为此信号源调用大模型，将使用共享者的最新分析方向和置信度，并以你当前账户的实时价格判断是否进入建议入场区间。
            </v-alert>
            <v-row dense class="mt-3">
              <v-col v-if="newSignalSource.params.analysis_mode === 'self_analysis'" cols="12" sm="6">
                <v-select
                  v-model="newSignalSource.params.model"
                  :items="aiSignalOptions.models"
                  label="运行模型"
                  :loading="aiSignalOptionsLoading"
                ></v-select>
              </v-col>
              <v-col v-if="newSignalSource.params.analysis_mode === 'self_analysis'" cols="12" sm="6">
                <v-select v-model.number="newSignalSource.params.analysis_interval_minutes" :items="aiIntervalOptionsFor(newSignalSource)" label="调用间隔" suffix="分钟" :hint="`${newSignalSource.period} 周期不能低于 ${periodMinutes(newSignalSource.period)} 分钟`" persistent-hint></v-select>
              </v-col>
              <v-col v-if="newSignalSource.params.analysis_mode === 'self_analysis'" cols="12" sm="6"><v-text-field v-model.number="newSignalSource.params.kline_count" label="分析K线数量" type="number" min="10" max="500"></v-text-field></v-col>
              <v-col cols="12" sm="6"><v-text-field v-model.number="newSignalSource.params.min_confidence" label="最低置信度" type="number" min="0" max="100" suffix="%"></v-text-field></v-col>
              <v-col cols="12" sm="6"><v-text-field v-model.number="newSignalSource.params.entry_threshold_percent" label="入场价接近阈值" type="number" step="0.01" min="0" max="10" suffix="%" hint="当前价与建议入场价的最大允许偏差，默认 0.08%" persistent-hint></v-text-field></v-col>
              <v-col v-if="newSignalSource.params.analysis_mode === 'self_analysis'" cols="12">
                <v-textarea
                  v-model="newSignalSource.params.system_prompt"
                  label="系统提示词"
                  rows="3"
                  auto-grow
                  hint="定义模型角色与输出边界；留空时使用平台默认提示词"
                  persistent-hint
                ></v-textarea>
              </v-col>
              <v-col v-if="newSignalSource.params.analysis_mode === 'self_analysis'" cols="12">
                <v-textarea
                  v-model="newSignalSource.params.analysis_prompt_template"
                  label="分析提示词模板"
                  rows="8"
                  auto-grow
                  hint="必须保留 {{strategy_context}} 和 {{market_data}} 两个占位符"
                  persistent-hint
                ></v-textarea>
              </v-col>
              <v-col v-if="newSignalSource.params.analysis_mode === 'self_analysis'" cols="12">
                <v-select
                  v-model="newSignalSource.params.reference_runtime_ids"
                  :items="sharedAIRuntimeOptions"
                  label="参考其他用户共享的 AI 运行数据"
                  multiple
                  chips
                  closable-chips
                  clearable
                  :loading="aiSignalOptionsLoading"
                  no-data-text="当前品种暂无共享 AI 运行数据"
                ></v-select>
              </v-col>
              <v-col v-else cols="12">
                <v-select
                  v-model="newSignalSource.params.shared_runtime_id"
                  :items="sharedAIRuntimeOptions"
                  label="选择共享 AI 运行数据"
                  chips
                  clearable
                  :loading="aiSignalOptionsLoading"
                  no-data-text="平台暂无可引用的共享 AI 运行数据"
                  @update:model-value="onSharedRuntimeSelected"
                ></v-select>
              </v-col>
              <v-col v-if="selectedSharedAIRuntimeData.length" cols="12">
                <div class="shared-ai-runtime-list">
                  <article v-for="item in selectedSharedAIRuntimeData" :key="item.share_id">
                    <div class="d-flex align-center justify-space-between ga-2">
                      <strong>{{ item.symbol }} · {{ item.period }} · {{ item.model }}</strong>
                      <div class="d-flex ga-1">
                        <v-chip size="x-small" color="success" variant="tonal">匹配 {{ formatSimilarity(item.symbol_similarity) }}</v-chip>
                        <v-chip size="x-small" color="info" variant="tonal">{{ lifecycleLabel(item.strategy_lifecycle) }}</v-chip>
                      </div>
                    </div>
                    <p>{{ item.strategy_name }} · 共享者 {{ item.owner_username }}</p>
                    <p>参数：{{ formatSharedSignalParams(item.signal_params) }}</p>
                    <p>最近运行：{{ formatTimestamp(item.last_run_at) }}</p>
                    <p class="text-caption text-medium-emphasis">提示词与私有参数受共享者保护，不对外展示。</p>
                    <details>
                      <summary>查看最近分析结果</summary>
                      <div class="shared-prompt-preview">{{ formatSharedRuntimeResult(item.result) }}</div>
                    </details>
                  </article>
                </div>
              </v-col>
            </v-row>
            </template>
          </template>
        </v-card-text>
        <v-card-actions>
          <v-spacer></v-spacer>
          <v-btn variant="text" @click="signalSourceDialog = false">取消</v-btn>
          <v-btn color="primary" :disabled="signalSourceDialogLoading || !canSaveSignalSource" @click="saveSignalSourceFromDialog">
            {{ signalSourceEditMode === 'edit' ? '保存' : '添加' }}
          </v-btn>
        </v-card-actions>
      </v-card>
    </v-dialog>

    <!-- 错误提示 -->
    <v-snackbar v-model="showError" color="error" timeout="5000" location="top">
      {{ errorMessage }}
    </v-snackbar>

    <!-- 成功提示 -->
    <v-snackbar v-model="showSuccess" color="success" timeout="3000" location="top">
      {{ successMessage }}
    </v-snackbar>
  </v-container>
</template>

<script>
import { ref, reactive, computed, onMounted, watch } from 'vue'
import { marketAPI } from '@/api/market'
import { accountAPI, authAPI } from '@/api/trading'
import { authState, saveAdminSessionForView, setAuthSession } from '@/auth'

export default {
  name: 'Settings',
  props: {
    mode: {
      type: String,
      default: 'system'
    }
  },
  setup(props) {
    const isStrategyPage = computed(() => props.mode === 'strategy')
    const pageTitle = computed(() => isStrategyPage.value ? '策略管理' : '用户配置')
    const settingsTab = ref('account')
    const llmWorkspaceTab = ref('providers')
    const structureEngineConfig = ref({ pivot_legs: 3, medium_pivot_legs: 8, large_pivot_legs: 25, min_reversal_atr: 0.5, break_buffer_atr: 0.1, break_confirm_bars: 2, retest_bars: 2, displacement_atr: 0.8, range_touch_tolerance: 0.003, range_touch_atr: 0.45, range_min_touches: 2, range_min_inside_ratio: 0.65, range_min_bars: 24, range_max_atr: 8, min_segment_bars: 12, trendline_touch_atr: 0.5, trendline_min_touches: 2, trendline_min_bars: 18, trend_min_direction_ratio: 0.62, trend_relaxed_direction_ratio: 0.55, trend_min_efficiency: 0.30, trend_min_net_change_atr: 1.5, entry_zone_atr: 0.35, stop_buffer_atr: 0.25, min_real_risk_reward: 1.2, trend_min_real_risk_reward: 0.5, breakout_target_atr: 3, breakout_retest_valid_bars: 6, enable_triangle_prebreakout: true, require_location_reclaim: true })
    const structureEngineSaving = ref(false)
    const structureProfiles = ref([])
    const structureProfileDraft = ref({ symbol: '', period: 'M5' })

    // 交易配置
    const tradeConfig = ref({
      enabled: true,
      default_volume: 0.01,
      default_sl_offset: 0.05,
      symbol_config: {}
    })

    // 添加新品种
    const newSymbol = ref('')
    const newVolume = ref(0.01)
    const newSlOffset = ref(0.05)
    const newKeyLevels = ref('')
    const newKeyLevelThreshold = ref(0.0008)
    const symbols = ref([])

    // 提示
    const showError = ref(false)
    const errorMessage = ref('')
    const showSuccess = ref(false)
    const successMessage = ref('')

    // 大模型配置
    const llmConfig = ref({
      provider_id: '',
      provider_name: '默认供应商',
      api_key: '',
      api_key_set: false,
      api_base: 'https://api.openai.com/v1',
      model: 'gpt-4o-mini',
      active: true,
      system_prompt: '',
      analysis_prompt_template: '',
      prompt_version: 1,
      enabled: false
    })
    const showApiKey = ref(false)
    const llmSaving = ref(false)
    const llmModelsSyncing = ref(false)
    const llmGovernance = ref({
      providers: [],
      active_provider: null,
      models: [],
      scenes: [],
      scene_model_warnings: [],
      free_daily_limit: 30
    })
    const enabledLLMModelIds = computed(() => llmGovernance.value.models
      .filter(model => model.enabled && model.available)
      .map(model => model.model_id))
    const llmAccess = ref({
      status: 'not_requested',
      access_granted: false,
      service_configured: false,
      feature_enabled: false,
      review_note: ''
    })
    const llmAccessRequesting = ref(false)
    const llmFreeQuota = ref({ limit: 30, used: 0, remaining: 30 })
    const llmAccessRequests = ref([])
    const llmRequestsLoading = ref(false)
    const llmReviewingId = ref(null)

    // 注册邮件服务配置
    const emailConfig = ref({
      smtp_host: 'smtp.qiye.aliyun.com',
      smtp_port: 465,
      use_ssl: true,
      sender_email: '',
      sender_name: 'AI Trader',
      password: '',
      password_set: false,
      enabled: false
    })
    const showEmailPassword = ref(false)
    const emailSaving = ref(false)
    const emailTesting = ref(false)

    const instrumentMappings = ref([])
    const instrumentObservations = ref([])
    const instrumentPriceObservations = ref([])
    const instrumentMappingSaving = ref(false)
    const instrumentMappingForm = ref({
      broker_name: '', native_symbol: '', mapping_group: '', display_name: '', enabled: true
    })

    // 管理员用户配额白名单
    const quotaUsers = ref([])
    const quotaLoading = ref(false)
    const quotaError = ref('')
    const quotaSaving = ref(null)
    const adminStrategies = ref([])
    const adminStrategiesLoading = ref(false)
    const adminStrategySaving = ref(null)
    const adminStrategySearch = ref('')
    const adminStrategyLifecycleFilter = ref('all')
    const adminDeploymentsDialog = ref(false)
    const adminDeploymentsLoading = ref(null)
    const adminDeploymentsDetail = ref(null)
    const adminDeploymentsError = ref('')
    const invitations = ref([])
    const invitationSaving = ref(false)
    const latestInviteLink = ref('')
    const invitationForm = ref({ label: '', max_uses: 1, expires_days: 7 })
    const myQuota = ref({
      usage: { datasets: 0, strategies: 0, signal_sources: 0 },
      limits: { datasets: 10, strategies: 5, signal_sources: 10 }
    })

    // 账户与安全
    const currentUser = computed(() => authState.user || {
      username: '未登录',
      role: 'user'
    })
    const isAdmin = computed(() => currentUser.value.role === 'admin')
    const roleLabel = computed(() =>
      currentUser.value.role === 'admin' ? '管理员' : '普通用户'
    )
    const llmAccessLabel = computed(() => ({
      not_requested: '未开通',
      pending: '审批中',
      approved: llmAccess.value.feature_enabled ? '已开通' : '等待服务配置',
      rejected: '申请未通过'
    }[llmAccess.value.status] || '未开通'))
    const llmAccessColor = computed(() => ({
      not_requested: 'grey',
      pending: 'warning',
      approved: llmAccess.value.feature_enabled ? 'success' : 'info',
      rejected: 'error'
    }[llmAccess.value.status] || 'grey'))
    const llmAccessDescription = computed(() => {
      if (llmAccess.value.status === 'pending') return '申请已提交，请等待管理员审批。'
      if (llmAccess.value.status === 'rejected') return '申请未通过，您可以重新提交申请。'
      if (llmAccess.value.status === 'approved' && !llmAccess.value.service_configured) {
        return '权限已开通，管理员配置大模型服务后即可使用。'
      }
      if (llmAccess.value.status === 'approved') return '您可以在信号推荐页面使用大模型行情分析。'
      return '开通后可使用 AI 多周期行情分析和交易信号辅助功能。'
    })
    const loadEmailConfig = async () => {
      if (!isAdmin.value) return
      try {
        const data = await authAPI.getEmailConfig()
        emailConfig.value = { ...emailConfig.value, ...(data.config || {}), password: '' }
      } catch (err) {
        errorMessage.value = err.response?.data?.detail || '加载邮件服务配置失败'
        showError.value = true
      }
    }

    const saveEmailConfig = async () => {
      emailSaving.value = true
      try {
        const data = await authAPI.saveEmailConfig(emailConfig.value)
        emailConfig.value = { ...emailConfig.value, ...(data.config || {}), password: '' }
        successMessage.value = data.message || '邮件服务配置已保存'
        showSuccess.value = true
        return true
      } catch (err) {
        errorMessage.value = err.response?.data?.detail || '保存邮件服务配置失败'
        showError.value = true
        return false
      } finally {
        emailSaving.value = false
      }
    }

    const testEmailConfig = async () => {
      emailTesting.value = true
      try {
        if (!await saveEmailConfig()) return
        const data = await authAPI.testEmailConfig(emailConfig.value.sender_email)
        successMessage.value = data.message || '测试邮件已发送'
        showSuccess.value = true
      } catch (err) {
        errorMessage.value = err.response?.data?.detail || '测试邮件发送失败'
        showError.value = true
      } finally {
        emailTesting.value = false
      }
    }

    const loadUserQuotas = async () => {
      if (!isAdmin.value) return
      quotaLoading.value = true
      quotaError.value = ''
      try {
        const data = await authAPI.getUserQuotas()
        quotaUsers.value = (data.users || []).map(item => ({
          ...item,
          membershipDraft: {
            membership_level: item.membership_level || 'silver',
            live_trading_enabled: Boolean(item.live_trading_enabled)
          },
          quotaDraft: {
            max_datasets: item.overrides.datasets ?? '',
            max_strategies: item.overrides.strategies ?? '',
            max_signal_sources: item.overrides.signal_sources ?? ''
          }
        }))
      } catch (err) {
        quotaError.value = err.response?.data?.detail || '加载用户配额失败，请稍后重试'
      } finally {
        quotaLoading.value = false
      }
    }

    const loadInvitations = async () => {
      if (!isAdmin.value) return
      const data = await authAPI.getInvitations()
      invitations.value = data.invitations || []
    }

    const createInvitation = async () => {
      invitationSaving.value = true
      try {
        const data = await authAPI.createInvitation(invitationForm.value)
        const code = data.invitation?.code
        latestInviteLink.value = code
          ? `${window.location.origin}/register?invite=${encodeURIComponent(code)}`
          : ''
        invitationForm.value = { label: '', max_uses: 1, expires_days: 7 }
        await loadInvitations()
        successMessage.value = '邀请链接已生成，请及时复制'
        showSuccess.value = true
      } catch (err) {
        errorMessage.value = err.response?.data?.detail || '生成邀请链接失败'
        showError.value = true
      } finally {
        invitationSaving.value = false
      }
    }

    const copyLatestInvite = async () => {
      try {
        await navigator.clipboard.writeText(latestInviteLink.value)
        successMessage.value = '邀请链接已复制'
        showSuccess.value = true
      } catch {
        errorMessage.value = '浏览器未允许复制，请手动选择链接'
        showError.value = true
      }
    }

    const setInvitationActive = async (item, active) => {
      invitationSaving.value = item.invitation_id
      try {
        await authAPI.setInvitationActive(item.invitation_id, active)
        await loadInvitations()
      } catch (err) {
        errorMessage.value = err.response?.data?.detail || '更新邀请码状态失败'
        showError.value = true
      } finally {
        invitationSaving.value = false
      }
    }

    const formatInvitationTime = (value) => value
      ? new Date(Number(value) * 1000).toLocaleString('zh-CN')
      : '长期有效'

    const loadMyQuota = async () => {
      try {
        const data = await authAPI.getMyQuota()
        myQuota.value = data.quota || myQuota.value
      } catch (err) {
        console.error('加载个人资源额度失败:', err)
      }
    }

    const loadAdminWorkspace = async () => {
      quotaSaving.value = 'loading'
      try {
        await Promise.all([
          loadInvitations(), loadEmailConfig(),
          loadLLMConfig(), loadLLMAccessRequests(), loadAdminStrategies(),
          loadInstrumentMappings(), loadInstrumentObservations(), loadInstrumentPriceObservations(),
          loadTradeConfig(), loadSymbols()
        ])
        const engineData = await marketAPI.getMarketStructureConfig()
        structureEngineConfig.value = { ...structureEngineConfig.value, ...(engineData.config || {}) }
        structureProfiles.value = Array.isArray(engineData.profiles) ? engineData.profiles : []
        if (settingsTab.value === 'quota') await loadUserQuotas()
        successMessage.value = '管理员运营数据已刷新'
        showSuccess.value = true
      } finally {
        quotaSaving.value = null
      }
    }

    const saveStructureEngineConfig = async () => {
      structureEngineSaving.value = true
      try {
        const data = await marketAPI.saveMarketStructureConfig({ ...structureEngineConfig.value, profiles: structureProfiles.value })
        structureEngineConfig.value = { ...structureEngineConfig.value, ...(data.config || {}) }
        successMessage.value = '系统结构识别参数已保存'
        showSuccess.value = true
      } catch (err) {
        errorMessage.value = err.response?.data?.detail || '保存结构识别参数失败'
        showError.value = true
      } finally { structureEngineSaving.value = false }
    }
    const saveStructureProfile = async () => {
      if (!structureProfileDraft.value.symbol) return
      const item = { symbol: structureProfileDraft.value.symbol, period: structureProfileDraft.value.period, ...structureEngineConfig.value }
      const index = structureProfiles.value.findIndex(x => x.symbol === item.symbol && x.period === item.period)
      if (index >= 0) structureProfiles.value.splice(index, 1, item); else structureProfiles.value.push(item)
      await saveStructureEngineConfig()
    }
    const removeStructureProfile = async item => { structureProfiles.value = structureProfiles.value.filter(x => !(x.symbol === item.symbol && x.period === item.period)); await saveStructureEngineConfig() }
    const loadInstrumentMappings = async () => {
      if (!isAdmin.value) return
      try {
        const data = await marketAPI.getInstrumentMappings()
        instrumentMappings.value = data.mappings || []
      } catch (err) {
        errorMessage.value = err.response?.data?.detail || '加载品种映射失败'
        showError.value = true
      }
    }

    const loadInstrumentObservations = async () => {
      if (!isAdmin.value) return
      try {
        const data = await marketAPI.getInstrumentObservations()
        instrumentObservations.value = data.items || []
      } catch (err) {
        errorMessage.value = err.response?.data?.detail || '加载已上报品种失败'
        showError.value = true
      }
    }

    const loadInstrumentPriceObservations = async () => {
      if (!isAdmin.value) return
      try {
        const data = await marketAPI.getInstrumentPriceObservations()
        instrumentPriceObservations.value = data.items || []
      } catch (err) {
        errorMessage.value = err.response?.data?.detail || '加载最近报价失败'
        showError.value = true
      }
    }

    const formatInstrumentPrice = (value) => {
      if (value === null || value === undefined || value === '') return '--'
      return Number(value).toFixed(5)
    }

    const useInstrumentPriceObservation = (item) => {
      useInstrumentObservation({ broker_name: item.broker_name, symbol: item.symbol })
    }

    const useInstrumentObservation = (item) => {
      instrumentMappingForm.value = {
        broker_name: item.broker_name || item.broker_server || '',
        native_symbol: item.symbol || '', mapping_group: '', display_name: '', enabled: true
      }
      successMessage.value = '已带入上报的交易商与品种，请填写关联组后保存'
      showSuccess.value = true
    }

    const saveInstrumentMapping = async () => {
      instrumentMappingSaving.value = true
      try {
        const data = await marketAPI.saveInstrumentMapping(instrumentMappingForm.value)
        instrumentMappingForm.value = {
          broker_name: '', native_symbol: '', mapping_group: '', display_name: '', enabled: true
        }
        successMessage.value = `已保存 ${data.mapping.native_symbol} 的关联映射`
        showSuccess.value = true
        await loadInstrumentMappings()
      } catch (err) {
        errorMessage.value = err.response?.data?.detail || '保存品种映射失败'
        showError.value = true
      } finally {
        instrumentMappingSaving.value = false
      }
    }

    const deleteInstrumentMapping = async (item) => {
      if (!confirm(`确定删除 ${item.effective_broker_name} / ${item.native_symbol} 的关联映射吗？`)) return
      try {
        await marketAPI.deleteInstrumentMapping(item.mapping_id)
        successMessage.value = '品种映射已删除'
        showSuccess.value = true
        await loadInstrumentMappings()
      } catch (err) {
        errorMessage.value = err.response?.data?.detail || '删除品种映射失败'
        showError.value = true
      }
    }

    const saveUserQuota = async (item) => {
      quotaSaving.value = item.user_id
      try {
        const payload = Object.fromEntries(Object.entries(item.quotaDraft).map(([key, value]) => [
          key, value === '' || value === null ? null : Number(value)
        ]))
        if (!liveEligibleLevel(item.membershipDraft.membership_level)) {
          item.membershipDraft.live_trading_enabled = false
        }
        await Promise.all([
          authAPI.saveUserMembership(item.user_id, item.membershipDraft),
          authAPI.saveUserQuota(item.user_id, payload)
        ])
        successMessage.value = `已更新 ${item.username} 的会员等级与配额`
        showSuccess.value = true
        await loadUserQuotas()
      } catch (err) {
        errorMessage.value = err.response?.data?.detail || '保存用户配额失败'
        showError.value = true
      } finally {
        quotaSaving.value = null
      }
    }

    const viewAsUser = async (item) => {
      if (item.role === 'admin') return
      try {
        saveAdminSessionForView()
        const data = await authAPI.createUserViewToken(item.user_id)
        setAuthSession({ token: data.token, user: data.user })
        window.location.href = '/'
      } catch (err) {
        errorMessage.value = err.response?.data?.detail || '进入用户查看模式失败'
        showError.value = true
      }
    }

    const loadAdminStrategies = async () => {
      if (!isAdmin.value) return
      adminStrategiesLoading.value = true
      try {
        const data = await marketAPI.getAdminStrategies()
        adminStrategies.value = (data.strategies || []).map(item => ({
          ...item,
          adminTargetStatus: item.lifecycle_status || 'draft',
          adminReason: ''
        }))
      } catch (err) {
        errorMessage.value = err.response?.data?.detail || '加载用户策略状态失败'
        showError.value = true
      } finally {
        adminStrategiesLoading.value = false
      }
    }

    const adminPromoteStrategy = async (item) => {
      const key = `${item.user_id}:${item.strategy_id}`
      adminStrategySaving.value = key
      try {
        const data = await marketAPI.adminTransitionStrategyLifecycle(
          item.user_id, item.strategy_id, item.adminTargetStatus, item.adminReason
        )
        if (data.status !== 'ok') {
          throw new Error(data.message || '策略状态推进失败')
        }
        successMessage.value = data.message || '策略状态已更新'
        showSuccess.value = true
        await loadAdminStrategies()
      } catch (err) {
        errorMessage.value = err.response?.data?.detail || err.message || '策略状态推进失败'
        showError.value = true
      } finally {
        adminStrategySaving.value = null
      }
    }

    const openAdminDeployments = async (item) => {
      const key = `${item.user_id}:${item.strategy_id}`
      adminDeploymentsLoading.value = key
      adminDeploymentsError.value = ''
      adminDeploymentsDetail.value = null
      try {
        const data = await marketAPI.getAdminStrategyDeployments(item.user_id, item.strategy_id)
        if (data.status !== 'ok') {
          throw new Error(data.message || '加载部署信息失败')
        }
        adminDeploymentsDetail.value = {
          strategy: data.strategy,
          deployments: data.deployments || [],
        }
        adminDeploymentsDialog.value = true
      } catch (err) {
        adminDeploymentsError.value = err.response?.data?.detail || err.message || '加载部署信息失败'
        adminDeploymentsDialog.value = true
      } finally {
        adminDeploymentsLoading.value = null
      }
    }

    const deploymentStatusLabelForAdmin = (status) => {
      return { active: '运行中', paused: '已暂停', completed: '期限已结束' }[status] || status || '未知'
    }

    const pnlClass = (value) => {
      const number = Number(value || 0)
      if (number > 0.0001) return 'text-success font-weight-medium'
      if (number < -0.0001) return 'text-error font-weight-medium'
      return 'text-medium-emphasis'
    }

    const membershipOptions = [
      { title: '普通用户', value: 'normal' },
      { title: '白银会员', value: 'silver' },
      { title: '黄金会员', value: 'gold' },
      { title: '钻石会员', value: 'diamond' }
    ]
    const membershipLabel = (level) => membershipOptions.find(
      item => item.value === level
    )?.title || '白银会员'
    const membershipColor = (level) => ({
      normal: 'grey', silver: 'blue-grey', gold: 'amber-darken-2', diamond: 'cyan-darken-1'
    }[level] || 'blue-grey')
    const liveEligibleLevel = (level) => ['gold', 'diamond'].includes(level)

    const loadCurrentUser = async () => {
      try {
        await authAPI.me()
      } catch (err) {
        console.error('加载当前用户信息失败:', err)
      }
    }

    // 可用品种列表（已连接但未配置的）
    const availableSymbols = computed(() => {
      const configured = Object.keys(tradeConfig.value.symbol_config || {})
      return symbols.value.filter(s => !configured.includes(s))
    })

    // 策略品种不应依赖当前内存中是否还有实时行情。
    const strategySymbolOptions = computed(() => Array.from(new Set([
      ...symbols.value,
      ...Object.keys(tradeConfig.value.symbol_config || {}),
      ...strategies.value.map(strategy => strategy.symbol)
    ])).filter(Boolean).sort())

    // 加载配置
    const loadTradeConfig = async () => {
      try {
        const data = await marketAPI.getTradeConfig()
        if (data.config) {
          tradeConfig.value = {
            enabled: data.config.enabled,
            default_volume: data.config.default_volume,
            default_sl_offset: data.config.default_sl_offset,
            symbol_config: data.config.symbol_config || {}
          }
        }
        if (data.governance) llmGovernance.value = data.governance
      } catch (err) {
        console.error('加载交易配置失败:', err)
      }
    }

    // 加载品种列表
    const loadSymbols = async () => {
      try {
        const data = await marketAPI.getSymbols()
        symbols.value = data.symbols || []
      } catch (err) {
        console.error('加载品种列表失败:', err)
        throw err
      }
    }

    // 保存配置
    const saveTradeConfig = async () => {
      try {
        const data = await marketAPI.updateTradeConfig({
          enabled: tradeConfig.value.enabled,
          default_volume: tradeConfig.value.default_volume,
          default_sl_offset: tradeConfig.value.default_sl_offset,
          symbol_config: tradeConfig.value.symbol_config
        })
        if (data.status !== 'ok') {
          errorMessage.value = data.message || '保存配置失败'
          showError.value = true
        } else {
          successMessage.value = '配置已保存'
          showSuccess.value = true
        }
      } catch (err) {
        errorMessage.value = `保存配置失败: ${err.message}`
        showError.value = true
      }
    }

    // 添加品种配置
    const addSymbolConfig = () => {
      if (!newSymbol.value) return
      const symbol = newSymbol.value
      tradeConfig.value.symbol_config[symbol] = {
        volume: newVolume.value || 0.01,
        sl_offset: newSlOffset.value || 0.05,
        key_levels: newKeyLevels.value || '',
        key_level_threshold: newKeyLevelThreshold.value || 0.0008
      }
      saveTradeConfig()
      // 清空输入
      newSymbol.value = ''
      newVolume.value = 0.01
      newSlOffset.value = 0.05
      newKeyLevels.value = ''
      newKeyLevelThreshold.value = 0.0008
    }

    // 删除品种配置
    const removeSymbolConfig = (symbol) => {
      delete tradeConfig.value.symbol_config[symbol]
      saveTradeConfig()
    }

    // 选择品种时自动填充默认值
    const onSymbolSelect = (symbol) => {
      if (symbol && tradeConfig.value.symbol_config && tradeConfig.value.symbol_config[symbol]) {
        const config = tradeConfig.value.symbol_config[symbol]
        newVolume.value = config.volume || 0.01
        newSlOffset.value = config.sl_offset || 0.05
        newKeyLevels.value = config.key_levels || ''
        newKeyLevelThreshold.value = config.key_level_threshold || 0.0008
      } else {
        newVolume.value = tradeConfig.value.default_volume || 0.01
        newSlOffset.value = tradeConfig.value.default_sl_offset || 0.05
        newKeyLevels.value = ''
        newKeyLevelThreshold.value = 0.0008
      }
    }

    // 加载大模型配置
    const loadLLMConfig = async () => {
      if (!isAdmin.value) return
      try {
        const data = await marketAPI.getLLMConfig()
        if (data.config) {
          const activeProvider = data.governance?.active_provider || {}
          llmConfig.value = {
            provider_id: activeProvider.provider_id || '',
            provider_name: activeProvider.provider_name || '默认供应商',
            api_key: '',  // 不显示已有key，只显示是否设置
            api_key_set: activeProvider.api_key_set || data.config.api_key_set || false,
            api_base: activeProvider.api_base || data.config.api_base || 'https://api.openai.com/v1',
            model: activeProvider.model || data.config.model || 'gpt-4o-mini',
            active: activeProvider.active ?? true,
            system_prompt: data.config.system_prompt || '',
            analysis_prompt_template: data.config.analysis_prompt_template || '',
            prompt_version: data.config.prompt_version || 1,
            enabled: data.config.enabled || false
          }
        }
        if (data.governance) llmGovernance.value = data.governance
      } catch (err) {
        console.error('加载大模型配置失败:', err)
      }
    }

    const loadLLMAccess = async () => {
      try {
        const data = await marketAPI.getLLMAccess()
        if (data.access) llmAccess.value = data.access
      } catch (err) {
        console.error('加载大模型开通状态失败:', err)
      }
    }

    const loadLLMFreeQuota = async () => {
      try {
        const data = await marketAPI.getLLMScene('backtest_report_analysis')
        if (data.scene?.quota) llmFreeQuota.value = data.scene.quota
      } catch (err) {
        console.error('加载大模型免费额度失败:', err)
      }
    }

    const requestLLMAccess = async () => {
      llmAccessRequesting.value = true
      try {
        const data = await marketAPI.requestLLMAccess()
        if (data.access) llmAccess.value = { ...llmAccess.value, ...data.access }
        successMessage.value = data.message || '申请已提交'
        showSuccess.value = true
        await loadLLMAccess()
      } catch (err) {
        errorMessage.value = err.response?.data?.detail || '申请提交失败'
        showError.value = true
      } finally {
        llmAccessRequesting.value = false
      }
    }

    const loadLLMAccessRequests = async () => {
      if (!isAdmin.value) return
      llmRequestsLoading.value = true
      try {
        const data = await marketAPI.getLLMAccessRequests('pending')
        llmAccessRequests.value = data.requests || []
      } catch (err) {
        errorMessage.value = err.response?.data?.detail || '加载开通申请失败'
        showError.value = true
      } finally {
        llmRequestsLoading.value = false
      }
    }

    const reviewLLMRequest = async (request, decision) => {
      llmReviewingId.value = request.id
      try {
        await marketAPI.reviewLLMAccessRequest(request.id, decision)
        successMessage.value = decision === 'approved' ? '已通过开通申请' : '已拒绝开通申请'
        showSuccess.value = true
        await loadLLMAccessRequests()
      } catch (err) {
        errorMessage.value = err.response?.data?.detail || '审批失败'
        showError.value = true
      } finally {
        llmReviewingId.value = null
      }
    }

    const formatTimestamp = (timestamp) => {
      if (!timestamp) return '--'
      return new Date(timestamp * 1000).toLocaleString('zh-CN')
    }

    const formatStrategyTime = (timestamp) => {
      if (!timestamp) return '--'
      const numeric = Number(timestamp)
      const date = Number.isFinite(numeric)
        ? new Date(numeric < 10_000_000_000 ? numeric * 1000 : numeric)
        : new Date(timestamp)
      if (Number.isNaN(date.getTime())) return '--'
      return date.toLocaleDateString('zh-CN', { month: '2-digit', day: '2-digit' })
    }

    // 保存大模型配置
    const saveLLMConfig = async () => {
      llmSaving.value = true
      try {
        const updateData = {
          provider_id: llmConfig.value.provider_id,
          provider_name: llmConfig.value.provider_name,
          api_base: llmConfig.value.api_base,
          model: llmConfig.value.model,
          active: llmConfig.value.active
        }
        // 只有输入了新的API Key才更新
        if (llmConfig.value.api_key) {
          updateData.api_key = llmConfig.value.api_key
        }

        const data = await marketAPI.saveLLMProvider(updateData)
        if (data.status === 'ok') {
          if (data.governance) llmGovernance.value = data.governance
          successMessage.value = data.governance?.scene_model_warnings?.length
            ? '供应商配置已保存，请检查场景模型切换提醒'
            : '供应商配置已保存'
          showSuccess.value = true
          // 重新加载配置
          await loadLLMConfig()
        } else {
          errorMessage.value = data.message || '保存配置失败'
          showError.value = true
        }
      } catch (err) {
        errorMessage.value = `保存配置失败: ${err.message}`
        showError.value = true
      } finally {
        llmSaving.value = false
      }
    }

    const selectLLMProvider = (provider) => {
      llmConfig.value.provider_id = provider.provider_id
      llmConfig.value.provider_name = provider.provider_name
      llmConfig.value.api_key = ''
      llmConfig.value.api_key_set = provider.api_key_set
      llmConfig.value.api_base = provider.api_base
      llmConfig.value.model = provider.model
      llmConfig.value.active = provider.active
    }

    const newLLMProvider = () => {
      llmConfig.value.provider_id = ''
      llmConfig.value.provider_name = ''
      llmConfig.value.api_key = ''
      llmConfig.value.api_key_set = false
      llmConfig.value.api_base = 'https://api.openai.com/v1'
      llmConfig.value.model = ''
      llmConfig.value.active = false
    }

    const activateLLMProvider = async (provider) => {
      try {
        const data = await marketAPI.activateLLMProvider(provider.provider_id)
        if (data.governance) llmGovernance.value = data.governance
        if (data.config) {
          llmConfig.value = {
            ...llmConfig.value,
            provider_id: data.provider.provider_id,
            provider_name: data.provider.provider_name,
            api_key: '',
            api_key_set: data.provider.api_key_set,
            api_base: data.provider.api_base,
            model: data.provider.model,
            active: true,
            enabled: data.config.enabled
          }
        }
        successMessage.value = data.governance?.scene_model_warnings?.length
          ? '已切换有效供应商，请检查场景模型切换提醒'
          : '已切换有效供应商'
        showSuccess.value = true
      } catch (err) {
        errorMessage.value = err.response?.data?.detail || '切换有效供应商失败'
        showError.value = true
      }
    }

    const syncLLMModels = async () => {
      llmModelsSyncing.value = true
      try {
        const data = await marketAPI.syncLLMModels()
        llmGovernance.value = data.governance
        successMessage.value = data.message || '模型列表已同步'
        showSuccess.value = true
      } catch (err) {
        errorMessage.value = err.response?.data?.detail || '模型同步失败'
        showError.value = true
      } finally {
        llmModelsSyncing.value = false
      }
    }

    const toggleLLMModel = async (model) => {
      if (!model.available) return
      try {
        const data = await marketAPI.setLLMModelEnabled(model.model_id, !model.enabled)
        llmGovernance.value = data.governance
      } catch (err) {
        errorMessage.value = err.response?.data?.detail || '模型状态更新失败'
        showError.value = true
      }
    }

    const saveLLMScene = async (scene) => {
      try {
        const data = await marketAPI.saveLLMScene(scene.scene_code, scene)
        const index = llmGovernance.value.scenes.findIndex(item => item.scene_code === scene.scene_code)
        if (index >= 0) llmGovernance.value.scenes[index] = data.scene
        successMessage.value = `${scene.display_name}配置已保存`
        showSuccess.value = true
      } catch (err) {
        errorMessage.value = err.response?.data?.detail || '场景配置保存失败'
        showError.value = true
      }
    }

    // ==================== 策略配置 ====================

    // 策略数据
    const strategies = ref([])
    const strategyQuota = ref({ usage: { signal_sources: 0 }, limits: { strategies: 5, signal_sources: 10 } })
    const strategiesLoading = ref(false)
    const strategiesError = ref('')
    const strategyPage = ref(1)
    const strategyPageSize = 10
    const strategyTotal = ref(0)
    const strategySaving = ref(null)
    const strategySymbolsLoading = ref(false)
    const strategyLifecycleSaving = ref(null)
    const strategyAdmissions = ref({})
    const positionPolicies = ref([])
    const positionPoliciesLoading = ref(false)
    const positionPoliciesError = ref('')
    const strategyWorkspaceTab = ref('mine')
    const strategyDetailTab = ref('overview')
    const selectedStrategy = ref(null)
    const selectedStrategySnapshot = ref('')
    const newStrategyDialog = ref(false)
    const strategySearch = ref('')
    const strategyLifecycleFilter = ref('all')
    const strategyVisibilityFilter = ref('all')
    const sharedStrategies = ref([])
    const sharedStrategiesLoading = ref(false)
    const sharedStrategyCopying = ref(null)
    const sharedStrategyTargetSymbols = reactive({})
    const paperDeployDialog = ref(false)
    const paperDeployLoading = ref(false)
    const paperDeploySubmitting = ref(false)
    const paperDeployStrategy = ref(null)
    const paperDeployAccountId = ref(null)
    const paperAccounts = ref([])
    const liveDeployDialog = ref(false)
    const liveDeployLoading = ref(false)
    const liveDeploySubmitting = ref(false)
    const liveDeployStrategy = ref(null)
    const liveDeployAccountId = ref(null)
    const liveAccounts = ref([])
    const usedSharedStrategyKeys = computed(() => new Set(
      strategies.value
        .filter(strategy => strategy.readonly_reference || strategy.source_owner_user_id)
        .map(strategy => `${strategy.source_owner_user_id}-${strategy.source_strategy_id}`)
    ))
    const paperAccountOptions = computed(() => paperAccounts.value.map(account => ({
      value: account.account_id,
      label: `${account.account_name} · ${account.currency || 'USD'} · 余额 ${Number(account.balance || 0).toFixed(2)}`,
    })))
    const liveAccountOptions = computed(() => liveAccounts.value.map(account => ({
      value: account.account_id,
      label: `${account.account_name} · ${account.mt5_login || 'MT5'} · ${account.mt5_server || '未知服务器'} · ${account.status === 'active' ? '活跃' : account.status}`,
    })))
    const positionPolicyOptions = computed(() => positionPolicies.value
      .filter(policy => policy.enabled)
      .map(policy => ({ title: policy.name, value: policy.policy_id })))
    const newStrategySymbol = ref('')
    const newStrategyName = ref('')
    const newStrategyPolicyId = ref('')
    const signalSourceDialog = ref(false)
    const signalSourceDialogLoading = ref(false)
    const signalSourceTarget = ref(null)
    const signalSourceEditMode = ref('add')
    const editingSignalSourceId = ref('')
    const aiSignalOptionsLoading = ref(false)
    const alphaLibrary = ref([])
    const aiSignalOptions = reactive({
      accessGranted: false,
      models: [],
      defaultSystemPrompt: '',
      defaultAnalysisPromptTemplate: '',
      sharedRuntimeData: []
    })
    const managedAISignalSources = ref([])
    const newSignalSource = reactive({
      source: 'key_level', period: 'M1', enabled: true, weight: 30, params: {}
    })
    const signalPeriods = ['M1', 'M5', 'M15', 'H1', 'H4']
    const signalSourceMeta = {
      key_level: { label: '关键点位信号', color: 'success', icon: 'mdi-map-marker-path' },
      ai_entry: { label: 'AI 入场信号', color: 'info', icon: 'mdi-brain' },
      pivot: { label: '转折点信号', color: 'primary', icon: 'mdi-chart-timeline-variant-shimmer' },
      moving_average: { label: '均线交叉信号', color: 'orange-darken-2', icon: 'mdi-chart-bell-curve' },
      alpha_factor: { label: '已验证 Alpha', color: 'teal-darken-1', icon: 'mdi-atom-variant' },
      structure_plan: { label: '结构交易计划', color: 'deep-orange', icon: 'mdi-chart-box-outline' },
    }
    const sourceMetaFor = (source) => signalSourceMeta[source] || {
      label: source || '未知信号源',
      color: 'grey',
      icon: 'mdi-help-circle-outline'
    }
    const signalSourceLabel = (source) => sourceMetaFor(source).label
    const strategySignalSources = (strategy) => {
      const sources = Array.isArray(strategy?.signal_sources)
        ? strategy.signal_sources
        : []
      return sources.some(source => source.source === 'key_level')
        ? sources.filter(source => source.source === 'key_level')
        : sources
    }
    const selectedStrategySignalSources = computed(() => {
      try {
        return strategySignalSources(selectedStrategy.value).map(source => (
          normalizeSignalSourceForDisplay(source)
        ))
      } catch (error) {
        console.error('信号源列表渲染失败:', error)
        return []
      }
    })
    const strategySourceBadges = (strategy) => strategySignalSources(strategy).map(source => ({
      key: source.signal_source_id,
      color: signalSourceMeta[source.source]?.color || 'grey',
      label: source.source === 'key_level'
        ? '关键点位'
        : source.source === 'ai_entry' && source.params?.analysis_mode === 'shared_reference'
          ? `共享 AI ${source.period}`
        : `${sourceMetaFor(source.source).label.replace('信号', '').trim()} ${source.period}`
    }))
    const signalSourceTypeOptions = computed(() => Object.entries(signalSourceMeta).map(([value, item]) => ({
      title: item.label,
      value,
      disabled: (
        (Boolean(signalSourceTarget.value) && !availablePeriodsForSource(
          value, editingSignalSourceId.value
        ).length)
      )
    })))
    const signalSourceDisabledReason = (sourceType) => {
      const sources = strategySignalSources(signalSourceTarget.value)
        .filter(item => item.signal_source_id !== editingSignalSourceId.value)
      if (sourceType === 'key_level' && sources.some(item => item.source !== 'key_level')) {
        return '已有其他信号'
      }
      if (sourceType !== 'key_level' && sources.some(item => item.source === 'key_level')) {
        return '已有关键点位'
      }
      return '周期已满'
    }
    const keyLevelModeOptions = [
      { title: '系统自动计算', value: 'automatic' },
      { title: '固定数字列表', value: 'levels' },
      { title: '价格表达式', value: 'expression' }
    ]
    const movingAverageTypeOptions = [
      { title: '简单移动平均线（SMA）', value: 'sma' },
      { title: '指数移动平均线（EMA）', value: 'ema' }
    ]
    const pivotSignalTypeOptions = [
      { title: '接近反转与突破跟随', value: 'both' },
      { title: '仅接近转折点反转', value: 'near' },
      { title: '仅突破转折点跟随', value: 'breakout' }
    ]
    const periodMinuteMap = { M1: 1, M5: 5, M15: 15, H1: 60, H4: 240 }
    const aiIntervalValues = [1, 2, 3, 5, 10, 15, 30, 60, 120, 240, 480, 720, 1440]
    const periodMinutes = (period) => periodMinuteMap[period] || 1
    const aiIntervalOptionsFor = (source) => aiIntervalValues
      .filter(value => value >= periodMinutes(source.period))
      .map(value => ({ title: `${value} 分钟`, value }))
    const sharedAIRuntimeOptions = computed(() => aiSignalOptions.sharedRuntimeData.map(item => ({
      value: item.share_id,
      title: `${item.symbol} · 匹配度 ${formatSimilarity(item.symbol_similarity)} · ${item.period} · ${item.model} · ${item.strategy_name} · ${lifecycleLabel(item.strategy_lifecycle)}`
    })))
    const managedAISignalSourceOptions = computed(() => managedAISignalSources.value
      .filter(item => item.enabled)
      .map(item => ({
        value: item.signal_source_id,
        title: `${item.is_owner ? '我的' : `共享自 ${item.owner_username || '平台用户'}`} · ${item.name} · ${item.symbol} · ${item.period}${item.locked ? ' · 已冻结' : ''}`
      })))
    const alphaLibraryOptions = computed(() => {
      const occupied = new Set(strategySignalSources(signalSourceTarget.value)
        .filter(item => item.source === 'alpha_factor')
        .filter(item => item.signal_source_id !== editingSignalSourceId.value)
        .map(item => item.params?.alpha_id))
      return alphaLibrary.value
      .filter(item => item.status === 'validated' && !occupied.has(item.alpha_id))
      .map(item => ({
        value: item.alpha_id,
        title: `${item.name} · v${item.version} · ${item.timeframe} · ${item.is_owner ? '我的' : `共享自 ${item.owner_username}`}`
      }))
    })
    const formatSimilarity = (value) => `${Math.round(Number(value || 0) * 100)}%`
    const buildClientId = (prefix = '', length = 12) => {
      const random = globalThis.crypto?.randomUUID?.().replaceAll('-', '')
        || Math.random().toString(36).slice(2)
      return `${prefix}${random}`.slice(0, length)
    }
    function lifecycleLabel (status) {
      return ({
        draft: '草稿', backtesting: '回测中', backtest_passed: '回测通过',
        paper_trading: '模拟盘验证', production: '可用于实盘', retired: '已停用'
      })[status] || status || '未知阶段'
    }
    const formatSharedSignalParams = (params = {}) => (
      `间隔 ${params.analysis_interval_minutes ?? '-'} 分钟，K线 ${params.kline_count ?? '-'} 根，最低置信度 ${params.min_confidence ?? '-'}%`
    )
    const formatSharedRuntimeResult = (result = {}) => {
      if (typeof result === 'string') return result
      try {
        return JSON.stringify(result || {}, null, 2)
      } catch (error) {
        return '共享分析结果暂无法展示'
      }
    }
    const normalizeSignalSourceForDisplay = (source) => {
      const cloned = cloneSignalSource(source)
      const sourceType = cloned.source || 'unknown'
      const fallbackId = `${sourceType}-${cloned.period || 'all'}`
      cloned.signal_source_id ||= fallbackId
      cloned.period ||= sourceType === 'key_level' ? 'M1' : 'M1'
      cloned.weight = Number(cloned.weight ?? 0)
      cloned.enabled = cloned.enabled !== false
      cloned.params = cloned.params && typeof cloned.params === 'object'
        ? cloned.params
        : {}
      return cloned
    }
    const selectedSharedAIRuntimeData = computed(() => {
      const params = newSignalSource.params || {}
      const selected = new Set(params.analysis_mode === 'shared_reference'
        ? [params.shared_runtime_id].filter(Boolean)
        : (params.reference_runtime_ids || []))
      return aiSignalOptions.sharedRuntimeData.filter(item => selected.has(item.share_id))
    })
    const canSaveSignalSource = computed(() => {
      if (!newSignalSource.period) return false
      if (newSignalSource.source === 'alpha_factor') {
        return Boolean(newSignalSource.params?.alpha_id)
      }
      if (newSignalSource.source !== 'ai_entry') return true
      return Boolean(newSignalSource.params?.ai_signal_source_id)
    })

    const loadAISignalOptions = async (symbol) => {
      aiSignalOptionsLoading.value = true
      try {
        const data = await marketAPI.getLLMSignalOptions(symbol)
        aiSignalOptions.accessGranted = Boolean(data.access_granted)
        aiSignalOptions.models = data.models || aiSignalOptions.models
        aiSignalOptions.defaultSystemPrompt = data.default_system_prompt || ''
        aiSignalOptions.defaultAnalysisPromptTemplate = data.default_analysis_prompt_template || ''
        aiSignalOptions.sharedRuntimeData = data.shared_runtime_data || []
        const sourceData = await marketAPI.getAISignalSources({
          symbol: symbol || undefined,
          include_shared: true
        })
        managedAISignalSources.value = sourceData.items || []
      } catch (error) {
        aiSignalOptions.accessGranted = Boolean(llmAccess.value.access_granted)
        aiSignalOptions.sharedRuntimeData = []
        managedAISignalSources.value = []
      } finally {
        aiSignalOptionsLoading.value = false
      }
    }

    const loadAlphaLibrary = async () => {
      try {
        const data = await marketAPI.getAlphaLibrary()
        alphaLibrary.value = data.items || []
      } catch (error) {
        alphaLibrary.value = []
      }
    }

    const normalizeAIInterval = (source) => {
      if (source.source !== 'ai_entry') return
      source.params ||= {}
      const minimum = periodMinutes(source.period)
      const current = Number(source.params.analysis_interval_minutes)
      source.params.analysis_interval_minutes = Math.max(
        minimum, Number.isFinite(current) ? current : minimum
      )
    }

    const sourceDefaults = (source, period) => ({
      signal_source_id: buildClientId('', 12),
      source,
      enabled: true,
      period,
      weight: 30,
      params: source === 'key_level'
          ? {
              level_mode: 'automatic', levels: [], levels_text: '',
              expression: '', proximity_threshold: 0.0008,
              order_distance: 0.0008,
              upward_approach_sell: true, downward_approach_buy: true,
              upward_breakout_buy: true, downward_breakout_sell: true,
              cooldown_seconds: 180
            }
          : source === 'pivot'
            ? {
                confirmation_strength: ({ M1: 6, M5: 4, M15: 3, H1: 3, H4: 3 })[period] || 3,
                signal_type: 'both',
                proximity_threshold: ({ M1: 0.0002, M5: 0.0005, M15: 0.0015, H1: 0.0015, H4: 0.0015 })[period] || 0.001,
                merge_distance: 0.0004,
                stop_buffer_ratio: 0.0005,
                risk_reward_ratio: 2,
                candidate_limit: 10,
                min_confirmation_count: 1,
                max_age_bars: 120,
                recency_half_life_bars: 30,
                min_pivot_score: 0,
                cooldown_seconds: 180
              }
            : source === 'moving_average'
            ? {
                fast_period: 5, slow_period: 20, ma_type: 'sma',
                min_confidence: 70,
                cooldown_seconds: 180
            }
            : source === 'structure_plan'
              ? { allowed_directions: ['buy', 'sell'], require_signal_consistency: false }
            : source === 'alpha_factor'
              ? {
                  alpha_id: '', alpha_version: 1, alpha_name: '',
                  alpha_snapshot: {}, min_confidence: 60, cooldown_seconds: 180
                }
              : {
              ai_signal_source_id: '', ai_signal_source_owner_id: '',
              min_confidence: 70, entry_threshold_percent: 0.08
            }
    })
    const cloneSignalSource = (source) => JSON.parse(JSON.stringify(source || {}))
    const pivotPercentFields = [
      ['proximity_threshold', 'proximity_threshold_percent'],
      ['merge_distance', 'merge_distance_percent'],
      ['stop_buffer_ratio', 'stop_buffer_percent']
    ]
    const hydratePivotPercentParams = (params) => {
      pivotPercentFields.forEach(([ratioField, percentField]) => {
        const ratio = Number(params[ratioField])
        params[percentField] = Number.isFinite(ratio)
          ? Number((ratio * 100).toFixed(6))
          : 0
      })
    }
    const serializePivotPercentParams = (params) => {
      pivotPercentFields.forEach(([ratioField, percentField]) => {
        const percent = Number(params[percentField])
        if (Number.isFinite(percent)) {
          params[ratioField] = Math.max(0, Math.min(5, percent)) / 100
        }
        delete params[percentField]
      })
    }
    const normalizeSignalSourceForDialog = (source) => {
      const sourceType = source?.source || 'key_level'
      const period = source?.period || (sourceType === 'key_level' ? 'M1' : signalPeriods[0])
      const defaults = sourceDefaults(sourceType, period)
      const cloned = cloneSignalSource(source)
      const params = {
        ...(defaults.params || {}),
        ...(cloned.params || {})
      }
      if (sourceType === 'ai_entry') {
        params.ai_signal_source_id ||= ''
        params.ai_signal_source_owner_id ||= ''
        const threshold = Number(params.entry_threshold)
        params.entry_threshold_percent = Number.isFinite(threshold)
          ? Number((threshold * 100).toFixed(4))
          : 0.08
      }
      if (sourceType === 'key_level') {
        params.levels = Array.isArray(params.levels) ? params.levels : []
        params.levels_text ??= params.levels.join(', ')
        params.order_distance ??= params.proximity_threshold ?? 0.0008
        params.proximity_threshold = params.order_distance
      }
      if (sourceType === 'pivot') hydratePivotPercentParams(params)
      return {
        ...defaults,
        ...cloned,
        source: sourceType,
        period: sourceType === 'key_level' ? 'M1' : period,
        enabled: cloned.enabled ?? defaults.enabled,
        weight: Number(cloned.weight ?? defaults.weight ?? 30),
        params
      }
    }
    const setDialogSignalSource = (source) => {
      const normalized = normalizeSignalSourceForDialog(source)
      Object.assign(newSignalSource, normalized)
      normalizeAIInterval(newSignalSource)
    }
    const onManagedAISignalSourceSelected = (sourceId) => {
      const source = managedAISignalSources.value.find(item => item.signal_source_id === sourceId)
      if (!source) return
      newSignalSource.signal_source_id = source.signal_source_id
      newSignalSource.period = source.period
      newSignalSource.params.ai_signal_source_id = source.signal_source_id
      newSignalSource.params.ai_signal_source_owner_id = source.user_id
      const config = source.config || {}
      newSignalSource.params.min_confidence = config.min_confidence ?? 70
      newSignalSource.params.entry_threshold_percent = config.entry_threshold_percent ?? 0.08
      normalizeAIInterval(newSignalSource)
    }

    // 策略选项
    const consistencyOptions = [
      { title: '任一信号即可', value: 'any' },
      { title: '多数信号一致（至少60%同向）', value: 'majority' },
      { title: '所有信号一致', value: 'all' }
    ]

    const loadPositionPolicies = async () => {
      positionPoliciesLoading.value = true
      positionPoliciesError.value = ''
      try {
        const data = await marketAPI.getPositionManagementPolicies()
        if (data.status !== 'ok') throw new Error(data.message || '持仓管理方案加载失败')
        positionPolicies.value = data.policies || []
        if (!newStrategyPolicyId.value) {
          newStrategyPolicyId.value = positionPolicyOptions.value[0]?.value || ''
        }
      } catch (err) {
        positionPolicies.value = []
        positionPoliciesError.value = err.response?.data?.detail || err.message || '持仓管理方案加载失败，请重试'
        throw err
      } finally {
        positionPoliciesLoading.value = false
      }
    }

    const lifecycleMeta = {
      draft: {
        label: '草稿',
        color: 'grey',
        description: '策略参数可以编辑，完成后进入历史回测。'
      },
      backtesting: {
        label: '回测中',
        color: 'info',
        description: '策略正在进行历史数据验证，暂不可用于交易。'
      },
      backtest_passed: {
        label: '回测通过',
        color: 'primary',
        description: '历史回测已通过，下一步进入模拟盘验证。'
      },
      paper_trading: {
        label: '模拟盘验证',
        color: 'warning',
        description: '策略正在模拟盘运行，确认实盘表现前不会真实下单。'
      },
      production: {
        label: '可用于实盘',
        color: 'success',
        description: '策略已完成验证，可以启用信号推荐。'
      },
      retired: {
        label: '已停用',
        color: 'error',
        description: '策略已经归档，不再参与信号和交易决策。'
      }
    }
    const lifecycleFilterOptions = [
      { title: '全部生命周期', value: 'all' },
      ...Object.entries(lifecycleMeta).map(([value, item]) => ({ title: item.label, value }))
    ]
    const adminLifecycleOptions = Object.entries(lifecycleMeta).map(([value, item]) => ({
      title: item.label,
      value
    }))
    const filteredAdminStrategies = computed(() => {
      const keyword = adminStrategySearch.value.trim().toLowerCase()
      return adminStrategies.value.filter(item => {
        const matchesLifecycle = adminStrategyLifecycleFilter.value === 'all' ||
          item.lifecycle_status === adminStrategyLifecycleFilter.value
        const matchesSearch = !keyword || [
          item.username, item.email, item.strategy_name, item.strategy_id, item.symbol
        ].some(value => String(value || '').toLowerCase().includes(keyword))
        return matchesLifecycle && matchesSearch
      })
    })
    const visibilityFilterOptions = [
      { title: '全部可见性', value: 'all' },
      { title: '私有策略', value: 'private' },
      { title: '平台共享', value: 'shared' }
    ]
    const strategyMetrics = computed(() => ({
      production: strategies.value.filter(item => item.lifecycle_status === 'production').length,
      deployed: strategies.value.filter(item => Number(item.deployment_count || 0) > 0).length,
      shared: strategies.value.filter(item => item.is_shared).length
    }))
    const filteredStrategies = computed(() => {
      const keyword = strategySearch.value.trim().toLowerCase()
      return strategies.value.filter(strategy => {
        const matchesSearch = !keyword || [
          strategy.strategy_name, strategy.symbol, strategy.strategy_id
        ].some(value => String(value || '').toLowerCase().includes(keyword))
        const matchesLifecycle = strategyLifecycleFilter.value === 'all' ||
          strategy.lifecycle_status === strategyLifecycleFilter.value
        const matchesVisibility = strategyVisibilityFilter.value === 'all' ||
          (strategyVisibilityFilter.value === 'shared' ? strategy.is_shared : !strategy.is_shared)
        return matchesSearch && matchesLifecycle && matchesVisibility
      })
    })
    const comparableStrategy = (strategy) => {
      if (!strategy) return ''
      const clone = JSON.parse(JSON.stringify(strategy))
      return JSON.stringify(clone)
    }
    const hasStrategyChanges = computed(() => Boolean(selectedStrategy.value) &&
      comparableStrategy(selectedStrategy.value) !== selectedStrategySnapshot.value)
    const openStrategyDetail = (strategy) => {
      const draft = JSON.parse(JSON.stringify(strategy))
      normalizeStrategyVisibility(draft)
      ensureSignalSources(draft)
      selectedStrategy.value = draft
      selectedStrategySnapshot.value = comparableStrategy(draft)
      strategyDetailTab.value = 'overview'
      window.scrollTo({ top: 0, behavior: 'smooth' })
    }
    const closeStrategyDetail = () => {
      if (hasStrategyChanges.value && !confirm('当前策略有未保存的修改，确定返回吗？')) return
      selectedStrategy.value = null
      selectedStrategySnapshot.value = ''
    }

    const lifecycleActions = {
      draft: [
        { target: 'backtesting', label: '开始回测', color: 'primary', icon: 'mdi-play-circle-outline' }
      ],
      backtesting: [
        { target: 'backtest_passed', label: '标记回测通过', color: 'success', icon: 'mdi-check-decagram-outline' },
        { target: 'draft', label: '取消回测', color: 'grey', icon: 'mdi-undo' }
      ],
      backtest_passed: [
        { target: 'paper_trading', label: '开始模拟盘', color: 'warning', icon: 'mdi-flask-outline' },
        { target: 'backtesting', label: '重新回测', color: 'info', icon: 'mdi-refresh' }
      ],
      paper_trading: [
        { target: 'production', label: '批准用于实盘', color: 'success', icon: 'mdi-rocket-launch-outline', confirm: true },
        { target: 'backtest_passed', label: '结束模拟盘', color: 'grey', icon: 'mdi-stop-circle-outline' }
      ],
      production: [
        { target: 'paper_trading', label: '结束实盘并回到模拟验证', color: 'warning', icon: 'mdi-undo-variant', confirm: true },
        { target: 'retired', label: '停用并归档', color: 'error', icon: 'mdi-archive-outline', confirm: true }
      ],
      retired: []
    }

    const getLifecycleMeta = (strategy) => (
      lifecycleMeta[strategy.lifecycle_status] || lifecycleMeta.draft
    )
    const getLifecycleColor = (status) => (
      lifecycleMeta[status]?.color || lifecycleMeta.draft.color
    )

    const getLifecycleActions = (strategy) => (
      lifecycleActions[strategy.lifecycle_status] || []
    )
    const getConsistencyLabel = (value) => (
      consistencyOptions.find(item => item.value === value)?.title || value || '未配置'
    )
    const signalSourceCount = (strategy) => (
      Array.isArray(strategy.signal_sources) ? strategy.signal_sources.length : 0
    )
    const signalSourceSummary = (source) => {
      try {
        const params = source.params || {}
        if (source.source === 'key_level') {
          const triggers = [
            params.upward_approach_sell ? '向上接近卖' : '',
            params.downward_approach_buy ? '向下接近买' : '',
            params.upward_breakout_buy ? '向上突破买' : '',
            params.downward_breakout_sell ? '向下突破卖' : ''
          ].filter(Boolean).join(' / ')
          return `下单距离 ${params.order_distance ?? params.proximity_threshold ?? 0}，冷却 ${params.cooldown_seconds ?? 0}s，${triggers || '未启用触发'}`
        }
        if (source.source === 'moving_average') {
          return `${params.ma_type || 'sma'} 快线 ${params.fast_period} / 慢线 ${params.slow_period}，最低置信度 ${params.min_confidence ?? 0}%`
        }
        if (source.source === 'pivot') {
          const trigger = ({ both: '反转 + 突破', near: '接近反转', breakout: '突破跟随' })[params.signal_type] || '反转 + 突破'
          return `${trigger}，最近 ${params.candidate_limit ?? 10} 个，至少确认 ${params.min_confirmation_count ?? 1} 次，有效 ${params.max_age_bars ?? 120} 根K线`
        }
        if (source.source === 'alpha_factor') {
          return `${params.alpha_name || '已验证 Alpha'} · ${source.period}，最低置信度 ${params.min_confidence ?? 0}%`
        }
        if (params.analysis_mode === 'shared_reference') {
          const shared = aiSignalOptions.sharedRuntimeData.find(
            item => item.share_id === params.shared_runtime_id
          )
          return `引用 ${shared?.owner_username || '平台用户'} 的 ${shared?.symbol || '共享'} 分析，最低置信度 ${params.min_confidence ?? 0}%`
        }
        return `${params.model || '平台默认模型'} · 每 ${params.analysis_interval_minutes ?? 0} 分钟分析 ${params.kline_count ?? 0} 根K线，最低置信度 ${params.min_confidence ?? 0}%`
      } catch (error) {
        console.error('信号源摘要渲染失败:', error)
        return '配置摘要暂不可用，请点编辑查看详情'
      }
    }
    const normalizeStrategyVisibility = (strategy) => {
      strategy.visibility = strategy.visibility === 'shared' || strategy.is_shared
        ? 'shared'
        : 'private'
      strategy.is_shared = strategy.visibility === 'shared'
      return strategy
    }

    const getAdmission = (strategy) => strategyAdmissions.value[strategy.strategy_id]
    const admissionStages = (strategy) => {
      const admission = getAdmission(strategy)
      return admission ? [
        { key: 'backtest', label: '历史回测', data: admission.backtest },
        { key: 'paper', label: '模拟盘验证', data: admission.paper }
      ] : []
    }
    const isLifecycleActionDisabled = (strategy, action) => {
      const admission = getAdmission(strategy)
      if (!admission) return ['backtest_passed', 'paper_trading', 'production'].includes(action.target)
      if (action.target === 'backtest_passed') return !admission.backtest.passed
      if (action.target === 'paper_trading') return !admission.eligible_for_paper
      if (action.target === 'production') return !admission.eligible_for_production
      return false
    }

    const transitionStrategyLifecycle = async (strategy, action) => {
      if (selectedStrategy.value?.strategy_id === strategy.strategy_id && hasStrategyChanges.value) {
        errorMessage.value = '请先保存当前策略修改，再变更生命周期'
        showError.value = true
        return
      }
      if (action.confirm && !confirm(`确定要执行“${action.label}”吗？`)) return
      strategyLifecycleSaving.value = strategy.strategy_id
      try {
        const data = await marketAPI.transitionStrategyLifecycle(
          strategy.strategy_id,
          action.target,
          action.label
        )
        if (data.status !== 'ok') {
          throw new Error(data.message || '生命周期状态转换失败')
        }
        if (data.strategy) {
          Object.assign(strategy, data.strategy)
          normalizeStrategyVisibility(strategy)
          ensureSignalSources(strategy)
          const listItem = strategies.value.find(item => item.strategy_id === strategy.strategy_id)
          if (listItem) Object.assign(listItem, JSON.parse(JSON.stringify(strategy)))
          if (selectedStrategy.value?.strategy_id === strategy.strategy_id) {
            selectedStrategySnapshot.value = comparableStrategy(strategy)
          }
        }
        await loadStrategyAdmissions()
        successMessage.value = data.message
        showSuccess.value = true
      } catch (err) {
        const detail = err.response?.data?.detail || err.message
        errorMessage.value = `生命周期状态转换失败: ${detail}`
        showError.value = true
      } finally {
        strategyLifecycleSaving.value = null
      }
    }

    // 加载策略列表
    const strategyPageCount = computed(() => Math.max(1, Math.ceil(strategyTotal.value / strategyPageSize)))
    const loadStrategies = async (requestedPage = strategyPage.value) => {
      strategyPage.value = Math.min(Math.max(1, Number(requestedPage) || 1), strategyPageCount.value || 1)
      strategiesLoading.value = true
      strategiesError.value = ''
      try {
        const [strategyResult] = await Promise.allSettled([
          marketAPI.getStrategies(strategyPage.value, strategyPageSize),
          loadPositionPolicies(),
        ])
        if (strategyResult.status !== 'fulfilled') throw strategyResult.reason
        const data = strategyResult.value
        if (data.status !== 'ok') throw new Error(data.message || '策略列表加载失败')
        strategies.value = data.strategies || []
        strategyTotal.value = Number(data.total ?? data.count ?? strategies.value.length)
        if (strategyPage.value > strategyPageCount.value) {
          strategyPage.value = strategyPageCount.value
          return await loadStrategies(strategyPage.value)
        }
        strategyQuota.value = data.quota || strategyQuota.value
        strategies.value.forEach(strategy => {
          normalizeStrategyVisibility(strategy)
          ensureSignalSources(strategy)
        })
        // 准入信息仅用于详情和生命周期操作，继续后台加载但不阻塞首屏。
        loadStrategyAdmissions().catch(err => console.warn('加载策略准入信息失败:', err))
      } catch (err) {
        strategies.value = []
        strategyTotal.value = 0
        strategiesError.value = err.response?.data?.detail || err.message || '策略列表加载失败，请重试'
        console.error('加载策略配置失败:', err)
      } finally {
        strategiesLoading.value = false
      }
    }

    const loadStrategyAdmissions = async () => {
      const data = await marketAPI.getStrategyAdmission()
      if (data.status === 'ok') {
        strategyAdmissions.value = Object.fromEntries(
          (data.items || []).map(item => [item.strategy_id, item])
        )
      }
    }

    const loadSharedStrategies = async () => {
      sharedStrategiesLoading.value = true
      try {
        const data = await marketAPI.getSharedStrategies()
        if (data.status === 'ok') {
          sharedStrategies.value = data.strategies || []
          sharedStrategies.value.forEach(item => {
            const key = sharedStrategyKey(item)
            sharedStrategyTargetSymbols[key] ||= item.target_symbol_options?.[0]?.symbol || item.symbol
          })
        } else {
          throw new Error(data.message || '加载共享策略失败')
        }
      } catch (err) {
        const detail = err.response?.data?.detail || err.message
        errorMessage.value = `加载共享策略失败: ${detail}`
        showError.value = true
      } finally {
        sharedStrategiesLoading.value = false
      }
    }

    const sharedStrategyKey = (item) => `${item.owner_user_id}-${item.strategy_id}`
    const isSharedStrategyUsed = (item) => usedSharedStrategyKeys.value.has(
      sharedStrategyKey(item)
    )

    const loadPaperAccountsForDeploy = async () => {
      paperDeployLoading.value = true
      try {
        const data = await accountAPI.list()
        paperAccounts.value = (data.accounts || []).filter(account => (
          account.account_type === 'paper'
          && account.status === 'active'
          && account.enabled
        ))
        paperDeployAccountId.value = paperAccounts.value[0]?.account_id || null
      } catch (err) {
        const detail = err.response?.data?.detail || err.message
        errorMessage.value = `加载模拟账户失败: ${detail}`
        showError.value = true
      } finally {
        paperDeployLoading.value = false
      }
    }

    const openPaperDeployDialog = async (strategy) => {
      if (selectedStrategy.value?.strategy_id === strategy.strategy_id && hasStrategyChanges.value) {
        errorMessage.value = '请先保存当前策略修改，再部署到模拟账户'
        showError.value = true
        return
      }
      paperDeployStrategy.value = strategy
      paperDeployDialog.value = true
      await loadPaperAccountsForDeploy()
    }

    const deploySelectedStrategyToPaper = async () => {
      if (!paperDeployStrategy.value || !paperDeployAccountId.value) return
      paperDeploySubmitting.value = true
      try {
        const data = await accountAPI.deployStrategy(
          paperDeployAccountId.value,
          paperDeployStrategy.value.strategy_id
        )
        paperDeployDialog.value = false
        successMessage.value = data.message || '策略已部署到模拟账户'
        showSuccess.value = true
        await loadStrategies()
      } catch (err) {
        const detail = err.response?.data?.detail || err.message
        errorMessage.value = `部署到模拟账户失败: ${detail}`
        showError.value = true
      } finally {
        paperDeploySubmitting.value = false
      }
    }

    const loadLiveAccountsForDeploy = async () => {
      liveDeployLoading.value = true
      try {
        const data = await accountAPI.list()
        liveAccounts.value = (data.accounts || []).filter(account => (
          account.account_type === 'mt5'
          && account.status === 'active'
          && account.enabled
          && account.trading_enabled
        ))
        liveDeployAccountId.value = liveAccounts.value[0]?.account_id || null
      } catch (err) {
        const detail = err.response?.data?.detail || err.message
        errorMessage.value = `加载实盘账户失败: ${detail}`
        showError.value = true
      } finally {
        liveDeployLoading.value = false
      }
    }

    const openLiveDeployDialog = async (strategy) => {
      if (selectedStrategy.value?.strategy_id === strategy.strategy_id && hasStrategyChanges.value) {
        errorMessage.value = '请先保存当前策略修改，再部署到实盘账户'
        showError.value = true
        return
      }
      liveDeployStrategy.value = strategy
      liveDeployDialog.value = true
      await loadLiveAccountsForDeploy()
    }

    const deploySelectedStrategyToLive = async () => {
      if (!liveDeployStrategy.value || !liveDeployAccountId.value) return
      liveDeploySubmitting.value = true
      try {
        const data = await accountAPI.deployStrategy(
          liveDeployAccountId.value,
          liveDeployStrategy.value.strategy_id
        )
        liveDeployDialog.value = false
        successMessage.value = data.message || '策略已部署到实盘账户'
        showSuccess.value = true
        await loadStrategies()
      } catch (err) {
        const detail = err.response?.data?.detail || err.message
        errorMessage.value = `部署到实盘账户失败: ${detail}`
        showError.value = true
      } finally {
        liveDeploySubmitting.value = false
      }
    }

    const useSharedStrategy = async (item) => {
      if (isSharedStrategyUsed(item) || sharedStrategyCopying.value) return
      const copyKey = sharedStrategyKey(item)
      sharedStrategyCopying.value = copyKey
      try {
        const data = await marketAPI.useSharedStrategy(
          item.owner_user_id,
          item.strategy_id,
          {
            target_symbol: sharedStrategyTargetSymbols[sharedStrategyKey(item)] || item.symbol,
          }
        )
        if (data.status !== 'ok') {
          throw new Error(data.message || '使用共享策略失败')
        }
        successMessage.value = data.message || '平台策略已添加'
        showSuccess.value = true
        await loadStrategies()
        await loadSharedStrategies()
        strategyWorkspaceTab.value = 'mine'
        const createdId = data.strategy?.strategy_id
        const created = strategies.value.find(strategy => strategy.strategy_id === createdId)
        if (created) openStrategyDetail(created)
      } catch (err) {
        const detail = err.response?.data?.detail || err.message
        errorMessage.value = `使用共享策略失败: ${detail}`
        showError.value = true
      } finally {
        sharedStrategyCopying.value = null
      }
    }

    // 更新策略
    const updateStrategy = async (strategy) => {
      strategySaving.value = strategy.strategy_id
      try {
        const data = await marketAPI.updateStrategy(strategy.strategy_id, {
          strategy_name: strategy.strategy_name,
          visibility: strategy.is_shared ? 'shared' : 'private',
          min_confidence: strategy.min_confidence,
          consistency_requirement: strategy.consistency_requirement,
          signal_sources: serializeSignalSources(strategy),
          signal_weights: strategy.signal_weights,
          fixed_volume: strategy.fixed_volume,
          max_positions: strategy.max_positions,
          max_same_direction: strategy.max_same_direction,
          risk_percent: strategy.risk_percent,
          position_management_policy_id: strategy.position_management_policy_id
        })
        if (data.status === 'ok') {
          successMessage.value = `${strategy.symbol} 策略配置已保存`
          showSuccess.value = true
          // 更新本地策略数据
          if (data.strategy) {
            Object.assign(strategy, data.strategy)
            normalizeStrategyVisibility(strategy)
            ensureSignalSources(strategy)
            const listItem = strategies.value.find(item => item.strategy_id === strategy.strategy_id)
            if (listItem) Object.assign(listItem, JSON.parse(JSON.stringify(strategy)))
            if (selectedStrategy.value?.strategy_id === strategy.strategy_id) {
              selectedStrategySnapshot.value = comparableStrategy(strategy)
            }
          }
        } else {
          errorMessage.value = data.message || '保存失败'
          showError.value = true
        }
      } catch (err) {
        console.error('保存策略失败:', err)
        const detail = err.response?.data?.message || err.response?.data?.detail || err.message
        errorMessage.value = `保存策略失败: ${detail}`
        showError.value = true
      } finally {
        strategySaving.value = null
      }
    }
    const saveSelectedStrategy = () => {
      if (selectedStrategy.value) updateStrategy(selectedStrategy.value)
    }

    const copyStrategy = async (strategy) => {
      strategySaving.value = `copy-${strategy.strategy_id}`
      try {
        const data = await marketAPI.copyStrategy(strategy.strategy_id)
        if (data.status !== 'ok') throw new Error(data.message || '复制策略失败')
        successMessage.value = data.message || '已复制为新策略'
        showSuccess.value = true
        await loadStrategies()
        const copied = strategies.value.find(item => item.strategy_id === data.strategy?.strategy_id)
        if (copied) openStrategyDetail(copied)
      } catch (err) {
        const detail = err.response?.data?.detail || err.message
        errorMessage.value = `复制策略失败: ${detail}`
        showError.value = true
      } finally {
        strategySaving.value = null
      }
    }

    function ensureSignalSources (strategy) {
      if (!Array.isArray(strategy.signal_sources)) strategy.signal_sources = []
      if (strategy.signal_sources.some(source => source.source === 'key_level')) {
        strategy.signal_sources = strategy.signal_sources.filter(
          source => source.source === 'key_level'
        )
      }
      strategy.signal_sources.forEach(source => {
        source.params ||= {}
        normalizeAIInterval(source)
        if (source.source === 'key_level' && source.params.levels_text === undefined) {
          source.params.levels_text = (source.params.levels || []).join(', ')
        }
        if (source.source === 'key_level') {
          source.period = 'M1'
          source.params.order_distance ??= source.params.proximity_threshold ?? 0.0008
          source.params.proximity_threshold = source.params.order_distance
          delete source.params.stop_loss_distance
          source.params.cooldown_seconds ??= 180
          source.params.upward_approach_sell ??= true
          source.params.downward_approach_buy ??= true
          source.params.upward_breakout_buy ??= true
          source.params.downward_breakout_sell ??= true
        }
      })
      return strategy.signal_sources
    }

    const serializeSignalSources = (strategy) => ensureSignalSources(strategy).map(source => {
      normalizeAIInterval(source)
      const clean = JSON.parse(JSON.stringify(source))
      if (clean.source === 'ai_entry' && clean.params.entry_threshold_percent !== undefined) {
        clean.params.entry_threshold = Math.max(
          0, Math.min(10, Number(clean.params.entry_threshold_percent ?? 0.08))
        ) / 100
        delete clean.params.entry_threshold_percent
      }
      if (clean.source === 'ai_entry') {
        // AI runtime configuration belongs to the independent signal source.
        clean.params = {
          ai_signal_source_id: clean.params.ai_signal_source_id || '',
          min_confidence: Number(clean.params.min_confidence || 0),
          entry_threshold: Number(clean.params.entry_threshold || 0.0008),
          cooldown_seconds: Number(clean.params.cooldown_seconds || 0),
        }
      }
      if (clean.source === 'key_level') {
        clean.period = 'M1'
        clean.params.proximity_threshold = Number(
          clean.params.order_distance || clean.params.proximity_threshold || 0
        )
        clean.params.levels = String(clean.params.levels_text || '')
          .split(/[,，\s]+/)
          .map(Number)
          .filter(value => Number.isFinite(value) && value > 0)
        delete clean.params.levels_text
      }
      if (clean.source === 'pivot') serializePivotPercentParams(clean.params)
      return clean
    })

    const periodOptionsFor = (strategy, current) => {
      if (current.source === 'key_level') return ['M1']
      const occupied = new Set(
        ensureSignalSources(strategy)
          .filter(item => item.source === current.source && item.signal_source_id !== current.signal_source_id)
          .map(item => item.period)
      )
      return signalPeriods.filter(period => !occupied.has(period))
    }

    const availablePeriodsForSource = (sourceType, currentSourceId = '') => {
      if (!signalSourceTarget.value) return []
      const sources = strategySignalSources(signalSourceTarget.value)
      const otherSources = sources.filter(
        item => item.signal_source_id !== currentSourceId
      )
      const hasKeyLevel = otherSources.some(item => item.source === 'key_level')
      const hasNonKeyLevel = otherSources.some(item => item.source !== 'key_level')
      if (sourceType === 'key_level') {
        return hasKeyLevel || hasNonKeyLevel ? [] : ['M1']
      }
      if (hasKeyLevel) {
        return []
      }
      if (sourceType === 'alpha_factor') {
        return alphaLibraryOptions.value.length
          ? signalPeriods
          : []
      }
      const occupied = new Set(
        sources
          .filter(item => item.source === sourceType)
          .filter(item => item.signal_source_id !== currentSourceId)
          .map(item => item.period)
      )
      return signalPeriods.filter(period => !occupied.has(period))
    }

    const availablePeriodsForNewSource = computed(
      () => availablePeriodsForSource(
        newSignalSource.source, editingSignalSourceId.value
      )
    )

    const selectFirstAvailablePeriod = (sourceType = newSignalSource.source) => {
      newSignalSource.period = sourceType === 'key_level'
        ? 'M1'
        : availablePeriodsForSource(sourceType)[0] || ''
    }

    const onNewSignalSourceTypeChange = (sourceType) => {
      if (!availablePeriodsForSource(
        sourceType, editingSignalSourceId.value
      ).length) {
        return
      }
      const period = sourceType === 'key_level'
        ? 'M1'
        : availablePeriodsForSource(sourceType, editingSignalSourceId.value)[0] || ''
      setDialogSignalSource(sourceDefaults(sourceType, period || 'M1'))
    }

    const onSharedRuntimeSelected = (shareId) => {
      if (!shareId) return
      const selected = aiSignalOptions.sharedRuntimeData.find(
        item => item.share_id === shareId
      )
      if (!selected) return
      const available = availablePeriodsForSource(
        'ai_entry', editingSignalSourceId.value
      )
      if (!available.includes(selected.period)) {
        newSignalSource.params.shared_runtime_id = ''
        errorMessage.value = `当前策略已经添加过 AI ${selected.period} 周期，不能重复引用`
        showError.value = true
        return
      }
      newSignalSource.period = selected.period
    }

    const onAlphaSelected = (alphaId) => {
      const alpha = alphaLibrary.value.find(item => item.alpha_id === alphaId)
      if (!alpha) return
      newSignalSource.period = alpha.timeframe
      newSignalSource.params.alpha_name = `${alpha.name} v${alpha.version}`
      newSignalSource.params.alpha_version = alpha.version
      newSignalSource.params.alpha_snapshot = alpha.definition
    }

    const refreshDialogDefaultsAfterOptionsLoaded = () => {}

    const openSignalSourceDialog = async (strategy, source = null) => {
      signalSourceDialogLoading.value = true
      try {
        signalSourceTarget.value = strategy
        if (source) {
          signalSourceEditMode.value = 'edit'
          editingSignalSourceId.value = source.signal_source_id
          setDialogSignalSource(source)
        } else {
          signalSourceEditMode.value = 'add'
          editingSignalSourceId.value = ''
          const firstAvailableType = Object.keys(signalSourceMeta).find(
            sourceType => availablePeriodsForSource(sourceType).length
          )
          if (!firstAvailableType) {
            errorMessage.value = '当前策略已没有可添加的信号源周期'
            showError.value = true
            return
          }
          const firstPeriod = firstAvailableType === 'key_level'
            ? 'M1'
            : availablePeriodsForSource(firstAvailableType)[0]
          setDialogSignalSource(sourceDefaults(firstAvailableType, firstPeriod))
        }
        // 弹窗先打开，但配置和保存按钮在共享信号源等数据加载完成前保持锁定。
        signalSourceDialog.value = true
        await Promise.all([loadAISignalOptions(strategy.symbol), loadAlphaLibrary()])
        refreshDialogDefaultsAfterOptionsLoaded()
      } catch (error) {
        console.error('打开信号源配置失败:', error)
        signalSourceDialog.value = false
        errorMessage.value = error?.message || '打开信号源配置失败，请刷新后重试'
        showError.value = true
      } finally {
        signalSourceDialogLoading.value = false
      }
    }

    const saveSignalSourceFromDialog = () => {
      if (!signalSourceTarget.value || !canSaveSignalSource.value) return
      const nextSource = cloneSignalSource(newSignalSource)
      if (nextSource.source === 'ai_entry') {
        nextSource.params.analysis_mode = 'managed_source'
        nextSource.params.entry_threshold = Math.max(
          0, Math.min(10, Number(nextSource.params.entry_threshold_percent ?? 0.08))
        ) / 100
        delete nextSource.params.entry_threshold_percent
      }
      if (nextSource.source === 'key_level') {
        nextSource.period = 'M1'
        nextSource.params.proximity_threshold = Number(
          nextSource.params.order_distance || nextSource.params.proximity_threshold || 0
        )
      }
      if (nextSource.source === 'pivot') {
        serializePivotPercentParams(nextSource.params)
      }
      const sources = ensureSignalSources(signalSourceTarget.value)
      if (signalSourceEditMode.value === 'edit') {
        const index = sources.findIndex(
          item => item.signal_source_id === editingSignalSourceId.value
        )
        if (index >= 0) sources.splice(index, 1, nextSource)
      } else {
        sources.push(nextSource)
      }
      signalSourceDialog.value = false
    }

    const removeSignalSource = (strategy, source) => {
      strategy.signal_sources = ensureSignalSources(strategy).filter(
        item => item.signal_source_id !== source.signal_source_id
      )
    }

    // 删除策略
    const deleteStrategy = async (strategy) => {
      if (!confirm(`确定要删除“${strategy.strategy_name}”策略吗？`)) return
      try {
        const data = await marketAPI.deleteStrategy(strategy.strategy_id)
        if (data.status === 'ok') {
          successMessage.value = '策略已删除'
          showSuccess.value = true
          if (selectedStrategy.value?.strategy_id === strategy.strategy_id) {
            selectedStrategy.value = null
            selectedStrategySnapshot.value = ''
          }
          await loadStrategies()
        } else {
          errorMessage.value = data.message || '删除失败'
          showError.value = true
        }
      } catch (err) {
        errorMessage.value = `删除策略失败: ${err.message}`
        showError.value = true
      }
    }

    const openNewStrategyDialog = async () => {
      newStrategyDialog.value = true
      const tasks = []
      if (!strategySymbolOptions.value.length) tasks.push((async () => {
        strategySymbolsLoading.value = true
        try { await loadSymbols() } catch (err) {
          errorMessage.value = err.response?.data?.detail || err.message || '加载交易品种失败'
          showError.value = true
        } finally { strategySymbolsLoading.value = false }
      })())
      if (!positionPolicies.value.length && !positionPoliciesLoading.value) {
        tasks.push(loadPositionPolicies().catch(() => {}))
      }
      await Promise.all(tasks)
    }

    const addStrategy = async () => {
      if (!newStrategySymbol.value || !newStrategyPolicyId.value) {
        errorMessage.value = '请先选择品种和持仓管理方案'
        showError.value = true
        return
      }
      strategySaving.value = 'new'
      try {
        const data = await marketAPI.createStrategy({
          symbol: newStrategySymbol.value,
          enabled: false,
          signal_sources: [],
          visibility: 'private',
          position_management_policy_id: newStrategyPolicyId.value,
          strategy_name: newStrategyName.value || `Strategy_${newStrategySymbol.value}`
        })
        if (data.status === 'ok') {
          successMessage.value = '策略已添加'
          showSuccess.value = true
          const createdId = data.strategy?.strategy_id
          newStrategyDialog.value = false
          newStrategySymbol.value = ''
          newStrategyName.value = ''
          await loadStrategies()
          const created = strategies.value.find(strategy => strategy.strategy_id === createdId)
          if (created) openStrategyDetail(created)
        } else {
          errorMessage.value = data.message || '添加失败'
          showError.value = true
        }
      } catch (err) {
        errorMessage.value = `添加策略失败: ${err.message}`
        showError.value = true
      } finally {
        strategySaving.value = null
      }
    }

    onMounted(async () => {
      if (isStrategyPage.value) {
        // 品种仅在新建策略弹窗打开时加载；交易配置属于系统页，
        // 策略工作区不需要在首屏请求它。
        loadStrategies()
        return
      }
      await loadCurrentUser()
      if (isAdmin.value) {
        loadAdminWorkspace()
      } else {
        loadLLMAccess()
        loadLLMFreeQuota()
        loadMyQuota()
      }
    })

    // 管理员工作区的批量初始化是异步的；如果用户立即点击“用户与会员”，
    // 直接补发该页请求，避免因其它运营接口较慢而看到空白表格。
    watch(settingsTab, tab => {
      if (tab === 'quota' && isAdmin.value && !quotaUsers.value.length && !quotaLoading.value) {
        loadUserQuotas()
      }
    })

    return {
      isStrategyPage,
      pageTitle,
      settingsTab,
      llmWorkspaceTab,
      structureEngineConfig,
      structureEngineSaving,
      saveStructureEngineConfig,
      structureProfiles,
      structureProfileDraft,
      saveStructureProfile,
      removeStructureProfile,
      tradeConfig,
      newSymbol,
      newVolume,
      newSlOffset,
      newKeyLevels,
      newKeyLevelThreshold,
      availableSymbols,
      symbols,
      showError,
      errorMessage,
      showSuccess,
      successMessage,
      currentUser,
      isAdmin,
      roleLabel,
      membershipOptions,
      membershipLabel,
      membershipColor,
      liveEligibleLevel,
      emailConfig,
      showEmailPassword,
      emailSaving,
      emailTesting,
      saveEmailConfig,
      testEmailConfig,
      instrumentMappings,
      instrumentObservations,
      instrumentPriceObservations,
      instrumentMappingForm,
      instrumentMappingSaving,
      loadInstrumentObservations,
      loadInstrumentPriceObservations,
      formatInstrumentPrice,
      useInstrumentPriceObservation,
      useInstrumentObservation,
      saveInstrumentMapping,
      deleteInstrumentMapping,
      quotaUsers,
      quotaLoading,
      quotaError,
      quotaSaving,
      saveUserQuota,
      viewAsUser,
      invitations,
      invitationForm,
      invitationSaving,
      latestInviteLink,
      createInvitation,
      copyLatestInvite,
      setInvitationActive,
      formatInvitationTime,
      myQuota,
      loadAdminWorkspace,
      adminStrategies,
      adminStrategiesLoading,
      adminStrategySaving,
      adminStrategySearch,
      adminStrategyLifecycleFilter,
      adminLifecycleOptions,
      filteredAdminStrategies,
      loadAdminStrategies,
      adminPromoteStrategy,
      saveTradeConfig,
      addSymbolConfig,
      removeSymbolConfig,
      onSymbolSelect,
      // 大模型配置
      llmConfig,
      showApiKey,
      llmSaving,
      llmModelsSyncing,
      llmGovernance,
      enabledLLMModelIds,
      saveLLMConfig,
      syncLLMModels,
      toggleLLMModel,
      saveLLMScene,
      selectLLMProvider,
      newLLMProvider,
      activateLLMProvider,
      llmAccess,
      llmFreeQuota,
      llmAccessLabel,
      llmAccessColor,
      llmAccessDescription,
      llmAccessRequesting,
      llmAccessRequests,
      llmRequestsLoading,
      llmReviewingId,
      requestLLMAccess,
      loadLLMAccessRequests,
      reviewLLMRequest,
      formatTimestamp,
      formatStrategyTime,
      // 策略配置
      strategies,
      strategyQuota,
      strategiesLoading,
      strategiesError,
      strategyPage,
      strategyPageSize,
      strategyTotal,
      strategyPageCount,
      strategySaving,
      strategyLifecycleSaving,
      strategyAdmissions,
      strategyWorkspaceTab,
      strategyDetailTab,
      selectedStrategy,
      newStrategyDialog,
      strategySymbolsLoading,
      strategySearch,
      strategyLifecycleFilter,
      strategyVisibilityFilter,
      lifecycleFilterOptions,
      visibilityFilterOptions,
      strategyMetrics,
      filteredStrategies,
      hasStrategyChanges,
      sharedStrategies,
      sharedStrategiesLoading,
      sharedStrategyCopying,
      sharedStrategyTargetSymbols,
      sharedStrategyKey,
      isSharedStrategyUsed,
      paperDeployDialog,
      paperDeployLoading,
      paperDeploySubmitting,
      paperDeployAccountId,
      paperAccounts,
      paperAccountOptions,
      openPaperDeployDialog,
      deploySelectedStrategyToPaper,
      liveDeployDialog,
      liveDeployLoading,
      liveDeploySubmitting,
      liveDeployAccountId,
      liveAccounts,
      liveAccountOptions,
      openLiveDeployDialog,
      deploySelectedStrategyToLive,
      newStrategySymbol,
      newStrategyName,
      consistencyOptions,
      positionPolicies,
      positionPoliciesLoading,
      positionPoliciesError,
      positionPolicyOptions,
      newStrategyPolicyId,
      strategySymbolOptions,
      loadStrategies,
      updateStrategy,
      saveSelectedStrategy,
      copyStrategy,
      openStrategyDetail,
      closeStrategyDetail,
      deleteStrategy,
      addStrategy,
      openNewStrategyDialog,
      getLifecycleMeta,
      getLifecycleColor,
      getLifecycleActions,
      getConsistencyLabel,
      signalSourceLabel,
      signalSourceCount,
      signalSourceSummary,
      strategySignalSources,
      selectedStrategySignalSources,
      strategySourceBadges,
      getAdmission,
      admissionStages,
      isLifecycleActionDisabled,
      transitionStrategyLifecycle,
      loadSharedStrategies,
      useSharedStrategy,
      // 信号配置
      signalSourceDialog,
      signalSourceDialogLoading,
      signalSourceEditMode,
      newSignalSource,
      signalSourceMeta,
      sourceMetaFor,
      signalSourceTypeOptions,
      signalSourceDisabledReason,
      keyLevelModeOptions,
      movingAverageTypeOptions,
      pivotSignalTypeOptions,
      periodMinutes,
      aiIntervalOptionsFor,
      aiSignalOptions,
      aiSignalOptionsLoading,
      signalPeriods,
      managedAISignalSourceOptions,
      sharedAIRuntimeOptions,
      alphaLibraryOptions,
      selectedSharedAIRuntimeData,
      canSaveSignalSource,
      lifecycleLabel,
      formatSimilarity,
      formatSharedSignalParams,
      formatSharedRuntimeResult,
      ensureSignalSources,
      periodOptionsFor,
      availablePeriodsForNewSource,
      selectFirstAvailablePeriod,
      onNewSignalSourceTypeChange,
      onManagedAISignalSourceSelected,
      onSharedRuntimeSelected,
      onAlphaSelected,
      openSignalSourceDialog,
      saveSignalSourceFromDialog,
      removeSignalSource
    }
  }
}
</script>

<style scoped>
.settings-workspace { --settings-ink: #18342b; --settings-muted: #6c7f77; --settings-line: #dfe9e4; --settings-green: #176b4d; }
.settings-hero { position: relative; display: flex; align-items: center; justify-content: space-between; gap: 28px; min-height: 192px; padding: 36px 38px; overflow: hidden; border-radius: 24px; color: #f7fff9; background: linear-gradient(120deg, #123b31 0%, #176b4d 60%, #d9a441 165%); box-shadow: 0 18px 45px rgba(26, 76, 59, .18); }
.settings-hero--admin { background: linear-gradient(120deg, #172f3d 0%, #236a68 57%, #d6a33e 165%); }
.settings-hero::after { position: absolute; right: -54px; bottom: -112px; width: 310px; height: 310px; border: 56px solid rgba(255,255,255,.08); border-radius: 50%; content: ''; }
.settings-hero>div { z-index: 1; }.settings-hero h2 { max-width: 720px; margin: 4px 0 8px; font-family: Georgia, "Noto Serif SC", serif; font-size: clamp(1.65rem, 3vw, 2.35rem); line-height: 1.15; }.settings-hero p { max-width: 690px; margin: 0; color: rgba(247,255,249,.76); }
.settings-hero__identity { display: flex; align-items: center; gap: 9px; margin-top: 17px; color: rgba(255,255,255,.9); font-size: .8rem; font-weight: 700; }.settings-hero .v-btn { z-index: 1; font-weight: 700; }
.settings-metrics { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 14px; margin: 22px 0 10px; }.settings-metrics article { position: relative; min-height: 123px; padding: 19px 21px; overflow: hidden; border: 1px solid var(--settings-line); border-radius: 17px; background: linear-gradient(145deg, #fff, #f7fbf9); }.settings-metrics span,.settings-metrics small { display: block; color: var(--settings-muted); }.settings-metrics span { font-size: .78rem; }.settings-metrics strong { display: block; margin-top: 4px; color: var(--settings-ink); font-family: Georgia, "Noto Serif SC", serif; font-size: clamp(1.25rem, 2.1vw, 1.8rem); line-height: 1.08; }.settings-metrics small { max-width: 85%; margin-top: 7px; font-size: .67rem; line-height: 1.35; }.settings-metrics .v-icon { position: absolute; right: 17px; bottom: 16px; color: #b9d4c8; }
.settings-main-tabs { margin-top: 22px; border-bottom: 1px solid var(--settings-line); background: rgba(255,255,255,.68); border-radius: 14px 14px 0 0; }.llm-workspace-tabs { margin: -2px -6px 22px; border-bottom: 1px solid var(--settings-line); }
	.user-settings-card { overflow: hidden; border: 1px solid var(--settings-line); border-radius: 19px !important; background: #fff; }.user-settings-card :deep(.v-card-text) { padding: 22px 24px 26px; }.settings-card-title { display: flex; align-items: center; justify-content: space-between; gap: 16px; min-height: 68px; padding: 18px 24px !important; border-bottom: 1px solid var(--settings-line); color: var(--settings-ink); }.settings-card-title>div { display: flex; align-items: center; gap: 10px; font-size: 1rem; font-weight: 700; }.settings-card-title>div .v-icon { color: var(--settings-green); }.settings-card-title>small { color: var(--settings-muted); font-size: .72rem; font-weight: 400; }.admin-service-card { border-color: #c8ddd4; background: linear-gradient(145deg, #fff 0%, #f7fcfa 100%); }.account-summary { padding: 8px; border: 1px solid #e2ece7; border-radius: 13px; background: #f9fcfa; }.quota-table :deep(th) { color: var(--settings-muted); font-size: .72rem; font-weight: 700; letter-spacing: .04em; white-space: nowrap; }.quota-table :deep(td) { padding-top: 12px; padding-bottom: 12px; }.quota-table small { display: block; margin-top: 3px; color: var(--settings-muted); font-size: .7rem; }
	.strategy-governance-toolbar { display: grid; grid-template-columns: minmax(260px, 1fr) 220px; gap: 12px; align-items: center; }.admin-strategy-table { min-width: 980px; }.admin-strategy-table :deep(.v-table__wrapper) { overflow-x: auto; }
	.invitation-form { display: grid; grid-template-columns: minmax(220px, 1fr) 140px 140px auto; gap: 12px; align-items: start; }.invite-result { display: flex; align-items: center; justify-content: space-between; gap: 16px; padding: 14px 16px; border: 1px solid #b9ddca; border-radius: 13px; background: #eef9f3; }.invite-result div { min-width: 0; }.invite-result small,.invite-result strong { display: block; }.invite-result strong { overflow-wrap: anywhere; color: #176b4d; }
.admission-panel { padding: 16px; border: 1px solid #dbe7e1; border-radius: 14px; background: linear-gradient(135deg, #f5f9f6, #fffaf0); }
.admission-title { display: flex; align-items: center; justify-content: space-between; gap: 16px; }
.admission-title strong,.admission-title span { display: block; }.admission-title span { margin-top: 2px; color: #7a8982; font-size: .72rem; }
.admission-stages { display: grid; grid-template-columns: 1fr 1fr; gap: 10px; margin-top: 12px; }
.admission-stages article { padding: 12px; border-radius: 10px; background: rgba(255,255,255,.85); }
.admission-stages article>div:first-child { display: flex; align-items: center; gap: 7px; }.admission-stages p { margin: 6px 0; color: #718079; font-size: .72rem; }
.admission-checks { display: flex; flex-wrap: wrap; gap: 5px; }
.llm-section-head { display: flex; align-items: center; justify-content: space-between; gap: 16px; margin-bottom: 14px; }
.llm-section-head.compact { margin: 18px 0 4px; }
.provider-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(260px, 1fr)); gap: 12px; }
.provider-card { padding: 15px; border: 1px solid #dce7e2; border-radius: 14px; background: #fbfdfc; transition: border-color .18s ease, background .18s ease, box-shadow .18s ease; }
.provider-card.active { border-color: #6fbd92; background: linear-gradient(135deg, #ecfbf2 0%, #fffaf0 100%); box-shadow: 0 10px 24px rgba(37, 112, 77, .08); }
.provider-card p { margin: 6px 0 0; color: #61756c; font-size: .74rem; word-break: break-all; }
.prompt-profile-head { display: flex; align-items: center; justify-content: space-between; gap: 16px; }.prompt-profile-head strong,.prompt-profile-head p { display: block; }.prompt-profile-head p { margin: 3px 0 0; color: var(--settings-muted); font-size: .74rem; }.prompt-profile-card { margin-top: 12px; padding: 16px; border: 1px solid #dce7e2; border-radius: 14px; background: #fbfdfc; }.prompt-profile-card.is-default { border-color: #6fbd92; background: linear-gradient(135deg, #effbf4 0%, #fffdf6 100%); }.prompt-profile-name { max-width: 250px; }
.prompt-template-editor :deep(textarea) { font-family: "IBM Plex Mono", "SFMono-Regular", Consolas, monospace; font-size: .78rem; line-height: 1.55; }
.strategy-toggle-alert { transition: background-color .18s ease, border-color .18s ease; }
.strategy-toggle-alert--active { background: linear-gradient(135deg, #e5f7ec 0%, #f2fff6 100%) !important; border-color: #68c58c !important; }
.key-level-trigger-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); gap: 4px 14px; }
.signal-source-type-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(170px, 1fr)); gap: 10px; }
.signal-source-type-card { display: flex; flex-direction: column; align-items: flex-start; gap: 6px; min-height: 92px; padding: 14px; border: 1px solid #dce7e2; border-radius: 14px; background: #fbfdfc; text-align: left; cursor: pointer; transition: border-color .18s ease, background .18s ease, transform .18s ease; }
.signal-source-type-card:hover:not(:disabled) { border-color: #5fa981; background: #f2fff7; transform: translateY(-1px); }
.signal-source-type-card--active { border-color: #35a66a; background: linear-gradient(135deg, #e7f8ee 0%, #fffaf0 100%); }
.signal-source-type-card--disabled { opacity: .52; cursor: not-allowed; }
.signal-source-type-card span { font-weight: 600; color: #26352d; }
.signal-source-type-card small { color: #8a5d38; }
.strategy-workspace { --strategy-ink: #18342b; --strategy-muted: #6c7f77; --strategy-line: #dfe9e4; --strategy-green: #176b4d; margin-top: 0; }
.strategy-hero { position: relative; display: flex; align-items: center; justify-content: space-between; gap: 28px; min-height: 180px; padding: 34px 38px; overflow: hidden; border-radius: 24px; color: #f7fff9; background: linear-gradient(120deg, #123b31 0%, #176b4d 58%, #d9a441 160%); box-shadow: 0 18px 45px rgba(26, 76, 59, .18); }
.strategy-hero::after { position: absolute; right: -55px; bottom: -110px; width: 300px; height: 300px; border: 55px solid rgba(255,255,255,.08); border-radius: 50%; content: ''; }
.strategy-hero h2,.strategy-detail-head h2 { margin: 4px 0 8px; font-family: Georgia, "Noto Serif SC", serif; font-size: clamp(1.65rem, 3vw, 2.35rem); line-height: 1.15; }
.strategy-hero p { max-width: 650px; margin: 0; color: rgba(247,255,249,.76); }
.strategy-eyebrow { color: #d9b968; font-size: .7rem; font-weight: 800; letter-spacing: .16em; }
.strategy-primary-action { z-index: 1; background: #f6c657 !important; color: #263a31 !important; font-weight: 700; }
.strategy-quick-action { z-index: 1; min-height: 44px; padding: 0 16px !important; border: 1px solid rgba(201, 244, 220, .72); border-radius: 12px !important; color: #f7fff9 !important; background: linear-gradient(135deg, #16865e, #0d6548) !important; box-shadow: 0 8px 18px rgba(4, 48, 33, .24); font-weight: 800; letter-spacing: .01em; }
.strategy-quick-action:hover { border-color: #e1ffec; background: linear-gradient(135deg, #1c9b6b, #0f7855) !important; transform: translateY(-1px); }
.strategy-quick-action__badge { margin-left: 7px; color: #145a40 !important; background: #e5f9ed !important; font-size: .62rem; font-weight: 800; }
.strategy-main-tabs { margin-top: 22px; border-bottom: 1px solid var(--strategy-line); }
.strategy-metrics { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 14px; margin: 22px 0 16px; }
.strategy-metrics article { position: relative; min-height: 106px; padding: 19px 21px; overflow: hidden; border: 1px solid var(--strategy-line); border-radius: 17px; background: #fff; }
.strategy-metrics span { display: block; color: var(--strategy-muted); font-size: .78rem; }
.strategy-metrics strong { display: block; margin-top: 3px; color: var(--strategy-ink); font-family: Georgia, serif; font-size: 2rem; }
.strategy-metrics .v-icon { position: absolute; right: 17px; bottom: 16px; color: #b9d4c8; }
.strategy-list-shell,.strategy-detail-shell { border: 1px solid var(--strategy-line); border-radius: 19px !important; background: #fff; }
.strategy-toolbar { display: grid; grid-template-columns: minmax(240px, 1.5fr) repeat(3, minmax(145px, .75fr)) auto; gap: 10px; padding: 16px; border-bottom: 1px solid var(--strategy-line); }
.strategy-table :deep(th) { height: 48px; color: var(--strategy-muted); font-size: .72rem; font-weight: 700; letter-spacing: .04em; white-space: nowrap; }
.strategy-table :deep(td) { height: 74px; color: #334940; }
.strategy-table tbody tr { cursor: pointer; transition: background .16s ease; }
.strategy-table tbody tr:hover { background: #f4faf7; }
.strategy-name-cell { display: flex; align-items: center; gap: 12px; min-width: 190px; }
.strategy-name-cell strong,.strategy-name-cell small { display: block; }
.strategy-name-cell strong { color: var(--strategy-ink); }
.strategy-name-cell small { margin-top: 2px; color: #91a098; font-size: .68rem; }
.strategy-symbol { display: inline-flex; align-items: center; justify-content: center; min-width: 48px; min-height: 28px; padding: 0 8px; border-radius: 8px; background: #e8f3ee; color: var(--strategy-green); font-size: .7rem; font-weight: 800; letter-spacing: .04em; }
.source-pill-row { display: flex; flex-wrap: wrap; gap: 5px; max-width: 260px; }
.status-dot { display: inline-block; width: 8px; height: 8px; margin-right: 7px; border-radius: 50%; background: #aeb9b4; }
.status-dot.is-active { background: #25a767; box-shadow: 0 0 0 4px rgba(37,167,103,.12); }
.strategy-empty { display: flex; min-height: 270px; flex-direction: column; align-items: center; justify-content: center; color: #8b9c94; text-align: center; }
.strategy-empty h3 { margin: 12px 0 3px; color: var(--strategy-ink); }
.strategy-empty p { margin: 0 0 14px; }
.strategy-empty.compact { min-height: 250px; border: 1px dashed #cddcd5; border-radius: 16px; background: #fbfdfc; }
.strategy-mobile-list { padding: 12px; }
.strategy-mobile-list article { padding: 16px; border-bottom: 1px solid var(--strategy-line); }
.shared-library-head { display: flex; align-items: center; justify-content: space-between; margin: 24px 0 16px; }
.shared-library-head h3,.shared-library-head p { margin: 0; }.shared-library-head p { margin-top: 4px; color: var(--strategy-muted); }
.shared-strategy-list { display: grid; grid-template-columns: repeat(auto-fit, minmax(310px, 1fr)); gap: 16px; }
.shared-strategy-card { display: flex; min-height: 230px; padding: 20px; flex-direction: column; border: 1px solid var(--strategy-line); border-radius: 17px; background: linear-gradient(145deg, #fff, #f7fbf9); transition: transform .18s ease, box-shadow .18s ease; }
.shared-strategy-card:hover { transform: translateY(-2px); box-shadow: 0 12px 30px rgba(37,72,59,.1); }
.shared-strategy-card__head { display: flex; align-items: flex-start; justify-content: space-between; gap: 12px; }
.shared-strategy-card__head h3 { margin: 12px 0 2px; color: var(--strategy-ink); }.shared-strategy-card__head p { margin: 0; color: var(--strategy-muted); font-size: .75rem; }
.shared-strategy-card__body { display: flex; flex-wrap: wrap; gap: 6px; margin-top: 18px; }
.shared-card-footer { display: flex; align-items: center; justify-content: space-between; gap: 12px; margin-top: auto; padding-top: 20px; color: var(--strategy-muted); font-size: .72rem; }
.strategy-detail-head { display: flex; align-items: center; justify-content: space-between; gap: 20px; margin-bottom: 18px; padding: 8px 2px; }
.strategy-detail-head h2 { margin: 3px 0 0; color: var(--strategy-ink); font-size: 1.85rem; }
.strategy-detail-tabs { padding: 0 12px; }
.strategy-detail-content { padding: 30px; min-height: 470px; }
.detail-section-title { display: flex; align-items: center; justify-content: space-between; gap: 20px; margin-bottom: 24px; }
.detail-section-title h3,.detail-section-title p { margin: 0; }.detail-section-title h3 { color: var(--strategy-ink); font-size: 1.05rem; }.detail-section-title p { margin-top: 4px; color: var(--strategy-muted); font-size: .78rem; }
.strategy-setting-card { display: flex; align-items: center; justify-content: space-between; gap: 18px; margin-top: 14px; padding: 16px 18px; border: 1px solid var(--strategy-line); border-radius: 14px; background: #fbfdfc; transition: background .18s ease, border-color .18s ease; }
.strategy-setting-card.is-active { border-color: #79bd99; background: #effaf4; }
.strategy-setting-card>div:first-child { display: flex; align-items: center; gap: 14px; }.strategy-setting-card strong,.strategy-setting-card p { display: block; margin: 0; }.strategy-setting-card p { margin-top: 2px; color: var(--strategy-muted); font-size: .74rem; }
.lifecycle-banner { display: flex; align-items: center; justify-content: space-between; gap: 24px; padding: 24px; border-radius: 16px; color: #f6fff9; background: linear-gradient(120deg, #183f34, #236b52); }.lifecycle-banner span { color: #b8d9ca; font-size: .72rem; }.lifecycle-banner h3 { margin: 3px 0; font-size: 1.4rem; }.lifecycle-banner p { margin: 0; color: #cfe2da; font-size: .78rem; }
.danger-zone { display: flex; align-items: center; justify-content: space-between; gap: 20px; margin-top: 26px; padding: 18px; border: 1px solid #f1d1ca; border-radius: 14px; background: #fff8f6; }.danger-zone strong,.danger-zone p { margin: 0; }.danger-zone p { margin-top: 3px; color: #8e7771; font-size: .74rem; }
.new-strategy-title { display: flex; gap: 13px; padding: 22px 24px 8px; }.new-strategy-title strong,.new-strategy-title span { display: block; }.new-strategy-title span { margin-top: 2px; color: #6c7f77; font-size: .72rem; font-weight: 400; }
.signal-source-heading { display: flex; align-items: center; justify-content: space-between; gap: 16px; }
.signal-source-list { display: grid; gap: 12px; }
.signal-source-card { padding: 16px; border: 1px solid #dce7e2; border-radius: 14px; background: linear-gradient(135deg, #fbfdfc 0%, #fffaf3 100%); }
.signal-source-card__head { display: flex; align-items: center; justify-content: space-between; gap: 12px; margin-bottom: 14px; }
.shared-ai-runtime-list { display: grid; gap: 10px; }
.shared-ai-runtime-list article { padding: 14px; border: 1px solid #d8e6df; border-radius: 13px; background: linear-gradient(135deg, #f6fbf8, #fffaf1); }
.shared-ai-runtime-list p { margin: 5px 0 0; color: #61756c; font-size: .76rem; }
.shared-ai-runtime-list summary { margin-top: 9px; color: #176b4d; font-size: .76rem; font-weight: 700; cursor: pointer; }
.shared-prompt-preview { max-height: 130px; margin-top: 8px; padding: 9px; overflow: auto; border-radius: 8px; background: rgba(255,255,255,.82); color: #53675e; font-family: "IBM Plex Mono", "SFMono-Regular", Consolas, monospace; font-size: .7rem; white-space: pre-wrap; }
@media (max-width: 960px) { .strategy-metrics,.settings-metrics { grid-template-columns: 1fr 1fr; }.strategy-toolbar { grid-template-columns: 1fr 1fr; }.strategy-toolbar>*:first-child { grid-column: 1 / -1; } }
	@media (max-width: 700px) { .admission-stages { grid-template-columns: 1fr; }.strategy-hero,.settings-hero,.strategy-detail-head,.detail-section-title,.lifecycle-banner,.danger-zone { align-items: flex-start; flex-direction: column; }.strategy-hero,.settings-hero { padding: 26px 22px; }.strategy-primary-action,.strategy-quick-action,.settings-hero .v-btn { width: 100%; }.strategy-metrics,.settings-metrics { grid-template-columns: 1fr 1fr; gap: 9px; }.strategy-metrics article,.settings-metrics article { min-height: 104px; padding: 15px; }.settings-metrics strong { font-size: 1.28rem; }.settings-card-title { align-items: flex-start; flex-direction: column; }.user-settings-card :deep(.v-card-text) { padding: 18px 16px 22px; }.quota-table { min-width: 860px; }.quota-table :deep(.v-table__wrapper) { overflow-x: auto; }.strategy-governance-toolbar { grid-template-columns: 1fr; }.invitation-form { grid-template-columns: 1fr; }.invite-result { align-items: flex-start; flex-direction: column; }.strategy-toolbar { grid-template-columns: 1fr; }.strategy-toolbar>*:first-child { grid-column: auto; }.strategy-detail-content { padding: 20px 16px; }.strategy-detail-head>div:last-child { width: 100%; }.strategy-detail-head>div:last-child .v-btn { flex: 1; }.shared-card-footer { align-items: flex-start; flex-direction: column; } }
</style>

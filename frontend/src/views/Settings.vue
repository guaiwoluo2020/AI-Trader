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
            <small>{{ isAdmin ? '负责新用户注册验证码' : '所有策略下已保存的信号源' }}</small>
            <v-icon>mdi-access-point</v-icon>
          </article>
          <article>
            <span>{{ isAdmin ? '平台 AI 服务' : 'AI 行情分析' }}</span>
            <strong>{{ isAdmin ? (llmConfig.enabled ? '启用' : '停用') : llmAccessLabel }}</strong>
            <small>{{ isAdmin ? '全局大模型服务状态' : llmAccessDescription }}</small>
            <v-icon>mdi-brain</v-icon>
          </article>
        </section>
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
            <v-btn color="primary" size="large" class="strategy-primary-action" @click="newStrategyDialog = true">
              <v-icon start>mdi-plus</v-icon>新建策略
            </v-btn>
          </section>

          <v-tabs v-model="strategyWorkspaceTab" color="primary" class="strategy-main-tabs">
            <v-tab value="mine"><v-icon start>mdi-briefcase-outline</v-icon>我的策略</v-tab>
            <v-tab value="shared" @click="loadSharedStrategies"><v-icon start>mdi-bookshelf</v-icon>平台策略库</v-tab>
          </v-tabs>

          <v-window v-model="strategyWorkspaceTab">
            <v-window-item value="mine">
              <div class="strategy-metrics">
                <article><span>全部策略</span><strong>{{ strategies.length }} / {{ strategyQuota.limits.strategies ?? '∞' }}</strong><v-icon>mdi-layers-triple-outline</v-icon></article>
                <article><span>实盘可用</span><strong>{{ strategyMetrics.production }}</strong><v-icon>mdi-rocket-launch-outline</v-icon></article>
                <article><span>正在启用</span><strong>{{ strategyMetrics.enabled }}</strong><v-icon>mdi-pulse</v-icon></article>
                <article><span>信号源用量</span><strong>{{ strategyQuota.usage.signal_sources }} / {{ strategyQuota.limits.signal_sources ?? '∞' }}</strong><v-icon>mdi-access-point</v-icon></article>
              </div>

              <v-card class="strategy-list-shell" elevation="0">
                <div class="strategy-toolbar">
                  <v-text-field v-model="strategySearch" label="搜索策略或品种" prepend-inner-icon="mdi-magnify" density="compact" hide-details clearable></v-text-field>
                  <v-select v-model="strategyLifecycleFilter" :items="lifecycleFilterOptions" label="生命周期" density="compact" hide-details></v-select>
                  <v-select v-model="strategyEnabledFilter" :items="enabledFilterOptions" label="启用状态" density="compact" hide-details></v-select>
                  <v-select v-model="strategyVisibilityFilter" :items="visibilityFilterOptions" label="可见性" density="compact" hide-details></v-select>
                  <v-btn icon="mdi-refresh" variant="text" :loading="strategiesLoading" @click="loadStrategies"></v-btn>
                </div>

                <v-table v-if="filteredStrategies.length" class="strategy-table hidden-sm-and-down">
                  <thead><tr><th>策略</th><th>信号源</th><th>生命周期</th><th>状态</th><th>可见性</th><th>更新时间</th><th class="text-right">操作</th></tr></thead>
                  <tbody>
                    <tr v-for="strategy in filteredStrategies" :key="strategy.strategy_id" @click="openStrategyDetail(strategy)">
                      <td><div class="strategy-name-cell"><span class="strategy-symbol">{{ strategy.symbol }}</span><div><strong>{{ strategy.strategy_name }}</strong><small>#{{ strategy.strategy_id }}</small></div></div></td>
                      <td><div class="source-pill-row"><v-chip v-for="source in strategySourceBadges(strategy)" :key="source.key" size="x-small" :color="source.color" variant="tonal">{{ source.label }}</v-chip><span v-if="!signalSourceCount(strategy)" class="text-caption text-medium-emphasis">未配置</span></div></td>
                      <td><v-chip :color="getLifecycleMeta(strategy).color" size="small" variant="tonal">{{ getLifecycleMeta(strategy).label }}</v-chip></td>
                      <td><span class="status-dot" :class="strategy.enabled ? 'is-active' : ''"></span>{{ strategy.enabled ? '已启用' : '未启用' }}</td>
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
                  <v-icon size="52">mdi-radar</v-icon><h3>{{ strategies.length ? '没有符合筛选条件的策略' : '还没有策略' }}</h3><p>{{ strategies.length ? '调整筛选条件后再试试。' : '创建第一条策略，开始配置交易信号。' }}</p>
                  <v-btn v-if="!strategies.length" color="primary" variant="tonal" @click="newStrategyDialog = true">新建策略</v-btn>
                </div>
              </v-card>
            </v-window-item>

            <v-window-item value="shared">
              <div class="shared-library-head"><div><h3>平台策略库</h3><p>复制一份经过分享的策略作为私有草稿，再按你的交易账户进行调整。</p></div><v-btn icon="mdi-refresh" variant="text" :loading="sharedStrategiesLoading" @click="loadSharedStrategies"></v-btn></div>
              <div v-if="sharedStrategiesLoading" class="strategy-empty"><v-progress-circular indeterminate color="primary"></v-progress-circular></div>
              <div v-else-if="sharedStrategies.length" class="shared-strategy-list">
                <article v-for="item in sharedStrategies" :key="`${item.owner_user_id}-${item.strategy_id}`" class="shared-strategy-card">
                  <div class="shared-strategy-card__head"><div><span class="strategy-symbol">{{ item.symbol }}</span><h3>{{ item.strategy_name }}</h3><p>由 {{ item.owner_username }} 分享</p></div><v-chip :color="getLifecycleColor(item.lifecycle_status)" size="small" variant="tonal">{{ item.lifecycle_label || getLifecycleMeta(item).label }}</v-chip></div>
                  <div class="shared-strategy-card__body"><v-chip size="x-small" variant="outlined">{{ signalSourceCount(item) }} 个信号源</v-chip><v-chip size="x-small" variant="outlined">置信度 {{ item.min_confidence }}%</v-chip><v-chip size="x-small" variant="outlined">{{ getConsistencyLabel(item.consistency_requirement) }}</v-chip></div>
                  <div class="shared-card-footer"><span>更新于 {{ formatStrategyTime(item.updated_at) }}</span><v-btn color="primary" size="small" :loading="sharedStrategyCopying === `${item.owner_user_id}-${item.strategy_id}`" @click="copySharedStrategy(item)"><v-icon start>mdi-content-copy</v-icon>复制并修改</v-btn></div>
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
              <v-btn color="primary" :loading="strategySaving === selectedStrategy.strategy_id" :disabled="!hasStrategyChanges" @click="saveSelectedStrategy"><v-icon start>mdi-content-save-outline</v-icon>保存修改</v-btn>
            </div>
          </section>

          <v-card class="strategy-detail-shell" elevation="0">
            <v-tabs v-model="strategyDetailTab" color="primary" class="strategy-detail-tabs">
              <v-tab value="overview">概览</v-tab><v-tab value="signals">信号源 <v-chip size="x-small" class="ml-2">{{ signalSourceCount(selectedStrategy) }}</v-chip></v-tab><v-tab value="risk">仓位与风控</v-tab><v-tab value="lifecycle">验证与生命周期</v-tab>
            </v-tabs>
            <v-divider></v-divider>
            <v-window v-model="strategyDetailTab" class="strategy-detail-content">
              <v-window-item value="overview">
                <div class="detail-section-title"><div><h3>策略基础信息</h3><p>这些信息用于识别策略并控制多信号源如何共同决策。</p></div></div>
                <v-row><v-col cols="12" md="6"><v-text-field v-model="selectedStrategy.strategy_name" label="策略名称"></v-text-field></v-col><v-col cols="12" md="3"><v-text-field :model-value="selectedStrategy.symbol" label="交易品种" readonly></v-text-field></v-col><v-col cols="12" md="3"><v-text-field v-model.number="selectedStrategy.min_confidence" label="最低置信度" type="number" min="0" max="100" suffix="%"></v-text-field></v-col><v-col cols="12" md="6"><v-select v-model="selectedStrategy.consistency_requirement" :items="consistencyOptions" label="一致性要求"></v-select></v-col></v-row>
                <div class="strategy-setting-card" :class="{ 'is-active': selectedStrategy.is_shared }"><div><v-icon>mdi-share-variant-outline</v-icon><div><strong>共享到平台策略库</strong><p>其他用户只能复制副本，无法修改你的原策略。</p></div></div><v-switch v-model="selectedStrategy.is_shared" color="success" hide-details></v-switch></div>
                <div class="strategy-setting-card" :class="{ 'is-active': selectedStrategy.enabled }"><div><v-icon>mdi-power</v-icon><div><strong>启用策略</strong><p>{{ selectedStrategy.lifecycle_status === 'production' ? '启用后参与当前账户的信号决策。' : '策略完成验证并进入实盘阶段后才能启用。' }}</p></div></div><v-switch v-model="selectedStrategy.enabled" color="success" hide-details :disabled="selectedStrategy.lifecycle_status !== 'production'"></v-switch></div>
              </v-window-item>

              <v-window-item value="signals">
                <div class="detail-section-title"><div><h3>信号源配置</h3><p>关键点位与 AI/均线互斥；AI 和均线可以按不同周期组合。</p></div><v-btn color="primary" variant="tonal" @click="openSignalSourceDialog(selectedStrategy)"><v-icon start>mdi-plus</v-icon>添加信号源</v-btn></div>
                <div v-if="strategySignalSources(selectedStrategy).length" class="signal-source-list">
                  <article v-for="source in strategySignalSources(selectedStrategy)" :key="source.signal_source_id" class="signal-source-card">
                    <div class="signal-source-card__head"><div class="d-flex align-center flex-wrap ga-2"><v-avatar size="34" color="grey-lighten-4"><v-icon :color="signalSourceMeta[source.source].color" size="19">{{ signalSourceMeta[source.source].icon }}</v-icon></v-avatar><div><strong>{{ signalSourceMeta[source.source].label }}</strong><div class="text-caption text-medium-emphasis">{{ source.source === 'key_level' ? '全周期共用' : source.period }}</div></div></div><div class="d-flex align-center"><v-switch v-model="source.enabled" color="success" density="compact" hide-details></v-switch><v-btn icon="mdi-pencil-outline" size="small" variant="text" color="primary" @click="openSignalSourceDialog(selectedStrategy, source)"></v-btn><v-btn icon="mdi-delete-outline" size="small" variant="text" color="error" @click="removeSignalSource(selectedStrategy, source)"></v-btn></div></div>
                    <div class="signal-source-summary"><v-chip size="x-small" variant="outlined">权重 {{ source.weight }}</v-chip><span class="text-caption text-medium-emphasis">{{ signalSourceSummary(source) }}</span></div>
                  </article>
                </div>
                <div v-else class="strategy-empty compact"><v-icon size="44">mdi-access-point-plus</v-icon><h3>还没有信号源</h3><p>添加关键点位、AI 入场、均线交叉或已验证 Alpha。</p></div>
              </v-window-item>

              <v-window-item value="risk">
                <div class="detail-section-title"><div><h3>仓位与风险约束</h3><p>控制每次交易的规模，以及策略能够同时持有的仓位。</p></div></div>
                <v-row><v-col cols="12" sm="6" md="3"><v-text-field v-model.number="selectedStrategy.fixed_volume" label="固定手数" type="number" step="0.01" min="0.01"></v-text-field></v-col><v-col cols="12" sm="6" md="3"><v-text-field v-model.number="selectedStrategy.max_positions" label="最大持仓数" type="number" min="1"></v-text-field></v-col><v-col cols="12" sm="6" md="3"><v-text-field v-model.number="selectedStrategy.max_same_direction" label="同向最大持仓" type="number" min="1"></v-text-field></v-col><v-col cols="12" sm="6" md="3"><v-text-field v-model.number="selectedStrategy.risk_percent" label="单笔风险比例" type="number" min="0.1" step="0.1" suffix="%"></v-text-field></v-col></v-row>
                <div class="detail-section-title mt-5"><div><h3>持仓管理方案</h3><p>开仓后的止损、止盈和移动保护由独立持仓管理器执行。</p></div><v-btn to="/position-management" variant="text" color="primary" prepend-icon="mdi-shield-edit-outline">管理方案</v-btn></div>
                <v-select v-model="selectedStrategy.position_management_policy_id" :items="positionPolicyOptions" label="选择持仓管理方案" max-width="620"></v-select>
              </v-window-item>

              <v-window-item value="lifecycle">
                <div class="lifecycle-banner"><div><span>当前阶段</span><h3>{{ getLifecycleMeta(selectedStrategy).label }}</h3><p>{{ getLifecycleMeta(selectedStrategy).description }}</p></div><div class="d-flex flex-wrap ga-2"><v-btn v-for="action in getLifecycleActions(selectedStrategy)" :key="action.target" :color="action.color" variant="outlined" :disabled="isLifecycleActionDisabled(selectedStrategy, action)" :loading="strategyLifecycleSaving === selectedStrategy.strategy_id" @click="transitionStrategyLifecycle(selectedStrategy, action)"><v-icon start>{{ action.icon }}</v-icon>{{ action.label }}</v-btn></div></div>
                <div v-if="getAdmission(selectedStrategy)" class="admission-panel mt-5"><div class="admission-title"><div><strong>策略准入证据</strong><span>只认可当前参数版本产生的验证结果</span></div><v-chip size="small" :color="getAdmission(selectedStrategy).eligible_for_production ? 'success' : 'warning'" variant="tonal">{{ getAdmission(selectedStrategy).eligible_for_production ? '满足实盘准入' : '验证进行中' }}</v-chip></div><div class="admission-stages"><article v-for="stage in admissionStages(selectedStrategy)" :key="stage.key"><div><v-icon size="18" :color="stage.data.passed ? 'success' : 'grey'">{{ stage.data.passed ? 'mdi-check-decagram' : 'mdi-progress-clock' }}</v-icon><strong>{{ stage.label }}</strong></div><p>{{ stage.data.message }}</p><div v-if="stage.data.checks?.length" class="admission-checks"><v-chip v-for="check in stage.data.checks" :key="check.key" size="x-small" :color="check.passed ? 'success' : 'error'" variant="tonal">{{ check.label }}</v-chip></div></article></div></div>
                <div class="danger-zone"><div><strong>删除策略</strong><p>删除后无法恢复；被回测任务引用时后端会阻止删除。</p></div><v-btn color="error" variant="outlined" @click="deleteStrategy(selectedStrategy)"><v-icon start>mdi-delete-outline</v-icon>删除策略</v-btn></div>
              </v-window-item>
            </v-window>
          </v-card>
        </template>
      </v-col>
    </v-row>

    <!-- 账户与安全 -->
    <v-row v-if="!isStrategyPage">
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
                </v-list>
              </v-col>

              <v-col cols="12" md="6">
                <div class="text-subtitle-2 mb-3">修改密码</div>
                <v-form @submit.prevent="changePassword">
                  <v-text-field
                    v-model="passwordForm.current_password"
                    label="当前密码"
                    prepend-inner-icon="mdi-lock-outline"
                    :type="showCurrentPassword ? 'text' : 'password'"
                    :append-inner-icon="showCurrentPassword ? 'mdi-eye-off' : 'mdi-eye'"
                    variant="outlined"
                    density="compact"
                    :disabled="passwordSaving"
                    @click:append-inner="showCurrentPassword = !showCurrentPassword"
                  />
                  <v-text-field
                    v-model="passwordForm.new_password"
                    label="新密码"
                    prepend-inner-icon="mdi-lock-reset"
                    :type="showNewPassword ? 'text' : 'password'"
                    :append-inner-icon="showNewPassword ? 'mdi-eye-off' : 'mdi-eye'"
                    variant="outlined"
                    density="compact"
                    hint="8-128 位，必须同时包含字母和数字"
                    persistent-hint
                    :disabled="passwordSaving"
                    @click:append-inner="showNewPassword = !showNewPassword"
                  />
                  <v-text-field
                    v-model="passwordForm.confirm_password"
                    label="确认新密码"
                    prepend-inner-icon="mdi-lock-check-outline"
                    :type="showConfirmPassword ? 'text' : 'password'"
                    :append-inner-icon="showConfirmPassword ? 'mdi-eye-off' : 'mdi-eye'"
                    variant="outlined"
                    density="compact"
                    :error-messages="passwordMismatch ? '两次输入的新密码不一致' : ''"
                    :disabled="passwordSaving"
                    @click:append-inner="showConfirmPassword = !showConfirmPassword"
                  />
                  <v-btn
                    type="submit"
                    color="primary"
                    :loading="passwordSaving"
                    :disabled="!canChangePassword"
                  >
                    <v-icon start>mdi-lock-reset</v-icon>
                    修改密码
                  </v-btn>
                </v-form>
              </v-col>
            </v-row>
          </v-card-text>
        </v-card>
      </v-col>
    </v-row>

    <!-- 管理员邮件服务配置 -->
    <v-row v-if="!isStrategyPage && isAdmin">
      <v-col cols="12">
        <v-card class="user-settings-card admin-service-card" elevation="0">
          <v-card-title class="settings-card-title d-flex align-center justify-space-between flex-wrap ga-2">
            <div><v-icon class="mr-2">mdi-email-lock-outline</v-icon>注册邮件服务</div>
            <v-chip :color="emailConfig.enabled && emailConfig.password_set ? 'success' : 'warning'" variant="tonal" size="small">
              {{ emailConfig.enabled && emailConfig.password_set ? '已启用' : emailConfig.password_set ? '已停用' : '待配置' }}
            </v-chip>
          </v-card-title>
          <v-card-text>
            <v-alert type="info" variant="tonal" density="compact" class="mb-5">
              用于发送新用户注册验证码。SMTP 密码加密存储且不会回显；留空表示保留现有密码。
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

    <!-- 管理员用户配额白名单 -->
    <v-row v-if="!isStrategyPage && isAdmin">
      <v-col cols="12">
        <v-card class="user-settings-card admin-service-card" elevation="0">
          <v-card-title class="settings-card-title d-flex align-center justify-space-between flex-wrap ga-2">
            <div><v-icon class="mr-2">mdi-account-star-outline</v-icon>用户配额白名单</div>
            <v-chip color="success" variant="tonal" size="small">默认：数据集 10 · 策略 5 · 信号源 10</v-chip>
          </v-card-title>
          <v-card-text>
            <v-alert type="info" variant="tonal" density="compact" class="mb-4">
              留空即使用普通用户默认配额；填写数值后仅覆盖该用户对应项目。管理员不受配额限制。
            </v-alert>
            <v-table density="comfortable" class="quota-table">
              <thead><tr><th>用户</th><th>当前用量</th><th>数据集上限</th><th>策略上限</th><th>信号源上限</th><th></th></tr></thead>
              <tbody>
                <tr v-for="item in quotaUsers" :key="item.user_id">
                  <td><strong>{{ item.username }}</strong><small>{{ item.email || '未绑定邮箱' }}</small></td>
                  <td>
                    <v-chip size="x-small" variant="tonal">数据集 {{ item.usage.datasets }}/{{ item.limits.datasets ?? '∞' }}</v-chip>
                    <v-chip size="x-small" variant="tonal" class="ml-1">策略 {{ item.usage.strategies }}/{{ item.limits.strategies ?? '∞' }}</v-chip>
                    <v-chip size="x-small" variant="tonal" class="ml-1">信号 {{ item.usage.signal_sources }}/{{ item.limits.signal_sources ?? '∞' }}</v-chip>
                  </td>
                  <td><v-text-field v-model="item.quotaDraft.max_datasets" :disabled="item.role === 'admin'" placeholder="默认 10" type="number" min="0" max="1000" density="compact" hide-details /></td>
                  <td><v-text-field v-model="item.quotaDraft.max_strategies" :disabled="item.role === 'admin'" placeholder="默认 5" type="number" min="0" max="1000" density="compact" hide-details /></td>
                  <td><v-text-field v-model="item.quotaDraft.max_signal_sources" :disabled="item.role === 'admin'" placeholder="默认 10" type="number" min="0" max="1000" density="compact" hide-details /></td>
                  <td><v-btn size="small" color="primary" :disabled="item.role === 'admin'" :loading="quotaSaving === item.user_id" @click="saveUserQuota(item)">保存</v-btn></td>
                </tr>
              </tbody>
            </v-table>
          </v-card-text>
        </v-card>
      </v-col>
    </v-row>

    <!-- 大模型功能与管理员配置 -->
    <v-row v-if="!isStrategyPage">
      <v-col cols="12">
        <v-card class="user-settings-card" :class="{ 'admin-service-card': isAdmin }" elevation="0">
          <v-card-title class="settings-card-title">
            <div><v-icon>mdi-brain</v-icon><span>{{ isAdmin ? '大模型配置与审批' : '大模型行情分析' }}</span></div>
            <small>{{ isAdmin ? '全局服务与用户开通申请' : '开通后可使用自主 AI 分析' }}</small>
          </v-card-title>
          <v-card-text>
            <v-form v-if="isAdmin" ref="llmForm">
              <v-row>
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
                  <v-select
                    v-model="llmConfig.model"
                    :items="enabledLLMModelIds"
                    label="模型名称"
                    dense
                    hide-details
                    placeholder="gpt-4o-mini"
                  ></v-select>
                </v-col>
              </v-row>
              <v-expansion-panels class="mt-4" variant="accordion">
                <v-expansion-panel>
                  <v-expansion-panel-title>
                    <div class="d-flex align-center ga-3">
                      <v-icon color="primary">mdi-text-box-edit-outline</v-icon>
                      <div>
                        <div class="font-weight-medium">分析提示词</div>
                        <div class="text-caption text-medium-emphasis">
                          版本 {{ llmConfig.prompt_version }} · 修改后实时分析与新回测共同生效
                        </div>
                      </div>
                    </div>
                  </v-expansion-panel-title>
                  <v-expansion-panel-text>
                    <v-alert type="info" variant="tonal" density="compact" class="mb-4">
                      分析模板必须保留 <code v-pre>{{strategy_context}}</code> 和
                      <code v-pre>{{market_data}}</code>，系统会在调用前注入策略约束与K线数据。
                    </v-alert>
                    <v-textarea
                      v-model="llmConfig.system_prompt"
                      label="System Prompt"
                      rows="4"
                      auto-grow
                      counter="10000"
                      variant="outlined"
                      hint="定义模型角色、行为边界和输出原则"
                      persistent-hint
                    />
                    <v-textarea
                      v-model="llmConfig.analysis_prompt_template"
                      label="分析 Prompt 模板"
                      rows="14"
                      auto-grow
                      counter="50000"
                      variant="outlined"
                      hint="建议保留JSON输出结构，避免分析结果无法解析"
                      persistent-hint
                      class="mt-3 prompt-template-editor"
                    />
                    <v-btn
                      variant="tonal"
                      color="warning"
                      prepend-icon="mdi-restore"
                      :loading="llmPromptResetting"
                      @click="resetLLMPrompts"
                    >
                      恢复系统默认提示词
                    </v-btn>
                  </v-expansion-panel-text>
                </v-expansion-panel>
              </v-expansion-panels>
              <v-row class="mt-2">
                <v-col cols="12">
                  <v-btn color="primary" @click="saveLLMConfig" :loading="llmSaving">
                    <v-icon start>mdi-content-save</v-icon>
                    保存配置
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
            </v-form>

            <div v-if="isAdmin" class="mt-6 llm-governance-panel">
              <div class="d-flex align-center justify-space-between mb-3">
                <div>
                  <div class="font-weight-bold">模型目录与场景路由</div>
                  <div class="text-caption text-medium-emphasis">
                    模型来自 API Base 的 /models；低频场景共享每日 {{ llmGovernance.free_daily_limit || 30 }} 次免费额度
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

              <v-row class="mt-2">
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
                    <v-btn block variant="tonal" color="primary" class="mt-3" @click="saveLLMScene(scene)">
                      保存场景配置
                    </v-btn>
                  </v-card>
                </v-col>
              </v-row>
            </div>

            <v-alert v-else type="info" variant="tonal" class="mt-4">
              回测报告和 Alpha 研究无需申请开通，共享每日 30 次免费大模型调用额度；
              今日剩余 {{ llmFreeQuota.remaining }} / {{ llmFreeQuota.limit }} 次。行情 AI 信号仍需申请开通。
            </v-alert>

            <div class="text-caption grey--text mt-3">
              <v-icon small>mdi-information</v-icon>
              管理员配置共享的大模型服务，审批通过的用户将使用此配置进行行情分析。
            </div>

            <div v-if="isAdmin" class="mt-6">
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

            <div v-else class="py-2">
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
          <v-select v-model="newStrategySymbol" :items="strategySymbolOptions" label="交易品种" prepend-inner-icon="mdi-currency-usd" class="mt-4"></v-select>
          <v-text-field v-model="newStrategyName" label="策略名称" placeholder="例如：GOLD M5 趋势策略" prepend-inner-icon="mdi-tag-outline"></v-text-field>
          <v-select v-model="newStrategyPolicyId" :items="positionPolicyOptions" label="持仓管理方案" prepend-inner-icon="mdi-shield-check-outline"></v-select>
          <v-alert type="info" variant="tonal" density="compact">新策略默认为私有草稿，不会立即参与交易。</v-alert>
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
          <v-alert type="info" variant="tonal" density="compact" class="mb-4">
            关键点位信号与 AI 入场、均线交叉、已验证 Alpha 互斥：策略中一旦存在关键点位，就不能再添加其他信号；存在其他信号时，也不能添加关键点位。
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
              <v-icon :color="signalSourceMeta[option.value].color">
                {{ signalSourceMeta[option.value].icon }}
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
              <v-col cols="12" sm="6"><v-text-field v-model.number="newSignalSource.params.entry_threshold" label="入场价接近阈值" type="number" step="0.0001" min="0"></v-text-field></v-col>
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
                <v-switch
                  v-model="newSignalSource.params.share_runtime_data"
                  color="success"
                  inset
                  label="共享本信号源的 AI 运行数据到平台"
                  hint="仅共享品种、信号参数、提示词、关联策略阶段和分析结果，不共享 API Key"
                  persistent-hint
                ></v-switch>
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
                    <details>
                      <summary>查看共享提示词</summary>
                      <div class="shared-prompt-preview">{{ item.system_prompt }}</div>
                      <div class="shared-prompt-preview">{{ item.analysis_prompt_template }}</div>
                    </details>
                    <details>
                      <summary>查看最近分析结果</summary>
                      <div class="shared-prompt-preview">{{ formatSharedRuntimeResult(item.result) }}</div>
                    </details>
                  </article>
                </div>
              </v-col>
            </v-row>
          </template>
        </v-card-text>
        <v-card-actions>
          <v-spacer></v-spacer>
          <v-btn variant="text" @click="signalSourceDialog = false">取消</v-btn>
          <v-btn color="primary" :disabled="!canSaveSignalSource" @click="saveSignalSourceFromDialog">
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
import { ref, reactive, computed, onMounted } from 'vue'
import { marketAPI } from '@/api/market'
import { authAPI } from '@/api/trading'
import { authState } from '@/auth'

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
      api_key: '',
      api_key_set: false,
      api_base: 'https://api.openai.com/v1',
      model: 'gpt-4o-mini',
      system_prompt: '',
      analysis_prompt_template: '',
      prompt_version: 1,
      enabled: false
    })
    const showApiKey = ref(false)
    const llmSaving = ref(false)
    const llmPromptResetting = ref(false)
    const llmModelsSyncing = ref(false)
    const llmGovernance = ref({ models: [], scenes: [], free_daily_limit: 30 })
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

    // 管理员用户配额白名单
    const quotaUsers = ref([])
    const quotaSaving = ref(null)
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
    const passwordForm = ref({
      current_password: '',
      new_password: '',
      confirm_password: ''
    })
    const showCurrentPassword = ref(false)
    const showNewPassword = ref(false)
    const showConfirmPassword = ref(false)
    const passwordSaving = ref(false)
    const passwordMismatch = computed(() =>
      Boolean(passwordForm.value.confirm_password) &&
      passwordForm.value.new_password !== passwordForm.value.confirm_password
    )
    const newPasswordValid = computed(() => {
      const password = passwordForm.value.new_password
      return (
        password.length >= 8 &&
        password.length <= 128 &&
        /[a-z]/i.test(password) &&
        /\d/.test(password)
      )
    })
    const canChangePassword = computed(() =>
      Boolean(passwordForm.value.current_password) &&
      newPasswordValid.value &&
      !passwordMismatch.value &&
      passwordForm.value.new_password === passwordForm.value.confirm_password
    )

    const changePassword = async () => {
      if (!canChangePassword.value) return

      passwordSaving.value = true
      try {
        const data = await authAPI.changePassword({
          current_password: passwordForm.value.current_password,
          new_password: passwordForm.value.new_password
        })
        successMessage.value = data.message || '密码修改成功'
        showSuccess.value = true
        passwordForm.value = {
          current_password: '',
          new_password: '',
          confirm_password: ''
        }
      } catch (err) {
        errorMessage.value = err.response?.data?.detail || '密码修改失败'
        showError.value = true
      } finally {
        passwordSaving.value = false
      }
    }

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
      try {
        const data = await authAPI.getUserQuotas()
        quotaUsers.value = (data.users || []).map(item => ({
          ...item,
          quotaDraft: {
            max_datasets: item.overrides.datasets ?? '',
            max_strategies: item.overrides.strategies ?? '',
            max_signal_sources: item.overrides.signal_sources ?? ''
          }
        }))
      } catch (err) {
        errorMessage.value = err.response?.data?.detail || '加载用户配额失败'
        showError.value = true
      }
    }

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
          loadUserQuotas(), loadEmailConfig(), loadLLMConfig(), loadLLMAccessRequests()
        ])
        successMessage.value = '管理员运营数据已刷新'
        showSuccess.value = true
      } finally {
        quotaSaving.value = null
      }
    }

    const saveUserQuota = async (item) => {
      quotaSaving.value = item.user_id
      try {
        const payload = Object.fromEntries(Object.entries(item.quotaDraft).map(([key, value]) => [
          key, value === '' || value === null ? null : Number(value)
        ]))
        await authAPI.saveUserQuota(item.user_id, payload)
        successMessage.value = `已更新 ${item.username} 的配额白名单`
        showSuccess.value = true
        await loadUserQuotas()
      } catch (err) {
        errorMessage.value = err.response?.data?.detail || '保存用户配额失败'
        showError.value = true
      } finally {
        quotaSaving.value = null
      }
    }

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
          llmConfig.value = {
            api_key: '',  // 不显示已有key，只显示是否设置
            api_key_set: data.config.api_key_set || false,
            api_base: data.config.api_base || 'https://api.openai.com/v1',
            model: data.config.model || 'gpt-4o-mini',
            system_prompt: data.config.system_prompt || '',
            analysis_prompt_template: data.config.analysis_prompt_template || '',
            prompt_version: data.config.prompt_version || 1,
            enabled: data.config.enabled || false
          }
        }
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
          api_base: llmConfig.value.api_base,
          model: llmConfig.value.model,
          system_prompt: llmConfig.value.system_prompt,
          analysis_prompt_template: llmConfig.value.analysis_prompt_template
        }
        // 只有输入了新的API Key才更新
        if (llmConfig.value.api_key) {
          updateData.api_key = llmConfig.value.api_key
        }

        const data = await marketAPI.configureLLM(updateData)
        if (data.status === 'ok') {
          successMessage.value = '大模型配置已保存'
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

    const syncLLMModels = async () => {
      llmModelsSyncing.value = true
      try {
        const data = await marketAPI.syncLLMModels()
        llmGovernance.value.models = data.models || []
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
        llmGovernance.value.models = data.models || []
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

    const resetLLMPrompts = async () => {
      if (!confirm('确定恢复系统默认提示词吗？当前自定义内容将被覆盖。')) return
      llmPromptResetting.value = true
      try {
        const data = await marketAPI.resetLLMPrompts()
        successMessage.value = data.message || '提示词已恢复为系统默认值'
        showSuccess.value = true
        await loadLLMConfig()
      } catch (err) {
        errorMessage.value = err.response?.data?.detail || '恢复默认提示词失败'
        showError.value = true
      } finally {
        llmPromptResetting.value = false
      }
    }

    // ==================== 策略配置 ====================

    // 策略数据
    const strategies = ref([])
    const strategyQuota = ref({ usage: { signal_sources: 0 }, limits: { strategies: 5, signal_sources: 10 } })
    const strategiesLoading = ref(false)
    const strategySaving = ref(null)
    const strategyLifecycleSaving = ref(null)
    const strategyAdmissions = ref({})
    const positionPolicies = ref([])
    const strategyWorkspaceTab = ref('mine')
    const strategyDetailTab = ref('overview')
    const selectedStrategy = ref(null)
    const selectedStrategySnapshot = ref('')
    const newStrategyDialog = ref(false)
    const strategySearch = ref('')
    const strategyLifecycleFilter = ref('all')
    const strategyEnabledFilter = ref('all')
    const strategyVisibilityFilter = ref('all')
    const sharedStrategies = ref([])
    const sharedStrategiesLoading = ref(false)
    const sharedStrategyCopying = ref(null)
    const positionPolicyOptions = computed(() => positionPolicies.value
      .filter(policy => policy.enabled)
      .map(policy => ({ title: policy.name, value: policy.policy_id })))
    const newStrategySymbol = ref('')
    const newStrategyName = ref('')
    const newStrategyPolicyId = ref('')
    const signalSourceDialog = ref(false)
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
    const newSignalSource = reactive({
      source: 'key_level', period: 'M1', enabled: true, weight: 30, params: {}
    })
    const signalPeriods = ['M1', 'M5', 'M15', 'H1', 'H4']
    const signalSourceMeta = {
      key_level: { label: '关键点位信号', color: 'success', icon: 'mdi-map-marker-path' },
      ai_entry: { label: 'AI 入场信号', color: 'info', icon: 'mdi-brain' },
      moving_average: { label: '均线交叉信号', color: 'orange-darken-2', icon: 'mdi-chart-bell-curve' },
      alpha_factor: { label: '已验证 Alpha', color: 'teal-darken-1', icon: 'mdi-atom-variant' }
    }
    const strategySignalSources = (strategy) => {
      const sources = Array.isArray(strategy?.signal_sources)
        ? strategy.signal_sources.filter(source => source.source !== 'pivot')
        : []
      return sources.some(source => source.source === 'key_level')
        ? sources.filter(source => source.source === 'key_level')
        : sources
    }
    const strategySourceBadges = (strategy) => strategySignalSources(strategy).map(source => ({
      key: source.signal_source_id,
      color: signalSourceMeta[source.source]?.color || 'grey',
      label: source.source === 'key_level'
        ? '关键点位'
        : source.source === 'ai_entry' && source.params?.analysis_mode === 'shared_reference'
          ? `共享 AI ${source.period}`
        : `${signalSourceMeta[source.source]?.label.replace('信号', '').trim() || source.source} ${source.period}`
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
    const periodMinuteMap = { M1: 1, M5: 5, M15: 15, H1: 60, H4: 240 }
    const aiIntervalValues = [1, 5, 10, 15, 30, 60, 120, 240, 480, 720, 1440]
    const periodMinutes = (period) => periodMinuteMap[period] || 1
    const aiIntervalOptionsFor = (source) => aiIntervalValues
      .filter(value => value >= periodMinutes(source.period))
      .map(value => ({ title: `${value} 分钟`, value }))
    const sharedAIRuntimeOptions = computed(() => aiSignalOptions.sharedRuntimeData.map(item => ({
      value: item.share_id,
      title: `${item.symbol} · 匹配度 ${formatSimilarity(item.symbol_similarity)} · ${item.period} · ${item.model} · ${item.strategy_name} · ${lifecycleLabel(item.strategy_lifecycle)}`
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
    function lifecycleLabel (status) {
      return ({
        draft: '草稿', backtesting: '回测中', backtest_passed: '回测通过',
        paper_trading: '模拟盘验证', production: '可用于实盘', retired: '已停用'
      })[status] || status || '未知阶段'
    }
    const formatSharedSignalParams = (params = {}) => (
      `间隔 ${params.analysis_interval_minutes ?? '-'} 分钟，K线 ${params.kline_count ?? '-'} 根，最低置信度 ${params.min_confidence ?? '-'}%`
    )
    const formatSharedRuntimeResult = (result = {}) => JSON.stringify(result, null, 2)
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
      const params = newSignalSource.params || {}
      return params.analysis_mode === 'shared_reference'
        ? Boolean(params.shared_runtime_id)
        : aiSignalOptions.accessGranted
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
      } catch (error) {
        aiSignalOptions.accessGranted = Boolean(llmAccess.value.access_granted)
        aiSignalOptions.sharedRuntimeData = []
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
      signal_source_id: crypto.randomUUID().replaceAll('-', '').slice(0, 12),
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
          : source === 'moving_average'
            ? {
                fast_period: 5, slow_period: 20, ma_type: 'sma',
                min_confidence: 70,
                cooldown_seconds: 180
            }
            : source === 'alpha_factor'
              ? {
                  alpha_id: '', alpha_version: 1, alpha_name: '',
                  alpha_snapshot: {}, min_confidence: 60, cooldown_seconds: 180
                }
              : {
              analysis_mode: 'self_analysis',
              analysis_interval_minutes: Math.max(5, periodMinutes(period)), kline_count: 100,
              min_confidence: 70, entry_threshold: 0.0001,
              model: aiSignalOptions.models[0] || '',
              system_prompt: aiSignalOptions.defaultSystemPrompt,
              analysis_prompt_template: aiSignalOptions.defaultAnalysisPromptTemplate,
              share_runtime_data: false,
              reference_runtime_ids: [],
              shared_runtime_id: ''
            }
    })
    const cloneSignalSource = (source) => JSON.parse(JSON.stringify(source))
    const setDialogSignalSource = (source) => {
      const cloned = cloneSignalSource(source)
      Object.keys(newSignalSource).forEach(key => delete newSignalSource[key])
      Object.assign(newSignalSource, cloned)
      newSignalSource.params ||= {}
      if (newSignalSource.source === 'key_level') {
        newSignalSource.period = 'M1'
        newSignalSource.params.levels_text ??= (newSignalSource.params.levels || []).join(', ')
      }
      if (newSignalSource.source === 'ai_entry') {
        newSignalSource.params.analysis_mode ||= 'self_analysis'
        newSignalSource.params.model ||= aiSignalOptions.models[0] || ''
        newSignalSource.params.system_prompt ||= aiSignalOptions.defaultSystemPrompt
        newSignalSource.params.analysis_prompt_template ||= aiSignalOptions.defaultAnalysisPromptTemplate
        newSignalSource.params.share_runtime_data ??= false
        newSignalSource.params.reference_runtime_ids ||= []
        newSignalSource.params.shared_runtime_id ||= ''
      }
      normalizeAIInterval(newSignalSource)
    }

    // 策略选项
    const consistencyOptions = [
      { title: '任一信号即可', value: 'any' },
      { title: '多数信号一致（至少60%同向）', value: 'majority' },
      { title: '所有信号一致', value: 'all' }
    ]

    const loadPositionPolicies = async () => {
      const data = await marketAPI.getPositionManagementPolicies()
      positionPolicies.value = data.policies || []
      if (!newStrategyPolicyId.value) {
        newStrategyPolicyId.value = positionPolicyOptions.value[0]?.value || ''
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
    const enabledFilterOptions = [
      { title: '全部状态', value: 'all' },
      { title: '已启用', value: 'enabled' },
      { title: '未启用', value: 'disabled' }
    ]
    const visibilityFilterOptions = [
      { title: '全部可见性', value: 'all' },
      { title: '私有策略', value: 'private' },
      { title: '平台共享', value: 'shared' }
    ]
    const strategyMetrics = computed(() => ({
      production: strategies.value.filter(item => item.lifecycle_status === 'production').length,
      enabled: strategies.value.filter(item => item.enabled).length,
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
        const matchesEnabled = strategyEnabledFilter.value === 'all' ||
          (strategyEnabledFilter.value === 'enabled' ? strategy.enabled : !strategy.enabled)
        const matchesVisibility = strategyVisibilityFilter.value === 'all' ||
          (strategyVisibilityFilter.value === 'shared' ? strategy.is_shared : !strategy.is_shared)
        return matchesSearch && matchesLifecycle && matchesEnabled && matchesVisibility
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
      if (source.source === 'alpha_factor') {
        return `${params.alpha_name || '已验证 Alpha'} · ${source.period}，最低置信度 ${params.min_confidence ?? 0}%`
      }
      if (params.analysis_mode === 'shared_reference') {
        const shared = aiSignalOptions.sharedRuntimeData.find(
          item => item.share_id === params.shared_runtime_id
        )
        return `引用 ${shared?.owner_username || '平台用户'} 的 ${shared?.symbol || '共享'} 分析，最低置信度 ${params.min_confidence ?? 0}%`
      }
      return `${params.model || '平台默认模型'} · 每 ${params.analysis_interval_minutes ?? 0} 分钟分析 ${params.kline_count ?? 0} 根K线，最低置信度 ${params.min_confidence ?? 0}%${params.share_runtime_data ? ' · 已共享运行数据' : ''}`
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
    const loadStrategies = async () => {
      strategiesLoading.value = true
      try {
        const [data, admissionData, policyData] = await Promise.all([
          marketAPI.getStrategies(), marketAPI.getStrategyAdmission(),
          marketAPI.getPositionManagementPolicies()
        ])
        positionPolicies.value = policyData.policies || []
        if (!newStrategyPolicyId.value) newStrategyPolicyId.value = positionPolicyOptions.value[0]?.value || ''
        if (data.status === 'ok') {
          strategies.value = data.strategies || []
          strategyQuota.value = data.quota || strategyQuota.value
          strategies.value.forEach(strategy => {
            normalizeStrategyVisibility(strategy)
            ensureSignalSources(strategy)
          })
        }
        if (admissionData.status === 'ok') {
          strategyAdmissions.value = Object.fromEntries(
            (admissionData.items || []).map(item => [item.strategy_id, item])
          )
        }
      } catch (err) {
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

    const copySharedStrategy = async (item) => {
      const policyId = newStrategyPolicyId.value || positionPolicyOptions.value[0]?.value || ''
      if (!policyId) {
        errorMessage.value = '请先创建并启用一个持仓管理方案，再复制共享策略'
        showError.value = true
        return
      }
      const copyKey = `${item.owner_user_id}-${item.strategy_id}`
      sharedStrategyCopying.value = copyKey
      try {
        const data = await marketAPI.copySharedStrategy(
          item.owner_user_id,
          item.strategy_id,
          { position_management_policy_id: policyId }
        )
        if (data.status !== 'ok') {
          throw new Error(data.message || '复制共享策略失败')
        }
        successMessage.value = data.message || '共享策略已复制'
        showSuccess.value = true
        await loadStrategies()
        strategyWorkspaceTab.value = 'mine'
        const createdId = data.strategy?.strategy_id
        const created = strategies.value.find(strategy => strategy.strategy_id === createdId)
        if (created) openStrategyDetail(created)
      } catch (err) {
        const detail = err.response?.data?.detail || err.message
        errorMessage.value = `复制共享策略失败: ${detail}`
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
          enabled: strategy.enabled,
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

    function ensureSignalSources (strategy) {
      if (!Array.isArray(strategy.signal_sources)) strategy.signal_sources = []
      strategy.signal_sources = strategy.signal_sources.filter(
        source => source.source !== 'pivot'
      )
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

    const openSignalSourceDialog = async (strategy, source = null) => {
      signalSourceTarget.value = strategy
      await Promise.all([loadAISignalOptions(strategy.symbol), loadAlphaLibrary()])
      if (source) {
        signalSourceEditMode.value = 'edit'
        editingSignalSourceId.value = source.signal_source_id
        setDialogSignalSource(source)
        signalSourceDialog.value = true
        return
      }
      signalSourceEditMode.value = 'add'
      editingSignalSourceId.value = ''
      const firstAvailableType = Object.keys(signalSourceMeta).find(
        sourceType => availablePeriodsForSource(sourceType).length
      ) || 'key_level'
      const firstPeriod = firstAvailableType === 'key_level'
        ? 'M1'
        : availablePeriodsForSource(firstAvailableType)[0] || 'M1'
      setDialogSignalSource(sourceDefaults(firstAvailableType, firstPeriod))
      signalSourceDialog.value = true
    }

    const saveSignalSourceFromDialog = () => {
      if (!signalSourceTarget.value || !canSaveSignalSource.value) return
      if (
        newSignalSource.source === 'ai_entry' &&
        newSignalSource.params.analysis_mode === 'self_analysis'
      ) {
        const template = String(newSignalSource.params.analysis_prompt_template || '')
        if (!template.includes('{{strategy_context}}') || !template.includes('{{market_data}}')) {
          errorMessage.value = 'AI分析提示词必须包含 {{strategy_context}} 和 {{market_data}}'
          showError.value = true
          return
        }
      }
      const nextSource = cloneSignalSource(newSignalSource)
      if (nextSource.source === 'key_level') {
        nextSource.period = 'M1'
        nextSource.params.proximity_threshold = Number(
          nextSource.params.order_distance || nextSource.params.proximity_threshold || 0
        )
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

    // 添加策略
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
        loadSymbols()
        loadTradeConfig()
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

    return {
      isStrategyPage,
      pageTitle,
      tradeConfig,
      newSymbol,
      newVolume,
      newSlOffset,
      newKeyLevels,
      newKeyLevelThreshold,
      availableSymbols,
      showError,
      errorMessage,
      showSuccess,
      successMessage,
      currentUser,
      isAdmin,
      roleLabel,
      passwordForm,
      showCurrentPassword,
      showNewPassword,
      showConfirmPassword,
      passwordSaving,
      passwordMismatch,
      canChangePassword,
      changePassword,
      emailConfig,
      showEmailPassword,
      emailSaving,
      emailTesting,
      saveEmailConfig,
      testEmailConfig,
      quotaUsers,
      quotaSaving,
      saveUserQuota,
      myQuota,
      loadAdminWorkspace,
      saveTradeConfig,
      addSymbolConfig,
      removeSymbolConfig,
      onSymbolSelect,
      // 大模型配置
      llmConfig,
      showApiKey,
      llmSaving,
      llmPromptResetting,
      llmModelsSyncing,
      llmGovernance,
      enabledLLMModelIds,
      saveLLMConfig,
      syncLLMModels,
      toggleLLMModel,
      saveLLMScene,
      resetLLMPrompts,
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
      strategySaving,
      strategyLifecycleSaving,
      strategyAdmissions,
      strategyWorkspaceTab,
      strategyDetailTab,
      selectedStrategy,
      newStrategyDialog,
      strategySearch,
      strategyLifecycleFilter,
      strategyEnabledFilter,
      strategyVisibilityFilter,
      lifecycleFilterOptions,
      enabledFilterOptions,
      visibilityFilterOptions,
      strategyMetrics,
      filteredStrategies,
      hasStrategyChanges,
      sharedStrategies,
      sharedStrategiesLoading,
      sharedStrategyCopying,
      newStrategySymbol,
      newStrategyName,
      consistencyOptions,
      positionPolicies,
      positionPolicyOptions,
      newStrategyPolicyId,
      strategySymbolOptions,
      loadStrategies,
      updateStrategy,
      saveSelectedStrategy,
      openStrategyDetail,
      closeStrategyDetail,
      deleteStrategy,
      addStrategy,
      getLifecycleMeta,
      getLifecycleColor,
      getLifecycleActions,
      getConsistencyLabel,
      signalSourceCount,
      signalSourceSummary,
      strategySignalSources,
      strategySourceBadges,
      getAdmission,
      admissionStages,
      isLifecycleActionDisabled,
      transitionStrategyLifecycle,
      loadSharedStrategies,
      copySharedStrategy,
      // 信号配置
      signalSourceDialog,
      signalSourceEditMode,
      newSignalSource,
      signalSourceMeta,
      signalSourceTypeOptions,
      signalSourceDisabledReason,
      keyLevelModeOptions,
      movingAverageTypeOptions,
      periodMinutes,
      aiIntervalOptionsFor,
      aiSignalOptions,
      aiSignalOptionsLoading,
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
.user-settings-card { overflow: hidden; border: 1px solid var(--settings-line); border-radius: 19px !important; background: #fff; }.user-settings-card :deep(.v-card-text) { padding: 22px 24px 26px; }.settings-card-title { display: flex; align-items: center; justify-content: space-between; gap: 16px; min-height: 68px; padding: 18px 24px !important; border-bottom: 1px solid var(--settings-line); color: var(--settings-ink); }.settings-card-title>div { display: flex; align-items: center; gap: 10px; font-size: 1rem; font-weight: 700; }.settings-card-title>div .v-icon { color: var(--settings-green); }.settings-card-title>small { color: var(--settings-muted); font-size: .72rem; font-weight: 400; }.admin-service-card { border-color: #c8ddd4; background: linear-gradient(145deg, #fff 0%, #f7fcfa 100%); }.account-summary { padding: 8px; border: 1px solid #e2ece7; border-radius: 13px; background: #f9fcfa; }.quota-table :deep(th) { color: var(--settings-muted); font-size: .72rem; font-weight: 700; letter-spacing: .04em; white-space: nowrap; }.quota-table :deep(td) { padding-top: 12px; padding-bottom: 12px; }.quota-table small { display: block; margin-top: 3px; color: var(--settings-muted); font-size: .7rem; }
.admission-panel { padding: 16px; border: 1px solid #dbe7e1; border-radius: 14px; background: linear-gradient(135deg, #f5f9f6, #fffaf0); }
.admission-title { display: flex; align-items: center; justify-content: space-between; gap: 16px; }
.admission-title strong,.admission-title span { display: block; }.admission-title span { margin-top: 2px; color: #7a8982; font-size: .72rem; }
.admission-stages { display: grid; grid-template-columns: 1fr 1fr; gap: 10px; margin-top: 12px; }
.admission-stages article { padding: 12px; border-radius: 10px; background: rgba(255,255,255,.85); }
.admission-stages article>div:first-child { display: flex; align-items: center; gap: 7px; }.admission-stages p { margin: 6px 0; color: #718079; font-size: .72rem; }
.admission-checks { display: flex; flex-wrap: wrap; gap: 5px; }
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
@media (max-width: 700px) { .admission-stages { grid-template-columns: 1fr; }.strategy-hero,.settings-hero,.strategy-detail-head,.detail-section-title,.lifecycle-banner,.danger-zone { align-items: flex-start; flex-direction: column; }.strategy-hero,.settings-hero { padding: 26px 22px; }.strategy-primary-action,.settings-hero .v-btn { width: 100%; }.strategy-metrics,.settings-metrics { grid-template-columns: 1fr 1fr; gap: 9px; }.strategy-metrics article,.settings-metrics article { min-height: 104px; padding: 15px; }.settings-metrics strong { font-size: 1.28rem; }.settings-card-title { align-items: flex-start; flex-direction: column; }.user-settings-card :deep(.v-card-text) { padding: 18px 16px 22px; }.quota-table { min-width: 860px; }.quota-table :deep(.v-table__wrapper) { overflow-x: auto; }.strategy-toolbar { grid-template-columns: 1fr; }.strategy-toolbar>*:first-child { grid-column: auto; }.strategy-detail-content { padding: 20px 16px; }.strategy-detail-head>div:last-child { width: 100%; }.strategy-detail-head>div:last-child .v-btn { flex: 1; }.shared-card-footer { align-items: flex-start; flex-direction: column; } }
</style>

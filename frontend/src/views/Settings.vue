<template>
  <v-container fluid>
    <v-row>
      <v-col cols="12">
        <h1 class="mb-4">{{ pageTitle }}</h1>
      </v-col>
    </v-row>

    <!-- 自动交易配置 -->
    <v-row v-if="isStrategyPage">
      <v-col cols="12">
        <v-card>
          <v-card-title>
            <v-icon class="mr-2">mdi-cog</v-icon>
            自动交易配置
          </v-card-title>
          <v-card-text>
            <v-row align="center">
              <v-col cols="12">
                <v-switch
                  v-model="tradeConfig.enabled"
                  label="启用自动生成"
                  color="success"
                  @change="saveTradeConfig"
                ></v-switch>
              </v-col>
            </v-row>

            <!-- 品种配置表格 -->
            <div class="text-subtitle-2 mt-2 mb-2">品种配置</div>
            <v-table density="compact">
              <template v-slot:default>
                <thead>
                  <tr>
                    <th>品种</th>
                    <th>手数</th>
                    <th>止损偏移(点)</th>
                    <th>关键点位</th>
                    <th>阈值</th>
                    <th>操作</th>
                  </tr>
                </thead>
                <tbody>
                  <tr v-for="(config, symbol) in tradeConfig.symbol_config" :key="symbol">
                    <td>
                      <strong>{{ symbol }}</strong>
                    </td>
                    <td>
                      <v-text-field
                        v-model.number="config.volume"
                        type="number"
                        step="0.01"
                        min="0.01"
                        dense
                        hide-details
                        style="width: 80px"
                      ></v-text-field>
                    </td>
                    <td>
                      <v-text-field
                        v-model.number="config.sl_offset"
                        type="number"
                        step="0.01"
                        min="0"
                        dense
                        hide-details
                        style="width: 80px"
                      ></v-text-field>
                    </td>
                    <td>
                      <v-text-field
                        v-model="config.key_levels"
                        type="text"
                        dense
                        hide-details
                        placeholder="如: 5000,5100"
                        style="width: 120px"
                      ></v-text-field>
                    </td>
                    <td>
                      <v-text-field
                        v-model.number="config.key_level_threshold"
                        type="number"
                        step="0.0001"
                        min="0"
                        dense
                        hide-details
                        style="width: 80px"
                      ></v-text-field>
                    </td>
                    <td>
                      <v-btn size="x-small" color="primary" @click="saveTradeConfig">保存</v-btn>
                      <v-btn size="x-small" color="error" outlined class="ml-1" @click="removeSymbolConfig(symbol)">删除</v-btn>
                    </td>
                  </tr>
                </tbody>
              </template>
            </v-table>

            <!-- 添加新品种配置 -->
            <v-row class="mt-3" align="center">
              <v-col cols="2">
                <v-select
                  v-model="newSymbol"
                  :items="availableSymbols"
                  label="选择品种"
                  dense
                  hide-details
                  @change="onSymbolSelect"
                ></v-select>
              </v-col>
              <v-col cols="2">
                <v-text-field
                  v-model.number="newVolume"
                  label="手数"
                  type="number"
                  step="0.01"
                  min="0.01"
                  dense
                  hide-details
                ></v-text-field>
              </v-col>
              <v-col cols="2">
                <v-text-field
                  v-model.number="newSlOffset"
                  label="止损偏移"
                  type="number"
                  step="0.01"
                  min="0"
                  dense
                  hide-details
                ></v-text-field>
              </v-col>
              <v-col cols="2">
                <v-text-field
                  v-model="newKeyLevels"
                  label="关键点位"
                  type="text"
                  dense
                  hide-details
                  placeholder="如: 5000,5100"
                ></v-text-field>
              </v-col>
              <v-col cols="2">
                <v-text-field
                  v-model.number="newKeyLevelThreshold"
                  label="阈值"
                  type="number"
                  step="0.0001"
                  min="0"
                  dense
                  hide-details
                ></v-text-field>
              </v-col>
              <v-col cols="2">
                <v-btn color="primary" small @click="addSymbolConfig">
                  <v-icon start small>mdi-plus</v-icon>
                  添加
                </v-btn>
              </v-col>
            </v-row>

            <div class="text-caption grey--text mt-3">
              <v-icon small>mdi-information</v-icon>
              品种基础配置：手数、止损偏移、关键点位等。
            </div>
          </v-card-text>
        </v-card>
      </v-col>
    </v-row>

    <!-- 策略配置 -->
    <v-row v-if="isStrategyPage" class="mt-4">
      <v-col cols="12">
        <v-card>
          <v-card-title>
            <v-icon class="mr-2">mdi-strategy</v-icon>
            策略配置
            <v-btn icon small class="ml-2" @click="loadStrategies" :loading="strategiesLoading">
              <v-icon small>mdi-refresh</v-icon>
            </v-btn>
          </v-card-title>
          <v-card-text>
            <!-- 策略列表 -->
            <v-expansion-panels v-if="strategies.length > 0">
              <v-expansion-panel v-for="strategy in strategies" :key="strategy.strategy_id">
                <v-expansion-panel-title>
                  <div class="d-flex align-center">
                    <strong class="mr-3">{{ strategy.symbol }}</strong>
                    <v-chip :color="getLifecycleMeta(strategy).color" size="x-small">
                      {{ getLifecycleMeta(strategy).label }}
                    </v-chip>
                    <v-chip :color="strategy.enabled ? 'success' : 'grey'" size="x-small">
                      {{ strategy.enabled ? '启用' : '禁用' }}
                    </v-chip>
                    <v-chip v-if="strategy.auto_execute" color="warning" size="x-small" class="ml-2">
                      自动下单
                    </v-chip>
                    <span class="text-caption grey--text ml-3">{{ strategy.strategy_name }}</span>
                    <span class="text-caption grey--text ml-2">#{{ strategy.strategy_id }}</span>
                  </div>
                </v-expansion-panel-title>
                <v-expansion-panel-text>
                  <v-alert type="info" variant="tonal" density="compact" class="mb-4">
                    <div class="d-flex flex-wrap align-center justify-space-between">
                      <div class="mr-4">
                        <strong>生命周期：{{ getLifecycleMeta(strategy).label }}</strong>
                        <div class="text-caption mt-1">
                          {{ getLifecycleMeta(strategy).description }}
                        </div>
                      </div>
                      <div class="d-flex flex-wrap ga-2 mt-2 mt-md-0">
                        <v-btn
                          v-for="action in getLifecycleActions(strategy)"
                          :key="action.target"
                          :color="action.color"
                          size="small"
                          variant="outlined"
                          :disabled="isLifecycleActionDisabled(strategy, action)"
                          :loading="strategyLifecycleSaving === strategy.strategy_id"
                          @click="transitionStrategyLifecycle(strategy, action)"
                        >
                          <v-icon start>{{ action.icon }}</v-icon>
                          {{ action.label }}
                        </v-btn>
                      </div>
                    </div>
                  </v-alert>

                  <div v-if="getAdmission(strategy)" class="admission-panel mb-4">
                    <div class="admission-title">
                      <div><strong>策略准入证据</strong><span>只认可当前策略参数版本产生的结果</span></div>
                      <v-chip size="small" :color="getAdmission(strategy).eligible_for_production ? 'success' : 'warning'" variant="tonal">
                        {{ getAdmission(strategy).eligible_for_production ? '满足实盘准入' : '验证进行中' }}
                      </v-chip>
                    </div>
                    <div class="admission-stages">
                      <article v-for="stage in admissionStages(strategy)" :key="stage.key">
                        <div><v-icon size="18" :color="stage.data.passed ? 'success' : 'grey'">{{ stage.data.passed ? 'mdi-check-decagram' : 'mdi-progress-clock' }}</v-icon><strong>{{ stage.label }}</strong></div>
                        <p>{{ stage.data.message }}</p>
                        <div v-if="stage.data.checks?.length" class="admission-checks">
                          <v-chip v-for="check in stage.data.checks" :key="check.key" size="x-small" :color="check.passed ? 'success' : 'error'" variant="tonal">{{ check.label }}</v-chip>
                        </div>
                      </article>
                    </div>
                  </div>

                  <!-- 基本信息 -->
                  <v-row class="mb-3">
                    <v-col cols="12" md="3">
                      <v-switch
                        v-model="strategy.enabled"
                        label="启用策略"
                        color="success"
                        dense
                        hide-details
                        :disabled="strategy.lifecycle_status !== 'production'"
                        @update:model-value="updateStrategy(strategy)"
                      ></v-switch>
                    </v-col>
                    <v-col cols="12" md="3">
                      <v-text-field
                        v-model="strategy.strategy_name"
                        label="策略名称"
                        dense
                        hide-details
                        @change="updateStrategy(strategy)"
                      ></v-text-field>
                    </v-col>
                    <v-col cols="12" md="3">
                      <v-text-field
                        v-model.number="strategy.min_confidence"
                        label="最低置信度(%)"
                        type="number"
                        min="0"
                        max="100"
                        dense
                        hide-details
                        @change="updateStrategy(strategy)"
                      ></v-text-field>
                    </v-col>
                    <v-col cols="12" md="3">
                      <v-select
                        v-model="strategy.consistency_requirement"
                        :items="consistencyOptions"
                        label="一致性要求"
                        dense
                        hide-details
                        @change="updateStrategy(strategy)"
                      ></v-select>
                    </v-col>
                  </v-row>

                  <v-alert type="warning" variant="tonal" density="compact" class="mb-3">
                    <div class="d-flex flex-wrap align-center">
                      <v-switch
                        v-model="strategy.auto_execute"
                        label="信号推荐后自动下单"
                        color="success"
                        density="compact"
                        hide-details
                        class="mr-3"
                        :disabled="strategy.lifecycle_status !== 'production'"
                        @change="updateStrategy(strategy)"
                      ></v-switch>
                      <span class="text-caption">
                        仅“可用于实盘”的策略可以开启；信号通过仓位和风险检查后将直接生成 MT5 交易指令。
                      </span>
                    </div>
                  </v-alert>

                  <!-- 信号权重 -->
                  <div class="text-subtitle-2 mb-2">信号源配置</div>
                  <v-row class="mb-3">
                    <v-col cols="12">
                      <v-table density="compact">
                        <template v-slot:default>
                          <thead>
                            <tr>
                              <th>信号源</th>
                              <th>启用</th>
                              <th>M1</th>
                              <th>M5</th>
                              <th>M15</th>
                              <th>H1</th>
                              <th>H4</th>
                            </tr>
                          </thead>
                          <tbody>
                            <!-- Pivot 信号 -->
                            <tr>
                              <td>
                                <v-chip size="x-small" color="primary">Pivot</v-chip>
                                <span class="ml-2 text-caption">转折点信号</span>
                              </td>
                              <td>
                                <v-checkbox
                                  :model-value="getSignalConfig(strategy, 'pivot').enabled"
                                  density="compact"
                                  hide-details
                                  @update:model-value="onSignalEnabledChange(strategy, 'pivot', $event)"
                                ></v-checkbox>
                              </td>
                              <td v-for="period in ['M1', 'M5', 'M15', 'H1', 'H4']" :key="period">
                                <div class="d-flex align-center">
                                  <v-checkbox
                                    :model-value="getPeriodConfig(strategy, 'pivot', period).enabled"
                                    density="compact"
                                    hide-details
                                    :disabled="!getSignalConfig(strategy, 'pivot').enabled"
                                    @update:model-value="onPeriodEnabledChange(strategy, 'pivot', period, $event)"
                                  ></v-checkbox>
                                  <v-text-field
                                    v-model.number="getPeriodConfig(strategy, 'pivot', period).weight"
                                    type="number"
                                    min="0"
                                    max="100"
                                    density="compact"
                                    hide-details
                                    style="width: 50px"
                                    :disabled="!getSignalConfig(strategy, 'pivot').enabled || !getPeriodConfig(strategy, 'pivot', period).enabled"
                                    @change="onSignalConfigChange(strategy, 'pivot')"
                                  ></v-text-field>
                                </div>
                              </td>
                            </tr>
                            <!-- KeyLevel 信号 -->
                            <tr>
                              <td>
                                <v-chip size="x-small" color="success">KeyLevel</v-chip>
                                <span class="ml-2 text-caption">关键点位信号</span>
                              </td>
                              <td>
                                <v-checkbox
                                  :model-value="getSignalConfig(strategy, 'key_level').enabled"
                                  density="compact"
                                  hide-details
                                  @update:model-value="onSignalEnabledChange(strategy, 'key_level', $event)"
                                ></v-checkbox>
                              </td>
                              <td colspan="5">
                                <v-text-field
                                  v-model.number="getSignalConfig(strategy, 'key_level').weight"
                                  label="权重"
                                  type="number"
                                  min="0"
                                  max="100"
                                  density="compact"
                                  hide-details
                                  style="width: 80px"
                                  :disabled="!getSignalConfig(strategy, 'key_level').enabled"
                                  @change="onSignalConfigChange(strategy, 'key_level')"
                                ></v-text-field>
                                <span class="text-caption grey--text ml-2">（不区分周期）</span>
                              </td>
                            </tr>
                            <!-- AI Entry 信号 -->
                            <tr>
                              <td>
                                <v-chip size="x-small" color="info">AI Entry</v-chip>
                                <span class="ml-2 text-caption">AI入场信号</span>
                              </td>
                              <td>
                                <v-checkbox
                                  :model-value="getSignalConfig(strategy, 'ai_entry').enabled"
                                  density="compact"
                                  hide-details
                                  @update:model-value="onSignalEnabledChange(strategy, 'ai_entry', $event)"
                                ></v-checkbox>
                              </td>
                              <td v-for="period in ['M1', 'M5', 'M15', 'H1', 'H4']" :key="period">
                                <div class="d-flex align-center">
                                  <v-checkbox
                                    :model-value="getPeriodConfig(strategy, 'ai_entry', period).enabled"
                                    density="compact"
                                    hide-details
                                    :disabled="!getSignalConfig(strategy, 'ai_entry').enabled"
                                    @update:model-value="onPeriodEnabledChange(strategy, 'ai_entry', period, $event)"
                                  ></v-checkbox>
                                  <v-text-field
                                    v-model.number="getPeriodConfig(strategy, 'ai_entry', period).weight"
                                    type="number"
                                    min="0"
                                    max="100"
                                    density="compact"
                                    hide-details
                                    style="width: 50px"
                                    :disabled="!getSignalConfig(strategy, 'ai_entry').enabled || !getPeriodConfig(strategy, 'ai_entry', period).enabled"
                                    @change="onSignalConfigChange(strategy, 'ai_entry')"
                                  ></v-text-field>
                                </div>
                              </td>
                            </tr>
                          </tbody>
                        </template>
                      </v-table>
                    </v-col>
                  </v-row>

                  <!-- 兼容旧版信号权重（隐藏但保留数据） -->
                  <div class="text-caption grey--text mb-2">
                    <v-icon small>mdi-information</v-icon>
                    勾选启用周期，设置权重值。KeyLevel信号不区分周期，只需设置权重。
                  </div>

                  <!-- 仓位管理 -->
                  <div class="text-subtitle-2 mb-2">仓位管理</div>
                  <v-row class="mb-3">
                    <v-col cols="3">
                      <v-text-field
                        v-model.number="strategy.fixed_volume"
                        label="固定手数"
                        type="number"
                        step="0.01"
                        min="0.01"
                        dense
                        hide-details
                        @change="updateStrategy(strategy)"
                      ></v-text-field>
                    </v-col>
                    <v-col cols="3">
                      <v-text-field
                        v-model.number="strategy.max_positions"
                        label="最大持仓数"
                        type="number"
                        min="1"
                        dense
                        hide-details
                        @change="updateStrategy(strategy)"
                      ></v-text-field>
                    </v-col>
                    <v-col cols="3">
                      <v-text-field
                        v-model.number="strategy.max_same_direction"
                        label="同向最大持仓"
                        type="number"
                        min="1"
                        dense
                        hide-details
                        @change="updateStrategy(strategy)"
                      ></v-text-field>
                    </v-col>
                    <v-col cols="3">
                      <v-text-field
                        v-model.number="strategy.risk_percent"
                        label="风险百分比(%)"
                        type="number"
                        min="0.1"
                        step="0.1"
                        dense
                        hide-details
                        @change="updateStrategy(strategy)"
                      ></v-text-field>
                    </v-col>
                  </v-row>

                  <!-- 止损止盈 -->
                  <div class="text-subtitle-2 mb-2">止损止盈规则</div>
                  <v-row class="mb-3">
                    <v-col cols="3">
                      <v-select
                        v-model="strategy.sl_mode"
                        :items="slModeOptions"
                        label="止损模式"
                        dense
                        hide-details
                        @change="updateStrategy(strategy)"
                      ></v-select>
                    </v-col>
                    <v-col cols="3">
                      <v-select
                        v-model="strategy.tp_mode"
                        :items="tpModeOptions"
                        label="止盈模式"
                        dense
                        hide-details
                        @change="updateStrategy(strategy)"
                      ></v-select>
                    </v-col>
                    <v-col cols="3">
                      <v-text-field
                        v-model.number="strategy.min_risk_reward"
                        label="最小盈亏比"
                        type="number"
                        min="0.5"
                        step="0.5"
                        dense
                        hide-details
                        @change="updateStrategy(strategy)"
                      ></v-text-field>
                    </v-col>
                    <v-col cols="3">
                      <v-text-field
                        v-model.number="strategy.tp_risk_reward"
                        label="止盈盈亏比"
                        type="number"
                        min="1"
                        step="0.5"
                        dense
                        hide-details
                        @change="updateStrategy(strategy)"
                      ></v-text-field>
                    </v-col>
                  </v-row>

                  <!-- 操作按钮 -->
                  <v-row>
                    <v-col cols="12">
                      <v-btn color="primary" small @click="updateStrategy(strategy)" :loading="strategySaving === strategy.strategy_id">
                        <v-icon start small>mdi-content-save</v-icon>
                        保存
                      </v-btn>
                      <v-btn color="error" small outlined class="ml-2" @click="deleteStrategy(strategy)">
                        <v-icon start small>mdi-delete</v-icon>
                        删除策略
                      </v-btn>
                    </v-col>
                  </v-row>
                </v-expansion-panel-text>
              </v-expansion-panel>
            </v-expansion-panels>

            <div v-else class="text-center grey--text py-4">
              <v-icon large>mdi-strategy</v-icon>
              <div class="mt-2">暂无策略配置，添加品种后会自动创建默认策略</div>
            </div>

            <!-- 添加新策略 -->
            <v-row class="mt-4" align="center">
              <v-col cols="4">
                <v-select
                  v-model="newStrategySymbol"
                  :items="strategySymbolOptions"
                  label="选择品种添加策略"
                  dense
                  hide-details
                ></v-select>
              </v-col>
              <v-col cols="4">
                <v-text-field
                  v-model="newStrategyName"
                  label="策略名称（可选）"
                  dense
                  hide-details
                  placeholder="默认：Strategy_{品种}"
                ></v-text-field>
              </v-col>
              <v-col cols="4">
                <v-btn color="primary" small @click="addStrategy" :loading="strategySaving === 'new'">
                  <v-icon start small>mdi-plus</v-icon>
                  添加策略
                </v-btn>
              </v-col>
            </v-row>

            <div class="text-caption grey--text mt-3">
              <v-icon small>mdi-information</v-icon>
              同一品种可配置多个策略；每条信号推荐会标明具体的触发策略。
            </div>
          </v-card-text>
        </v-card>
      </v-col>
    </v-row>

    <!-- 账户与安全 -->
    <v-row v-if="!isStrategyPage">
      <v-col cols="12">
        <v-card>
          <v-card-title>
            <v-icon class="mr-2">mdi-account-lock</v-icon>
            账户与安全
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

    <!-- 大模型功能与管理员配置 -->
    <v-row v-if="!isStrategyPage">
      <v-col cols="12">
        <v-card>
          <v-card-title>
            <v-icon class="mr-2">mdi-brain</v-icon>
            {{ isAdmin ? '大模型配置' : '大模型行情分析' }}
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
                  <v-text-field
                    v-model="llmConfig.model"
                    label="模型名称"
                    dense
                    hide-details
                    placeholder="gpt-4o-mini"
                  ></v-text-field>
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
import { ref, computed, onMounted, onUnmounted } from 'vue'
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
    const pageTitle = computed(() => isStrategyPage.value ? '策略配置' : '用户配置')

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
    const llmAccess = ref({
      status: 'not_requested',
      access_granted: false,
      service_configured: false,
      feature_enabled: false,
      review_note: ''
    })
    const llmAccessRequesting = ref(false)
    const llmAccessRequests = ref([])
    const llmRequestsLoading = ref(false)
    const llmReviewingId = ref(null)
    const strategySaveTimers = new Map()

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
    const strategiesLoading = ref(false)
    const strategySaving = ref(null)
    const strategyLifecycleSaving = ref(null)
    const strategyAdmissions = ref({})
    const newStrategySymbol = ref('')
    const newStrategyName = ref('')

    // 策略选项
    const consistencyOptions = [
      { title: '任一信号即可', value: 'any' },
      { title: '多数信号一致', value: 'majority' },
      { title: '所有信号一致', value: 'all' }
    ]

    const slModeOptions = [
      { title: '使用信号建议', value: 'signal' },
      { title: '固定点数', value: 'fixed_points' }
    ]

    const tpModeOptions = [
      { title: '使用信号建议', value: 'signal' },
      { title: '固定点数', value: 'fixed_points' },
      { title: '风险回报比', value: 'risk_reward' }
    ]

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
        description: '策略已完成验证，可以启用信号推荐或自动下单。'
      },
      retired: {
        label: '已停用',
        color: 'error',
        description: '策略已经归档，不再参与信号和交易决策。'
      }
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

    const getLifecycleActions = (strategy) => (
      lifecycleActions[strategy.lifecycle_status] || []
    )

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
        if (data.strategy) Object.assign(strategy, data.strategy)
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
        const [data, admissionData] = await Promise.all([
          marketAPI.getStrategies(), marketAPI.getStrategyAdmission()
        ])
        if (data.status === 'ok') {
          strategies.value = data.strategies || []
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

    // 更新策略
    const updateStrategy = async (strategy) => {
      const pendingTimer = strategySaveTimers.get(strategy.strategy_id)
      if (pendingTimer) {
        clearTimeout(pendingTimer)
        strategySaveTimers.delete(strategy.strategy_id)
      }
      strategySaving.value = strategy.strategy_id
      try {
        // 确保signal_config存在
        if (!strategy.signal_config) {
          strategy.signal_config = {
            pivot: {
              enabled: true,
              periods: {
                M1: { enabled: true, weight: 15 },
                M5: { enabled: true, weight: 20 },
                M15: { enabled: false, weight: 25 },
                H1: { enabled: false, weight: 20 },
                H4: { enabled: false, weight: 20 }
              }
            },
            key_level: { enabled: true, weight: 40 },
            ai_entry: {
              enabled: true,
              periods: {
                M1: { enabled: false, weight: 15 },
                M5: { enabled: true, weight: 20 },
                M15: { enabled: true, weight: 30 },
                H1: { enabled: true, weight: 25 },
                H4: { enabled: false, weight: 20 }
              }
            }
          }
        }

        const data = await marketAPI.updateStrategy(strategy.strategy_id, {
          enabled: strategy.enabled,
          auto_execute: Boolean(strategy.auto_execute),
          strategy_name: strategy.strategy_name,
          min_confidence: strategy.min_confidence,
          consistency_requirement: strategy.consistency_requirement,
          signal_config: strategy.signal_config,
          signal_weights: strategy.signal_weights,
          fixed_volume: strategy.fixed_volume,
          max_positions: strategy.max_positions,
          max_same_direction: strategy.max_same_direction,
          risk_percent: strategy.risk_percent,
          sl_mode: strategy.sl_mode,
          tp_mode: strategy.tp_mode,
          min_risk_reward: strategy.min_risk_reward,
          tp_risk_reward: strategy.tp_risk_reward
        })
        if (data.status === 'ok') {
          successMessage.value = `${strategy.symbol} 策略配置已保存`
          showSuccess.value = true
          // 更新本地策略数据
          if (data.strategy) {
            Object.assign(strategy, data.strategy)
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

    // 获取信号源配置
    const getSignalConfig = (strategy, source) => {
      if (!strategy.signal_config) {
        strategy.signal_config = {
          pivot: { enabled: true, periods: {} },
          key_level: { enabled: true, weight: 40 },
          ai_entry: { enabled: true, periods: {} }
        }
      }
      if (!strategy.signal_config[source]) {
        if (source === 'key_level') {
          strategy.signal_config[source] = { enabled: true, weight: 40 }
        } else {
          strategy.signal_config[source] = { enabled: true, periods: {} }
        }
      }
      return strategy.signal_config[source]
    }

    // 获取周期配置
    const getPeriodConfig = (strategy, source, period) => {
      const config = getSignalConfig(strategy, source)
      if (source === 'key_level') {
        return { enabled: true, weight: config.weight || 40 }
      }
      if (!config.periods) {
        config.periods = {}
      }
      if (!config.periods[period]) {
        config.periods[period] = { enabled: false, weight: 20 }
      }
      return config.periods[period]
    }

    // 合并同一策略短时间内的信号配置变更，避免父子开关产生乱序保存。
    const scheduleSignalConfigSave = (strategy) => {
      const existingTimer = strategySaveTimers.get(strategy.strategy_id)
      if (existingTimer) clearTimeout(existingTimer)

      const timer = setTimeout(() => {
        strategySaveTimers.delete(strategy.strategy_id)
        updateStrategy(strategy)
      }, 150)
      strategySaveTimers.set(strategy.strategy_id, timer)
    }

    const onSignalEnabledChange = (strategy, source, enabled) => {
      getSignalConfig(strategy, source).enabled = Boolean(enabled)
      scheduleSignalConfigSave(strategy)
    }

    const onPeriodEnabledChange = (strategy, source, period, enabled) => {
      getPeriodConfig(strategy, source, period).enabled = Boolean(enabled)
      scheduleSignalConfigSave(strategy)
    }

    const onSignalConfigChange = (strategy) => {
      scheduleSignalConfigSave(strategy)
    }

    // 删除策略
    const deleteStrategy = async (strategy) => {
      if (!confirm(`确定要删除“${strategy.strategy_name}”策略吗？`)) return
      try {
        const data = await marketAPI.deleteStrategy(strategy.strategy_id)
        if (data.status === 'ok') {
          successMessage.value = '策略已删除'
          showSuccess.value = true
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
      if (!newStrategySymbol.value) return
      strategySaving.value = 'new'
      try {
        const data = await marketAPI.createStrategy({
          symbol: newStrategySymbol.value,
          enabled: false,
          auto_execute: false,
          strategy_name: newStrategyName.value || `Strategy_${newStrategySymbol.value}`
        })
        if (data.status === 'ok') {
          successMessage.value = '策略已添加'
          showSuccess.value = true
          newStrategySymbol.value = ''
          newStrategyName.value = ''
          await loadStrategies()
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
        loadLLMConfig()
        loadLLMAccessRequests()
      } else {
        loadLLMAccess()
      }
    })

    onUnmounted(() => {
      strategySaveTimers.forEach(timer => clearTimeout(timer))
      strategySaveTimers.clear()
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
      saveTradeConfig,
      addSymbolConfig,
      removeSymbolConfig,
      onSymbolSelect,
      // 大模型配置
      llmConfig,
      showApiKey,
      llmSaving,
      llmPromptResetting,
      saveLLMConfig,
      resetLLMPrompts,
      llmAccess,
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
      // 策略配置
      strategies,
      strategiesLoading,
      strategySaving,
      strategyLifecycleSaving,
      strategyAdmissions,
      newStrategySymbol,
      newStrategyName,
      consistencyOptions,
      slModeOptions,
      tpModeOptions,
      strategySymbolOptions,
      loadStrategies,
      updateStrategy,
      deleteStrategy,
      addStrategy,
      getLifecycleMeta,
      getLifecycleActions,
      getAdmission,
      admissionStages,
      isLifecycleActionDisabled,
      transitionStrategyLifecycle,
      // 信号配置
      getSignalConfig,
      getPeriodConfig,
      onSignalEnabledChange,
      onPeriodEnabledChange,
      onSignalConfigChange
    }
  }
}
</script>

<style scoped>
.admission-panel { padding: 16px; border: 1px solid #dbe7e1; border-radius: 14px; background: linear-gradient(135deg, #f5f9f6, #fffaf0); }
.admission-title { display: flex; align-items: center; justify-content: space-between; gap: 16px; }
.admission-title strong,.admission-title span { display: block; }.admission-title span { margin-top: 2px; color: #7a8982; font-size: .72rem; }
.admission-stages { display: grid; grid-template-columns: 1fr 1fr; gap: 10px; margin-top: 12px; }
.admission-stages article { padding: 12px; border-radius: 10px; background: rgba(255,255,255,.85); }
.admission-stages article>div:first-child { display: flex; align-items: center; gap: 7px; }.admission-stages p { margin: 6px 0; color: #718079; font-size: .72rem; }
.admission-checks { display: flex; flex-wrap: wrap; gap: 5px; }
.prompt-template-editor :deep(textarea) { font-family: "IBM Plex Mono", "SFMono-Regular", Consolas, monospace; font-size: .78rem; line-height: 1.55; }
@media (max-width: 700px) { .admission-stages { grid-template-columns: 1fr; } }
</style>

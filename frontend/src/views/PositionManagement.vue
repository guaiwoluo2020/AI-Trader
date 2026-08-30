<template>
  <v-container fluid class="policy-page">
    <div class="page-hero mb-6">
      <div>
        <div class="eyebrow">POSITION CONTROL</div>
        <h1>持仓管理</h1>
        <p>把止损、止盈和持仓后的保护动作组合成可复用方案。</p>
      </div>
      <div class="d-flex ga-3 flex-wrap">
        <v-btn variant="outlined" size="large" prepend-icon="mdi-plus" @click="openCreate">
          新建方案
        </v-btn>
        <v-btn color="primary" size="large" prepend-icon="mdi-layers-triple" @click="openCreateMulti">
          新建多层结构方案
        </v-btn>
      </div>
    </div>

    <v-alert v-if="message" :type="messageType" closable class="mb-4" @click:close="message = ''">
      {{ message }}
    </v-alert>

    <v-card class="policy-workspace" elevation="0">
      <v-tabs v-model="activeTab" color="primary" class="policy-tabs">
        <v-tab value="mine"><v-icon start>mdi-shield-account-outline</v-icon>我的方案 <v-chip size="x-small" class="ml-2">{{ policies.length }}</v-chip></v-tab>
        <v-tab value="shared" @click="loadShared"><v-icon start>mdi-shield-star-outline</v-icon>平台方案库 <v-chip size="x-small" class="ml-2">{{ sharedPolicies.length }}</v-chip></v-tab>
      </v-tabs>

      <v-window v-model="activeTab">
        <v-window-item value="mine">
          <v-alert type="info" variant="tonal" density="compact" class="mx-5 mt-5">
            共享方案被其他用户应用后会冻结，不能再原地修改；需要演进时请复制新版本。
          </v-alert>
          <v-row v-if="policies.length" class="pa-5">
      <v-col v-for="policy in policies" :key="policy.policy_id" cols="12" md="6" xl="4">
        <v-card class="policy-card" elevation="0">
          <v-card-text>
            <div class="d-flex align-start justify-space-between">
              <div>
                <div class="text-h6 font-weight-bold">{{ policy.name }}</div>
                <div class="text-caption text-medium-emphasis">#{{ policy.policy_id }}</div>
              </div>
              <v-chip :color="policy.enabled ? 'success' : 'grey'" size="small">
                {{ policy.enabled ? '启用' : '停用' }}
              </v-chip>
              <v-chip v-if="policy.config.management_mode === 'multi_level_exit'" color="deep-purple" size="small" variant="tonal">多层结构</v-chip>
              <v-chip :color="policy.is_shared ? 'teal' : 'grey'" size="small" variant="tonal">
                {{ policy.readonly_reference ? '共享引用' : (policy.is_shared ? '已共享' : '私有') }}
              </v-chip>
            </div>
            <div class="rule-summary mt-5">
              <div><span>初始止损</span><strong>{{ ruleNames(policy.config.initial_stop_rules) }}</strong></div>
              <div><span>初始止盈</span><strong>{{ ruleNames(policy.config.initial_take_profit_rules) }}</strong></div>
              <div><span>持仓动作</span><strong>{{ ruleNames(policy.config.management_rules) || '仅固定保护' }}</strong></div>
              <div><span>场景规则</span><strong>{{ policy.config.setup_profiles?.length || 0 }} 个</strong></div>
            </div>
          </v-card-text>
          <v-card-actions>
            <v-btn variant="text" prepend-icon="mdi-content-copy" @click="copyPolicy(policy)">复制</v-btn>
            <v-btn variant="text" prepend-icon="mdi-pencil-outline" :disabled="policy.readonly_reference" @click="openEdit(policy)">编辑</v-btn>
            <v-spacer />
            <v-btn color="error" variant="text" icon="mdi-delete-outline" :disabled="policy.readonly_reference" @click="remove(policy)" />
          </v-card-actions>
        </v-card>
      </v-col>
    </v-row>
    <v-card v-else class="empty-state ma-5" elevation="0">
      <v-icon size="56">mdi-shield-plus-outline</v-icon>
      <h2>先建立第一套持仓管理方案</h2>
      <p>策略需要绑定方案后才能进入回测。</p>
    </v-card>
        </v-window-item>

        <v-window-item value="shared">
          <div class="shared-head">
            <div>
              <h3>平台共享持仓方案</h3>
              <p>可以直接引用其他用户共享的方案。引用后显示在“我的方案”，可直接被策略选择；如需修改，请先复制为私有方案。</p>
            </div>
            <v-btn icon="mdi-refresh" variant="text" :loading="sharedLoading" @click="loadShared"></v-btn>
          </div>
          <div v-if="sharedLoading" class="empty-state compact"><v-progress-circular indeterminate color="primary" /></div>
          <v-row v-else-if="sharedPolicies.length" class="pa-5 pt-0">
            <v-col v-for="policy in sharedPolicies" :key="`${policy.owner_user_id}-${policy.policy_id}`" cols="12" md="6" xl="4">
              <v-card class="policy-card shared-card" elevation="0">
                <v-card-text>
                  <div class="d-flex align-start justify-space-between">
                    <div>
                      <div class="text-h6 font-weight-bold">{{ policy.name }}</div>
                      <div class="text-caption text-medium-emphasis">由 {{ policy.owner_username }} 分享 · #{{ policy.policy_id }}</div>
                    </div>
                    <v-chip color="teal" size="small" variant="tonal">共享</v-chip>
                  </div>
                  <div class="rule-summary mt-5">
                    <div><span>初始止损</span><strong>{{ ruleNames(policy.config.initial_stop_rules) }}</strong></div>
                    <div><span>初始止盈</span><strong>{{ ruleNames(policy.config.initial_take_profit_rules) }}</strong></div>
                    <div><span>持仓动作</span><strong>{{ ruleNames(policy.config.management_rules) || '仅固定保护' }}</strong></div>
                  </div>
                  <v-alert :type="isSharedPolicyUsed(policy) ? 'success' : 'info'" variant="tonal" density="compact" class="mt-4">
                    {{ policy.usage_notice || '使用后会创建只读引用，源方案冻结后通过复制新版本演进。' }}
                  </v-alert>
                </v-card-text>
                <v-card-actions>
                  <v-btn
                    :color="isSharedPolicyUsed(policy) ? 'success' : 'primary'"
                    variant="tonal"
                    :prepend-icon="isSharedPolicyUsed(policy) ? 'mdi-check-circle-outline' : 'mdi-link-variant'"
                    :loading="usingSharedId === sharedPolicyKey(policy)"
                    :disabled="isSharedPolicyUsed(policy)"
                    @click="useShared(policy)"
                  >
                    {{ isSharedPolicyUsed(policy) ? '已使用' : '使用方案' }}
                  </v-btn>
                </v-card-actions>
              </v-card>
            </v-col>
          </v-row>
          <v-card v-else class="empty-state ma-5" elevation="0">
            <v-icon size="56">mdi-shield-search</v-icon>
            <h2>暂无平台共享方案</h2>
            <p>当其他用户共享持仓管理方案后，会出现在这里。</p>
          </v-card>
        </v-window-item>
      </v-window>
    </v-card>

    <v-dialog v-model="dialog" max-width="920" persistent>
      <v-card>
        <v-card-title class="d-flex align-center">
          {{ form.policy_id ? '编辑持仓管理方案' : '新建持仓管理方案' }}
          <v-spacer />
          <v-btn icon="mdi-close" variant="text" @click="dialog = false" />
        </v-card-title>
        <v-card-text>
          <v-row>
            <v-col cols="12" md="8"><v-text-field v-model="form.name" label="方案名称" /></v-col>
            <v-col cols="12" md="4"><v-switch v-model="form.enabled" color="success" label="启用方案" /></v-col>
            <v-col cols="12" md="4"><v-switch v-model="form.is_shared" color="success" label="共享到平台方案库" /></v-col>
          </v-row>
          <v-alert v-if="form.config.management_mode === 'multi_level_exit'" type="success" variant="tonal" class="mb-4">
            多层结构持仓管理：Internal、Swing、External 点位由后端虚拟执行分批止损/止盈；MT5 只设置位于最外层结构之外的灾难保护止损，不设置固定止盈。
          </v-alert>
          <template v-if="form.config.management_mode === 'multi_level_exit'">
            <div class="section-title mt-4">多层结构退出</div>
            <v-row>
              <v-col cols="12" md="3"><v-text-field v-model.number="form.config.multi_level_exit.disaster_stop_buffer_atr" label="灾难止损缓冲" suffix="ATR" type="number" min="0" step="0.1" /></v-col>
            </v-row>
            <div class="multi-level-grid">
              <div class="multi-level-head">结构层级</div><div class="multi-level-head">分批止损</div><div class="multi-level-head">分批止盈</div>
              <template v-for="layer in structureLayers" :key="layer.value">
                <strong>{{ layer.title }}</strong>
                <v-text-field v-model.number="form.config.multi_level_exit.stop_close_percent[layer.value]" suffix="%" type="number" min="0" max="100" density="compact" hide-details />
                <v-text-field v-model.number="form.config.multi_level_exit.take_profit_close_percent[layer.value]" suffix="%" type="number" min="0" max="100" density="compact" hide-details />
              </template>
            </div>
            <v-alert type="info" variant="tonal" density="compact" class="mt-3">最外层有效点位始终平掉全部剩余仓位；前两层比例按初始仓位计算，行情跳价跨越多个层级时会合并执行。</v-alert>
          </template>
          <RuleChain v-model="form.config.initial_stop_rules" title="初始止损规则链" kind="stop" />
          <RuleChain v-model="form.config.initial_take_profit_rules" title="初始止盈规则链" kind="take" />
          <div class="section-title mt-6">持仓后管理</div>
          <v-row>
            <v-col cols="12" md="4"><v-switch v-model="management.breakEven" color="success" label="盈利后移动至保本" /></v-col>
            <v-col cols="6" md="2"><v-text-field v-model.number="management.breakEvenR" label="启动盈利" suffix="R" type="number" min="0.1" step="0.1" /></v-col>
            <v-col cols="12" md="4"><v-switch v-model="management.trailing" color="success" label="启用移动止损" /></v-col>
            <v-col cols="6" md="2"><v-text-field v-model.number="management.trailingActivationR" label="移动启动" suffix="R" type="number" min="0.1" step="0.1" /></v-col>
            <v-col cols="6" md="2"><v-text-field v-model.number="management.trailingR" label="移动距离" suffix="R" type="number" min="0.1" step="0.1" /></v-col>
            <v-col cols="12" md="4"><v-switch v-model="management.pivotTrailing" color="success" label="按新转折点跟进" /></v-col>
            <v-col cols="6" md="2"><v-select v-model="management.pivotPeriod" :items="periods" label="转折周期" /></v-col>
            <v-col cols="12" md="4"><v-switch v-model="management.structureTrailing" color="success" label="按结构保护点跟进" /></v-col>
            <v-col cols="6" md="2"><v-text-field v-model.number="management.structureBuffer" label="ATR缓冲" suffix="ATR" type="number" min="0" step="0.05" /></v-col>
            <v-col cols="6" md="2"><v-text-field v-model.number="management.structureImprove" label="最小改善" suffix="ATR" type="number" min="0" step="0.05" /></v-col>
            <v-col cols="12" md="3"><v-switch v-model="management.reverse" color="success" label="反向信号退出" /></v-col>
            <v-col cols="12" md="3"><v-switch v-model="management.timeout" color="success" label="最大持仓时间" /></v-col>
            <v-col cols="6" md="2"><v-text-field v-model.number="management.timeoutBars" label="K线数量" type="number" min="1" /></v-col>
            <v-col cols="6" md="2"><v-select v-model="management.timeoutPeriod" :items="periods" label="计时周期" /></v-col>
          </v-row>
          <div class="section-title mt-6">分批止盈</div>
          <v-switch v-model="management.partialTakeProfit" color="success" label="启用分批止盈" />
          <div v-if="management.partialTakeProfit" class="partial-levels">
            <div v-for="(level, index) in management.partialLevels" :key="level.level_id" class="partial-row">
              <span class="rule-index">{{ index + 1 }}</span>
              <v-text-field v-model.number="level.trigger_r" label="触发盈利" suffix="R" type="number" min="0.1" step="0.1" density="compact" />
              <v-text-field v-model.number="level.close_percent" label="平仓比例" suffix="%" type="number" min="1" max="100" density="compact" />
              <v-select v-model="level.move_sl" :items="partialMoveOptions" label="触发后止损" density="compact" />
              <v-btn icon="mdi-delete-outline" color="error" variant="text" :disabled="management.partialLevels.length === 1" @click="removePartialLevel(index)" />
            </div>
            <v-btn variant="tonal" color="primary" prepend-icon="mdi-plus" @click="addPartialLevel">增加止盈层级</v-btn>
          </div>
          <v-row>
            <v-col cols="6" md="3"><v-text-field v-model.number="form.config.min_risk_reward" label="最小盈亏比" type="number" min="0" step="0.1" /></v-col>
            <v-col cols="6" md="3"><v-text-field v-model.number="form.config.min_stop_percent" label="最小止损比例" suffix="%" hint="默认 0.1%，按入场价计算" persistent-hint type="number" min="0" step="0.01" /></v-col>
            <v-col cols="6" md="3"><v-text-field v-model.number="form.config.max_stop_percent" label="最大止损比例" suffix="%" hint="默认 0.7%，按入场价计算" persistent-hint type="number" min="0" step="0.01" /></v-col>
          </v-row>
          <v-divider class="my-5" />
          <div class="section-title">连续亏损熔断</div>
          <v-alert type="info" variant="tonal" density="compact" class="mb-3">按“策略部署 × 交易账户”统计完整平仓后的净盈亏。触发后只禁止新开仓和新挂单；已有仓位的止损、止盈和风控平仓不受影响。</v-alert>
          <v-row align="center">
            <v-col cols="12" md="4"><v-switch v-model="form.config.loss_streak_circuit_breaker_enabled" color="error" label="启用连续亏损熔断" /></v-col>
            <v-col cols="6" md="4"><v-text-field v-model.number="form.config.loss_streak_limit" label="连续亏损次数" type="number" min="1" max="20" :disabled="!form.config.loss_streak_circuit_breaker_enabled" /></v-col>
            <v-col cols="6" md="4"><v-text-field v-model.number="form.config.loss_streak_pause_minutes" label="暂停时长" suffix="分钟" type="number" min="1" max="1440" :disabled="!form.config.loss_streak_circuit_breaker_enabled" /></v-col>
          </v-row>
          <v-divider class="my-6" />
          <div class="d-flex align-center ga-3"><div><div class="section-title">场景规则</div><div class="text-caption text-medium-emphasis">按具体Setup、通用场景族或信号来源覆盖默认规则；未命中时继续使用上面的默认方案。</div></div><v-spacer/><v-btn color="primary" variant="tonal" prepend-icon="mdi-plus" @click="addSetupProfile">新增场景规则</v-btn></div>
          <v-alert v-if="!form.config.setup_profiles?.length" type="info" variant="tonal" density="compact" class="mt-4">当前没有场景覆盖，所有信号使用默认持仓管理规则。</v-alert>
          <div v-for="(profile, index) in form.config.setup_profiles" :key="profile.profile_id" class="setup-profile mt-4">
            <div class="d-flex align-center ga-3"><div class="setup-profile-title"><strong>{{ profile.name }}</strong><small>系统推荐模板 · 参数自动维护</small></div><v-spacer/><v-switch v-model="profile.enabled" label="启用" color="success" density="compact" hide-details/><v-btn icon="mdi-delete-outline" color="error" variant="text" @click="removeSetupProfile(index)"/></div>
            <v-row class="mt-2"><v-col cols="12" md="5"><v-select v-model="profile.match_kind" :items="setupMatchKinds" label="匹配级别" density="compact" @update:model-value="normalizeProfileMatch(profile)"/></v-col><v-col cols="12" md="7"><v-select v-model="profile.match_value" :items="setupMatchOptions(profile.match_kind)" label="匹配内容" density="compact" @update:model-value="normalizeProfileMatch(profile)"/></v-col></v-row>
            <div class="recommendation-grid"><div><span>初始止损</span><strong>{{ ruleNames(effectiveProfileValue(profile, 'initial_stop_rules')) }}</strong><small>{{ profile.parameter_mode === 'custom' ? '用户自定义' : '继承默认方案' }}</small></div><div><span>初始止盈</span><strong>{{ ruleNames(effectiveProfileValue(profile, 'initial_take_profit_rules')) }}</strong><small>{{ profile.parameter_mode === 'custom' ? '用户自定义' : '继承默认方案' }}</small></div><div><span>持仓后管理</span><strong>{{ recommendedManagementText(profile) }}</strong><small>{{ profile.parameter_mode === 'custom' ? '用户自定义' : '系统按场景自动代入' }}</small></div><div><span>最低盈亏比</span><strong>{{ effectiveProfileValue(profile, 'min_risk_reward') }} R</strong><small>{{ profile.parameter_mode === 'custom' ? '用户自定义' : '继承默认方案' }}</small></div></div>
            <div class="d-flex align-center ga-2 mt-3"><v-alert type="success" variant="tonal" density="compact" class="flex-grow-1">{{ profile.parameter_mode === 'custom' ? '当前使用用户自定义参数。' : '推荐参数已自动写入；切换场景时会同步更换模板。' }}</v-alert><v-btn v-if="profile.parameter_mode !== 'custom'" color="primary" variant="tonal" @click="enableProfileCustomization(profile)">自定义参数</v-btn><v-btn v-else color="primary" variant="outlined" @click="restoreRecommendedProfile(profile)">恢复推荐值</v-btn></div>
            <div v-if="profile.parameter_mode === 'custom'" class="custom-profile-editor mt-3">
              <RuleChain v-model="profile.overrides.initial_stop_rules" title="场景初始止损" kind="stop" />
              <RuleChain v-model="profile.overrides.initial_take_profit_rules" title="场景初始止盈" kind="take" />
              <v-row class="mt-2"><v-col cols="12" md="4"><v-text-field v-model.number="profile.overrides.min_risk_reward" label="最低盈亏比" type="number" min="0" step="0.1" density="compact"/></v-col></v-row>
              <div class="section-title mt-2">持仓后管理参数</div>
              <div v-for="rule in profile.overrides.management_rules" :key="rule.type" class="profile-management-rule">
                <template v-if="rule.type === 'break_even'"><strong>移动至保本</strong><v-text-field v-model.number="rule.activation_r" label="启动盈利" suffix="R" type="number" min="0.1" step="0.1" density="compact" hide-details/></template>
                <template v-else-if="rule.type === 'trailing_stop'"><strong>移动止损</strong><v-text-field v-model.number="rule.activation_r" label="启动盈利" suffix="R" type="number" min="0.1" step="0.1" density="compact" hide-details/><v-text-field v-model.number="rule.distance_r" label="移动距离" suffix="R" type="number" min="0.1" step="0.1" density="compact" hide-details/></template>
                <template v-else-if="rule.type === 'partial_take_profit'"><strong>分批止盈</strong><div v-for="level in rule.levels" :key="level.level_id" class="profile-partial-level"><v-text-field v-model.number="level.trigger_r" label="触发盈利" suffix="R" type="number" min="0.1" step="0.1" density="compact" hide-details/><v-text-field v-model.number="level.close_percent" label="平仓比例" suffix="%" type="number" min="1" max="100" density="compact" hide-details/><v-select v-model="level.move_sl" :items="partialMoveOptions" label="触发后止损" density="compact" hide-details/></div></template>
              </div>
            </div>
            <div class="effective-preview">匹配顺序：具体 Setup ＞ 通用场景族 ＞ 信号来源；未命中时使用默认持仓管理方案。</div>
          </div>
        </v-card-text>
        <v-card-actions class="pa-5"><v-spacer /><v-btn variant="text" @click="dialog = false">取消</v-btn><v-btn color="primary" :loading="saving" @click="save">保存方案</v-btn></v-card-actions>
      </v-card>
    </v-dialog>
  </v-container>
</template>

<script setup>
import { computed, defineComponent, h, onMounted, reactive, ref } from 'vue'
import { marketAPI } from '../api/market'

const periods = ['M1', 'M5', 'M15', 'H1', 'H4']
const structureLayers = [
  { title: 'Internal 内部结构', value: 'internal' },
  { title: 'Swing 主结构', value: 'swing' },
  { title: 'External 外部结构', value: 'external' },
]
const labels = { signal: '信号建议', pivot: '转折点', atr: 'ATR', fixed_points: '固定点数', fixed_percent: '固定百分比', risk_reward: '盈亏比', none: '不设固定止盈', break_even: '保本', pivot_trailing: '转折跟进', trailing_stop: '移动止损', partial_take_profit: '分批止盈', reverse_signal: '反向退出', max_holding_bars: '时间退出' }
const partialMoveOptions = [
  { title: '不调整', value: 'none' },
  { title: '推到保本', value: 'break_even' },
  { title: '跟随移动止损', value: 'trail' },
]
const setupMatchKinds = [
  { title: '具体 Setup（最高优先）', value: 'setup_type' },
  { title: '通用场景族', value: 'setup_family' },
  { title: '信号来源（兜底）', value: 'signal_source' },
]
const setupFamilies = [
  { title: '反转 / 箱体低买高卖', value: 'reversal' },
  { title: '突破', value: 'breakout' },
  { title: '趋势跟随', value: 'trend_follow' },
  { title: '趋势回调', value: 'pullback' },
  { title: '均值回归', value: 'mean_reversion' },
  { title: '因子信号', value: 'factor' },
  { title: '手工信号', value: 'manual' },
  { title: '通用兜底', value: 'generic' },
]
const setupTypes = [
  { title: '箱体反转', value: 'range_reversal' },
  { title: '箱体突破', value: 'range_breakout' },
  { title: '趋势回调入场', value: 'trend_pullback' },
  { title: '趋势反弹入场', value: 'trend_rebound' },
  { title: '三角形突破', value: 'triangle_breakout' },
  { title: '转折点反转', value: 'pivot_reversal' },
  { title: '转折点突破', value: 'pivot_breakout' },
  { title: '关键位反转', value: 'key_level_reversal' },
  { title: '关键位突破', value: 'key_level_breakout' },
  { title: '均线交叉', value: 'ma_crossover' },
  { title: '因子入场', value: 'factor_entry' },
  { title: '手工入场', value: 'manual_entry' },
  { title: '通用入场', value: 'generic_entry' },
]
const signalSources = [
  { title: 'AI 行情建议', value: 'ai_entry' },
  { title: '转折点', value: 'pivot' },
  { title: '关键位', value: 'key_level' },
  { title: '均线', value: 'moving_average' },
  { title: 'Alpha 因子', value: 'alpha_factor' },
  { title: '结构交易计划', value: 'structure_plan' },
  { title: '手工信号', value: 'manual' },
]
const setupTypeFamilies = {
  range_reversal: 'reversal', pivot_reversal: 'reversal', key_level_reversal: 'reversal',
  range_breakout: 'breakout', triangle_breakout: 'breakout', pivot_breakout: 'breakout', key_level_breakout: 'breakout',
  trend_pullback: 'pullback', trend_rebound: 'pullback', ma_crossover: 'trend_follow',
  factor_entry: 'factor', manual_entry: 'manual', generic_entry: 'generic',
}
const recommendedFamilyOrder = ['reversal', 'breakout', 'pullback', 'trend_follow', 'mean_reversion', 'factor', 'manual', 'generic']
const policies = ref([])
const sharedPolicies = ref([])
const activeTab = ref('mine')
const dialog = ref(false)
const saving = ref(false)
const sharedLoading = ref(false)
const usingSharedId = ref('')
const message = ref('')
const messageType = ref('success')
const deepClone = value => JSON.parse(JSON.stringify(value))
const usedSharedPolicyKeys = computed(() => new Set(
  policies.value
    .filter(policy => policy.readonly_reference)
    .map(policy => `${policy.source_owner_user_id}-${policy.source_policy_id}`)
))

const defaultConfig = () => ({
  management_mode: 'standard',
  multi_level_exit: {
    disaster_stop_buffer_atr: 0.5,
    stop_close_percent: { internal: 30, swing: 40, external: 100 },
    take_profit_close_percent: { internal: 30, swing: 30, external: 100 },
  },
  initial_stop_rules: [{ type: 'pivot', period: 'M5', selection: 'nearest', max_age_bars: 100, buffer: { type: 'fixed_points', value: 0 } }, { type: 'fixed_percent', value: 0.003 }],
  initial_take_profit_rules: [{ type: 'risk_reward', value: 2 }],
  management_rules: [], min_risk_reward: 1, min_stop_percent: 0.1, max_stop_percent: 0.7,
  min_stop_distance: 0, max_stop_distance: 0,
  loss_streak_circuit_breaker_enabled: true, loss_streak_limit: 3, loss_streak_pause_minutes: 10,
  setup_profiles: [],
})
const form = reactive({ policy_id: '', name: '', enabled: true, is_shared: false, config: defaultConfig() })
const management = reactive({
  breakEven: true, breakEvenR: 1,
  trailing: true, trailingActivationR: 1, trailingR: 0.8,
  partialTakeProfit: true,
  partialLevels: [
    { level_id: 'tp1', trigger_r: 1, close_percent: 30, move_sl: 'break_even' },
    { level_id: 'tp2', trigger_r: 2, close_percent: 30, move_sl: 'trail' },
  ],
  pivotTrailing: true, pivotPeriod: 'M5',
  structureTrailing: true, structureBuffer: 0.15, structureImprove: 0.10,
  reverse: false, timeout: false, timeoutBars: 120, timeoutPeriod: 'M1'
})

const RuleChain = defineComponent({
  props: { modelValue: Array, title: String, kind: String }, emits: ['update:modelValue'],
  setup(props, { emit }) {
    const options = computed(() => props.kind === 'stop'
      ? ['pivot', 'signal', 'atr', 'fixed_points', 'fixed_percent']
      : ['risk_reward', 'pivot', 'signal', 'atr', 'fixed_points', 'fixed_percent', 'none'])
    const current = () => Array.isArray(props.modelValue) ? props.modelValue : []
    const update = (index, key, value) => { const next = deepClone(current()); next[index][key] = value; emit('update:modelValue', next) }
    const remove = index => emit('update:modelValue', current().filter((_, i) => i !== index))
    const add = () => emit('update:modelValue', [...current(), { type: props.kind === 'stop' ? 'fixed_percent' : 'risk_reward', value: props.kind === 'stop' ? 0.003 : 2 }])
    const asPercent = value => {
      const decimal = Number(value)
      return Number.isFinite(decimal) ? Number((decimal * 100).toFixed(8)) : 0
    }
    const asDecimal = value => {
      const percent = Number(value)
      return Number.isFinite(percent) ? percent / 100 : 0
    }
    return () => h('div', { class: 'rule-chain mt-5' }, [
      h('div', { class: 'd-flex align-center mb-2' }, [h('div', { class: 'section-title' }, props.title), h('div', { class: 'flex-grow-1' }), h('button', { class: 'add-rule', onClick: add }, '+ 添加兜底规则')]),
      ...current().map((rule, index) => h('div', { class: 'rule-row' }, [
        h('span', { class: 'rule-index' }, String(index + 1)),
        h('select', { value: rule.type, onChange: e => update(index, 'type', e.target.value) }, options.value.map(value => h('option', { value }, labels[value]))),
        rule.type === 'pivot' ? h('select', { value: rule.period || 'M5', onChange: e => update(index, 'period', e.target.value) }, periods.map(value => h('option', { value }, value))) : null,
        rule.type === 'fixed_percent'
          ? h('div', { class: 'percent-input' }, [
              h('input', { type: 'number', min: 0, max: 100, step: 0.01, value: asPercent(rule.value), onInput: e => update(index, 'value', asDecimal(e.target.value)) }),
              h('span', { class: 'percent-suffix' }, '%'),
            ])
          : ['atr', 'fixed_points', 'risk_reward'].includes(rule.type)
            ? h('input', { type: 'number', min: 0, step: 0.1, value: rule.value, onInput: e => update(index, 'value', Number(e.target.value)) })
            : null,
        h('button', { class: 'remove-rule', disabled: current().length === 1, onClick: () => remove(index) }, '移除'),
      ])),
    ])
  }
})

function syncManagement(rules = []) {
  const find = type => rules.find(rule => rule.type === type)
  const be = find('break_even'); management.breakEven = Boolean(be); management.breakEvenR = be?.activation_r || 1
  const trail = find('trailing_stop'); management.trailing = Boolean(trail); management.trailingActivationR = trail?.activation_r || 1; management.trailingR = trail?.distance_r || 0.8
  const partial = find('partial_take_profit'); management.partialTakeProfit = Boolean(partial); management.partialLevels = deepClone(partial?.levels?.length ? partial.levels : [{ level_id: 'tp1', trigger_r: 1, close_percent: 30, move_sl: 'break_even' }])
  const pivot = find('pivot_trailing'); management.pivotTrailing = Boolean(pivot && pivot.enabled !== false); management.pivotPeriod = pivot?.period || 'M5'
  const structure = find('structure_trailing'); management.structureTrailing = Boolean(structure && structure.enabled !== false); management.structureBuffer = structure?.buffer_value ?? 0.15; management.structureImprove = structure?.min_improvement_atr ?? 0.10
  management.reverse = Boolean(find('reverse_signal'))
  const timeout = find('max_holding_bars'); management.timeout = Boolean(timeout); management.timeoutBars = timeout?.bars || 120; management.timeoutPeriod = timeout?.period || 'M1'
}
function setupMatchOptions(kind) {
  if (kind === 'setup_type') return setupTypes
  if (kind === 'signal_source') return signalSources
  return setupFamilies
}
function normalizeProfileMatch(profile) {
  const kind = profile.match_kind || 'setup_family'
  const options = setupMatchOptions(kind)
  if (!options.some(item => item.value === profile.match_value)) {
    profile.match_value = options[0]?.value || ''
  }
  profile.match = { setup_types: [], setup_families: [], signal_sources: [] }
  if (kind === 'setup_type') profile.match.setup_types = [profile.match_value]
  else if (kind === 'signal_source') profile.match.signal_sources = [profile.match_value]
  else profile.match.setup_families = [profile.match_value]
  applyRecommendedProfile(profile)
}
function profileFamily(profile) {
  if (profile.match_kind === 'setup_family') return profile.match_value
  if (profile.match_kind === 'setup_type') return setupTypeFamilies[profile.match_value] || 'generic'
  return 'generic'
}
function recommendedManagementRules(family) {
  if (['reversal', 'mean_reversion'].includes(family)) return [
    { type: 'break_even', activation_r: 1, offset_r: 0 },
    { type: 'trailing_stop', activation_r: 1, distance_r: 0.8 },
    { type: 'partial_take_profit', levels: [{ level_id: 'reversal_tp1', trigger_r: 1, close_percent: 50, move_sl: 'break_even' }] },
  ]
  if (family === 'breakout') return [
    { type: 'break_even', activation_r: 1, offset_r: 0 },
    { type: 'trailing_stop', activation_r: 1.5, distance_r: 1 },
    { type: 'partial_take_profit', levels: [
      { level_id: 'breakout_tp1', trigger_r: 1, close_percent: 30, move_sl: 'break_even' },
      { level_id: 'breakout_tp2', trigger_r: 2, close_percent: 30, move_sl: 'trail' },
    ] },
  ]
  if (['pullback', 'trend_follow', 'trend'].includes(family)) return [
    { type: 'break_even', activation_r: 1, offset_r: 0 },
    { type: 'trailing_stop', activation_r: 1, distance_r: 0.8 },
    { type: 'partial_take_profit', levels: [
      { level_id: 'trend_tp1', trigger_r: 1, close_percent: 30, move_sl: 'break_even' },
      { level_id: 'trend_tp2', trigger_r: 2, close_percent: 30, move_sl: 'trail' },
    ] },
  ]
  return null
}
function selectedMatchTitle(profile) {
  return setupMatchOptions(profile.match_kind).find(item => item.value === profile.match_value)?.title || '通用场景'
}
function applyRecommendedProfile(profile) {
  const rules = recommendedManagementRules(profileFamily(profile))
  profile.name = `${selectedMatchTitle(profile)} · 系统推荐`
  profile.parameter_mode = 'recommended'
  profile.priority = 100
  profile.inherit_default = true
  profile.overrides = rules ? { management_rules: deepClone(rules) } : {}
}
function enableProfileCustomization(profile) {
  profile.parameter_mode = 'custom'
  profile.overrides ||= {}
  profile.overrides.initial_stop_rules ||= deepClone(form.config.initial_stop_rules)
  profile.overrides.initial_take_profit_rules ||= deepClone(form.config.initial_take_profit_rules)
  profile.overrides.management_rules ||= deepClone(
    form.config.management_rules?.length ? form.config.management_rules : buildManagementRules()
  )
  profile.overrides.min_risk_reward ??= Number(form.config.min_risk_reward ?? 1)
}
function restoreRecommendedProfile(profile) { applyRecommendedProfile(profile) }
function managementRulesText(rules = []) {
  const enabledRules = rules.filter(rule => rule.enabled !== false)
  if (!enabledRules.length) return '继承默认持仓后管理'
  return enabledRules.map(rule => {
    if (rule.type === 'break_even') return `${rule.activation_r}R 保本`
    if (rule.type === 'trailing_stop') return `${rule.activation_r}R 启动、${rule.distance_r}R 移动止损`
    if (rule.type === 'partial_take_profit') return (rule.levels || []).map(level => `${level.trigger_r}R 平仓 ${level.close_percent}%`).join('，')
    return labels[rule.type] || rule.type
  }).join('；')
}
function recommendedManagementText(profile) {
  const rules = Object.hasOwn(profile.overrides || {}, 'management_rules')
    ? profile.overrides.management_rules
    : (form.config.management_rules || buildManagementRules())
  return managementRulesText(rules)
}
function effectiveProfileValue(profile, key) {
  if (Object.hasOwn(profile.overrides || {}, key)) return profile.overrides[key]
  return form.config[key]
}
function hydrateSetupProfiles() {
  const profiles = Array.isArray(form.config.setup_profiles) ? form.config.setup_profiles : []
  form.config.setup_profiles = profiles.map((raw, index) => {
    const profile = deepClone(raw)
    const match = profile.match || {}
    if (match.setup_types?.length) {
      profile.match_kind = 'setup_type'
      profile.match_value = match.setup_types[0]
    } else if (match.setup_families?.length) {
      profile.match_kind = 'setup_family'
      profile.match_value = match.setup_families[0]
    } else {
      profile.match_kind = 'signal_source'
      profile.match_value = match.signal_sources?.[0] || 'ai_entry'
    }
    profile.profile_id ||= `profile-${Date.now()}-${index}`
    profile.enabled = profile.enabled !== false
    profile.priority = Number(profile.priority ?? 100)
    profile.inherit_default = true
    profile.overrides ||= {}
    if (profile.parameter_mode === 'custom') enableProfileCustomization(profile)
    else applyRecommendedProfile(profile)
    return profile
  })
}
function addSetupProfile() {
  const configured = new Set((form.config.setup_profiles || [])
    .filter(item => item.match_kind === 'setup_family')
    .map(item => item.match_value))
  const family = recommendedFamilyOrder.find(item => !configured.has(item)) || 'generic'
  const profile = {
    profile_id: `profile-${Date.now()}`,
    name: '', enabled: true, priority: 100, inherit_default: true,
    match_kind: 'setup_family', match_value: family, match: {}, overrides: {},
  }
  normalizeProfileMatch(profile)
  form.config.setup_profiles.push(profile)
}
function removeSetupProfile(index) { form.config.setup_profiles.splice(index, 1) }
function buildManagementRules() {
  const rules = []
  if (management.breakEven) rules.push({ type: 'break_even', activation_r: management.breakEvenR, offset_r: 0 })
  rules.push({ type: 'pivot_trailing', enabled: management.pivotTrailing, period: management.pivotPeriod, buffer: { type: 'fixed_points', value: 0 } })
  rules.push({ type: 'structure_trailing', enabled: management.structureTrailing, structure_layer: 'swing', buffer_type: 'atr', buffer_value: Number(management.structureBuffer) || 0.15, min_improvement_atr: Number(management.structureImprove) || 0.10, confirm_bars: 1, cooldown_seconds: 30 })
  if (management.trailing) rules.push({ type: 'trailing_stop', activation_r: management.trailingActivationR, distance_r: management.trailingR })
  if (management.partialTakeProfit) rules.push({ type: 'partial_take_profit', levels: deepClone(management.partialLevels) })
  if (management.reverse) rules.push({ type: 'reverse_signal' })
  if (management.timeout) rules.push({ type: 'max_holding_bars', period: management.timeoutPeriod, bars: management.timeoutBars })
  return rules
}
function serializeConfig() {
  const config = deepClone(form.config)
  config.management_rules = buildManagementRules()
  config.setup_profiles = (config.setup_profiles || []).map(profile => {
    const clean = {
      profile_id: profile.profile_id,
      name: profile.name,
      enabled: profile.enabled,
      priority: profile.priority,
      inherit_default: true,
      parameter_mode: profile.parameter_mode === 'custom' ? 'custom' : 'recommended',
      match: profile.match,
      overrides: profile.overrides,
    }
    return clean
  })
  return config
}
function addPartialLevel() {
  const next = management.partialLevels.length + 1
  management.partialLevels.push({ level_id: `tp${next}`, trigger_r: next, close_percent: 25, move_sl: 'trail' })
}
function removePartialLevel(index) { management.partialLevels.splice(index, 1) }
async function load() { const data = await marketAPI.getPositionManagementPolicies(); policies.value = data.policies || [] }
async function loadShared() {
  sharedLoading.value = true
  try {
    const data = await marketAPI.getSharedPositionManagementPolicies()
    sharedPolicies.value = data.policies || []
  } catch (error) {
    messageType.value = 'error'
    message.value = error.response?.data?.detail || '加载平台方案库失败'
  } finally {
    sharedLoading.value = false
  }
}
function sharedPolicyKey(policy) {
  return `${policy.owner_user_id}-${policy.policy_id}`
}
function isSharedPolicyUsed(policy) {
  return usedSharedPolicyKeys.value.has(sharedPolicyKey(policy))
}
function openCreate() { Object.assign(form, { policy_id: '', name: '', enabled: true, is_shared: false, config: defaultConfig() }); syncManagement([{ type: 'break_even', activation_r: 1 }, { type: 'pivot_trailing', period: 'M5' }, { type: 'structure_trailing', structure_layer: 'swing', buffer_type: 'atr', buffer_value: 0.15, min_improvement_atr: 0.10 }, { type: 'trailing_stop', activation_r: 1, distance_r: 0.8 }, { type: 'partial_take_profit', levels: [{ level_id: 'tp1', trigger_r: 1, close_percent: 30, move_sl: 'break_even' }, { level_id: 'tp2', trigger_r: 2, close_percent: 30, move_sl: 'trail' }] }]); hydrateSetupProfiles(); dialog.value = true }
function openCreateMulti() {
  openCreate()
  form.name = '多层结构持仓管理'
  form.config.management_mode = 'multi_level_exit'
  // Structure levels own partial exits; generic R-based partial exits would
  // otherwise compete for the same remaining volume.
  syncManagement([])
}
function openEdit(policy) { Object.assign(form, deepClone(policy)); form.config.setup_profiles ||= []; syncManagement(form.config.management_rules); hydrateSetupProfiles(); dialog.value = true }
async function save() { saving.value = true; try { const payload = { name: form.name, enabled: form.enabled, visibility: form.is_shared ? 'shared' : 'private', config: serializeConfig() }; if (form.policy_id) await marketAPI.updatePositionManagementPolicy(form.policy_id, payload); else await marketAPI.createPositionManagementPolicy(payload); dialog.value = false; messageType.value = 'success'; message.value = '持仓管理方案已保存'; await load() } catch (error) { messageType.value = 'error'; message.value = error.response?.data?.detail || error.message } finally { saving.value = false } }
async function remove(policy) { if (!confirm(`确定删除“${policy.name}”吗？`)) return; try { await marketAPI.deletePositionManagementPolicy(policy.policy_id); await load() } catch (error) { messageType.value = 'error'; message.value = error.response?.data?.detail || error.message } }
async function copyPolicy(policy) { try { const data = await marketAPI.copyPositionManagementPolicy(policy.policy_id); messageType.value = 'success'; message.value = data.message || '已复制方案'; await load() } catch (error) { messageType.value = 'error'; message.value = error.response?.data?.detail || error.message } }
async function useShared(policy) {
  if (isSharedPolicyUsed(policy) || usingSharedId.value) return
  usingSharedId.value = sharedPolicyKey(policy)
  try {
    const data = await marketAPI.useSharedPositionManagementPolicy(
      policy.owner_user_id, policy.policy_id
    )
    messageType.value = 'success'
    message.value = data.message || '已添加共享方案引用'
    activeTab.value = 'mine'
    await load()
    await loadShared()
  } catch (error) {
    messageType.value = 'error'
    message.value = error.response?.data?.detail || '使用共享方案失败'
  } finally {
    usingSharedId.value = ''
  }
}
function ruleNames(rules = []) { return rules.filter(rule => rule.enabled !== false).map(rule => labels[rule.type] || rule.type).join(' → ') }
onMounted(load)
</script>

<style scoped>
.policy-page { max-width: 1500px; padding: 32px; }
.page-hero { display:flex; align-items:end; justify-content:space-between; padding:32px; border-radius:24px; color:#17342d; background:linear-gradient(125deg,#dff3e8,#f4edda 72%,#f4d8b5); }
.page-hero h1 { font-family:Georgia,serif; font-size:42px; line-height:1; margin:6px 0 10px; }
.page-hero p { margin:0; color:#52635e; }.eyebrow { font-size:12px; letter-spacing:.22em; font-weight:800; color:#23745c; }
.policy-workspace { overflow:hidden; border:1px solid #dce5df; border-radius:24px; background:rgba(255,255,255,.76); }
.policy-tabs { padding:8px 16px 0; border-bottom:1px solid #e2ebe5; background:rgba(248,251,249,.88); }
.shared-head { display:flex; align-items:center; justify-content:space-between; gap:16px; padding:24px 26px 12px; }
.shared-head h3 { margin:0; color:#17342d; font-size:20px; }
.shared-head p { margin:4px 0 0; color:#607269; font-size:13px; }
.policy-card { height:100%; border:1px solid #dce5df; border-radius:20px; background:linear-gradient(155deg,#fff,#f8faf7); }
.shared-card { background:linear-gradient(155deg,#fff,#effbf6); }
.rule-summary { display:grid; gap:12px; }.rule-summary div { display:flex; flex-direction:column; padding:12px; border-radius:12px; background:#edf4ef; }.rule-summary span { font-size:11px; color:#718078; }.rule-summary strong { margin-top:3px; font-size:13px; }
.empty-state { padding:70px; text-align:center; border:1px dashed #aebdb4; border-radius:22px; color:#607269; }
.empty-state.compact { min-height:150px; display:grid; place-items:center; padding:34px; }
.section-title { font-size:14px; font-weight:800; color:#26483d; }.rule-chain { padding:18px; border:1px solid #dce7e0; border-radius:16px; background:#f8fbf9; }
.rule-row { display:grid; grid-template-columns:32px minmax(160px,1fr) 110px 110px 62px; gap:10px; align-items:center; margin-top:8px; }.rule-row select,.rule-row input { height:40px; border:1px solid #c9d6ce; border-radius:8px; padding:0 10px; background:white; }.percent-input { position:relative; }.percent-input input { width:100%; padding-right:28px; }.percent-suffix { position:absolute; right:10px; top:50%; transform:translateY(-50%); color:#607269; font-size:13px; pointer-events:none; }.rule-index { width:28px;height:28px;border-radius:50%;display:grid;place-items:center;background:#245d4c;color:white;font-size:12px; }.add-rule,.remove-rule { border:0;background:transparent;color:#23745c;cursor:pointer;font-weight:700; }.remove-rule { color:#b84b43; }
.partial-levels { display:grid; gap:10px; padding:16px; border:1px solid #dce7e0; border-radius:16px; background:#fbfdfb; }
.multi-level-grid { display:grid; grid-template-columns:minmax(190px,1.4fr) repeat(2,minmax(150px,1fr)); gap:10px; align-items:center; padding:16px; border:1px solid #cfe1d7; border-radius:16px; background:#f4faf6; }
.multi-level-head { color:#607269; font-size:12px; font-weight:800; }
.partial-row { display:grid; grid-template-columns:32px 1fr 1fr 1fr 44px; gap:10px; align-items:center; }
.setup-profile { padding:20px; border:1px solid #cddfd5; border-radius:18px; background:linear-gradient(145deg,#fbfdfb,#f1f7f3); }
.setup-profile-title { display:flex; flex-direction:column; gap:3px; color:#254b3f; }.setup-profile-title small { color:#74837c; font-size:11px; }
.recommendation-grid { display:grid; grid-template-columns:repeat(4,minmax(0,1fr)); gap:10px; }.recommendation-grid>div { display:flex; flex-direction:column; min-height:92px; padding:13px; border-radius:12px; background:#eaf3ed; }.recommendation-grid span,.recommendation-grid small { color:#74837c; font-size:11px; }.recommendation-grid strong { margin:5px 0; color:#274a3f; font-size:13px; }
.custom-profile-editor { padding:16px; border:1px dashed #9cb9a9; border-radius:14px; background:#fff; }.profile-management-rule { display:grid; grid-template-columns:140px repeat(3,minmax(130px,1fr)); align-items:center; gap:10px; margin-top:10px; padding:12px; border-radius:10px; background:#f3f7f4; }.profile-partial-level { display:contents; }
.effective-preview { margin-top:12px; padding:12px 14px; border-radius:10px; color:#476158; background:#e7f1eb; font-size:12px; }
@media (max-width:700px) { .policy-page{padding:16px}.page-hero{align-items:start;gap:20px;flex-direction:column}.rule-row{grid-template-columns:28px 1fr}.rule-row select,.rule-row input,.rule-row .percent-input,.remove-rule{grid-column:2}.page-hero h1{font-size:34px}.recommendation-grid{grid-template-columns:1fr}.profile-management-rule{grid-template-columns:1fr}.profile-partial-level{display:grid;grid-template-columns:1fr} }
</style>

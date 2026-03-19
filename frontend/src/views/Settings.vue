<template>
  <v-container fluid>
    <v-row>
      <v-col cols="12">
        <h1 class="mb-4">系统设置</h1>
      </v-col>
    </v-row>

    <!-- 自动交易配置 -->
    <v-row>
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
    <v-row class="mt-4">
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
              <v-expansion-panel v-for="strategy in strategies" :key="strategy.symbol">
                <v-expansion-panel-title>
                  <div class="d-flex align-center">
                    <strong class="mr-3">{{ strategy.symbol }}</strong>
                    <v-chip :color="strategy.enabled ? 'success' : 'grey'" size="x-small">
                      {{ strategy.enabled ? '启用' : '禁用' }}
                    </v-chip>
                    <span class="text-caption grey--text ml-3">{{ strategy.strategy_name }}</span>
                  </div>
                </v-expansion-panel-title>
                <v-expansion-panel-text>
                  <!-- 基本信息 -->
                  <v-row class="mb-3">
                    <v-col cols="12" md="3">
                      <v-switch
                        v-model="strategy.enabled"
                        label="启用策略"
                        dense
                        hide-details
                        @change="updateStrategy(strategy)"
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
                                  v-model="getSignalConfig(strategy, 'pivot').enabled"
                                  density="compact"
                                  hide-details
                                  @change="onSignalConfigChange(strategy, 'pivot')"
                                ></v-checkbox>
                              </td>
                              <td v-for="period in ['M1', 'M5', 'M15', 'H1', 'H4']" :key="period">
                                <div class="d-flex align-center">
                                  <v-checkbox
                                    v-model="getPeriodConfig(strategy, 'pivot', period).enabled"
                                    density="compact"
                                    hide-details
                                    :disabled="!getSignalConfig(strategy, 'pivot').enabled"
                                    @change="onSignalConfigChange(strategy, 'pivot')"
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
                                  v-model="getSignalConfig(strategy, 'key_level').enabled"
                                  density="compact"
                                  hide-details
                                  @change="onSignalConfigChange(strategy, 'key_level')"
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
                                  v-model="getSignalConfig(strategy, 'ai_entry').enabled"
                                  density="compact"
                                  hide-details
                                  @change="onSignalConfigChange(strategy, 'ai_entry')"
                                ></v-checkbox>
                              </td>
                              <td v-for="period in ['M1', 'M5', 'M15', 'H1', 'H4']" :key="period">
                                <div class="d-flex align-center">
                                  <v-checkbox
                                    v-model="getPeriodConfig(strategy, 'ai_entry', period).enabled"
                                    density="compact"
                                    hide-details
                                    :disabled="!getSignalConfig(strategy, 'ai_entry').enabled"
                                    @change="onSignalConfigChange(strategy, 'ai_entry')"
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
                      <v-btn color="primary" small @click="updateStrategy(strategy)" :loading="strategySaving === strategy.symbol">
                        <v-icon start small>mdi-content-save</v-icon>
                        保存
                      </v-btn>
                      <v-btn color="error" small outlined class="ml-2" @click="deleteStrategy(strategy.symbol)">
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
                  :items="symbolsWithoutStrategy"
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
              策略配置：信号权重、过滤规则、仓位管理、止损止盈等。每个品种绑定一个策略。
            </div>
          </v-card-text>
        </v-card>
      </v-col>
    </v-row>

    <!-- 大模型配置 -->
    <v-row class="mt-4">
      <v-col cols="12">
        <v-card>
          <v-card-title>
            <v-icon class="mr-2">mdi-brain</v-icon>
            大模型配置
          </v-card-title>
          <v-card-text>
            <v-form ref="llmForm">
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
              配置大模型用于生成AI趋势分析和交易建议。支持OpenAI兼容的API接口。
            </div>
          </v-card-text>
        </v-card>
      </v-col>
    </v-row>

    <!-- 品种数据状态 -->
    <v-row class="mt-4">
      <v-col cols="12">
        <v-card>
          <v-card-title>
            <v-icon class="mr-2">mdi-chart-line</v-icon>
            品种数据状态
            <v-btn icon small class="ml-2" @click="loadSymbolStatus" :loading="symbolStatusLoading">
              <v-icon small>mdi-refresh</v-icon>
            </v-btn>
          </v-card-title>
          <v-card-text>
            <v-table dense v-if="symbolStatus.length > 0">
              <template v-slot:default>
                <thead>
                  <tr>
                    <th>品种</th>
                    <th>数据状态</th>
                    <th>M1数量</th>
                    <th>最新M1时间</th>
                    <th>距上次更新</th>
                    <th>市场状态</th>
                  </tr>
                </thead>
                <tbody>
                  <tr v-for="item in symbolStatus" :key="item.symbol">
                    <td><strong>{{ item.symbol }}</strong></td>
                    <td>
                      <v-chip size="x-small" :color="item.has_data ? 'success' : 'error'">
                        {{ item.has_data ? '有数据' : '无数据' }}
                      </v-chip>
                    </td>
                    <td>{{ item.m1_count || 0 }}</td>
                    <td>{{ item.latest_m1_time || '-' }}</td>
                    <td>
                      <span v-if="item.seconds_ago !== null">{{ item.seconds_ago }}秒前</span>
                      <span v-else>-</span>
                    </td>
                    <td>
                      <v-chip size="x-small" :color="getMarketStatusColor(item.market_status)">
                        {{ getMarketStatusText(item.market_status) }}
                      </v-chip>
                    </td>
                  </tr>
                </tbody>
              </template>
            </v-table>
            <div v-else class="text-center grey--text py-4">
              <v-icon large>mdi-database-off</v-icon>
              <div class="mt-2">暂无已配置的品种</div>
            </div>

            <div class="text-caption grey--text mt-3">
              <v-icon small>mdi-information</v-icon>
              显示交易配置中的品种K线数据状态。M1数据超过3分钟未更新视为休市。
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
import { ref, computed, onMounted } from 'vue'
import { marketAPI } from '@/api/market'

export default {
  name: 'Settings',
  setup() {
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
      enabled: false
    })
    const showApiKey = ref(false)
    const llmSaving = ref(false)

    // 品种数据状态
    const symbolStatus = ref([])
    const symbolStatusLoading = ref(false)

    // 可用品种列表（已连接但未配置的）
    const availableSymbols = computed(() => {
      const configured = Object.keys(tradeConfig.value.symbol_config || {})
      return symbols.value.filter(s => !configured.includes(s))
    })

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
      try {
        const data = await marketAPI.getLLMConfig()
        if (data.config) {
          llmConfig.value = {
            api_key: '',  // 不显示已有key，只显示是否设置
            api_key_set: data.config.api_key_set || false,
            api_base: data.config.api_base || 'https://api.openai.com/v1',
            model: data.config.model || 'gpt-4o-mini',
            enabled: data.config.enabled || false
          }
        }
      } catch (err) {
        console.error('加载大模型配置失败:', err)
      }
    }

    // 保存大模型配置
    const saveLLMConfig = async () => {
      llmSaving.value = true
      try {
        const updateData = {
          api_base: llmConfig.value.api_base,
          model: llmConfig.value.model
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

    // 加载品种数据状态
    const loadSymbolStatus = async () => {
      symbolStatusLoading.value = true
      try {
        const data = await marketAPI.getConfiguredSymbols()
        if (data.status === 'ok') {
          symbolStatus.value = data.symbols || []
        }
      } catch (err) {
        console.error('加载品种状态失败:', err)
      } finally {
        symbolStatusLoading.value = false
      }
    }

    // 获取市场状态颜色
    const getMarketStatusColor = (status) => {
      switch (status) {
        case 'active': return 'success'
        case 'stale': return 'warning'
        case 'closed': return 'error'
        default: return 'grey'
      }
    }

    // 获取市场状态文本
    const getMarketStatusText = (status) => {
      switch (status) {
        case 'active': return '活跃'
        case 'stale': return '数据过期'
        case 'closed': return '休市中'
        default: return '未知'
      }
    }

    // ==================== 策略配置 ====================

    // 策略数据
    const strategies = ref([])
    const strategiesLoading = ref(false)
    const strategySaving = ref(null)
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

    // 没有策略的品种
    const symbolsWithoutStrategy = computed(() => {
      const strategySymbols = strategies.value.map(s => s.symbol)
      return symbols.value.filter(s => !strategySymbols.includes(s))
    })

    // 加载策略列表
    const loadStrategies = async () => {
      strategiesLoading.value = true
      try {
        const data = await marketAPI.getStrategies()
        if (data.status === 'ok') {
          strategies.value = data.strategies || []
        }
      } catch (err) {
        console.error('加载策略配置失败:', err)
      } finally {
        strategiesLoading.value = false
      }
    }

    // 更新策略
    const updateStrategy = async (strategy) => {
      strategySaving.value = strategy.symbol
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

        const data = await marketAPI.updateStrategy(strategy.symbol, {
          enabled: strategy.enabled,
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
        errorMessage.value = `保存策略失败: ${err.message}`
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

    // 信号配置变更处理
    const onSignalConfigChange = (strategy, source) => {
      // 触发保存
      updateStrategy(strategy)
    }

    // 删除策略
    const deleteStrategy = async (symbol) => {
      if (!confirm(`确定要删除 ${symbol} 的策略配置吗？`)) return
      try {
        const data = await marketAPI.deleteStrategy(symbol)
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
        const data = await marketAPI.updateStrategy(newStrategySymbol.value, {
          enabled: true,
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

    onMounted(() => {
      loadSymbols()
      loadTradeConfig()
      loadLLMConfig()
      loadSymbolStatus()
      loadStrategies()
    })

    return {
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
      saveTradeConfig,
      addSymbolConfig,
      removeSymbolConfig,
      onSymbolSelect,
      // 大模型配置
      llmConfig,
      showApiKey,
      llmSaving,
      saveLLMConfig,
      // 品种数据状态
      symbolStatus,
      symbolStatusLoading,
      loadSymbolStatus,
      getMarketStatusColor,
      getMarketStatusText,
      // 策略配置
      strategies,
      strategiesLoading,
      strategySaving,
      newStrategySymbol,
      newStrategyName,
      consistencyOptions,
      slModeOptions,
      tpModeOptions,
      symbolsWithoutStrategy,
      loadStrategies,
      updateStrategy,
      deleteStrategy,
      addStrategy,
      // 信号配置
      getSignalConfig,
      getPeriodConfig,
      onSignalConfigChange
    }
  }
}
</script>
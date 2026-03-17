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
            <v-simple-table dense>
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
                      <v-btn x-small color="primary" @click="saveTradeConfig">保存</v-btn>
                      <v-btn x-small color="error" outlined class="ml-1" @click="removeSymbolConfig(symbol)">删除</v-btn>
                    </td>
                  </tr>
                </tbody>
              </template>
            </v-simple-table>

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
                  <v-icon left small>mdi-plus</v-icon>
                  添加
                </v-btn>
              </v-col>
            </v-row>

            <div class="text-caption grey--text mt-3">
              <v-icon small>mdi-information</v-icon>
              支撑压力策略: M1周期接近转折点时自动生成交易指令，止损偏移为固定点数。<br/>
              关键点位策略: 价格接近关键点位时生成反向订单。例如下降趋势接近5000时生成买单。阈值表示触发距离（默认0.0008）。
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
                    <v-icon left>mdi-content-save</v-icon>
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
            <v-simple-table dense v-if="symbolStatus.length > 0">
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
                      <v-chip x-small :color="item.has_data ? 'success' : 'error'">
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
                      <v-chip x-small :color="getMarketStatusColor(item.market_status)">
                        {{ getMarketStatusText(item.market_status) }}
                      </v-chip>
                    </td>
                  </tr>
                </tbody>
              </template>
            </v-simple-table>
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
    <v-snackbar v-model="showError" color="error" timeout="5000">
      {{ errorMessage }}
    </v-snackbar>

    <!-- 成功提示 -->
    <v-snackbar v-model="showSuccess" color="success" timeout="3000">
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

    onMounted(() => {
      loadSymbols()
      loadTradeConfig()
      loadLLMConfig()
      loadSymbolStatus()
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
      getMarketStatusText
    }
  }
}
</script>
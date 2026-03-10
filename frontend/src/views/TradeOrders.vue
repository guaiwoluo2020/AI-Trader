<template>
  <v-container fluid>
    <v-row>
      <v-col cols="12">
        <h1 class="mb-4">交易指令管理</h1>
      </v-col>
    </v-row>

    <!-- 发送交易指令表单 -->
    <v-row>
      <v-col cols="12" md="6">
        <v-card>
          <v-card-title>发送交易指令</v-card-title>
          <v-card-text>
            <v-form ref="form" v-model="formValid">
              <v-select
                v-model="tradeForm.symbol"
                :items="symbols"
                label="交易品种"
                required
                :rules="[v => !!v || '请选择交易品种']"
              ></v-select>

              <v-select
                v-model="tradeForm.direction"
                :items="directions"
                label="买卖方向"
                required
                :rules="[v => !!v || '请选择买卖方向']"
              ></v-select>

              <v-text-field
                v-model.number="tradeForm.volume"
                label="手数"
                type="number"
                step="0.01"
                required
                :rules="[v => v > 0 || '手数必须大于0']"
              ></v-text-field>

              <v-text-field
                v-model.number="tradeForm.price"
                label="执行价格"
                type="number"
                step="0.00001"
                required
                :rules="[v => v > 0 || '执行价格必须大于0']"
              ></v-text-field>

              <v-text-field
                v-model.number="tradeForm.sl"
                label="止损价格"
                type="number"
                step="0.00001"
                :rules="[v => !v || v > 0 || '止损价格必须大于0']"
              ></v-text-field>

              <v-text-field
                v-model.number="tradeForm.tp"
                label="止盈价格"
                type="number"
                step="0.00001"
                :rules="[v => !v || v > 0 || '止盈价格必须大于0']"
              ></v-text-field>

              <v-btn
                color="primary"
                :disabled="!formValid"
                :loading="sending"
                @click="sendTrade"
                block
                class="mt-4"
              >
                发送交易指令
              </v-btn>
            </v-form>
          </v-card-text>
        </v-card>
      </v-col>

      <!-- 待执行指令列表 -->
      <v-col cols="12" md="6">
        <v-card>
          <v-card-title class="d-flex align-center justify-space-between">
            待执行指令
            <v-btn
              color="error"
              size="small"
              :loading="clearing"
              @click="clearAllTrades"
            >
              清空全部
            </v-btn>
          </v-card-title>
          <v-card-text>
            <v-data-table
              :headers="tradeHeaders"
              :items="pendingTrades"
              :loading="loadingTrades"
              no-data-text="暂无待执行指令"
              density="compact"
            >
              <template v-slot:item.direction="{ item }">
                <v-chip
                  :color="item.direction === 'BUY' ? 'success' : 'error'"
                  size="small"
                >
                  {{ item.direction === 'BUY' ? '买入' : '卖出' }}
                </v-chip>
              </template>

              <template v-slot:item.timestamp="{ item }">
                {{ formatTime(item.timestamp) }}
              </template>
            </v-data-table>
          </v-card-text>
        </v-card>
      </v-col>
    </v-row>

    <!-- 错误信息 -->
    <v-row v-if="error">
      <v-col cols="12">
        <v-alert type="error" dismissible>
          {{ error }}
        </v-alert>
      </v-col>
    </v-row>

    <!-- 成功信息 -->
    <v-row v-if="success">
      <v-col cols="12">
        <v-alert type="success" dismissible>
          {{ success }}
        </v-alert>
      </v-col>
    </v-row>
  </v-container>
</template>

<script>
import { ref, onMounted } from 'vue'
import { tradingAPI } from '@/api/trading'
import { marketAPI } from '@/api/market'

export default {
  name: 'TradeOrders',
  setup() {
    const form = ref(null)
    const formValid = ref(false)
    const sending = ref(false)
    const loadingTrades = ref(false)
    const clearing = ref(false)
    const error = ref('')
    const success = ref('')

    const tradeForm = ref({
      symbol: '',
      direction: '',
      volume: 0.01,
      price: 0,
      sl: 0,
      tp: 0,
    })

    const symbols = ref([])
    const directions = [
      { title: '买入', value: 'BUY' },
      { title: '卖出', value: 'SELL' },
    ]

    const tradeHeaders = [
      { title: '品种', key: 'symbol', width: '20%' },
      { title: '方向', key: 'direction', width: '15%' },
      { title: '手数', key: 'volume', width: '15%' },
      { title: '价格', key: 'price', width: '20%' },
      { title: '时间', key: 'timestamp', width: '30%' },
    ]

    const pendingTrades = ref([])

    const loadPendingTrades = async () => {
      try {
        loadingTrades.value = true
        error.value = ''
        const data = await tradingAPI.getPendingTrades()

        // 将对象格式转换为数组格式
        const tradesObj = data.pending_trades || {}
        const tradesArray = []
        Object.keys(tradesObj).forEach(symbol => {
          tradesObj[symbol].forEach(trade => {
            tradesArray.push({
              ...trade,
              direction: trade.action === 'b' ? 'BUY' : 'SELL',
              volume: trade.mount
            })
          })
        })
        pendingTrades.value = tradesArray
      } catch (err) {
        error.value = `加载指令失败: ${err.message}`
        console.error('Load trades error:', err)
      } finally {
        loadingTrades.value = false
      }
    }

    const sendTrade = async () => {
      try {
        sending.value = true
        error.value = ''
        success.value = ''

        // 价格验证
        if (tradeForm.value.sl > 0 && tradeForm.value.tp > 0) {
          if (tradeForm.value.direction === 'BUY' && !(tradeForm.value.sl < tradeForm.value.price && tradeForm.value.price < tradeForm.value.tp)) {
            error.value = '买入指令必须满足: 止损 < 执行价格 < 止盈'
            return
          }
          if (tradeForm.value.direction === 'SELL' && !(tradeForm.value.tp < tradeForm.value.price && tradeForm.value.price < tradeForm.value.sl)) {
            error.value = '卖出指令必须满足: 止盈 < 执行价格 < 止损'
            return
          }
        }

        // 转换数据格式
        const instruction = {
          symbol: tradeForm.value.symbol,
          action: tradeForm.value.direction === 'BUY' ? 'b' : 's',
          mount: tradeForm.value.volume,
          price: tradeForm.value.price,
          sl: tradeForm.value.sl || 0,
          tp: tradeForm.value.tp || 0
        }

        await tradingAPI.sendTradeInstructions([instruction])
        success.value = '交易指令发送成功！'
        form.value.reset()
        await loadPendingTrades()
      } catch (err) {
        error.value = `发送指令失败: ${err.message}`
        console.error('Send trade error:', err)
      } finally {
        sending.value = false
      }
    }

    const clearAllTrades = async () => {
      if (!confirm('确定要清空所有待执行指令吗？')) return

      try {
        clearing.value = true
        error.value = ''
        success.value = ''
        await tradingAPI.clearTrades()
        success.value = '已清空所有指令！'
        await loadPendingTrades()
      } catch (err) {
        error.value = `清空指令失败: ${err.message}`
        console.error('Clear trades error:', err)
      } finally {
        clearing.value = false
      }
    }

    const formatTime = (timestamp) => {
      if (!timestamp) return ''
      return new Date(timestamp * 1000).toLocaleString('zh-CN')
    }

    const loadSymbols = async () => {
      try {
        const data = await marketAPI.getSymbols()
        symbols.value = data.symbols || []
      } catch (err) {
        console.error('加载品种列表失败:', err)
      }
    }

    onMounted(() => {
      loadSymbols()
      loadPendingTrades()
      // 每10秒自动刷新
      setInterval(loadPendingTrades, 10000)
    })

    return {
      form,
      formValid,
      sending,
      loadingTrades,
      clearing,
      error,
      success,
      tradeForm,
      symbols,
      directions,
      tradeHeaders,
      pendingTrades,
      loadPendingTrades,
      sendTrade,
      clearAllTrades,
      formatTime,
    }
  },
}
</script>
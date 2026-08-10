import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'

const pageSource = readFileSync(new URL('../src/views/TradeOrders.vue', import.meta.url), 'utf8')
const apiSource = readFileSync(new URL('../src/api/trading.js', import.meta.url), 'utf8')

test('trade orders page only monitors generated instructions and EA reports', () => {
  assert.match(pageSource, /待执行指令/)
  assert.match(pageSource, /EA 执行结果/)
  assert.doesNotMatch(pageSource, /发送交易指令|sendTrade|tradeForm/)
  assert.doesNotMatch(apiSource, /sendTradeInstructions|send_trade_instructions/)
})

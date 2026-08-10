import assert from 'node:assert/strict'
import fs from 'node:fs'
import test from 'node:test'

const app = fs.readFileSync(new URL('../src/App.vue', import.meta.url), 'utf8')
const router = fs.readFileSync(new URL('../src/router/index.js', import.meta.url), 'utf8')
const trades = fs.readFileSync(new URL('../src/views/TradeOrders.vue', import.meta.url), 'utf8')
const positions = fs.readFileSync(new URL('../src/views/Positions.vue', import.meta.url), 'utf8')

test('trading navigation uses supported and distinct icons', () => {
  assert.match(app, /交易账户.*mdi-bank-outline/)
  assert.match(app, /交易指令.*mdi-format-list-bulleted/)
  assert.match(app, /仓位管理.*mdi-chart-box/)
  assert.doesNotMatch(app, /mdi-wallet-bifold-outline/)
})

test('trade and position pages share the strategy execution visual language', () => {
  for (const source of [trades, positions]) {
    assert.match(source, /section-kicker/)
    assert.match(source, /metric-card/)
    assert.match(source, /linear-gradient\(125deg,#123c35/)
    assert.match(source, /selectedAccount\?\.active/)
  }
  assert.match(positions, /<v-window v-model="activeTab">/)
})

test('standalone statistics analysis page is removed', () => {
  assert.doesNotMatch(app, /title: '统计数据'/)
  assert.doesNotMatch(router, /Statistics|\/statistics/)
  assert.equal(fs.existsSync(new URL('../src/views/Statistics.vue', import.meta.url)), false)
})

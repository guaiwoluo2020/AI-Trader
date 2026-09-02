import assert from 'node:assert/strict'
import fs from 'node:fs'
import test from 'node:test'

const app = fs.readFileSync(new URL('../src/App.vue', import.meta.url), 'utf8')
const router = fs.readFileSync(new URL('../src/router/index.js', import.meta.url), 'utf8')
const positions = fs.readFileSync(new URL('../src/views/Positions.vue', import.meta.url), 'utf8')

test('trading navigation uses supported and distinct icons', () => {
  assert.match(app, /交易账户.*mdi-bank-outline/)
  assert.match(app, /仓位管理.*mdi-chart-box/)
  assert.doesNotMatch(app, /交易指令/)
  assert.doesNotMatch(app, /持仓管理/)
  assert.doesNotMatch(app, /mdi-wallet-bifold-outline/)
})

test('position page keeps the strategy execution visual language', () => {
  assert.match(positions, /section-kicker/)
  assert.match(positions, /metric-card/)
  assert.match(positions, /linear-gradient\(125deg,#123c35/)
  assert.match(positions, /selectedAccount\?\.active/)
  assert.match(positions, /<v-window v-model="activeTab">/)
})

test('standalone trading pages are retired and old URLs redirect to accounts', () => {
  assert.doesNotMatch(router, /component: TradeOrders|component: PositionManagement/)
  assert.match(router, /path: '\/trades'[\s\S]*redirect: '\/accounts'/)
  assert.match(router, /path: '\/position-management'[\s\S]*redirect: '\/accounts'/)
})

test('standalone statistics analysis page is removed', () => {
  assert.doesNotMatch(app, /title: '统计数据'/)
  assert.doesNotMatch(router, /Statistics|\/statistics/)
  assert.equal(fs.existsSync(new URL('../src/views/Statistics.vue', import.meta.url)), false)
})

import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'

const app = readFileSync(new URL('../src/App.vue', import.meta.url), 'utf8')
const router = readFileSync(new URL('../src/router/index.js', import.meta.url), 'utf8')
const api = readFileSync(new URL('../src/api/market.js', import.meta.url), 'utf8')
const page = readFileSync(new URL('../src/views/AIMarket.vue', import.meta.url), 'utf8')

test('AI market is an account-aware trading management page', () => {
  assert.match(app, /title: 'AI 行情'.*path: '\/ai-market'.*mdi-brain/)
  assert.match(app, /'AIMarket'/)
  assert.match(router, /path: '\/ai-market'/)
  assert.match(api, /getAIMarketView\(accountId/)
  assert.match(api, /params\.account_id = accountId/)
})

test('AI market separates analysis from executable signals', () => {
  assert.match(page, /我的 AI 分析/)
  assert.match(page, /共享 AI 分析/)
  assert.match(page, /为什么尚未形成交易信号/)
  assert.match(page, /观察中/)
  assert.match(page, /等待价格/)
  assert.match(page, /已形成信号/)
  assert.doesNotMatch(page, /确认执行|立即下单/)
})

test('shared AI data can be derived into the current user analysis', () => {
  assert.match(page, /共享派生/)
  assert.match(page, /只有你在自己的策略中明确引用并配置阈值后/)
  assert.match(page, /!access\.access_granted && !filteredOwn\.length/)
  assert.match(page, /v-if="filteredOwn\.length"/)
})

import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'

const appSource = readFileSync(new URL('../src/App.vue', import.meta.url), 'utf8')
const pageSource = readFileSync(new URL('../src/views/Market.vue', import.meta.url), 'utf8')
const apiSource = readFileSync(new URL('../src/api/market.js', import.meta.url), 'utf8')

test('signal recommendation is replaced by the strategy execution center', () => {
  assert.match(appSource, /title: '策略执行'/)
  assert.match(pageSource, /策略执行中心/)
  assert.match(pageSource, /待处理决策/)
  assert.match(pageSource, /最新策略决策/)
  assert.doesNotMatch(pageSource, /AI趋势分析|triggerLLMAnalysis|loadLLMAnalysis/)
})

test('strategy decision history is account scoped and filterable', () => {
  assert.match(apiSource, /filters\.strategy_id/)
  assert.match(apiSource, /filters\.status/)
  assert.match(apiSource, /filters\.date_from/)
  assert.match(apiSource, /params\.account_id = accountId/)
})

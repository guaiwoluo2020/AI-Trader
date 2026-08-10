import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'

const view = readFileSync(new URL('../src/views/AlphaResearch.vue', import.meta.url), 'utf8')
const router = readFileSync(new URL('../src/router/index.js', import.meta.url), 'utf8')
const navigation = readFileSync(new URL('../src/App.vue', import.meta.url), 'utf8')
const settings = readFileSync(new URL('../src/views/Settings.vue', import.meta.url), 'utf8')
const api = readFileSync(new URL('../src/api/market.js', import.meta.url), 'utf8')

test('Alpha research exposes all three exit modes with reverse signal as default', () => {
  assert.match(view, /exitMode: 'reverse_signal'/)
  assert.match(view, /form\.exitMode === 'fixed_horizon'/)
  assert.match(view, /reverse_signal: '反向信号退出'/)
  assert.match(view, /fixed_horizon: '固定周期退出'/)
  assert.match(view, /neutral_signal: '回到观望退出'/)
})

test('Alpha research is available from the authenticated research navigation', () => {
  assert.match(router, /path: '\/alpha-research'/)
  assert.match(router, /name: 'AlphaResearch'/)
  assert.match(navigation, /title: 'Alpha 研究', path: '\/alpha-research'/)
})

test('AI research is the default and manual factors are an advanced option', () => {
  assert.match(view, /researchMode = ref\('ai'\)/)
  assert.match(view, /AI 生成候选/)
  assert.match(view, /高级自定义/)
  assert.match(view, /浏览因子库/)
  assert.match(view, /factor\.category_label/)
})

test('Alpha backtests expose composable protective exit rules', () => {
  assert.match(view, /固定止损/)
  assert.match(view, /固定止盈/)
  assert.match(view, /移动止损/)
  assert.match(view, /最大持有 K 线/)
})

test('AI Alpha research exposes iterative LLM optimization and prompt audit', () => {
  assert.match(view, /llmIterationCount: 3/)
  assert.match(view, /LLM 结构优化轮次/)
  assert.match(view, /llm_iteration_count: form\.llmIterationCount/)
  assert.match(view, /LLM × Optuna 迭代轨迹/)
  assert.match(view, /查看发送给大模型的改进提示词/)
  assert.match(view, /隐藏测试仅在最终执行一次/)
})

test('Alpha report separates factor decay from strategy performance metrics', () => {
  assert.match(view, /Level 2/)
  assert.match(view, /IC_IR/)
  assert.match(view, /因子衰减 Decay/)
  assert.match(view, /Level 3/)
  assert.match(view, /Sharpe（逐笔）/)
  assert.match(view, /Sortino（逐笔）/)
  assert.match(view, /Profit Factor/)
  assert.match(view, /策略换手率/)
})

test('validated Alpha can be published and selected as a strategy signal source', () => {
  assert.match(view, /Alpha 准入检查/)
  assert.match(view, /发布到 Alpha 库/)
  assert.match(view, /因子诊断与正交性/)
  assert.match(api, /publishAlphaResearchRun/)
  assert.match(api, /getAlphaLibrary/)
  assert.match(settings, /alpha_factor/)
  assert.match(settings, /已验证 Alpha/)
  assert.match(settings, /onAlphaSelected/)
})

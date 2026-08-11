import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'

const view = readFileSync(new URL('../src/views/AlphaResearch.vue', import.meta.url), 'utf8')
const router = readFileSync(new URL('../src/router/index.js', import.meta.url), 'utf8')
const navigation = readFileSync(new URL('../src/App.vue', import.meta.url), 'utf8')
const settings = readFileSync(new URL('../src/views/Settings.vue', import.meta.url), 'utf8')
const api = readFileSync(new URL('../src/api/market.js', import.meta.url), 'utf8')

test('Alpha research stays focused on factor validity rather than trading exits', () => {
  assert.match(view, /因子有效性研究/)
  assert.match(view, /信号定义/)
  assert.match(view, /不会生成订单或持仓/)
  assert.doesNotMatch(view, /主退出规则/)
  assert.doesNotMatch(view, /固定止盈/)
  assert.doesNotMatch(view, /最佳候选交易流水/)
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
  assert.match(view, /时段与昨日同期效应/)
  assert.match(view, /同期观察交易日/)
  assert.match(view, /Asia\/Shanghai/)
})

test('Alpha candidate generation explains missing research description', () => {
  assert.doesNotMatch(
    view,
    /:disabled="form\.researchDescription\.trim\(\)\.length < 10"/
  )
  assert.match(view, /请先输入不少于 10 个字的研究目标/)
})

test('AI Alpha research exposes iterative LLM optimization and prompt audit', () => {
  assert.match(view, /llmIterationCount: 3/)
  assert.match(view, /LLM 结构优化轮次/)
  assert.match(view, /llm_iteration_count: form\.llmIterationCount/)
  assert.match(view, /LLM × Optuna 迭代轨迹/)
  assert.match(view, /查看发送给大模型的改进提示词/)
  assert.match(view, /隐藏测试仅在最终执行一次/)
})

test('Alpha report exposes factor diagnostics without strategy performance metrics', () => {
  assert.match(view, /Level 2/)
  assert.match(view, /IC_IR/)
  assert.match(view, /因子衰减 Decay/)
  assert.match(view, /交易规则请进入策略回测工作台验证/)
  assert.doesNotMatch(view, /Sharpe（逐笔）/)
  assert.doesNotMatch(view, /Profit Factor/)
  assert.doesNotMatch(view, /策略换手率/)
  assert.match(view, /时段规律报告/)
  assert.match(view, /平均未来收益/)
})

test('validated Alpha can be published and selected as a strategy signal source', () => {
  assert.match(view, /Alpha 准入检查/)
  assert.match(view, /核心门槛必须全部通过/)
  assert.match(view, /check\.required/)
  assert.match(view, /发布到 Alpha 库/)
  assert.match(view, /因子诊断与正交性/)
  assert.match(api, /publishAlphaResearchRun/)
  assert.match(api, /getAlphaLibrary/)
  assert.match(settings, /alpha_factor/)
  assert.match(settings, /已验证 Alpha/)
  assert.match(settings, /onAlphaSelected/)
})

test('Alpha report exposes layered independent residual and ablation experiments', () => {
  assert.match(view, /独立因子评估 · 每个 Trial 门槛/)
  assert.match(view, /残差评估 · 本轮 Top 5 增量信息/)
  assert.match(view, /最终消融实验 · N\+1 组合/)
  assert.match(view, /残差调整后/)
  assert.match(view, /可能冗余/)
  assert.match(view, /independent_pruned_trials/)
  assert.match(view, /residual_candidates/)
})

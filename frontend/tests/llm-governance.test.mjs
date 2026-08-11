import assert from 'node:assert/strict'
import fs from 'node:fs'
import path from 'node:path'
import test from 'node:test'
import { fileURLToPath } from 'node:url'

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..')
const settings = fs.readFileSync(path.join(root, 'src/views/Settings.vue'), 'utf8')
const api = fs.readFileSync(path.join(root, 'src/api/market.js'), 'utf8')

test('admin LLM settings use synchronized models and scene routing', () => {
  assert.match(settings, /模型目录与场景路由/)
  assert.match(settings, /syncLLMModels/)
  assert.match(settings, /scene\.model_ids/)
  assert.match(api, /admin\/llm\/models\/sync/)
  assert.match(api, /admin\/llm\/scenes/)
})

test('user settings explain the shared low-frequency free quota', () => {
  assert.match(settings, /回测报告和 Alpha 研究无需申请开通/)
  assert.match(settings, /每日 30 次免费大模型调用额度/)
  assert.match(settings, /llmFreeQuota\.remaining/)
})

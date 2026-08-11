import assert from 'node:assert/strict'
import fs from 'node:fs'
import path from 'node:path'
import test from 'node:test'
import { fileURLToPath } from 'node:url'

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..')
const settings = fs.readFileSync(path.join(root, 'src/views/Settings.vue'), 'utf8')
const api = fs.readFileSync(path.join(root, 'src/api/market.js'), 'utf8')

test('admin LLM settings use synchronized models and scene routing', () => {
  assert.match(settings, /供应商配置/)
  assert.match(settings, /可保存多套 BASE URL\/API Key/)
  assert.match(settings, /设为有效/)
  assert.match(settings, /场景模型与提示词/)
  assert.match(settings, /llmGovernance\.providers/)
  assert.match(settings, /scene_model_warnings/)
  assert.match(settings, /失效模型/)
  assert.match(settings, /syncLLMModels/)
  assert.match(settings, /尚未同步模型列表/)
  assert.match(settings, /已同步模型，但还没有启用模型/)
  assert.match(settings, /scene\.model_ids/)
  assert.match(settings, /场景提示词/)
  assert.match(settings, /scene\.system_prompt/)
  assert.match(settings, /scene\.user_prompt_template/)
  assert.match(settings, /scenePromptHint/)
  assert.match(settings, /提示词版本/)
  assert.match(settings, /新增提示词/)
  assert.match(settings, /scene\.prompt_profiles/)
  assert.match(settings, /设为默认/)
  assert.match(settings, /addAIScenePrompt/)
  assert.match(settings, /if \(data\.governance\) llmGovernance\.value = data\.governance/)
  assert.match(api, /admin\/llm\/models\/sync/)
  assert.match(api, /admin\/llm\/providers/)
  assert.match(api, /admin\/llm\/scenes/)
})

test('user settings explain the shared low-frequency free quota', () => {
  assert.match(settings, /回测报告和 Alpha 研究无需申请开通/)
  assert.match(settings, /每日 30 次免费大模型调用额度/)
  assert.match(settings, /llmFreeQuota\.remaining/)
})

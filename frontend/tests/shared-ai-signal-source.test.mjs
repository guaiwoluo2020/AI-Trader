import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'

const page = readFileSync(new URL('../src/views/Settings.vue', import.meta.url), 'utf8')

test('AI signal source supports self analysis and direct shared reference modes', () => {
  assert.match(page, /value="self_analysis">自主 AI 分析/)
  assert.match(page, /value="shared_reference">引用共享 AI 数据/)
  assert.match(page, /params\.shared_runtime_id/)
  assert.match(page, /无需开通即可参与策略决策/)
  assert.match(page, /onSharedRuntimeSelected/)
})

test('only self analysis requires paid LLM access and prompt placeholders', () => {
  assert.match(page, /params\.analysis_mode === 'shared_reference'/)
  assert.match(page, /: aiSignalOptions\.accessGranted/)
  assert.match(page, /params\.analysis_mode === 'self_analysis'/)
})

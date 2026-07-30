import assert from 'node:assert/strict'
import fs from 'node:fs'
import test from 'node:test'

const settingsSource = fs.readFileSync(
  new URL('../src/views/Settings.vue', import.meta.url),
  'utf8'
)

test('strategy configuration switches use green when enabled', () => {
  assert.match(
    settingsSource,
    /v-model="tradeConfig\.enabled"[\s\S]*?label="启用自动生成"[\s\S]*?color="success"/
  )
  assert.match(
    settingsSource,
    /v-model="strategy\.enabled"[\s\S]*?label="启用策略"[\s\S]*?color="success"/
  )
})

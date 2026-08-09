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
    /v-model="selectedStrategy\.is_shared" color="success"/
  )
  assert.match(
    settingsSource,
    /v-model="selectedStrategy\.enabled" color="success"/
  )
  assert.match(
    settingsSource,
    /v-model="newSignalSource\.params\.share_runtime_data"[\s\S]*?color="success"/
  )
})

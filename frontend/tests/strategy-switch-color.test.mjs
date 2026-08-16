import assert from 'node:assert/strict'
import fs from 'node:fs'
import test from 'node:test'

const settingsSource = fs.readFileSync(
  new URL('../src/views/Settings.vue', import.meta.url),
  'utf8'
)

test('strategy bindings do not retain AI runtime sharing state', () => {
  assert.match(
    settingsSource,
    /v-model="selectedStrategy\.is_shared" color="success"/
  )
  assert.doesNotMatch(settingsSource, /params\.share_runtime_data/)
})

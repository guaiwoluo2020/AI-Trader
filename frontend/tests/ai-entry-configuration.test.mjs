import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import path from 'node:path'

const testDir = path.dirname(fileURLToPath(import.meta.url))
const source = readFileSync(
  path.resolve(testDir, '../src/views/Settings.vue'), 'utf8'
)

assert.match(source, /aiIntervalValues = \[1, 2, 3, 5,/)
assert.match(source, /entry_threshold_percent/)
assert.match(source, /suffix="%"/)
assert.match(source, /默认 0\.08%/)
assert.match(source, /entry_threshold: 0\.0008/)
assert.match(source, /\) \/ 100/)

console.log('AI entry configuration test passed')

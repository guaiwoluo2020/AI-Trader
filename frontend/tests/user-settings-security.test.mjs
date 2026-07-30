import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import path from 'node:path'

const testDir = path.dirname(fileURLToPath(import.meta.url))
const frontendDir = path.resolve(testDir, '..')
const settingsSource = readFileSync(
  path.join(frontendDir, 'src/views/Settings.vue'),
  'utf8',
)
const apiSource = readFileSync(
  path.join(frontendDir, 'src/api/trading.js'),
  'utf8',
)

assert.match(settingsSource, /账户与安全/)
assert.match(settingsSource, /currentUser\.role/)
assert.match(settingsSource, /修改密码/)
assert.match(settingsSource, /authAPI\.changePassword/)
assert.match(apiSource, /async changePassword\(passwords\)/)
assert.match(apiSource, /\/auth\/change-password/)

console.log('user-settings-security test passed')

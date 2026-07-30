import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import path from 'node:path'

const testDir = path.dirname(fileURLToPath(import.meta.url))
const frontendDir = path.resolve(testDir, '..')
const appSource = readFileSync(path.join(frontendDir, 'src/App.vue'), 'utf8')
const routerSource = readFileSync(path.join(frontendDir, 'src/router/index.js'), 'utf8')
const settingsSource = readFileSync(path.join(frontendDir, 'src/views/Settings.vue'), 'utf8')

assert.match(appSource, /title: '策略配置', path: '\/strategy-settings'/)
assert.match(appSource, /title: '用户配置', path: '\/settings'/)
assert.match(routerSource, /path: '\/strategy-settings'/)
assert.match(settingsSource, /props:\s*\{[\s\S]*mode:/)
assert.match(settingsSource, /v-if="isStrategyPage"/)
assert.match(settingsSource, /v-if="!isStrategyPage"/)
assert.doesNotMatch(settingsSource, /品种数据状态/)
assert.doesNotMatch(settingsSource, /loadSymbolStatus/)

console.log('strategy-settings-routing test passed')

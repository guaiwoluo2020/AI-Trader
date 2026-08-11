import assert from 'node:assert/strict'
import fs from 'node:fs'
import path from 'node:path'
import test from 'node:test'
import { fileURLToPath } from 'node:url'

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..')
const page = fs.readFileSync(path.join(root, 'src/views/SystemLog.vue'), 'utf8')
const api = fs.readFileSync(path.join(root, 'src/api/market.js'), 'utf8')

test('system log page separates admin operations from account events', () => {
  assert.match(page, /平台运行中心/)
  assert.match(page, /账户运行记录/)
  assert.match(page, /authState\.user\?\.role === 'admin'/)
  assert.match(page, /异常与告警/)
  assert.match(page, /交易审计/)
})

test('system log page uses authenticated dedicated live events', () => {
  assert.match(page, /\/ws\/system-logs/)
  assert.match(page, /type: 'auth'/)
  assert.match(page, /account_id/)
  assert.doesNotMatch(api, /clearSystemLogs/)
})

test('only administrators receive retention controls', () => {
  assert.match(page, /v-if="isAdmin"[^>]*.*日志保留/)
  assert.match(api, /admin\/system\/logs\/purge/)
  assert.match(page, /审计事件不可删除/)
})

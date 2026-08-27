import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'

const accountView = readFileSync(
  new URL('../src/views/Accounts.vue', import.meta.url), 'utf8',
)
const paperService = readFileSync(
  new URL('../../paper_trading.py', import.meta.url), 'utf8',
)
const accountRoutes = readFileSync(
  new URL('../../routes_accounts.py', import.meta.url), 'utf8',
)

test('account runtime views expose per-deployment strategy performance', () => {
  assert.match(accountView, /策略收益贡献/)
  assert.match(accountView, /完整交易/)
  assert.match(accountView, /盈利金额/)
  assert.match(accountView, /最大回撤 \/ 连亏/)
  assert.match(paperService, /build_paper_performance/)
  assert.match(accountRoutes, /build_live_performance/)
})

import assert from 'node:assert/strict'
import fs from 'node:fs'
import test from 'node:test'
import {
  countPendingTrades,
  normalizeActiveSymbols,
} from '../src/utils/dashboard-view-data.js'

assert.equal(
  countPendingTrades({
    pending_trades: {
      GOLD: [{ id: 1 }, { id: 2 }],
      EURUSD: [{ id: 3 }],
      INVALID: null,
    },
  }),
  3
)

assert.deepEqual(
  normalizeActiveSymbols({
    store: {
      GOLD: {
        H4: { count: 100, initialized: true },
        H1: { count: 0, initialized: false },
        M15: { count: 50, initialized: true },
        M5: { count: 0, initialized: false },
        M1: { count: 10, initialized: true },
      },
      EMPTY: {
        H4: { count: 0, initialized: false },
      },
    },
  }),
  [
    {
      symbol: 'GOLD',
      periods: [
        { name: 'H4', count: 100 },
        { name: 'M15', count: 50 },
        { name: 'M1', count: 10 },
      ],
    },
  ]
)

test('dashboard uses the account-scoped operational overview', () => {
  const dashboard = fs.readFileSync(
    new URL('../src/views/Dashboard.vue', import.meta.url),
    'utf8',
  )
  const tradingApi = fs.readFileSync(
    new URL('../src/api/trading.js', import.meta.url),
    'utf8',
  )

  assert.match(tradingApi, /getDashboardOverview.*dashboard\/overview/s)
  assert.match(dashboard, /getDashboardOverview\(selectedAccountId\.value\)/)
  assert.match(dashboard, /账户与风控/)
  assert.match(dashboard, /策略运行概览/)
  assert.match(dashboard, /最新 AI 机会/)
  assert.match(dashboard, /行情健康度/)
  assert.doesNotMatch(dashboard, /统计记录/)
  assert.doesNotMatch(dashboard, /getStatistics\(/)
})

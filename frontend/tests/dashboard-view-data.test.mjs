import assert from 'node:assert/strict'
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

console.log('dashboard-view-data test passed')

import assert from 'node:assert/strict'

async function loadNormalizer() {
  try {
    const mod = await import('../src/utils/statistics-view-data.js')
    return mod.normalizeStatisticsForView
  } catch {
    return null
  }
}

const normalizeStatisticsForView = await loadNormalizer()

assert.equal(
  typeof normalizeStatisticsForView,
  'function',
  'normalizeStatisticsForView should exist'
)

const apiResponse = [
  {
    symbol: 'BTCUSD#',
    timestamp: '2026-07-30T20:34:41.538822',
    bid_price: 64891.4,
    ask_price: 64913.9,
    tick_count: 6,
    balance: 0.57,
  },
]

const [item] = normalizeStatisticsForView(apiResponse)

assert.equal(item.bidPrice, 64891.4)
assert.equal(item.askPrice, 64913.9)
assert.equal(item.tickCount, 6)
assert.equal(item.balance, 0.57)
assert.equal(item.midPrice, 64902.65)

console.log('statistics-view-data test passed')

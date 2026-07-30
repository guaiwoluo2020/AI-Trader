import assert from 'node:assert/strict'
import test from 'node:test'

import {
  formatTradePrice,
  normalizeTradingDecision,
} from '../src/utils/trading-decision.js'

test('executable decision exposes the real pending order', () => {
  const alert = normalizeTradingDecision({
    decision_id: 'decision-1',
    symbol: 'GOLD_',
    action: 'buy',
    status: 'confirmed',
    entry_price: 4114.2,
    sl: 4095,
    tp: 4140,
    volume: 0.01,
    order_id: 'order-1',
  })

  assert.equal(alert.rejected, false)
  assert.equal(alert.pending_order.order_id, 'order-1')
  assert.equal(alert.price, 4114.2)
})

test('rejected decision keeps prices but cannot be confirmed', () => {
  const alert = normalizeTradingDecision({
    decision_id: 'decision-2',
    symbol: 'GOLD_',
    action: 'sell',
    status: 'rejected',
    decision_type: 'rejected',
    entry_price: 4113.78,
    sl: 4124.6,
    tp: 4059.67,
    position_check: {
      warnings: ['同向持仓将超过限制 2'],
    },
  })

  assert.equal(alert.rejected, true)
  assert.equal(alert.action, 'sell')
  assert.equal(alert.price, 4113.78)
  assert.equal(alert.pending_order, null)
  assert.deepEqual(alert.risk_warnings, ['同向持仓将超过限制 2'])
})

test('invalid trade prices are not rendered as zero', () => {
  assert.equal(formatTradePrice(4113.789), '4113.79')
  assert.equal(formatTradePrice(0), '--')
  assert.equal(formatTradePrice(undefined), '--')
})

import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'

const source = readFileSync(
  new URL('../src/views/BacktestDatasets.vue', import.meta.url),
  'utf8',
)

test('backtest datasets use hour-level inputs with a two-hour minimum range', () => {
  assert.equal((source.match(/type="datetime-local"/g) || []).length, 2)
  assert.match(source, /const minimumRangeSeconds = 2 \* 60 \* 60/)
  assert.match(source, /requestedEnd\.value - requestedStart\.value < minimumRangeSeconds/)
  assert.doesNotMatch(source, /T00:00:00Z|T23:59:59Z/)
})

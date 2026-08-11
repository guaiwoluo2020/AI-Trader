import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'

const source = readFileSync(
  new URL('../src/views/BacktestDatasets.vue', import.meta.url),
  'utf8',
)

test('backtest datasets use minute-level inputs with a two-hour minimum range', () => {
  assert.equal((source.match(/type="datetime-local"/g) || []).length, 2)
  assert.equal((source.match(/step="60"/g) || []).length, 2)
  assert.doesNotMatch(source, /step="3600"/)
  assert.match(source, /const minimumRangeSeconds = 2 \* 60 \* 60/)
  assert.match(source, /requestedEnd\.value - requestedStart\.value < minimumRangeSeconds/)
  assert.match(source, /最近 2 小时/)
  assert.match(source, /applyRangePreset/)
  assert.doesNotMatch(source, /T00:00:00Z|T23:59:59Z/)
})

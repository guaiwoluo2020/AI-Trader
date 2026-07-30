import assert from 'node:assert/strict'
import fs from 'node:fs'
import test from 'node:test'

const runtimeSource = fs.readFileSync(
  new URL('../src/api/runtime.js', import.meta.url),
  'utf8'
)
const marketSource = fs.readFileSync(
  new URL('../src/api/market.js', import.meta.url),
  'utf8'
)
const tradingSource = fs.readFileSync(
  new URL('../src/api/trading.js', import.meta.url),
  'utf8'
)

test('production API and WebSocket URLs are runtime-aware', () => {
  assert.match(runtimeSource, /runtimeEnv\.DEV \? 'http:\/\/localhost:8000' : '\/api'/)
  assert.match(marketSource, /baseURL: API_BASE_URL/)
  assert.match(marketSource, /new WebSocket\(getMarketWebSocketUrl\(\)\)/)
  assert.match(tradingSource, /baseURL: API_BASE_URL/)
})

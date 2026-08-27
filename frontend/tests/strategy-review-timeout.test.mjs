import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'

const apiSource = readFileSync(
  new URL('../src/api/market.js', import.meta.url), 'utf8',
)

test('AI strategy review submits an asynchronous job and exposes polling', () => {
  const reviewMethod = apiSource.slice(
    apiSource.indexOf('async reviewStrategyExecution'),
    apiSource.indexOf('async applyStrategyReview'),
  )
  assert.match(reviewMethod, /ai-review/)
  assert.match(reviewMethod, /getStrategyReviewStatus/)
})

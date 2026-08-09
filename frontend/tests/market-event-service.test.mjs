import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'

const apiSource = readFileSync(new URL('../src/api/market.js', import.meta.url), 'utf8')
const eventPage = readFileSync(new URL('../src/views/News.vue', import.meta.url), 'utf8')
const signalPage = readFileSync(new URL('../src/views/Market.vue', import.meta.url), 'utf8')

test('market event page owns the three public data feeds', () => {
  assert.match(apiSource, /getMarketCalendar/)
  assert.match(apiSource, /getMarketKeyEvents/)
  assert.match(apiSource, /getMarketFlashNews/)
  assert.match(apiSource, /getApiWebSocketUrl\('\/news\/ws'\)/)
  assert.match(eventPage, /market_flash_news_updated/)
  assert.doesNotMatch(eventPage, /market_calendar_updated|market_key_events_updated/)
})

test('signal recommendation page no longer displays market events', () => {
  assert.doesNotMatch(signalPage, /latestFlashNews|topCalendarEvent|\/api\/news/)
})

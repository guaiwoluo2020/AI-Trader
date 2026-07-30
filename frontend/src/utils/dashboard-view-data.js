const PERIOD_ORDER = ['H4', 'H1', 'M15', 'M5', 'M1']

export function countPendingTrades(response = {}) {
  const pendingTrades = response.pending_trades || {}

  return Object.values(pendingTrades).reduce(
    (total, trades) => total + (Array.isArray(trades) ? trades.length : 0),
    0
  )
}

export function normalizeActiveSymbols(response = {}) {
  const store = response.store || {}

  return Object.entries(store)
    .map(([symbol, periods = {}]) => {
      const activePeriods = PERIOD_ORDER
        .filter((period) => Number(periods[period]?.count) > 0)
        .map((period) => ({
          name: period,
          count: Number(periods[period].count),
        }))

      return {
        symbol,
        periods: activePeriods,
      }
    })
    .filter((item) => item.periods.length > 0)
    .sort((left, right) => left.symbol.localeCompare(right.symbol))
}

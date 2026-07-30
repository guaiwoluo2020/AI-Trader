export function normalizeStatisticsForView(items = []) {
  return items.map((item) => {
    const bidPrice = Number(item.bidPrice ?? item.bid_price ?? 0)
    const askPrice = Number(item.askPrice ?? item.ask_price ?? 0)
    const tickCount = Number(item.tickCount ?? item.tick_count ?? 0)

    return {
      ...item,
      bidPrice,
      askPrice,
      tickCount,
      balance: Number(item.balance ?? 0),
      midPrice: Number(((bidPrice + askPrice) / 2).toFixed(5)),
    }
  })
}

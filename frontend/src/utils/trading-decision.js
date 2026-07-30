export function normalizeTradingDecision(decision) {
  const rejected =
    decision.status === 'rejected' || decision.decision_type === 'rejected'
  const action = ['buy', 'sell'].includes(decision.action)
    ? decision.action
    : null

  return {
    type: 'trading_decision',
    decision_id: decision.decision_id,
    symbol: decision.symbol,
    action,
    status: decision.status,
    rejected,
    price: toNumberOrNull(decision.entry_price),
    sl: toNumberOrNull(decision.sl),
    tp: toNumberOrNull(decision.tp),
    volume: toNumberOrNull(decision.volume),
    reason: decision.decision_reason,
    confidence: toNumberOrNull(decision.confidence_score),
    signals: decision.signals || [],
    signal_summary: decision.signal_summary || {},
    risk_reward_ratio: toNumberOrNull(decision.risk_reward_ratio),
    risk_warnings: collectRiskWarnings(decision),
    timestamp:
      decision.created_at || decision.timestamp || new Date().toISOString(),
    pending_order:
      !rejected && decision.order_id
        ? {
            order_id: decision.order_id,
            action: action === 'buy' ? 'b' : 's',
            price: toNumberOrNull(decision.entry_price),
            sl: toNumberOrNull(decision.sl),
            tp: toNumberOrNull(decision.tp),
            mount: toNumberOrNull(decision.volume),
            reason: decision.decision_reason,
          }
        : null,
  }
}

export function formatTradePrice(value) {
  const number = Number(value)
  return Number.isFinite(number) && number > 0 ? number.toFixed(2) : '--'
}

function collectRiskWarnings(decision) {
  return [
    ...(decision.position_check?.warnings || []),
    ...(decision.risk_check?.warnings || []),
  ]
}

function toNumberOrNull(value) {
  const number = Number(value)
  return Number.isFinite(number) ? number : null
}

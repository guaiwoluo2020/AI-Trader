export function normalizeTradingDecision(decision) {
  const source = decision && typeof decision === 'object' ? decision : {}
  const rejected =
    source.status === 'rejected' || source.decision_type === 'rejected'
  const action = normalizeAction(source.action)

  return {
    type: 'trading_decision',
    decision_id: source.decision_id,
    symbol: source.symbol,
    strategy_id: source.strategy_id,
    strategy_name: source.strategy_name || '未命名策略',
    auto_executed: Boolean(source.auto_executed),
    execution_mode: source.execution_mode === 'paper' ? 'paper' : 'live',
    action,
    status: source.status,
    rejected,
    price: toNumberOrNull(source.entry_price),
    sl: toNumberOrNull(source.sl),
    tp: toNumberOrNull(source.tp),
    volume: toNumberOrNull(source.volume),
    reason: source.decision_reason,
    confidence: toNumberOrNull(source.confidence_score),
    signals: source.signals || [],
    signal_summary: source.signal_summary || {},
    risk_reward_ratio: toNumberOrNull(source.risk_reward_ratio),
    risk_warnings: collectRiskWarnings(source),
    timestamp:
      source.created_at || source.timestamp || new Date().toISOString(),
    observation_count: Math.max(1, Number(source.observation_count) || 1),
    first_observed_at: source.first_observed_at || null,
    last_observed_at: source.last_observed_at || null,
    pending_order:
      !rejected && source.order_id
        ? {
            order_id: source.order_id,
            action: action === 'buy' ? 'b' : 's',
            price: toNumberOrNull(source.entry_price),
            sl: toNumberOrNull(source.sl),
            tp: toNumberOrNull(source.tp),
            mount: toNumberOrNull(source.volume),
            reason: source.decision_reason,
            strategy_id: source.strategy_id,
            strategy_name: source.strategy_name || '未命名策略',
            confirmed: Boolean(source.auto_executed),
            auto_executed: Boolean(source.auto_executed),
          }
        : null,
  }
}

function normalizeAction(value) {
  const normalized = String(value || '').trim().toLowerCase()
  if (['buy', 'b', 'long'].includes(normalized)) return 'buy'
  if (['sell', 's', 'short'].includes(normalized)) return 'sell'
  return null
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

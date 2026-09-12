# Shared Structure Plan Execution Consistency Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make every live and Paper deployment consume the same immutable Structure Plan Tick snapshot exactly once, with durable gate-level audit records that explain any account divergence.

**Architecture:** The market path creates one immutable `TickExecutionContext` containing a stable tick id and per-strategy signals. Live and Paper adapters pass that same context into one eligibility evaluator and one plan execution repository; account-specific position, risk, and execution checks remain independent but are persisted as a gate trace. Plan claims are keyed by deployment, plan, stage, and direction and transition through explicit lifecycle states.

**Tech Stack:** Python 3, FastAPI, MySQL/InnoDB, pytest, Vue 3.

**Spec:** Confirmed P0/P1/P2 checklist in the September 12, 2026 task conversation.

## Global Constraints

- Preserve all unrelated uncommitted account notification and auto-flatten changes.
- MySQL is the only database; do not introduce SQLite-specific behavior.
- Public market state remains scoped by `user_id + canonical_symbol + period`; execution remains account/deployment scoped.
- Paper must never rerun a stateful signal generator when the shared Tick snapshot is absent.
- `no_action`, rejection, and technical failure outcomes must be durable and queryable.

---

### Task 1: Immutable Tick execution context

**Files:**
- Create: `market/services/tick_execution_context.py`
- Modify: `server.py`
- Modify: `routes_ea.py`
- Modify: `paper_trading.py`
- Test: `tests/test_tick_execution_context.py`
- Test: `test_paper_trading.py`

**Interfaces:**
- Produces: `TickExecutionContext.create(...)`, `context.signals_for(strategy_id)`, and `context.to_audit_dict()`.
- Consumes: live-generated `TradingSignal` collections and the native quote symbol/price.

- [ ] Write a failing test proving contexts are immutable, have stable tick ids, and return defensive signal copies.
- [ ] Run `pytest -q tests/test_tick_execution_context.py` and verify failure because the type does not exist.
- [ ] Implement the frozen context and replace the mutable `_tick_signal_snapshots` payload.
- [ ] Write a failing Paper test proving a missing snapshot records `snapshot_missing` and never invokes `generate_signals_for_strategy`.
- [ ] Remove the Paper fallback generator and pass the context from `routes_ea.py`.
- [ ] Run the focused tests and verify they pass.

### Task 2: Durable gate audit and unified eligibility

**Files:**
- Create: `market/services/execution_eligibility.py`
- Modify: `market/services/strategy/strategy_service.py`
- Modify: `market/services/decision_audit_service.py`
- Modify: `mysql_storage.py`
- Modify: `market/store/structure_plan_store.py`
- Test: `tests/test_execution_eligibility.py`
- Test: `test_strategy_service.py`

**Interfaces:**
- Produces: `ExecutionGateResult`, `ExecutionEligibilityEvaluator.evaluate(...)`, and repository `record_gate_audit(...)`.
- Gate reason codes include `snapshot_missing`, `no_direction`, `no_new_trigger`, `decision_cooldown`, `entry_guard`, `position_policy`, `invalid_volume`, `position_limit`, `risk_limit`, `claim_conflict`, and `eligible`.

- [ ] Write failing tests for stable reason codes and ordered gate traces.
- [ ] Add MySQL execution audit columns/table migration with tick, stage, mode, reason code, gate trace, and account snapshot fields.
- [ ] Implement the evaluator and use it from both execution modes.
- [ ] Change decision audit so `no_action` is persisted as a compact execution audit while still aggregating UI noise in memory.
- [ ] Run focused service and migration tests.

### Task 3: Persistent scoped cooldown

**Files:**
- Create: `market/store/execution_cooldown_store.py`
- Modify: `mysql_storage.py`
- Modify: `market/services/strategy/strategy_service.py`
- Test: `tests/test_execution_cooldown_store.py`
- Test: `test_strategy_service.py`

**Interfaces:**
- Produces: `ExecutionCooldownStore.is_active(key, at)`, `activate(key, at, seconds, reason_code)`, and `clear_expired(at)`.
- Cooldown key is `user/account/deployment/strategy/plan/stage/direction`; decisions without a plan use the selected signal id.

- [ ] Write failing tests proving one plan does not cool down another and `no_action` never activates cooldown.
- [ ] Create the MySQL cooldown table and repository.
- [ ] Replace the process-memory cooldown map for account execution paths.
- [ ] Activate cooldown only after a plan is successfully claimed/order creation begins.
- [ ] Run focused tests.

### Task 4: Transactional exactly-once plan lifecycle

**Files:**
- Modify: `market/store/structure_plan_store.py`
- Modify: `market/services/plan_execution_service.py`
- Modify: `market/services/structure_plan_execution_coordinator.py`
- Modify: `market/services/paper_order_service.py`
- Modify: `server.py`
- Test: `tests/test_structure_plan_execution_coordinator.py`
- Test: `tests/test_structure_plan_store.py`

**Interfaces:**
- Produces: lifecycle `available → claimed → consumed`, with `released` and `invalidated` terminal alternatives.
- Exactly-once identity contains deployment, plan id, plan stage, and direction; grouped alternatives remain mutually exclusive only within the same stage.

- [ ] Write failing concurrent-claim and per-stage consumption tests.
- [ ] Extend deterministic execution identity and unique keys to include stage and direction.
- [ ] Make claim/check/order-record transitions transactional and remove runtime-entity scanning from plan replay decisions.
- [ ] Route both Live and Paper order creation through the same coordinator API.
- [ ] Run focused concurrency and order tests.

### Task 5: Account execution matrix and divergence detection

**Files:**
- Create: `market/services/execution_divergence_monitor.py`
- Modify: `routes_structure_plans.py`
- Modify: `frontend/src/views/StructureAnalysis.vue`
- Test: `tests/test_execution_divergence_monitor.py`
- Test: `tests/test_structure_plan_routes.py`

**Interfaces:**
- Produces: per-plan `execution_matrix` rows with account, deployment, mode, status, reason code, gate trace, and timestamps.
- Produces divergence findings for missing snapshots, triggered plans without audits, and materially different Live/Paper outcomes.

- [ ] Write failing API tests for the complete account matrix and gate trace.
- [ ] Implement divergence classification using durable execution audits.
- [ ] Extend the plans API without per-row database queries.
- [ ] Add expandable account execution rows to each Structure Plan card.
- [ ] Run backend route tests and `npm run build` in `frontend`.

### Task 6: Regression verification

**Files:**
- Modify: relevant focused tests only when a verified compatibility assertion requires it.

**Interfaces:**
- Consumes: all interfaces above.
- Produces: repeatable proof that shared plans cannot silently fork between execution adapters.

- [ ] Run all focused Structure Plan, StrategyService, Paper, route, and migration tests.
- [ ] Run the complete backend test suite and record any unrelated pre-existing failures separately.
- [ ] Run the frontend production build.
- [ ] Inspect `git diff --check` and verify unrelated user changes remain intact.

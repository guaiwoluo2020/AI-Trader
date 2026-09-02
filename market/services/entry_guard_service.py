"""Unified entry-guard boundary shared by live and Paper execution paths."""


class EntryGuardService:
    def __init__(self, *, replay_guard, circuit_breaker):
        self.replay_guard = replay_guard
        self.circuit_breaker = circuit_breaker

    def check_live(self, user_id, account_id, strategy, signal, enabled=True):
        if not enabled:
            return {"allowed": True, "loss_streak": 0, "scope": "live"}
        replay = self.replay_guard.check_live(user_id, account_id, strategy, signal)
        if not replay.get("allowed", True):
            return replay
        return self.circuit_breaker.check_live(user_id, account_id, strategy, signal)

    @staticmethod
    def check_paper(legacy_checker, *args, **kwargs):
        """Temporary adapter keeps Paper's persistence-specific checks intact."""
        return legacy_checker(*args, **kwargs)

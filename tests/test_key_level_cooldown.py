from market.services.signal.key_level_signal import KeyLevelSignalGenerator


class RecordingCooldownRepository:
    def __init__(self):
        self.calls = []
        self.active = set()

    def get_active_until(self, cooldown_id, now=None):
        return None

    def claim_cooldown(self, cooldown_id, cooldown_seconds, now=None):
        self.calls.append((cooldown_id, cooldown_seconds))
        if cooldown_id in self.active:
            return False
        self.active.add(cooldown_id)
        return True


def test_integer_level_cooldown_cannot_be_reduced_by_strategy_configuration():
    repository = RecordingCooldownRepository()
    generator = KeyLevelSignalGenerator(cooldown_repository=repository)

    assert generator._claim_cooldown(
        "GOLD#", 4419, "strategy-1", "source-1",
        "key_level_19_breakout", "buy", "M1", 7200,
    ) is True

    assert repository.calls[-1][1] == 48 * 60 * 60


def test_integer_level_cooldown_is_shared_by_direction_and_setup():
    repository = RecordingCooldownRepository()
    generator = KeyLevelSignalGenerator(cooldown_repository=repository)

    assert generator._claim_cooldown(
        "GOLD#", 4419, "strategy-1", "source-1",
        "key_level_19_breakout", "buy", "M1", 7200,
    ) is True
    assert generator._claim_cooldown(
        "GOLD#", 4419, "strategy-1", "source-1",
        "key_level_19_resistance_reversal", "sell", "M1", 7200,
    ) is False


def test_integer_level_cooldown_key_ignores_direction_and_setup():
    generator = KeyLevelSignalGenerator()
    buy_key = generator._cooldown_key(
        "GOLD#", 4419, "strategy-1", "source-1",
        "key_level_19_breakout", "buy", "M1",
    )
    sell_key = generator._cooldown_key(
        "GOLD#", 4419, "strategy-1", "source-1",
        "key_level_19_resistance_reversal", "sell", "M1",
    )
    assert buy_key == sell_key

#!/usr/bin/env python3
"""Tests for centralized LLM model and quota governance."""

import tempfile
import unittest
from pathlib import Path

from llm_governance import (
    AI_SIGNAL_ANALYSIS,
    ALPHA_CANDIDATE_GENERATION,
    FREE_DAILY_LIMIT,
    LLMGovernanceService,
    LLMQuotaExceeded,
)
from sqlite_storage import LLMConfigRepository, SQLiteStorage, UserRepository


class LLMGovernanceTestCase(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.storage = SQLiteStorage(str(Path(self.temp_dir.name) / "test.db"))
        self.storage.initialize()
        users = UserRepository(self.storage)
        self.admin = users.create_user("llm-admin", "hash", "salt", role="admin")
        self.user = users.create_user("llm-user", "hash", "salt")
        LLMConfigRepository(self.storage).save_config(
            self.admin.user_id,
            api_key="test-key",
            api_base="https://llm.example/v1",
            model="model-a",
        )
        self.service = LLMGovernanceService(self.storage)
        self.service._bootstrap_model("model-a")

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_low_frequency_scene_is_available_without_paid_access(self):
        reservation = self.service.reserve_call(
            self.user.user_id, ALPHA_CANDIDATE_GENERATION
        )
        self.assertEqual(reservation["model"], "model-a")
        self.assertEqual(self.service.quota_status(self.user.user_id)["used"], 1)

    def test_low_frequency_scenes_share_daily_free_quota(self):
        for index in range(FREE_DAILY_LIMIT):
            self.service.reserve_call(
                self.user.user_id,
                ALPHA_CANDIDATE_GENERATION,
                object_id=str(index),
            )
        with self.assertRaisesRegex(LLMQuotaExceeded, "30次"):
            self.service.reserve_call(
                self.user.user_id, ALPHA_CANDIDATE_GENERATION
            )

    def test_high_frequency_scene_still_requires_access(self):
        with self.assertRaisesRegex(PermissionError, "尚未开通"):
            self.service.reserve_call(self.user.user_id, AI_SIGNAL_ANALYSIS)

    def test_scene_rejects_model_outside_admin_allowlist(self):
        self.service._bootstrap_model("model-b")
        self.service.save_scene(ALPHA_CANDIDATE_GENERATION, {
            "enabled": True,
            "model_ids": ["model-a"],
            "default_model_id": "model-a",
            "allow_user_selection": False,
        }, self.admin.user_id)
        with self.assertRaisesRegex(ValueError, "可用模型列表"):
            self.service.reserve_call(
                self.user.user_id, ALPHA_CANDIDATE_GENERATION, "model-b"
            )


if __name__ == "__main__":
    unittest.main()

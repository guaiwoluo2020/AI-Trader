#!/usr/bin/env python3
"""Tests for centralized LLM model and quota governance."""

import tempfile
import unittest
from pathlib import Path

from llm_governance import (
    AI_SIGNAL_ANALYSIS,
    AI_SIGNAL_PROMPT_GENERATION,
    ALPHA_CANDIDATE_GENERATION,
    ALPHA_CANDIDATE_PROMPT_TEMPLATE,
    ALPHA_SYSTEM_PROMPT,
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
        self.service.save_scene(
            ALPHA_CANDIDATE_GENERATION,
            self.scene_payload(),
            self.admin.user_id,
        )

    def tearDown(self):
        self.temp_dir.cleanup()

    def scene_payload(self, **overrides):
        payload = {
            "enabled": True,
            "model_ids": ["model-a"],
            "default_model_id": "model-a",
            "allow_user_selection": False,
            "system_prompt": ALPHA_SYSTEM_PROMPT,
            "user_prompt_template": ALPHA_CANDIDATE_PROMPT_TEMPLATE,
        }
        payload.update(overrides)
        return payload

    def test_low_frequency_scene_is_available_without_paid_access(self):
        reservation = self.service.reserve_call(
            self.user.user_id, ALPHA_CANDIDATE_GENERATION
        )
        self.assertEqual(reservation["model"], "model-a")
        self.assertIn("量化研究助手", reservation["system_prompt"])
        self.assertIn("{{factor_catalog}}", reservation["user_prompt_template"])
        self.assertEqual(self.service.quota_status(self.user.user_id)["used"], 1)

    def test_prompt_generation_scene_reuses_signal_models_when_unconfigured(self):
        options = self.service.scene_options(
            self.user.user_id, AI_SIGNAL_PROMPT_GENERATION
        )
        self.assertEqual(options["models"], ["model-a"])
        self.assertEqual(options["default_model_id"], "model-a")

    def test_missing_builtin_scene_is_reseeded_on_use(self):
        self.storage.execute(
            "DELETE FROM llm_scene_policies WHERE scene_code = ?",
            (AI_SIGNAL_PROMPT_GENERATION,),
        )

        options = self.service.scene_options(
            self.user.user_id, AI_SIGNAL_PROMPT_GENERATION
        )

        self.assertEqual(options["scene_code"], AI_SIGNAL_PROMPT_GENERATION)
        self.assertEqual(options["models"], ["model-a"])

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

    def test_ai_scene_can_keep_multiple_prompts_and_use_the_selected_default(self):
        scene = next(
            item for item in self.service.list_scenes()
            if item["scene_code"] == AI_SIGNAL_ANALYSIS
        )
        prompts = [dict(item) for item in scene["prompt_profiles"]]
        prompts[0]["is_default"] = False
        prompts.append({
            "prompt_id": "ai-signal-alternative",
            "prompt_name": "趋势优先版本",
            "system_prompt": "你是趋势交易分析师，只输出 JSON。",
            "user_prompt_template": (
                "策略：{{strategy_context}}\n市场：{{market_data}}"
            ),
            "is_default": True,
        })
        saved = self.service.save_scene(
            AI_SIGNAL_ANALYSIS,
            {
                "enabled": True,
                "model_ids": ["model-a"],
                "default_model_id": "model-a",
                "allow_user_selection": True,
                "prompt_profiles": prompts,
            },
            self.admin.user_id,
        )

        self.assertEqual(len(saved["prompt_profiles"]), 2)
        self.assertEqual(
            next(item for item in saved["prompt_profiles"] if item["is_default"])["prompt_id"],
            "ai-signal-alternative",
        )
        options = self.service.scene_options(self.user.user_id, AI_SIGNAL_ANALYSIS)
        self.assertEqual(options["system_prompt"], "你是趋势交易分析师，只输出 JSON。")

    def test_scene_rejects_model_outside_admin_allowlist(self):
        self.service._bootstrap_model("model-b")
        self.service.save_scene(
            ALPHA_CANDIDATE_GENERATION,
            self.scene_payload(),
            self.admin.user_id,
        )
        with self.assertRaisesRegex(ValueError, "可用模型列表"):
            self.service.reserve_call(
                self.user.user_id, ALPHA_CANDIDATE_GENERATION, "model-b"
            )

    def test_scene_prompt_requires_required_variables(self):
        with self.assertRaisesRegex(ValueError, "必须保留"):
            self.service.save_scene(
                ALPHA_CANDIDATE_GENERATION,
                self.scene_payload(user_prompt_template="只输出JSON"),
                self.admin.user_id,
            )

    def test_only_one_provider_can_be_active_and_warns_invalid_scene_models(self):
        self.service.save_scene(
            ALPHA_CANDIDATE_GENERATION,
            self.scene_payload(),
            self.admin.user_id,
        )
        second = self.service.configs.save_provider_config(
            self.admin.user_id,
            provider_name="备用供应商",
            api_key="test-key-b",
            api_base="https://llm-b.example/v1",
            model="model-b",
            active=True,
        )

        providers = self.service.configs.list_provider_configs(self.admin.user_id)
        self.assertEqual(sum(1 for item in providers if item["active"]), 1)
        self.assertTrue(second["active"])
        self.assertEqual(self.service._admin_config().api_base, "https://llm-b.example/v1")
        self.storage.execute("UPDATE llm_models SET available = 0 WHERE model_id = 'model-a'")
        self.service._bootstrap_model("model-b")
        warnings = self.service.overview()["scene_model_warnings"]
        self.assertTrue(
            any(item["scene_code"] == ALPHA_CANDIDATE_GENERATION for item in warnings)
        )


if __name__ == "__main__":
    unittest.main()

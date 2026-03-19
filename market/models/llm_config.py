#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
LLM 配置数据结构
"""

from dataclasses import dataclass
from typing import Dict


@dataclass
class LLMConfig:
    """LLM 配置"""
    api_key: str = ""
    api_base: str = "https://api.openai.com/v1"
    model: str = "gpt-4o-mini"

    @property
    def enabled(self) -> bool:
        """是否启用（有 API Key 才启用）"""
        return bool(self.api_key)

    def to_dict(self) -> Dict:
        """转换为字典（API Key 脱敏）"""
        masked_key = ""
        if self.api_key:
            if len(self.api_key) > 8:
                masked_key = self.api_key[:4] + "****" + self.api_key[-4:]
            else:
                masked_key = "****"

        return {
            "api_key": masked_key,
            "api_key_set": bool(self.api_key),
            "api_base": self.api_base,
            "model": self.model,
            "enabled": self.enabled
        }

    @classmethod
    def from_dict(cls, data: Dict) -> 'LLMConfig':
        """从字典创建"""
        return cls(
            api_key=data.get("api_key", ""),
            api_base=data.get("api_base", "https://api.openai.com/v1"),
            model=data.get("model", "gpt-4o-mini")
        )
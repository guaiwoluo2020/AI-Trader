#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
LLM 配置数据结构
"""

from dataclasses import dataclass
from typing import Dict


DEFAULT_SYSTEM_PROMPT = (
    "你是一位专业的金融分析师，擅长技术分析和趋势判断。"
    "请用JSON格式输出分析结果，不要有任何额外的文字说明。"
)

DEFAULT_ANALYSIS_PROMPT_TEMPLATE = """你是一位专业的金融分析师。请分析以下交易品种的K线数据，并给出趋势判断和交易建议。

## 分析要求

1. 对策略启用的每个周期判断趋势类型、置信度(0-100)和理由
2. 给出整体趋势方向、强度(0-100)和总结
3. 根据K线数据分别给出3个关键支撑位和压力位
4. 交易建议必须覆盖策略启用的全部AI周期，period只能是H4、H1、M15、M5、M1之一
5. 每条建议的止盈止损必须满足关联策略中最高的最低盈亏比要求

趋势类型可选：单边上涨、单边下跌、区间震荡、震荡上升、震荡下跌、震荡收窄、震荡扩大。

## 策略约束

{{strategy_context}}

## K线数据

{{market_data}}

## 输出格式

必须输出纯JSON，不要包含Markdown代码块或其他说明：
{
  "品种": {
    "trend_analysis": {
      "策略启用周期": {"trend": "趋势类型", "confidence": 置信度, "reason": "判断理由"}
    },
    "overall_trend": {"direction": "方向", "strength": 强度, "summary": "总结"},
    "key_levels": {"resistance": [压力位1, 压力位2, 压力位3], "support": [支撑位1, 支撑位2, 支撑位3]},
    "trade_suggestions": [{
      "strategy_id": "策略ID", "signal_source_id": "信号源实例ID",
      "period": "策略启用周期",
      "direction": "buy或sell", "confidence": 置信度,
      "entry_price": 入场价格, "stop_loss": 止损价格, "take_profit": 止盈价格,
      "reason": "交易理由"
    }]
  }
}
"""


@dataclass
class LLMConfig:
    """LLM 配置"""
    api_key: str = ""
    api_base: str = "https://api.openai.com/v1"
    model: str = "gpt-4o-mini"
    system_prompt: str = DEFAULT_SYSTEM_PROMPT
    analysis_prompt_template: str = DEFAULT_ANALYSIS_PROMPT_TEMPLATE
    prompt_version: int = 1

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
            "system_prompt": self.system_prompt,
            "analysis_prompt_template": self.analysis_prompt_template,
            "prompt_version": self.prompt_version,
            "enabled": self.enabled
        }

    @classmethod
    def from_dict(cls, data: Dict) -> 'LLMConfig':
        """从字典创建"""
        return cls(
            api_key=data.get("api_key", ""),
            api_base=data.get("api_base", "https://api.openai.com/v1"),
            model=data.get("model", "gpt-4o-mini"),
            system_prompt=data.get("system_prompt") or DEFAULT_SYSTEM_PROMPT,
            analysis_prompt_template=(
                data.get("analysis_prompt_template")
                or DEFAULT_ANALYSIS_PROMPT_TEMPLATE
            ),
            prompt_version=int(data.get("prompt_version", 1)),
        )

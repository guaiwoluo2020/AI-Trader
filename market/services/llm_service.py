#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
LLM 服务模块
处理 LLM 分析相关的业务逻辑
"""

import os
import json
import requests
from datetime import datetime
from typing import Dict, List, Optional

from ..models import LLMConfig, LLMAnalysisResult
from ..store import LLMStore
from .kline_service import KlineService


class LLMService:
    """LLM 服务（处理业务逻辑）"""

    # 分析间隔（秒）
    ANALYZE_INTERVAL = 300  # 5分钟

    # 各周期K线数量限制
    KLINE_LIMITS = {
        'H4': 20,
        'H1': 24,
        'M15': 32,
        'M5': 48,
        'M1': 60
    }

    # 数据过期阈值（秒）
    STALE_THRESHOLD = 180  # 3分钟

    def __init__(self, llm_store: LLMStore, kline_service: KlineService):
        self.llm_store = llm_store
        self.kline_service = kline_service

        # 从环境变量补充配置
        self._load_env_config()

        print("[LLMService] LLM服务已初始化")

    def _load_env_config(self):
        """从环境变量加载配置"""
        config = self.llm_store.get_config()

        if not config.api_key and os.environ.get("LLM_API_KEY"):
            self.llm_store.update_config(api_key=os.environ.get("LLM_API_KEY"))

        if os.environ.get("LLM_API_BASE"):
            self.llm_store.update_config(api_base=os.environ.get("LLM_API_BASE"))

        if os.environ.get("LLM_MODEL"):
            self.llm_store.update_config(model=os.environ.get("LLM_MODEL"))

    # ==================== 配置管理 ====================

    def get_config(self) -> Dict:
        """获取配置"""
        return self.llm_store.get_config().to_dict()

    def configure(self, api_key: str = None, api_base: str = None, model: str = None) -> Dict:
        """配置 LLM 参数"""
        config = self.llm_store.update_config(api_key, api_base, model)
        return {
            "status": "ok",
            "enabled": config.enabled,
            "model": config.model,
            "api_base": config.api_base
        }

    def is_enabled(self) -> bool:
        """是否启用"""
        return self.llm_store.get_config().enabled

    # ==================== 数据收集 ====================

    def collect_klines_for_analysis(self, symbols: List[str]) -> Dict[str, Dict]:
        """
        收集指定品种的K线数据用于分析

        Returns:
            {symbol: {period: [klines]}}
        """
        all_klines = {}

        for symbol in symbols:
            klines_data = {}
            for period in ['H4', 'H1', 'M15', 'M5', 'M1']:
                limit = self.KLINE_LIMITS.get(period, 30)
                klines = self.kline_service.get_klines(symbol, period, limit)
                if klines:
                    klines_data[period] = klines

            if klines_data:
                all_klines[symbol] = klines_data

        return all_klines

    # ==================== Prompt 构建 ====================

    def build_analysis_prompt(self, all_klines: Dict[str, Dict]) -> str:
        """构建分析提示词"""
        prompt = """你是一位专业的金融分析师。请分析以下多个交易品种的K线数据，给出每个品种的趋势判断和交易建议。

## 分析要求

对于每个品种，请分析：
1. 各周期（H4、H1、M15、M5、M1）的趋势判断，包含趋势类型、置信度(0-100)和判断理由
2. 整体趋势方向、强度(0-100)和总结
3. 关键支撑位和压力位（请根据K线数据自行判断，各列出3个）
4. 交易建议：必须包含M1、M5、M15三个周期的具体交易建议

趋势类型可选值：单边上涨、单边下跌、区间震荡、震荡上升、震荡下跌、震荡收窄、震荡扩大

请按以下JSON格式输出（必须是有效的JSON格式，包含所有品种）：

```json
{
  "品种1": {
    "trend_analysis": {
      "H4": {"trend": "趋势类型", "confidence": 置信度, "reason": "判断理由"},
      "H1": {"trend": "趋势类型", "confidence": 置信度, "reason": "判断理由"},
      "M15": {"trend": "趋势类型", "confidence": 置信度, "reason": "判断理由"},
      "M5": {"trend": "趋势类型", "confidence": 置信度, "reason": "判断理由"},
      "M1": {"trend": "趋势类型", "confidence": 置信度, "reason": "判断理由"}
    },
    "overall_trend": {
      "direction": "整体趋势方向",
      "strength": 强度,
      "summary": "整体趋势总结"
    },
    "key_levels": {
      "resistance": [压力位1, 压力位2, 压力位3],
      "support": [支撑位1, 支撑位2, 支撑位3]
    },
    "trade_suggestions": [
      {
        "period": "M15",
        "direction": "buy或sell",
        "entry_price": 入场价格,
        "stop_loss": 止损价格,
        "take_profit": 止盈价格,
        "reason": "交易理由"
      }
    ]
  }
}
```

## K线数据
"""
        # 添加各品种的K线数据
        for symbol, klines_data in all_klines.items():
            prompt += f"\n### {symbol}\n"
            for period, klines in klines_data.items():
                prompt += f"\n#### {period} 周期（{len(klines)}根K线）\n"
                prompt += "| 时间 | 开盘 | 最高 | 最低 | 收盘 |\n"
                prompt += "|------|------|------|------|------|\n"
                for k in klines:
                    prompt += f"| {k['timestamp']} | {k['open']:.2f} | {k['high']:.2f} | {k['low']:.2f} | {k['close']:.2f} |\n"

        prompt += """

请确保输出是纯JSON格式，不要有其他文字说明。每个品种的分析结果都要完整，trade_suggestions必须包含M1、M5、M15三个周期的建议。
"""
        return prompt

    # ==================== LLM API 调用 ====================

    def call_llm(self, prompt: str) -> Optional[Dict]:
        """调用 LLM API（非流式）"""
        config = self.llm_store.get_config()
        if not config.api_key:
            return None

        try:
            headers = {
                "Authorization": f"Bearer {config.api_key}",
                "Content-Type": "application/json"
            }

            data = {
                "model": config.model,
                "messages": [
                    {"role": "system", "content": "你是一位专业的金融分析师，擅长技术分析和趋势判断。请用JSON格式输出分析结果，不要有任何额外的文字说明。"},
                    {"role": "user", "content": prompt}
                ],
                "temperature": 0.3,
                "max_tokens": 4000
            }

            response = requests.post(
                f"{config.api_base}/chat/completions",
                headers=headers,
                json=data,
                timeout=120
            )

            if response.status_code == 200:
                result = response.json()
                content = result["choices"][0]["message"]["content"]
                return self._parse_llm_response(content)
            else:
                print(f"[LLMService] API调用失败: {response.status_code} - {response.text}")
                return None

        except Exception as e:
            print(f"[LLMService] 调用异常: {e}")
            return None

    def call_llm_stream(self, prompt: str, on_chunk: callable = None) -> Optional[Dict]:
        """
        调用 LLM API（流式）

        Args:
            prompt: 提示词
            on_chunk: 回调函数，参数为 (chunk_count, full_content)
        """
        config = self.llm_store.get_config()
        if not config.api_key:
            return None

        try:
            headers = {
                "Authorization": f"Bearer {config.api_key}",
                "Content-Type": "application/json"
            }

            data = {
                "model": config.model,
                "messages": [
                    {"role": "system", "content": "你是一位专业的金融分析师，擅长技术分析和趋势判断。请用JSON格式输出分析结果，不要有任何额外的文字说明。"},
                    {"role": "user", "content": prompt}
                ],
                "temperature": 0.3,
                "max_tokens": 4000,
                "stream": True
            }

            response = requests.post(
                f"{config.api_base}/chat/completions",
                headers=headers,
                json=data,
                timeout=120,
                stream=True
            )

            if response.status_code != 200:
                print(f"[LLMService] API调用失败: {response.status_code} - {response.text}")
                return None

            # 收集完整响应
            full_content = ""
            chunk_count = 0

            for line in response.iter_lines():
                if not line:
                    continue

                line = line.decode('utf-8')
                if line.startswith('data: '):
                    data_str = line[6:]
                    if data_str == '[DONE]':
                        break

                    try:
                        chunk_data = json.loads(data_str)
                        if 'choices' in chunk_data and len(chunk_data['choices']) > 0:
                            delta = chunk_data['choices'][0].get('delta', {})
                            content_piece = delta.get('content', '')
                            if content_piece:
                                full_content += content_piece
                                chunk_count += 1

                                if on_chunk:
                                    on_chunk(chunk_count, full_content)
                    except json.JSONDecodeError:
                        continue

            print(f"[LLMService] 流式接收完成，共 {chunk_count} 个chunk，{len(full_content)} 字符")
            return self._parse_llm_response(full_content)

        except Exception as e:
            print(f"[LLMService] 流式调用异常: {e}")
            return None

    def _parse_llm_response(self, content: str) -> Optional[Dict]:
        """解析 LLM 响应"""
        try:
            # 提取JSON部分
            if "```json" in content:
                content = content.split("```json")[1].split("```")[0]
            elif "```" in content:
                content = content.split("```")[1].split("```")[0]

            return json.loads(content.strip())
        except json.JSONDecodeError as e:
            print(f"[LLMService] JSON解析失败: {e}")
            return None

    # ==================== 入场价检测 ====================

    def check_entry_price_nearby(self, symbol: str, current_price: float,
                                  threshold: float = 0.0001) -> List[Dict]:
        """
        检查当前价格是否接近 AI 建议的入场价

        Args:
            symbol: 交易品种
            current_price: 当前价格
            threshold: 价格接近阈值，默认万分之一

        Returns:
            匹配的交易建议列表
        """
        matched = []

        result = self.llm_store.get_analysis_result(symbol)
        if not result or not result.trade_suggestions:
            return matched

        for suggestion in result.trade_suggestions:
            entry_price = suggestion.get('entry_price')
            period = suggestion.get('period')
            direction = suggestion.get('direction')
            stop_loss = suggestion.get('stop_loss')
            take_profit = suggestion.get('take_profit')

            if not entry_price or entry_price <= 0:
                continue

            # 验证止损止盈
            if not stop_loss or not take_profit or stop_loss <= 0 or take_profit <= 0:
                print(f"[LLMService] 跳过无效建议: {period} sl={stop_loss}, tp={take_profit}")
                continue

            price_diff_pct = abs(current_price - entry_price) / entry_price

            if price_diff_pct <= threshold:
                # 检查冷却
                can_alert = self.llm_store.check_entry_alert_cooldown(
                    symbol, period, direction, entry_price
                )

                if can_alert:
                    matched.append({
                        "symbol": symbol,
                        "period": period,
                        "direction": direction,
                        "entry_price": entry_price,
                        "current_price": current_price,
                        "price_diff_pct": round(price_diff_pct * 100, 4),
                        "stop_loss": stop_loss,
                        "take_profit": take_profit,
                        "reason": suggestion.get('reason'),
                        "analyzed_at": result.analyzed_at
                    })
                    print(f"[LLMService] 价格接近AI入场价: {symbol} {period} "
                          f"入场价 {entry_price:.2f}, 当前价 {current_price:.2f}")

        # 清理过期记录
        self.llm_store.cleanup_entry_alerts()

        return matched

    # ==================== 分析执行 ====================

    def run_analysis(self, on_status: callable = None, on_complete: callable = None) -> Dict:
        """
        执行分析

        Args:
            on_status: 状态回调
            on_complete: 完成回调

        Returns:
            分析结果
        """
        if not self.is_enabled():
            return {"status": "error", "message": "LLM 未启用"}

        # 获取品种列表
        symbols = self.kline_service.get_symbols()
        if not symbols:
            if on_status:
                on_status("error", "没有品种数据")
            return {"status": "error", "message": "没有品种数据"}

        if on_status:
            on_status("analyzing", f"正在检查 {len(symbols)} 个品种...")

        # 检查数据状态
        status = self.kline_service.check_symbols_status(symbols, self.STALE_THRESHOLD)
        active_symbols = status["active"]

        # 更新过期和休市品种状态
        for symbol in status["stale"]:
            self.llm_store.update_market_status(symbol, "stale", data_stale=True)
        for symbol in status["closed"]:
            self.llm_store.update_market_status(symbol, "closed", data_stale=True)

        if not active_symbols:
            if on_status:
                on_status("stale", "所有品种数据均未更新")
            return {"status": "ok", "message": "所有品种数据均未更新"}

        if on_status:
            on_status("analyzing", f"正在分析 {len(active_symbols)} 个品种...")

        # 收集K线数据
        all_klines = self.collect_klines_for_analysis(active_symbols)
        if not all_klines:
            if on_status:
                on_status("error", "无K线数据可分析")
            return {"status": "error", "message": "无K线数据可分析"}

        # 构建提示词
        prompt = self.build_analysis_prompt(all_klines)

        # 调用 LLM
        def on_chunk(count, content):
            if on_status and count % 50 == 0:
                on_status("streaming", f"正在接收分析结果... ({len(content)} 字符)")

        response = self.call_llm_stream(prompt, on_chunk)

        # 保存结果
        if response:
            for symbol, analysis in response.items():
                if isinstance(analysis, dict):
                    self.llm_store.save_analysis_dict(symbol, analysis)

        if on_complete:
            on_complete(response)

        return {
            "status": "ok",
            "analyzed_symbols": list(response.keys()) if response else []
        }

    # ==================== 查询 ====================

    def get_analysis(self, symbol: str = None) -> Dict:
        """获取分析结果"""
        return self.llm_store.get_analysis(symbol)

    def get_status(self) -> Dict:
        """获取状态"""
        return self.llm_store.get_status()
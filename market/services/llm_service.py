#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
LLM 服务模块
处理 LLM 分析相关的业务逻辑
"""

import os
import json
import re
import requests
from datetime import datetime
from typing import Dict, List, Optional

from ..models import LLMConfig, LLMAnalysisResult
from ..store import LLMStore
from .kline_service import KlineService


class LLMRequestError(RuntimeError):
    """大模型供应商请求失败。"""


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
        self._strategy_store = None

        # 从环境变量补充配置
        self._load_env_config()

        print("[LLMService] LLM服务已初始化")

    def set_strategy_store(self, strategy_store) -> None:
        """注入当前用户的策略仓储，用于约束分析范围。"""
        self._strategy_store = strategy_store

    def _build_ai_analysis_plan(self, available_symbols: List[str]) -> Dict[str, Dict]:
        """聚合同一品种多策略启用的 AI 周期和分析约束。"""
        if self._strategy_store is None:
            return {
                symbol: {
                    "periods": {
                        period: {"weight": 0}
                        for period in ['H4', 'H1', 'M15', 'M5', 'M1']
                    },
                    "strategies": [],
                }
                for symbol in available_symbols
            }

        available = set(available_symbols)
        plan: Dict[str, Dict] = {}
        for strategy in self._strategy_store.get_all_strategies():
            if not strategy.enabled or strategy.symbol not in available:
                continue

            ai_config = (strategy.signal_config or {}).get("ai_entry", {})
            if not ai_config.get("enabled", False):
                continue

            enabled_periods = {}
            for period, config in ai_config.get("periods", {}).items():
                weight = int(config.get("weight", 0))
                if config.get("enabled", False) and weight > 0:
                    enabled_periods[period] = weight
            if not enabled_periods:
                continue

            symbol_plan = plan.setdefault(
                strategy.symbol,
                {"periods": {}, "strategies": []},
            )
            for period, weight in enabled_periods.items():
                current = symbol_plan["periods"].get(period, {"weight": 0})
                current["weight"] = max(current["weight"], weight)
                symbol_plan["periods"][period] = current
            symbol_plan["strategies"].append({
                "strategy_id": strategy.strategy_id,
                "strategy_name": strategy.strategy_name,
                "periods": enabled_periods,
                "min_confidence": strategy.min_confidence,
                "min_risk_reward": strategy.min_risk_reward,
            })
        return plan

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

    def collect_klines_for_analysis(
        self,
        symbols: List[str],
        analysis_plan: Optional[Dict[str, Dict]] = None,
    ) -> Dict[str, Dict]:
        """
        收集指定品种的K线数据用于分析

        Returns:
            {symbol: {period: [klines]}}
        """
        all_klines = {}

        for symbol in symbols:
            klines_data = {}
            periods = (
                analysis_plan[symbol]["periods"].keys()
                if analysis_plan and symbol in analysis_plan
                else ['H4', 'H1', 'M15', 'M5', 'M1']
            )
            for period in periods:
                limit = self.KLINE_LIMITS.get(period, 30)
                klines = self.kline_service.get_klines(symbol, period, limit)
                if klines:
                    klines_data[period] = klines

            if klines_data:
                all_klines[symbol] = klines_data

        return all_klines

    # ==================== Prompt 构建 ====================

    def build_analysis_prompt(
        self,
        all_klines: Dict[str, Dict],
        analysis_plan: Optional[Dict[str, Dict]] = None,
    ) -> str:
        """构建分析提示词"""
        prompt = """你是一位专业的金融分析师。请分析以下多个交易品种的K线数据，给出每个品种的趋势判断和交易建议。

## 分析要求

对于每个品种，请分析：
1. 对每个品种实际提供的策略启用周期进行趋势判断，包含趋势类型、置信度(0-100)和判断理由
2. 整体趋势方向、强度(0-100)和总结
3. 关键支撑位和压力位（请根据K线数据自行判断，各列出3个）
4. 交易建议必须覆盖该品种策略启用的全部 AI 周期，period 必须只填写 H4、H1、M15、M5、M1 之一
5. 每条建议的止盈止损必须满足该周期所关联策略中最高的最低盈亏比要求

趋势类型可选值：单边上涨、单边下跌、区间震荡、震荡上升、震荡下跌、震荡收窄、震荡扩大

请按以下JSON格式输出（必须是有效的JSON格式，包含所有品种）：

```json
{
  "品种1": {
    "trend_analysis": {
      "策略启用周期": {"trend": "趋势类型", "confidence": 置信度, "reason": "判断理由"}
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
        "period": "策略启用周期",
        "direction": "buy或sell",
        "confidence": 置信度,
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
            if analysis_plan and symbol in analysis_plan:
                prompt += "\n#### 策略分析要求\n"
                for profile in analysis_plan[symbol]["strategies"]:
                    periods = "、".join(
                        f"{period}(权重{weight})"
                        for period, weight in profile["periods"].items()
                    )
                    prompt += (
                        f"- {profile['strategy_name']} ({profile['strategy_id']}): "
                        f"AI周期 {periods}；最低置信度 "
                        f"{profile['min_confidence']}%；最低盈亏比 "
                        f"{profile['min_risk_reward']}\n"
                    )
            for period, klines in klines_data.items():
                prompt += f"\n#### {period} 周期（{len(klines)}根K线）\n"
                prompt += "| 时间 | 开盘 | 最高 | 最低 | 收盘 |\n"
                prompt += "|------|------|------|------|------|\n"
                for k in klines:
                    prompt += f"| {k['timestamp']} | {k['open']:.2f} | {k['high']:.2f} | {k['low']:.2f} | {k['close']:.2f} |\n"

        prompt += """

请确保输出是纯JSON格式，不要有其他文字说明。每个品种的分析结果都要完整，trade_suggestions必须覆盖该品种策略启用的全部AI周期。
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
            raise LLMRequestError("未配置大模型 API Key")

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
                detail = response.text
                try:
                    payload = response.json()
                    detail = (
                        payload.get("error", {}).get("message")
                        or payload.get("message")
                        or detail
                    )
                except (ValueError, AttributeError):
                    pass
                raise LLMRequestError(
                    f"大模型接口返回 HTTP {response.status_code}: {str(detail)[:300]}"
                )

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
            parsed = self._parse_llm_response(full_content)
            if not parsed:
                raise LLMRequestError("大模型返回内容为空或不是有效 JSON")
            return parsed

        except LLMRequestError:
            raise
        except requests.RequestException as e:
            print(f"[LLMService] 流式调用异常: {e}")
            raise LLMRequestError(f"连接大模型服务失败: {e}") from e
        except Exception as e:
            print(f"[LLMService] 流式调用异常: {e}")
            raise LLMRequestError(f"处理大模型响应失败: {e}") from e

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

    @staticmethod
    def _canonical_period(value, symbol_plan: Dict) -> Optional[str]:
        """将模型生成的自然语言周期归一化为策略使用的周期代码。"""
        text = str(value or "").upper()
        for profile in symbol_plan.get("strategies", []):
            if profile["strategy_id"].upper() in text and len(profile["periods"]) == 1:
                return next(iter(profile["periods"]))

        match = re.search(r"(?<![A-Z0-9])(M15|M5|M1|H4|H1)(?![A-Z0-9])", text)
        if match:
            return match.group(1)

        chinese_periods = (
            ("15分钟", "M15"),
            ("5分钟", "M5"),
            ("1分钟", "M1"),
            ("4小时", "H4"),
            ("1小时", "H1"),
        )
        for label, period in chinese_periods:
            if label in str(value or ""):
                return period
        return None

    def _normalize_analysis_response(
        self, response: Dict, analysis_plan: Dict[str, Dict]
    ) -> Dict:
        """规范模型建议，并确保止盈满足对应策略的最低盈亏比。"""
        for symbol, analysis in response.items():
            if not isinstance(analysis, dict) or symbol not in analysis_plan:
                continue

            symbol_plan = analysis_plan[symbol]
            enabled_periods = set(symbol_plan.get("periods", {}))
            normalized = []
            for suggestion in analysis.get("trade_suggestions", []):
                if not isinstance(suggestion, dict):
                    continue
                period = self._canonical_period(suggestion.get("period"), symbol_plan)
                if period not in enabled_periods:
                    continue

                try:
                    entry = float(suggestion.get("entry_price", 0))
                    stop_loss = float(suggestion.get("stop_loss", 0))
                    take_profit = float(suggestion.get("take_profit", 0))
                except (TypeError, ValueError):
                    continue

                direction = str(suggestion.get("direction", "")).lower()
                valid_levels = (
                    direction == "buy" and stop_loss < entry < take_profit
                ) or (
                    direction == "sell" and take_profit < entry < stop_loss
                )
                if entry <= 0 or stop_loss <= 0 or take_profit <= 0 or not valid_levels:
                    continue

                required_rr = max(
                    [1.0]
                    + [
                        float(profile.get("min_risk_reward", 1.0))
                        for profile in symbol_plan.get("strategies", [])
                        if period in profile.get("periods", {})
                    ]
                )
                risk = abs(entry - stop_loss)
                reward = abs(take_profit - entry)
                if risk <= 0:
                    continue
                if reward / risk < required_rr:
                    take_profit = (
                        entry + risk * required_rr
                        if direction == "buy"
                        else entry - risk * required_rr
                    )

                suggestion["period"] = period
                suggestion["entry_price"] = entry
                suggestion["stop_loss"] = stop_loss
                suggestion["take_profit"] = round(take_profit, 8)
                normalized.append(suggestion)

            analysis["trade_suggestions"] = normalized
        return response

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
                        "confidence": suggestion.get('confidence', 75),
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
        def report(status: str, message: str):
            self.llm_store.set_analysis_status(status, message)
            if on_status:
                on_status(status, message)

        if not self.is_enabled():
            report("error", "大模型分析未启用")
            return {"status": "error", "message": "大模型分析未启用"}

        # 获取品种列表
        symbols = self.kline_service.get_symbols()
        if not symbols:
            report("error", "没有品种数据")
            return {"status": "error", "message": "没有品种数据"}

        analysis_plan = self._build_ai_analysis_plan(symbols)
        if not analysis_plan:
            report("skipped", "没有启用大模型入场信号的策略，跳过 AI 分析")
            return {
                "status": "skipped",
                "message": "没有启用大模型入场信号的策略，跳过 AI 分析",
            }

        strategy_symbols = list(analysis_plan.keys())
        report("analyzing", f"正在检查 {len(strategy_symbols)} 个策略品种...")

        # 检查数据状态
        status = self.kline_service.check_symbols_status(
            strategy_symbols, self.STALE_THRESHOLD
        )
        active_symbols = status["active"]

        # 更新过期和休市品种状态
        for symbol in status["stale"]:
            self.llm_store.update_market_status(symbol, "stale", data_stale=True)
        for symbol in status["closed"]:
            self.llm_store.update_market_status(symbol, "closed", data_stale=True)

        if not active_symbols:
            report("stale", "所有品种行情均超过 3 分钟未更新，暂不发起 AI 分析")
            return {
                "status": "stale",
                "message": "所有品种行情均超过 3 分钟未更新，暂不发起 AI 分析",
            }

        report("analyzing", f"正在分析 {len(active_symbols)} 个品种...")

        # 收集K线数据
        all_klines = self.collect_klines_for_analysis(
            active_symbols, analysis_plan
        )
        if not all_klines:
            report("error", "无K线数据可分析")
            return {"status": "error", "message": "无K线数据可分析"}

        # 构建提示词
        prompt = self.build_analysis_prompt(all_klines, analysis_plan)

        # 调用 LLM
        def on_chunk(count, content):
            if count % 50 == 0:
                report("streaming", f"正在接收分析结果... ({len(content)} 字符)")

        try:
            response = self.call_llm_stream(prompt, on_chunk)
        except LLMRequestError as exc:
            message = str(exc)
            report("error", message)
            return {"status": "error", "message": message}

        response = self._normalize_analysis_response(response, analysis_plan)

        # 保存结果
        if response:
            for symbol, analysis in response.items():
                if isinstance(analysis, dict):
                    self.llm_store.save_analysis_dict(symbol, analysis)

        if on_complete:
            on_complete(response)

        analyzed_symbols = list(response.keys())
        report("completed", f"分析完成，共生成 {len(analyzed_symbols)} 个品种的结果")
        return {
            "status": "ok",
            "message": "分析完成",
            "analyzed_symbols": analyzed_symbols,
        }

    # ==================== 查询 ====================

    def get_analysis(self, symbol: str = None) -> Dict:
        """获取分析结果"""
        return self.llm_store.get_analysis(symbol)

    def get_status(self) -> Dict:
        """获取状态"""
        return self.llm_store.get_status()

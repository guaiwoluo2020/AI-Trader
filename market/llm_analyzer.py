#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
大模型行情趋势分析模块
使用大语言模型分析K线数据，生成趋势判断和交易建议
"""

import os
import json
import threading
import asyncio
import requests
from datetime import datetime
from typing import List, Dict, Optional, Set
from collections import defaultdict

# 加载 .env 文件
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

from .system_log import get_system_log


class LLMAnalyzer:
    """大模型行情分析器"""

    # 分析间隔（秒）
    ANALYZE_INTERVAL = 300  # 5分钟

    # 趋势类型
    TREND_TYPES = [
        "单边上涨",
        "单边下跌",
        "区间震荡",
        "震荡上升",
        "震荡下跌",
        "震荡收窄",
        "震荡扩大"
    ]

    # 各周期K线数量限制
    KLINE_LIMITS = {
        'H4': 20,   # 4小时，发送最近20根
        'H1': 24,   # 1小时，发送最近24根（一天）
        'M15': 32,  # 15分钟，发送最近32根（8小时）
        'M5': 48,   # 5分钟，发送最近48根（4小时）
        'M1': 60    # 1分钟，发送最近60根（1小时）
    }

    # 配置文件路径
    CONFIG_FILE = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "llm_config.json")

    def __init__(self, market_store):
        """
        初始化大模型分析器

        Args:
            market_store: K线存储对象
        """
        self.market_store = market_store

        # 存储分析结果: {SYMBOL: analysis_result}
        self._analysis_results = {}
        self._last_analysis_time = None
        self._lock = threading.RLock()

        # WebSocket连接管理
        self._ws_clients: Set = set()
        self._ws_lock = threading.Lock()

        # 主事件循环引用（在FastAPI启动时设置）
        self._main_loop = None

        # 已提醒的AI入场价记录（避免重复提醒）
        # 结构: {(symbol, period, direction, entry_price): datetime}
        self._alerted_entries: Dict[tuple, datetime] = {}
        self._entry_alert_lock = threading.Lock()

        # AI入场价提醒冷却时间（秒）
        self.entry_alert_cooldown = 300  # 5分钟

        # 配置（先从文件加载，再从环境变量补充）
        self._api_key = ""
        self._api_base = "https://api.openai.com/v1"
        self._model = "gpt-4o-mini"
        self._enabled = False

        # 从文件加载配置
        self._load_from_file()

        # 环境变量覆盖（如果文件中没有配置）
        if not self._api_key and os.environ.get("LLM_API_KEY"):
            self._api_key = os.environ.get("LLM_API_KEY", "")
        if not self._api_base or self._api_base == "https://api.openai.com/v1":
            self._api_base = os.environ.get("LLM_API_BASE", "https://api.openai.com/v1")
        if not self._model or self._model == "gpt-4o-mini":
            self._model = os.environ.get("LLM_MODEL", "gpt-4o-mini")

        self._enabled = bool(self._api_key)

        # 启动定时分析线程
        if self._enabled:
            self._start_analyze_thread()
            print("[LLMAnalyzer] 大模型分析器已初始化（已启用）")
        else:
            print("[LLMAnalyzer] 大模型分析器已初始化（未配置API Key，功能禁用）")

    def set_event_loop(self, loop):
        """设置主事件循环引用"""
        self._main_loop = loop
        print(f"[LLMAnalyzer] 已设置主事件循环")

    def _load_from_file(self):
        """从文件加载配置"""
        try:
            if os.path.exists(self.CONFIG_FILE):
                with open(self.CONFIG_FILE, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    self._api_key = data.get("api_key", "")
                    self._api_base = data.get("api_base", "https://api.openai.com/v1")
                    self._model = data.get("model", "gpt-4o-mini")
                print(f"[LLMAnalyzer] 已从文件加载配置: {self.CONFIG_FILE}")
        except Exception as e:
            print(f"[LLMAnalyzer] 加载配置文件失败: {e}")

    def _save_to_file(self):
        """保存配置到文件"""
        try:
            # 确保目录存在
            config_dir = os.path.dirname(self.CONFIG_FILE)
            os.makedirs(config_dir, exist_ok=True)

            data = {
                "api_key": self._api_key,
                "api_base": self._api_base,
                "model": self._model
            }
            with open(self.CONFIG_FILE, 'w', encoding='utf-8') as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
            print(f"[LLMAnalyzer] 配置已保存到文件")
        except Exception as e:
            print(f"[LLMAnalyzer] 保存配置文件失败: {e}")

    def get_config(self) -> Dict:
        """获取当前配置（API Key会脱敏显示）"""
        # 脱敏API Key：只显示前4位和后4位
        masked_key = ""
        if self._api_key:
            if len(self._api_key) > 8:
                masked_key = self._api_key[:4] + "****" + self._api_key[-4:]
            else:
                masked_key = "****"

        return {
            "api_key": masked_key,
            "api_key_set": bool(self._api_key),
            "api_base": self._api_base,
            "model": self._model,
            "enabled": self._enabled
        }

    def _start_analyze_thread(self):
        """启动定时分析线程"""
        def analyze_loop():
            # 等待事件循环设置完成
            import time
            time.sleep(5)  # 等待5秒让服务完全启动
            print("[LLMAnalyzer] 分析线程启动，开始第一次分析...")

            while True:
                try:
                    self._run_analysis()
                except Exception as e:
                    print(f"[LLMAnalyzer] 分析异常: {e}")
                    import traceback
                    traceback.print_exc()
                # 等待5分钟
                threading.Event().wait(self.ANALYZE_INTERVAL)

        thread = threading.Thread(target=analyze_loop, daemon=True)
        thread.start()
        print("[LLMAnalyzer] 分析线程已创建")

    def _run_analysis(self):
        """执行分析 - 合并所有品种到一次请求（流式输出）"""
        symbols = self.market_store.get_symbols()
        print(f"[LLMAnalyzer] _run_analysis 调用，获取到 {len(symbols) if symbols else 0} 个品种")

        if not symbols:
            print("[LLMAnalyzer] 没有品种数据，跳过分析")
            return

        print(f"[LLMAnalyzer] 开始分析 {len(symbols)} 个品种: {symbols}")

        # 广播分析开始
        self._broadcast_analysis_status("analyzing", f"正在检查 {len(symbols)} 个品种的数据更新状态...")

        # 检查每个品种的M1 K线更新状态（3分钟内有效）
        STALE_THRESHOLD = 180  # 3分钟

        active_symbols = []  # 有数据更新的品种
        stale_symbols = []   # 数据过期的品种

        for symbol in symbols:
            m1_status = self.market_store.check_m1_updated_within(symbol, STALE_THRESHOLD)
            market_status = m1_status.get("market_status", "closed")

            if market_status == "active":
                active_symbols.append(symbol)
                print(f"[LLMAnalyzer] {symbol} M1数据有效，距今 {m1_status['seconds_ago']} 秒")
            elif market_status == "stale":
                stale_symbols.append(symbol)
                print(f"[LLMAnalyzer] {symbol} M1数据过期，距今 {m1_status['seconds_ago']} 秒，跳过分析")
            else:  # closed
                stale_symbols.append(symbol)
                print(f"[LLMAnalyzer] {symbol} 休市中，无新数据，跳过分析")
                # 标记休市状态
                with self._lock:
                    if symbol in self._analysis_results:
                        self._analysis_results[symbol]["market_status"] = "closed"
                    else:
                        # 没有历史分析结果，创建一个标记休市的记录
                        self._analysis_results[symbol] = {
                            "symbol": symbol,
                            "analysis": None,
                            "analyzed_at": None,
                            "market_status": "closed",
                            "data_stale": True
                        }

        # 更新过期品种的状态标记（不包括休市品种，它们已经在上面处理了）
        with self._lock:
            for symbol in stale_symbols:
                m1_status = self.market_store.check_m1_updated_within(symbol, STALE_THRESHOLD)
                if m1_status.get("market_status") == "stale" and symbol in self._analysis_results:
                    # 保留上次分析结果，但标记为过期
                    self._analysis_results[symbol]["data_stale"] = True
                    self._analysis_results[symbol]["market_status"] = "stale"
                    self._analysis_results[symbol]["stale_seconds"] = m1_status.get("seconds_ago")

        # 如果没有活跃品种，广播状态并返回
        if not active_symbols:
            print("[LLMAnalyzer] 所有品种数据均过期，跳过大模型调用")
            self._broadcast_analysis_status("stale", "所有品种行情数据均未更新，使用上次分析结果")
            self._last_analysis_time = datetime.now().isoformat()
            self._broadcast_analysis_update()
            return

        # 广播实际分析的品种
        if stale_symbols:
            self._broadcast_analysis_status("analyzing",
                f"分析 {len(active_symbols)} 个品种，{len(stale_symbols)} 个品种数据未更新")
        else:
            self._broadcast_analysis_status("analyzing",
                f"正在分析 {len(active_symbols)} 个品种...")

        # 收集活跃品种的K线数据
        all_klines_data = {}
        for symbol in active_symbols:
            klines_data = {}
            for period in ['H4', 'H1', 'M15', 'M5', 'M1']:
                limit = self.KLINE_LIMITS.get(period, 30)
                klines = self.market_store.get_klines(symbol, period, limit)
                if klines:
                    klines_data[period] = klines
                    print(f"[LLMAnalyzer] {symbol} {period} 获取到 {len(klines)} 条K线")
            if klines_data:
                all_klines_data[symbol] = klines_data

        print(f"[LLMAnalyzer] 共收集 {len(all_klines_data)} 个品种的K线数据: {list(all_klines_data.keys())}")

        if not all_klines_data:
            print("[LLMAnalyzer] 无K线数据可分析")
            self._broadcast_analysis_status("error", "无K线数据可分析")
            return

        # 构建合并的提示词
        prompt = self._build_combined_prompt(all_klines_data)

        # 记录分析开始
        system_log = get_system_log()
        system_log.add_log(
            "llm_analysis_start",
            {"symbols": active_symbols, "symbol_count": len(active_symbols)},
            message=f"开始分析 {len(active_symbols)} 个品种"
        )

        # 调用大模型（流式）
        response = self._call_llm_stream(prompt)

        print(f"[LLMAnalyzer] 大模型返回结果: {type(response)}, 内容长度: {len(response) if response else 0}")

        if response:
            print(f"[LLMAnalyzer] 返回的品种: {list(response.keys())}")
            # 解析结果，按品种存储
            with self._lock:
                for symbol, analysis in response.items():
                    if isinstance(analysis, dict):
                        self._analysis_results[symbol] = {
                            "symbol": symbol,
                            "analysis": analysis,
                            "analyzed_at": datetime.now().isoformat(),
                            "data_stale": False  # 标记数据是最新的
                        }
                        print(f"[LLMAnalyzer] 已存储 {symbol} 的分析结果")

            # 记录分析完成
            system_log.add_log(
                "llm_analysis_complete",
                {"symbols": list(response.keys()), "symbol_count": len(response)},
                message=f"分析完成，{len(response)} 个品种"
            )
        else:
            print(f"[LLMAnalyzer] 大模型返回为空，分析失败")
            # 记录分析错误
            system_log.add_log(
                "llm_analysis_error",
                {"reason": "大模型返回为空"},
                message="分析失败"
            )

        self._last_analysis_time = datetime.now().isoformat()
        print(f"[LLMAnalyzer] 分析完成，时间: {self._last_analysis_time}")

        # 广播分析完成通知
        self._broadcast_analysis_update()

    def _build_combined_prompt(self, all_klines_data: Dict) -> str:
        """构建合并的分析提示词"""
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
      },
      {
        "period": "M5",
        "direction": "buy或sell",
        "entry_price": 入场价格,
        "stop_loss": 止损价格,
        "take_profit": 止盈价格,
        "reason": "交易理由"
      },
      {
        "period": "M1",
        "direction": "buy或sell",
        "entry_price": 入场价格,
        "stop_loss": 止损价格,
        "take_profit": 止盈价格,
        "reason": "交易理由"
      }
    ]
  },
  "品种2": { ... }
}
```

## K线数据
"""
        # 添加各品种的K线数据
        for symbol, klines_data in all_klines_data.items():
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

    def _call_llm(self, prompt: str) -> Optional[Dict]:
        """调用大模型API（非流式，保留兼容）"""
        if not self._api_key:
            return None

        try:
            headers = {
                "Authorization": f"Bearer {self._api_key}",
                "Content-Type": "application/json"
            }

            data = {
                "model": self._model,
                "messages": [
                    {"role": "system", "content": "你是一位专业的金融分析师，擅长技术分析和趋势判断。请用JSON格式输出分析结果，不要有任何额外的文字说明。"},
                    {"role": "user", "content": prompt}
                ],
                "temperature": 0.3,
                "max_tokens": 4000
            }

            response = requests.post(
                f"{self._api_base}/chat/completions",
                headers=headers,
                json=data,
                timeout=120
            )

            if response.status_code == 200:
                result = response.json()
                content = result["choices"][0]["message"]["content"]

                # 提取JSON部分
                if "```json" in content:
                    content = content.split("```json")[1].split("```")[0]
                elif "```" in content:
                    content = content.split("```")[1].split("```")[0]

                return json.loads(content.strip())
            else:
                print(f"[LLMAnalyzer] API调用失败: {response.status_code} - {response.text}")
                return None

        except Exception as e:
            print(f"[LLMAnalyzer] 调用异常: {e}")
            import traceback
            traceback.print_exc()
            return None

    def _call_llm_stream(self, prompt: str) -> Optional[Dict]:
        """调用大模型API（流式输出）"""
        if not self._api_key:
            return None

        try:
            headers = {
                "Authorization": f"Bearer {self._api_key}",
                "Content-Type": "application/json"
            }

            data = {
                "model": self._model,
                "messages": [
                    {"role": "system", "content": "你是一位专业的金融分析师，擅长技术分析和趋势判断。请用JSON格式输出分析结果，不要有任何额外的文字说明。"},
                    {"role": "user", "content": prompt}
                ],
                "temperature": 0.3,
                "max_tokens": 4000,
                "stream": True  # 启用流式输出
            }

            response = requests.post(
                f"{self._api_base}/chat/completions",
                headers=headers,
                json=data,
                timeout=120,
                stream=True  # 流式响应
            )

            if response.status_code != 200:
                print(f"[LLMAnalyzer] API调用失败: {response.status_code} - {response.text}")
                self._broadcast_analysis_status("error", f"API调用失败: {response.status_code}")
                return None

            # 收集完整响应
            full_content = ""
            chunk_count = 0

            for line in response.iter_lines():
                if not line:
                    continue

                line = line.decode('utf-8')
                if line.startswith('data: '):
                    data_str = line[6:]  # 去掉 'data: '
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

                                # 每50个chunk广播一次进度
                                if chunk_count % 50 == 0:
                                    self._broadcast_analysis_status(
                                        "streaming",
                                        f"正在接收分析结果... ({len(full_content)} 字符)"
                                    )
                    except json.JSONDecodeError:
                        continue

            print(f"[LLMAnalyzer] 流式接收完成，共 {chunk_count} 个chunk，{len(full_content)} 字符")

            # 提取JSON部分
            if "```json" in full_content:
                full_content = full_content.split("```json")[1].split("```")[0]
            elif "```" in full_content:
                full_content = full_content.split("```")[1].split("```")[0]

            result = json.loads(full_content.strip())
            return result

        except json.JSONDecodeError as e:
            print(f"[LLMAnalyzer] JSON解析失败: {e}")
            self._broadcast_analysis_status("error", "JSON解析失败")
            return None
        except Exception as e:
            print(f"[LLMAnalyzer] 流式调用异常: {e}")
            import traceback
            traceback.print_exc()
            self._broadcast_analysis_status("error", f"调用异常: {str(e)}")
            return None

    def get_analysis(self, symbol: str = None) -> Dict:
        """
        获取分析结果

        Args:
            symbol: 品种名称，不指定则返回所有

        Returns:
            分析结果
        """
        with self._lock:
            if symbol:
                return self._analysis_results.get(symbol)
            return dict(self._analysis_results)

    def get_status(self) -> Dict:
        """获取分析器状态"""
        with self._lock:
            return {
                "enabled": self._enabled,
                "model": self._model,
                "api_base": self._api_base,
                "last_analysis_time": self._last_analysis_time,
                "symbols_analyzed": list(self._analysis_results.keys()),
                "interval_seconds": self.ANALYZE_INTERVAL
            }

    def trigger_analysis(self) -> Dict:
        """手动触发分析"""
        if not self._enabled:
            return {"status": "error", "message": "大模型分析未启用"}

        try:
            print("[LLMAnalyzer] 手动触发分析...")
            self._run_analysis()
            return {"status": "ok", "message": "分析完成", "analyzed_at": self._last_analysis_time}
        except Exception as e:
            print(f"[LLMAnalyzer] 手动触发分析失败: {e}")
            import traceback
            traceback.print_exc()
            return {"status": "error", "message": str(e)}

    def configure(self, api_key: str = None, api_base: str = None, model: str = None) -> Dict:
        """
        配置大模型参数

        Args:
            api_key: API密钥
            api_base: API基础URL
            model: 模型名称

        Returns:
            配置结果
        """
        if api_key:
            self._api_key = api_key
            os.environ["LLM_API_KEY"] = api_key

        if api_base:
            self._api_base = api_base
            os.environ["LLM_API_BASE"] = api_base

        if model:
            self._model = model
            os.environ["LLM_MODEL"] = model

        # 保存到文件
        self._save_to_file()

        # 检查是否可以启用
        was_enabled = self._enabled
        self._enabled = bool(self._api_key)

        # 如果从禁用变为启用，启动分析线程
        if self._enabled and not was_enabled:
            self._start_analyze_thread()

        return {
            "status": "ok",
            "enabled": self._enabled,
            "model": self._model,
            "api_base": self._api_base
        }

    # ==================== WebSocket管理 ====================

    def add_ws_client(self, client):
        """添加WebSocket客户端"""
        with self._ws_lock:
            self._ws_clients.add(client)
            print(f"[LLMAnalyzer] WebSocket客户端已连接, 当前连接数: {len(self._ws_clients)}")

    def remove_ws_client(self, client):
        """移除WebSocket客户端"""
        with self._ws_lock:
            self._ws_clients.discard(client)
            print(f"[LLMAnalyzer] WebSocket客户端已断开, 当前连接数: {len(self._ws_clients)}")

    def _broadcast_analysis_update(self):
        """广播分析更新通知"""
        message = json.dumps({
            "type": "llm_analysis_update",
            "timestamp": self._last_analysis_time,
            "symbols": list(self._analysis_results.keys())
        })

        self._broadcast_message(message)

    def _broadcast_analysis_status(self, status: str, message: str):
        """广播分析状态更新"""
        msg = json.dumps({
            "type": "llm_analysis_status",
            "status": status,
            "message": message,
            "timestamp": datetime.now().isoformat()
        })

        self._broadcast_message(msg)

    def _broadcast_message(self, message: str):
        """广播消息到所有WebSocket客户端"""
        with self._ws_lock:
            clients = list(self._ws_clients)

        if not clients:
            return

        # 使用保存的主事件循环
        if self._main_loop and self._main_loop.is_running():
            for client in clients:
                try:
                    asyncio.run_coroutine_threadsafe(
                        self._send_to_client(client, message),
                        self._main_loop
                    )
                except Exception as e:
                    print(f"[LLMAnalyzer] 广播消息失败: {e}")
        else:
            print(f"[LLMAnalyzer] 事件循环未就绪，跳过广播（{len(clients)}个客户端）")

    async def _send_to_client(self, client, message: str):
        """发送消息到客户端"""
        try:
            await client.send_text(message)
        except Exception as e:
            print(f"[LLMAnalyzer] 发送消息到客户端失败: {e}")
            with self._ws_lock:
                self._ws_clients.discard(client)

    def check_entry_price_nearby(self, symbol: str, current_price: float, threshold: float = 0.0001) -> List[Dict]:
        """
        检查当前价格是否接近AI建议的入场价

        Args:
            symbol: 交易品种
            current_price: 当前价格
            threshold: 价格接近阈值，默认万分之一（0.0001）

        Returns:
            匹配的交易建议列表
        """
        matched_suggestions = []
        current_time = datetime.now()

        with self._lock:
            analysis_data = self._analysis_results.get(symbol)
            if not analysis_data or 'analysis' not in analysis_data:
                return matched_suggestions

            trade_suggestions = analysis_data['analysis'].get('trade_suggestions', [])
            if not trade_suggestions:
                return matched_suggestions

            for suggestion in trade_suggestions:
                entry_price = suggestion.get('entry_price')
                period = suggestion.get('period')
                direction = suggestion.get('direction')

                if not entry_price or entry_price <= 0:
                    continue

                # 计算价格差距百分比
                if entry_price > 0:
                    price_diff_pct = abs(current_price - entry_price) / entry_price

                    # 如果在阈值范围内
                    if price_diff_pct <= threshold:
                        # 检查冷却
                        alert_key = (symbol, period, direction, entry_price)

                        with self._entry_alert_lock:
                            should_alert = True

                            if alert_key in self._alerted_entries:
                                last_alert_time = self._alerted_entries[alert_key]
                                elapsed = (current_time - last_alert_time).total_seconds()

                                if elapsed < self.entry_alert_cooldown:
                                    should_alert = False
                                    print(f"[LLMAnalyzer] 跳过AI入场价提醒（冷却中）: {symbol} {period} "
                                          f"入场价 {entry_price:.2f}, 剩余 {self.entry_alert_cooldown - elapsed:.0f}秒")

                            if should_alert:
                                # 记录提醒时间
                                self._alerted_entries[alert_key] = current_time

                                matched = {
                                    "symbol": symbol,
                                    "period": period,
                                    "direction": direction,
                                    "entry_price": entry_price,
                                    "current_price": current_price,
                                    "price_diff_pct": round(price_diff_pct * 100, 4),
                                    "stop_loss": suggestion.get('stop_loss'),
                                    "take_profit": suggestion.get('take_profit'),
                                    "reason": suggestion.get('reason'),
                                    "analyzed_at": analysis_data.get('analyzed_at'),
                                    "match_type": "ai_entry_nearby"
                                }
                                matched_suggestions.append(matched)
                                print(f"[LLMAnalyzer] 价格接近AI入场价: {symbol} {period} "
                                      f"入场价 {entry_price:.2f}, 当前价 {current_price:.2f}, 差距 {price_diff_pct*100:.4f}%")

        # 清理过期的提醒记录
        self._cleanup_entry_alerts()

        return matched_suggestions

    def _cleanup_entry_alerts(self):
        """清理过期的AI入场价提醒记录"""
        current_time = datetime.now()

        with self._entry_alert_lock:
            keys_to_remove = []
            for key, alert_time in self._alerted_entries.items():
                elapsed = (current_time - alert_time).total_seconds()
                if elapsed > self.entry_alert_cooldown * 2:
                    keys_to_remove.append(key)

            for key in keys_to_remove:
                del self._alerted_entries[key]
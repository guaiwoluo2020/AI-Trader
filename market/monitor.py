#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
转折点监控模块
实时监控价格与转折点的接近程度，并通过WebSocket推送提醒
"""

from typing import Dict, List, Optional, Set
from datetime import datetime
import threading
import asyncio
import json
import os

from .store import MarketStore
from .pivot_detector import PivotDetector
from .pending_orders import PendingOrderManager


# 配置文件路径
CONFIG_FILE = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'data', 'trade_config.json')


# 交易配置
class TradeConfig:
    """交易配置"""
    _instance = None
    _lock = threading.Lock()

    def __init__(self):
        self.enabled = True  # 是否启用自动生成

        # 默认配置
        self.default_volume = 0.01  # 默认手数
        self.default_sl_offset = 0.05  # 默认止损偏移（固定点数）

        # MT5服务器时区偏移（单位：小时）
        # 正数表示MT5时间比本地时间快，负数表示比本地时间慢
        # 例如：MT5服务器时间是GMT+2，本地时间是GMT+8，则偏移为 -6
        self.mt5_timezone_offset = 0

        # 按品种配置: {symbol: {"volume": 0.01, "sl_offset": 0.05, "key_levels": "5000,5100", "key_level_threshold": 0.0008}}
        self.symbol_config = {
            "GOLD#": {"volume": 0.01, "sl_offset": 0.5},
            "OILCASH#": {"volume": 0.01, "sl_offset": 0.05},
        }

        # 启动时自动加载配置文件
        self._load_from_file()

    def _load_from_file(self):
        """从配置文件加载配置"""
        try:
            if os.path.exists(CONFIG_FILE):
                with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    self.update(data)
                print(f"[TradeConfig] 已从配置文件加载: mt5_timezone_offset={self.mt5_timezone_offset}")
            else:
                print(f"[TradeConfig] 配置文件不存在: {CONFIG_FILE}，使用默认配置")
        except Exception as e:
            print(f"[TradeConfig] 加载配置文件失败: {e}，使用默认配置")

    def save_to_file(self):
        """保存配置到文件"""
        try:
            os.makedirs(os.path.dirname(CONFIG_FILE), exist_ok=True)
            with open(CONFIG_FILE, 'w', encoding='utf-8') as f:
                json.dump(self.to_dict(), f, indent=2, ensure_ascii=False)
            print(f"[TradeConfig] 配置已保存到: {CONFIG_FILE}")
            return True
        except Exception as e:
            print(f"[TradeConfig] 保存配置文件失败: {e}")
            return False

    @classmethod
    def get_instance(cls):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    def get_symbol_config(self, symbol: str) -> Dict:
        """获取品种配置，如果未配置则返回默认值"""
        if symbol in self.symbol_config:
            config = self.symbol_config[symbol]
            return {
                "volume": config.get("volume", self.default_volume),
                "sl_offset": config.get("sl_offset", self.default_sl_offset),
                "key_levels": config.get("key_levels", ""),
                "key_level_threshold": config.get("key_level_threshold", 0.0008)
            }
        return {
            "volume": self.default_volume,
            "sl_offset": self.default_sl_offset,
            "key_levels": "",
            "key_level_threshold": 0.0008
        }

    def get_key_levels(self, symbol: str) -> List[float]:
        """
        获取品种的关键点位列表

        Args:
            symbol: 品种名称

        Returns:
            关键点位列表，如 [5000, 5100, 5200]
        """
        config = self.get_symbol_config(symbol)
        key_levels_str = config.get("key_levels", "")
        if not key_levels_str:
            return []

        levels = []
        for level_str in key_levels_str.split(","):
            level_str = level_str.strip()
            if level_str:
                try:
                    levels.append(float(level_str))
                except ValueError:
                    continue
        return sorted(levels)

    def to_dict(self) -> Dict:
        return {
            "enabled": self.enabled,
            "default_volume": self.default_volume,
            "default_sl_offset": self.default_sl_offset,
            "mt5_timezone_offset": self.mt5_timezone_offset,
            "symbol_config": self.symbol_config
        }

    def update(self, data: Dict):
        if "enabled" in data:
            self.enabled = bool(data["enabled"])
        if "default_volume" in data:
            self.default_volume = float(data["default_volume"])
        if "default_sl_offset" in data:
            self.default_sl_offset = float(data["default_sl_offset"])
        if "mt5_timezone_offset" in data:
            self.mt5_timezone_offset = float(data["mt5_timezone_offset"])
        if "symbol_config" in data:
            self.symbol_config = data["symbol_config"]


class PivotMonitor:
    """转折点监控器"""

    def __init__(self, store: MarketStore, detector: PivotDetector,
                 pending_orders: PendingOrderManager = None, llm_analyzer=None):
        self.store = store
        self.detector = detector
        self.pending_orders = pending_orders
        self.trade_config = TradeConfig.get_instance()
        self.llm_analyzer = llm_analyzer

        # WebSocket连接管理
        self._ws_clients: Set = set()
        self._ws_lock = threading.Lock()

        # 已提醒的转折点（避免重复提醒）
        # 结构: {(symbol, period, timestamp, price): datetime}
        self._alerted_pivots: Dict[tuple, datetime] = {}
        self._alert_lock = threading.Lock()

        # AI入场价提醒冷却（避免重复提醒）
        self._alerted_ai_entries: Dict[str, datetime] = {}

        # 关键点位订单冷却（避免重复生成订单）
        # 结构: {symbol_key: datetime}
        self._alerted_key_levels: Dict[str, datetime] = {}

        # 主事件循环引用（在FastAPI启动时设置）
        self._main_loop = None

        # 提醒冷却时间（秒）
        self.alert_cooldown = 300  # 5分钟内同一转折点不重复提醒

        # 关键点位订单冷却时间（秒）- 与订单超时时间一致
        self.key_level_cooldown = 180  # 3分钟

        print("[PivotMonitor] 转折点监控器已初始化")

    def set_event_loop(self, loop):
        """设置主事件循环引用"""
        self._main_loop = loop
        print(f"[PivotMonitor] 已设置主事件循环")

    def set_statistics_history(self, statistics_history):
        """设置统计数据历史引用（用于获取价差）"""
        self._statistics_history = statistics_history

    def _get_symbol_spread(self, symbol: str) -> Optional[float]:
        """
        获取指定品种的最新价差

        Args:
            symbol: 品种名称

        Returns:
            价差（金额），如果没有返回None
        """
        if not hasattr(self, '_statistics_history') or not self._statistics_history:
            return None

        symbol_normalized = symbol.replace('#', '')

        # 从最新的统计数据中查找该品种的价差
        for stat in reversed(list(self._statistics_history)):
            stat_symbol = stat.get('symbol', '')
            stat_normalized = stat_symbol.replace('#', '')
            if stat_normalized == symbol_normalized:
                spread = stat.get('spread')
                if spread is not None and spread > 0:
                    return spread

        return None

    def _calculate_take_profit(self, action: str, entry_price: float, sl: float, tp: float = None) -> Optional[float]:
        """
        计算并修正止盈价格

        规则：
        1. 止盈方向必须正确（买入止盈>入场价，卖出止盈<入场价）
        2. 风险回报比至少为1（止盈距离 >= 止损距离）
        3. 如果不满足，按照风险回报比=1重新计算

        Args:
            action: 'b' 买入 或 's' 卖出
            entry_price: 入场价格
            sl: 止损价格
            tp: 原始止盈价格（可能为None）

        Returns:
            修正后的止盈价格，如果止损设置有问题返回None
        """
        if action == 'b':
            # 买入：止损应该 < 入场价
            risk = entry_price - sl
            if risk <= 0:
                # 止损设置有问题（止损高于入场价），不生成订单
                print(f"[PivotMonitor] 警告: 买入止损{sl}高于入场价{entry_price}，跳过订单")
                return None

            # 计算最小止盈（风险回报比=1）
            min_tp = entry_price + risk

            # 如果没有止盈，或者止盈不满足条件，使用最小止盈
            if tp is None or tp <= entry_price or (tp - entry_price) < risk:
                print(f"[PivotMonitor] 修正买入止盈: 原{tp} -> 新{min_tp:.2f} (风险={risk:.2f})")
                return round(min_tp, 2)
            return round(tp, 2)

        else:  # action == 's'
            # 卖出：止损应该 > 入场价
            risk = sl - entry_price
            if risk <= 0:
                # 止损设置有问题（止损低于入场价），不生成订单
                print(f"[PivotMonitor] 警告: 卖出止损{sl}低于入场价{entry_price}，跳过订单")
                return None

            # 计算最小止盈（风险回报比=1）
            min_tp = entry_price - risk

            # 如果没有止盈，或者止盈不满足条件，使用最小止盈
            if tp is None or tp >= entry_price or (entry_price - tp) < risk:
                print(f"[PivotMonitor] 修正卖出止盈: 原{tp} -> 新{min_tp:.2f} (风险={risk:.2f})")
                return round(min_tp, 2)
            return round(tp, 2)

    def _get_auto_key_levels(self, symbol: str, current_price: float) -> List[float]:
        """
        根据品种价格位数自动计算关键点位

        规则：
        - 一位数价格：能被1整除
        - 两位数价格：能被5整除
        - 三位数价格：能被10整除
        - 四位数价格：能被100整除
        - 五位数或六位数价格：能被1000整除

        Args:
            symbol: 品种名称
            current_price: 当前价格

        Returns:
            关键点位列表（当前价格上下各3个）
        """
        if current_price <= 0:
            return []

        # 计算整数部分位数
        int_part = int(current_price)
        num_digits = len(str(int_part)) if int_part > 0 else 1

        # 根据位数确定步长
        if num_digits == 1:
            step = 1
        elif num_digits == 2:
            step = 5
        elif num_digits == 3:
            step = 10
        elif num_digits == 4:
            step = 100
        else:  # 5位数或6位数
            step = 1000

        # 计算当前价格所在的基础点位
        base_level = int(current_price / step) * step

        # 生成上下各3个关键点位
        levels = []
        for i in range(-3, 4):
            level = base_level + i * step
            if level > 0:  # 确保价格为正
                levels.append(float(level))

        return sorted(levels)

    def check_key_levels(self, symbol: str, current_price: float) -> Optional[Dict]:
        """
        检查价格是否接近关键点位，并生成交易指令

        策略逻辑：
        - 向下走接近关键点位 → 买入（支撑位）
        - 向上走接近关键点位 → 卖出（压力位）

        如果没有配置关键点位，则自动计算关键点位

        Args:
            symbol: 交易品种
            current_price: 当前价格

        Returns:
            交易指令或None
        """
        if not self.trade_config.enabled:
            return None

        # 获取关键点位配置
        key_levels = self.trade_config.get_key_levels(symbol)

        # 如果没有配置关键点位，自动计算
        if not key_levels:
            key_levels = self._get_auto_key_levels(symbol, current_price)

        if not key_levels:
            return None

        threshold = self.trade_config.get_symbol_config(symbol).get("key_level_threshold", 0.0008)

        # 找到最近的关键点位
        nearest_level = None
        min_distance = float('inf')

        for level in key_levels:
            distance_pct = abs(current_price - level) / current_price
            if distance_pct < min_distance:
                min_distance = distance_pct
                nearest_level = level

        if nearest_level is None:
            return None

        # 判断是否在阈值范围内
        distance_pct = abs(current_price - nearest_level) / current_price
        if distance_pct > threshold:
            return None

        # 检查是否已经为该关键点位生成过订单（在冷却时间内）
        current_time = datetime.now()
        key_level_key = f"{symbol}_{nearest_level}"
        if key_level_key in self._alerted_key_levels:
            last_alert = self._alerted_key_levels[key_level_key]
            elapsed = (current_time - last_alert).total_seconds()
            if elapsed < self.key_level_cooldown:
                # 还在冷却时间内，跳过
                return None

        # 记录提醒时间
        self._alerted_key_levels[key_level_key] = current_time

        # 判断走势方向：通过价格相对于关键点位的位置

        # 获取品种配置
        config = self.trade_config.get_symbol_config(symbol)
        volume = config["volume"]

        # 获取价差
        spread = self._get_symbol_spread(symbol)

        # 根据价格与关键点位的关系判断方向
        if current_price > nearest_level:
            # 价格在关键点位上方，向下接近 → 买入（支撑位）
            action = 'b'
            sl = nearest_level - (nearest_level * 0.006)  # 关键点位下方万分之六
            if spread:
                sl -= spread  # 买入止损需要更低
            # 止盈：1.5倍风险回报比
            risk = current_price - sl
            tp = current_price + risk * 1.5
            if spread:
                tp -= spread  # 买入止盈需要更低
            reason = f"关键点位策略: 价格向下接近 {nearest_level}（支撑位）"
        else:
            # 价格在关键点位下方，向上接近 → 卖出（压力位）
            action = 's'
            sl = nearest_level + (nearest_level * 0.006)  # 关键点位上方万分之六
            if spread:
                sl += spread  # 卖出止损需要更高
            # 止盈：1.5倍风险回报比
            risk = sl - current_price
            tp = current_price - risk * 1.5
            if spread:
                tp += spread  # 卖出止盈需要更高
            reason = f"关键点位策略: 价格向上接近 {nearest_level}（压力位）"

        # 验证并修正止盈
        tp = self._calculate_take_profit(action, current_price, sl, tp)
        if tp is None:
            # 止损设置有问题，不生成订单
            return None

        # 获取各周期的AI建议方向
        ai_directions = self._get_ai_directions_by_period(symbol)
        key_level_direction_text = '买入' if action == 'b' else '卖出'

        # 判断方向一致性并生成建议
        direction_analysis = self._analyze_direction_consistency(action, ai_directions)

        # 创建订单
        order = {
            "symbol": symbol,
            "action": action,
            "price": current_price,
            "mount": volume,
            "sl": round(sl, 2),
            "tp": tp,
            "reason": reason,
            "description": "Key Level Strategy",
            "source": "key_level",
            "key_level": nearest_level,
            "distance_pct": round(distance_pct * 100, 4),
            "generated_at": current_time.isoformat(),
            # 新增AI方向对比字段
            "ai_directions": ai_directions,  # 各周期AI方向
            "key_level_direction_text": key_level_direction_text,
            "direction_consistent": direction_analysis['is_consistent'],
            "consistent_periods": direction_analysis['consistent_periods'],
            "inconsistent_periods": direction_analysis['inconsistent_periods'],
            "recommendation": direction_analysis['recommendation'],
            "recommendation_color": direction_analysis['recommendation_color']
        }

        # 添加到待确认订单
        if self.pending_orders:
            order_id = self.pending_orders.add_order(order)
            order["order_id"] = order_id

            print(f"[PivotMonitor] 关键点位策略生成订单: {order_id} - {action} {symbol} @ {current_price}, 关键位={nearest_level}, SL={sl:.2f}, TP={tp:.2f}")
            print(f"[PivotMonitor] AI各周期方向: {ai_directions}, 关键点位方向: {key_level_direction_text}, 一致周期: {direction_analysis['consistent_periods']}, 建议: {direction_analysis['recommendation']}")

            # 推送关键点位订单通知到前端
            self._broadcast_key_level_order(order)

            return order

        return None

    def _get_ai_directions_by_period(self, symbol: str) -> Dict[str, Dict]:
        """
        获取AI各周期的交易建议方向

        Args:
            symbol: 交易品种

        Returns:
            {period: {'direction': 'buy'/'sell', 'text': '买入'/'卖出', 'entry_price': xxx}}
        """
        if not self.llm_analyzer:
            return {}

        result = {}
        try:
            analysis = self.llm_analyzer.get_analysis(symbol)
            if not analysis:
                return {}

            # 从交易建议中获取各周期方向
            analysis_data = analysis.get('analysis', {})
            trade_suggestions = analysis_data.get('trade_suggestions', [])

            for suggestion in trade_suggestions:
                period = suggestion.get('period', '')
                direction = suggestion.get('direction', '')
                entry_price = suggestion.get('entry_price')

                if period and direction:
                    # 标准化方向
                    direction_lower = direction.lower().strip()
                    if direction_lower in ['buy', '买入', '多头']:
                        direction_normalized = 'buy'
                        direction_text = '买入'
                    elif direction_lower in ['sell', '卖出', '空头']:
                        direction_normalized = 'sell'
                        direction_text = '卖出'
                    else:
                        continue

                    result[period] = {
                        'direction': direction_normalized,
                        'text': direction_text,
                        'entry_price': entry_price
                    }

            return result
        except Exception as e:
            print(f"[PivotMonitor] 获取AI各周期方向失败: {e}")
            return {}

    def _analyze_direction_consistency(self, key_level_action: str, ai_directions: Dict[str, Dict]) -> Dict:
        """
        分析关键点位方向与AI各周期方向的一致性

        Args:
            key_level_action: 'b' 或 's'
            ai_directions: {period: {'direction': 'buy'/'sell', ...}}

        Returns:
            {
                'is_consistent': bool,  # 是否有任一周期一致
                'consistent_periods': [],  # 一致的周期列表
                'inconsistent_periods': [],  # 不一致的周期列表
                'recommendation': str,  # 建议文本
                'recommendation_color': str  # 建议颜色
            }
        """
        if not ai_directions:
            return {
                'is_consistent': False,
                'consistent_periods': [],
                'inconsistent_periods': [],
                'recommendation': 'AI暂无建议，请谨慎操作',
                'recommendation_color': 'warning'
            }

        consistent_periods = []
        inconsistent_periods = []

        for period, dir_info in ai_directions.items():
            ai_dir = dir_info.get('direction', '')

            # b = buy, s = sell
            if (key_level_action == 'b' and ai_dir == 'buy') or \
               (key_level_action == 's' and ai_dir == 'sell'):
                consistent_periods.append(period)
            else:
                inconsistent_periods.append(period)

        # 判断整体一致性
        is_consistent = len(consistent_periods) > 0 and len(inconsistent_periods) == 0

        # 生成建议
        if len(consistent_periods) == len(ai_directions):
            # 全部一致
            recommendation = f"AI各周期方向一致，建议下单"
            recommendation_color = "success"
        elif len(consistent_periods) > 0:
            # 部分一致
            recommendation = f"AI部分周期一致({','.join(consistent_periods)})，建议谨慎"
            recommendation_color = "warning"
        else:
            # 全部不一致
            recommendation = f"AI方向不一致，建议慎重"
            recommendation_color = "error"

        return {
            'is_consistent': is_consistent,
            'consistent_periods': consistent_periods,
            'inconsistent_periods': inconsistent_periods,
            'recommendation': recommendation,
            'recommendation_color': recommendation_color
        }

    def check_ai_entry(self, symbol: str, current_price: float) -> List[Dict]:
        """
        检查价格是否接近AI建议的入场价，并生成交易指令

        Args:
            symbol: 交易品种
            current_price: 当前价格

        Returns:
            AI入场价提醒列表
        """
        if not self.llm_analyzer:
            return []

        if not self.trade_config.enabled:
            return []

        # 检查AI入场价
        ai_matches = self.llm_analyzer.check_entry_price_nearby(symbol, current_price, threshold=0.0001)

        ai_entry_alerts = []
        current_time = datetime.now()

        # 获取价差
        spread = self._get_symbol_spread(symbol)

        for match in ai_matches:
            # 生成待确认订单
            action = 'b' if match['direction'] == 'buy' else 's'

            # 检查是否已经提醒过这个AI入场价（5分钟内不重复）
            ai_key = f"{symbol}_{match['period']}_{match['entry_price']}_{match['direction']}"
            if ai_key in self._alerted_ai_entries:
                last_alert = self._alerted_ai_entries[ai_key]
                elapsed = (current_time - last_alert).total_seconds()
                if elapsed < self.alert_cooldown:
                    continue

            # 记录提醒时间
            self._alerted_ai_entries[ai_key] = current_time

            # 根据方向调整止损止盈（考虑价差）
            sl = match['stop_loss']
            tp = match['take_profit']
            if spread:
                if action == 'b':
                    # 买入：止损需要更低，止盈需要更低
                    sl -= spread
                    tp -= spread
                else:
                    # 卖出：止损需要更高，止盈需要更高
                    sl += spread
                    tp += spread

            # 验证并修正止盈
            tp = self._calculate_take_profit(action, current_price, sl, tp)
            if tp is None:
                # 止损设置有问题，跳过此订单
                continue

            order = {
                "symbol": symbol,
                "action": action,
                "price": current_price,
                "mount": self.trade_config.get_symbol_config(symbol).get("volume", 0.01),
                "sl": round(sl, 2) if sl else None,
                "tp": tp,
                "reason": f"AI建议入场: {match['reason']}",
                "description": "AI Trend Strategy",
                "source": "ai_entry_nearby",
                "ai_period": match['period'],
                "ai_entry_price": match['entry_price'],
                "ai_direction": match['direction'],
                "generated_at": current_time.isoformat()
            }

            # 添加到待确认订单
            if self.pending_orders:
                order_id = self.pending_orders.add_order(order)
                order["order_id"] = order_id

                # 构建提醒
                alert = {
                    "type": "ai_entry_alert",
                    "symbol": symbol,
                    "period": match['period'],
                    "direction": match['direction'],
                    "entry_price": match['entry_price'],
                    "current_price": current_price,
                    "price_diff_pct": match['price_diff_pct'],
                    "stop_loss": sl,
                    "take_profit": tp,
                    "reason": match['reason'],
                    "pending_order": order,
                    "timestamp": current_time.isoformat()
                }
                ai_entry_alerts.append(alert)

                print(f"[PivotMonitor] AI趋势策略生成订单: {order_id} - {action} {symbol} @ {current_price}, AI入场价={match['entry_price']}")

                # 广播AI入场价提醒
                self._broadcast_alert(alert)

        return ai_entry_alerts

    def check_and_alert(self, symbol: str, current_price: float) -> List[Dict]:
        """
        检查价格是否接近转折点，并发送提醒
        同时检测关键点位策略

        Args:
            symbol: 交易品种
            current_price: 当前价格

        Returns:
            接近的转折点列表
        """
        # 检查关键点位策略
        self.check_key_levels(symbol, current_price)

        # 检查AI趋势策略
        self.check_ai_entry(symbol, current_price)

        # 检查是否接近转折点
        near_pivots = self.detector.check_near_pivot(symbol, current_price)

        if not near_pivots:
            return []

        # 过滤已提醒过的转折点
        new_alerts = []
        current_time = datetime.now()

        with self._alert_lock:
            for pivot in near_pivots:
                key = (
                    pivot['symbol'],
                    pivot['period'],
                    pivot['timestamp'],
                    pivot['price']
                )

                # 检查是否已提醒过
                if key in self._alerted_pivots:
                    last_alert = self._alerted_pivots[key]
                    elapsed = (current_time - last_alert).total_seconds()

                    # 如果在冷却时间内，跳过
                    if elapsed < self.alert_cooldown:
                        continue

                # 记录提醒时间
                self._alerted_pivots[key] = current_time

                # 构建提醒消息
                is_breakthrough = pivot.get('is_breakthrough', False)
                alert_type = pivot.get('alert_type', '')
                period = pivot['period']

                # 根据类型生成不同的消息
                if is_breakthrough:
                    if 'high' in alert_type:
                        message = f"{pivot['symbol']} {period} 已突破高点 {pivot['price']}, 当前价格 {pivot['current_price']}"
                    else:
                        message = f"{pivot['symbol']} {period} 已突破低点 {pivot['price']}, 当前价格 {pivot['current_price']}"
                else:
                    if 'high' in alert_type:
                        message = f"{pivot['symbol']} {period} 接近高点 {pivot['price']}, 当前价格 {pivot['current_price']}, 距离 {pivot['distance_pct']}%"
                    else:
                        message = f"{pivot['symbol']} {period} 接近低点 {pivot['price']}, 当前价格 {pivot['current_price']}, 距离 {pivot['distance_pct']}%"

                alert = {
                    "type": "pivot_alert",
                    "symbol": pivot['symbol'],
                    "period": period,
                    "direction": pivot['direction'],
                    "pivot_price": pivot['price'],
                    "current_price": pivot['current_price'],
                    "distance_pct": pivot['distance_pct'],
                    "threshold_pct": pivot['threshold_pct'],
                    "timestamp": current_time.isoformat(),
                    "alert_type": alert_type,
                    "is_breakthrough": is_breakthrough,
                    "message": message
                }

                # M1和M5周期接近转折点时，自动生成交易指令
                pending_order = None
                if period in ['M1', 'M5'] and not is_breakthrough:
                    pending_order = self._auto_generate_order(pivot, current_time)

                # 如果生成了订单，加入通知中
                if pending_order:
                    alert["pending_order"] = pending_order

                new_alerts.append(alert)

                # 异步推送WebSocket消息
                self._broadcast_alert(alert)

        # 清理过期的提醒记录
        self._cleanup_alerted()

        return new_alerts

    def _auto_generate_order(self, pivot: Dict, current_time: datetime) -> Optional[Dict]:
        """
        M1周期接近转折点时，自动生成交易指令

        Args:
            pivot: 转折点信息
            current_time: 当前时间

        Returns:
            生成的订单信息，包含order_id
        """
        if not self.pending_orders:
            return None

        if not self.trade_config.enabled:
            return None

        symbol = pivot['symbol']
        current_price = pivot['current_price']
        pivot_price = pivot['price']
        direction = pivot['direction']
        alert_type = pivot['alert_type']

        # 只处理"接近"类型（near_high, near_low）
        if not alert_type.startswith('near_'):
            return None

        # 获取品种配置
        config = self.trade_config.get_symbol_config(symbol)
        volume = config["volume"]
        sl_offset = config["sl_offset"]  # 固定点数偏移

        # 获取价差
        spread = self._get_symbol_spread(symbol)

        order = None

        if alert_type == 'near_low':
            # 接近低点 → 买入
            # 止损 = 低点 - 配置的偏移
            sl = pivot_price - sl_offset
            if spread:
                sl -= spread  # 买入止损需要更低
            # 止盈 = 最近的高点
            tp = self._find_nearest_pivot_price(symbol, 'high', current_price)

            # 验证并修正止盈
            tp = self._calculate_take_profit('b', current_price, sl, tp)
            if tp is not None:
                order = {
                    "symbol": symbol,
                    "action": "b",  # 买入
                    "price": current_price,
                    "mount": volume,
                    "sl": round(sl, 2),
                    "tp": tp,
                    "reason": f"M1接近低点{pivot_price:.2f}，建议买入，止损{sl:.2f}，止盈{tp:.2f}",
                    "description": "Pivot Strategy",
                    "source": "auto_pivot_m1",
                    "pivot_price": pivot_price,
                    "generated_at": current_time.isoformat()
                }

        elif alert_type == 'near_high':
            # 接近高点 → 卖出
            # 止损 = 高点 + 配置的偏移
            sl = pivot_price + sl_offset
            if spread:
                sl += spread  # 卖出止损需要更高
            # 止盈 = 最近的低点
            tp = self._find_nearest_pivot_price(symbol, 'low', current_price)

            # 验证并修正止盈
            tp = self._calculate_take_profit('s', current_price, sl, tp)
            if tp is not None:
                order = {
                    "symbol": symbol,
                    "action": "s",  # 卖出
                    "price": current_price,
                    "mount": volume,
                    "sl": round(sl, 2),
                    "tp": tp,
                    "reason": f"M1接近高点{pivot_price:.2f}，建议卖出，止损{sl:.2f}，止盈{tp:.2f}",
                    "description": "Pivot Strategy",
                    "source": "auto_pivot_m1",
                    "pivot_price": pivot_price,
                    "generated_at": current_time.isoformat()
                }

        if order:
            order_id = self.pending_orders.add_order(order)
            print(f"[PivotMonitor] 自动生成交易指令: {order_id} - {order['action']} {symbol} @ {current_price}")
            # 返回订单信息（包含order_id）
            order["order_id"] = order_id
            return order

        return None

    def _find_nearest_pivot_price(self, symbol: str, direction: str,
                                   current_price: float) -> Optional[float]:
        """
        找到离当前价格最近的转折点价格

        Args:
            symbol: 交易品种
            direction: 'high' 或 'low'
            current_price: 当前价格

        Returns:
            最近的转折点价格，如果没有返回None
        """
        nearest_price = None
        min_distance = float('inf')

        with self.detector._lock:
            for period in self.detector._pivots[symbol]:
                pivots = self.detector._pivots[symbol][period]

                for pivot in pivots:
                    if pivot.direction != direction:
                        continue

                    # 对于高点，只考虑价格高于当前价的
                    # 对于低点，只考虑价格低于当前价的
                    if direction == 'high' and pivot.price <= current_price:
                        continue
                    if direction == 'low' and pivot.price >= current_price:
                        continue

                    distance = abs(pivot.price - current_price)
                    if distance < min_distance:
                        min_distance = distance
                        nearest_price = pivot.price

        return nearest_price

    def _broadcast_new_order(self, order_id: str, order: Dict) -> None:
        """广播新订单通知"""
        message = json.dumps({
            "type": "new_order",
            "order_id": order_id,
            "order": order
        })

        with self._ws_lock:
            clients = list(self._ws_clients)

        for client in clients:
            try:
                asyncio.create_task(self._send_to_client(client, message))
            except Exception as e:
                print(f"[PivotMonitor] 发送新订单通知失败: {e}")

    def _cleanup_alerted(self):
        """清理过期的提醒记录"""
        current_time = datetime.now()

        with self._alert_lock:
            keys_to_remove = []
            for key, alert_time in self._alerted_pivots.items():
                elapsed = (current_time - alert_time).total_seconds()
                if elapsed > self.alert_cooldown * 2:
                    keys_to_remove.append(key)

            for key in keys_to_remove:
                del self._alerted_pivots[key]

    def _broadcast_alert(self, alert: Dict):
        """广播提醒到所有WebSocket客户端"""
        message = json.dumps(alert)

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
                    print(f"[PivotMonitor] 发送WebSocket消息失败: {e}")
        else:
            # 如果事件循环未就绪，尝试直接创建任务
            try:
                for client in clients:
                    asyncio.create_task(self._send_to_client(client, message))
            except Exception as e:
                print(f"[PivotMonitor] 广播消息失败: {e}")

    def _broadcast_key_level_order(self, order: Dict):
        """广播关键点位订单通知到前端"""
        action_text = '买入' if order['action'] == 'b' else '卖出'
        alert = {
            "type": "key_level_alert",
            "symbol": order['symbol'],
            "action": order['action'],
            "action_text": action_text,
            "price": order['price'],
            "sl": order['sl'],
            "tp": order['tp'],
            "key_level": order['key_level'],
            "distance_pct": order['distance_pct'],
            "reason": order['reason'],
            "pending_order": order,
            "message": f"{order['symbol']} 关键点位策略: {action_text} @ {order['price']}, 关键位={order['key_level']}"
        }
        self._broadcast_alert(alert)

    async def _send_to_client(self, client, message: str):
        """发送消息到客户端"""
        try:
            await client.send_text(message)
        except Exception as e:
            print(f"[PivotMonitor] 发送消息到客户端失败: {e}")
            # 移除失效的客户端
            with self._ws_lock:
                self._ws_clients.discard(client)

    def add_ws_client(self, client):
        """添加WebSocket客户端"""
        with self._ws_lock:
            self._ws_clients.add(client)
            print(f"[PivotMonitor] WebSocket客户端已连接, 当前连接数: {len(self._ws_clients)}")

    def remove_ws_client(self, client):
        """移除WebSocket客户端"""
        with self._ws_lock:
            self._ws_clients.discard(client)
            print(f"[PivotMonitor] WebSocket客户端已断开, 当前连接数: {len(self._ws_clients)}")

    def get_ws_client_count(self) -> int:
        """获取WebSocket客户端数量"""
        with self._ws_lock:
            return len(self._ws_clients)

    def clear_symbol(self, symbol: str):
        """清除某个Symbol的提醒记录"""
        with self._alert_lock:
            keys_to_remove = [k for k in self._alerted_pivots if k[0] == symbol]
            for key in keys_to_remove:
                del self._alerted_pivots[key]

    def get_status(self) -> Dict:
        """获取监控状态"""
        with self._alert_lock:
            alerted_count = len(self._alerted_pivots)

        return {
            "ws_clients": self.get_ws_client_count(),
            "alerted_pivots": alerted_count,
            "alert_cooldown": self.alert_cooldown
        }
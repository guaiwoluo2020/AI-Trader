#!/usr/bin/env python3
"""Lightweight factor research, Optuna search, and signal backtesting."""

from __future__ import annotations

import inspect
import json
import math
import threading
import time
import uuid
from dataclasses import dataclass
from zoneinfo import ZoneInfo
from typing import Callable, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from backtest_data import BacktestDatasetRepository
from backtest_engine import HistoricalBarReader, PERIOD_SECONDS
from sqlite_storage import SQLiteStorage, get_storage


ALPHA_PERIOD_SECONDS = dict(PERIOD_SECONDS)


class AlphaResearchCanceled(RuntimeError):
    pass


class FactorCatalog:
    """Expose the pandas-ta catalog behind a stable application boundary."""

    # These are composition helpers that require another indicator as input,
    # rather than factors computable directly from OHLCV data.
    COMPOSITION_HELPERS = {"ma", "long_run", "short_run", "tsignals", "xsignals"}
    CATEGORY_META = {
        "cycles": ("周期特征", "周期", "识别价格序列中的周期与相位变化"),
        "statistics": ("统计特征", "统计", "描述价格或收益序列的分布与偏离程度"),
        "momentum": ("动量", "动量", "衡量价格运动速度、强度和持续性"),
        "trend": ("趋势", "趋势", "识别趋势方向、强度和市场状态"),
        "volatility": ("波动率", "波动", "衡量价格波动范围和市场活跃程度"),
        "candles": ("K线形态", "形态", "从单根或多根 K 线结构识别局部形态"),
        "performance": ("收益表现", "收益", "计算收益率、回撤等表现序列"),
        "overlap": ("价格平滑", "趋势", "对价格进行平滑、均线或通道变换"),
        "volume": ("量价关系", "量价", "结合成交量判断价格运动是否得到确认"),
    }
    CHINESE_NAMES = {
        "ema": "指数移动平均", "sma": "简单移动平均", "rsi": "相对强弱指标",
        "macd": "指数平滑异同均线", "adx": "平均趋向指标", "atr": "平均真实波幅",
        "natr": "标准化真实波幅", "bbands": "布林带", "donchian": "唐奇安通道",
        "cci": "顺势指标", "roc": "变动率", "mom": "动量指标", "stoch": "随机指标",
        "obv": "能量潮", "mfi": "资金流量指标", "cmf": "蔡金资金流量",
        "vwap": "成交量加权均价", "zscore": "标准分数", "entropy": "信息熵",
        "supertrend": "超级趋势", "aroon": "阿隆指标", "psar": "抛物线转向",
    }
    NATIVE_FACTORS = {
        "previous_day_same_slot_return": {
            "display_name": "昨日同期收益率",
            "description": "上一个交易日同一时段 K 线的涨跌幅",
        },
        "previous_day_same_slot_range": {
            "display_name": "昨日同期振幅",
            "description": "上一个交易日同一时段 K 线的高低振幅",
        },
        "price_vs_previous_day_same_slot": {
            "display_name": "相对昨日同期价格偏离",
            "description": "当前收盘价相对昨日同期收盘价的偏离",
        },
        "same_slot_mean_return": {
            "display_name": "多日同期平均收益",
            "description": "过去 N 个交易日同一时段的平均涨跌幅",
        },
        "same_slot_win_rate": {
            "display_name": "多日同期上涨比例",
            "description": "过去 N 个交易日同一时段上涨的比例",
        },
    }

    @staticmethod
    def _library():
        try:
            import pandas_ta_classic as ta
        except ImportError as exc:
            raise RuntimeError("缺少 pandas-ta-classic，请先安装项目依赖") from exc
        return ta

    def list(self) -> List[Dict]:
        ta = self._library()
        factors = []
        for category, names in ta.Category.items():
            for name in names:
                if name in self.COMPOSITION_HELPERS:
                    continue
                function = getattr(ta, name, None)
                if not callable(function):
                    continue
                signature = inspect.signature(function)
                category_label, theme_label, description = self.CATEGORY_META.get(
                    category, (category, category, "技术分析因子")
                )
                inputs = [
                    item for item in signature.parameters
                    if item in {"open", "open_", "high", "low", "close", "volume"}
                ]
                factors.append({
                    "name": name,
                    "label": name.upper(),
                    "display_name": self.CHINESE_NAMES.get(name, name.upper()),
                    "category": category,
                    "category_label": category_label,
                    "research_theme": theme_label,
                    "description": description,
                    "inputs": inputs,
                    "supports_length": "length" in signature.parameters,
                    "parameters": list(signature.parameters),
                })
        factors.extend({
            "name": name,
            "label": name.upper(),
            "display_name": item["display_name"],
            "category": "time_session",
            "category_label": "时段效应",
            "research_theme": "时段",
            "description": item["description"],
            "inputs": ["time", "open", "high", "low", "close", "volume"],
            "supports_length": name in {"same_slot_mean_return", "same_slot_win_rate"},
            "parameters": ["length"] if name in {"same_slot_mean_return", "same_slot_win_rate"} else [],
            "is_native": True,
        } for name, item in self.NATIVE_FACTORS.items())
        return sorted(factors, key=lambda item: (item["category"], item["name"]))

    def calculate(
        self, frame: pd.DataFrame, name: str, length: int,
        time_zone: str = "Asia/Shanghai",
    ) -> pd.Series:
        if name in self.NATIVE_FACTORS:
            return self._calculate_time_session_factor(frame, name, length, time_zone)
        ta = self._library()
        function = getattr(ta, name, None)
        if not callable(function) or not any(
            name in values for values in ta.Category.values()
        ):
            raise ValueError(f"不支持的 pandas-ta 因子: {name}")

        signature = inspect.signature(function)
        working = frame.copy()
        working.index = pd.to_datetime(working["time"], unit="s", utc=True)
        available = {
            "open_": working["open"],
            "open": working["open"],
            "high": working["high"],
            "low": working["low"],
            "close": working["close"],
            "volume": working["tick_volume"],
        }
        kwargs = {}
        required_missing = []
        for parameter_name, parameter in signature.parameters.items():
            if parameter_name in available:
                kwargs[parameter_name] = available[parameter_name]
            elif parameter_name == "length":
                kwargs[parameter_name] = int(length)
            elif parameter.default is inspect.Parameter.empty and parameter.kind not in {
                inspect.Parameter.VAR_POSITIONAL,
                inspect.Parameter.VAR_KEYWORD,
            }:
                required_missing.append(parameter_name)
        if required_missing:
            raise ValueError(
                f"因子 {name} 需要额外参数: {', '.join(required_missing)}"
            )
        result = function(**kwargs)
        if isinstance(result, tuple):
            result = next((item for item in result if isinstance(item, (pd.Series, pd.DataFrame))), None)
        if isinstance(result, pd.DataFrame):
            numeric = result.select_dtypes(include=[np.number])
            if numeric.empty:
                raise ValueError(f"因子 {name} 没有数值输出")
            result = numeric.iloc[:, 0]
        if result is None:
            raise ValueError(f"因子 {name} 没有可用输出")
        values = np.asarray(result)
        if values.ndim != 1 or len(values) != len(frame):
            raise ValueError(f"因子 {name} 输出长度与行情不一致")
        return pd.Series(values, index=frame.index).pipe(
            pd.to_numeric, errors="coerce"
        ).replace([np.inf, -np.inf], np.nan)

    @staticmethod
    def _calculate_time_session_factor(
        frame: pd.DataFrame, name: str, length: int, time_zone: str,
    ) -> pd.Series:
        """Build causal same-time-of-day features from the uploaded bar timestamps."""
        try:
            zone = ZoneInfo(time_zone)
        except Exception as exc:
            raise ValueError("研究时区无效") from exc
        local_time = pd.to_datetime(frame["time"], unit="s", utc=True).dt.tz_convert(zone)
        working = pd.DataFrame(index=frame.index)
        working["date"] = local_time.dt.date
        working["slot"] = local_time.dt.hour * 60 + local_time.dt.minute
        working["close"] = pd.to_numeric(frame["close"], errors="coerce")
        working["bar_return"] = working["close"].div(
            pd.to_numeric(frame["open"], errors="coerce")
        ).sub(1)
        working["bar_range"] = pd.to_numeric(frame["high"], errors="coerce").sub(
            pd.to_numeric(frame["low"], errors="coerce")
        ).div(working["close"])

        prior_close = working.groupby("slot", sort=False)["close"].shift(1)
        if name == "previous_day_same_slot_return":
            values = working.groupby("slot", sort=False)["bar_return"].shift(1)
        elif name == "previous_day_same_slot_range":
            values = working.groupby("slot", sort=False)["bar_range"].shift(1)
        elif name == "price_vs_previous_day_same_slot":
            values = working["close"].div(prior_close).sub(1)
        elif name == "same_slot_mean_return":
            values = working.groupby("slot", sort=False)["bar_return"].transform(
                lambda series: series.shift(1).rolling(max(2, int(length)), min_periods=2).mean()
            )
        else:
            values = working.groupby("slot", sort=False)["bar_return"].transform(
                lambda series: series.shift(1).gt(0).rolling(max(2, int(length)), min_periods=2).mean()
            )
        return pd.Series(values, index=frame.index).replace([np.inf, -np.inf], np.nan)


class AlphaResearchRepository:
    def __init__(self, storage: Optional[SQLiteStorage] = None):
        self.storage = storage or get_storage()

    def create(self, user_id: int, config: Dict) -> Dict:
        run_id = uuid.uuid4().hex[:12]
        now = int(time.time())
        self.storage.execute(
            """
            INSERT INTO alpha_research_runs(
                run_id, user_id, dataset_id, research_name, config_json, created_at
            ) VALUES(?, ?, ?, ?, ?, ?)
            """,
            (
                run_id, user_id, config["dataset_id"], config["research_name"],
                json.dumps(config, ensure_ascii=False, separators=(",", ":")), now,
            ),
        )
        return self.get(user_id, run_id)

    def list_for_user(self, user_id: int) -> List[Dict]:
        rows = self.storage.fetchall(
            """
            SELECT r.*, d.dataset_name, d.symbol
            FROM alpha_research_runs r
            LEFT JOIN backtest_datasets d ON d.dataset_id = r.dataset_id
            WHERE r.user_id = ? ORDER BY r.created_at DESC
            """,
            (user_id,),
        )
        return [self._row(row) for row in rows]

    def get(self, user_id: int, run_id: str) -> Optional[Dict]:
        row = self.storage.fetchone(
            """
            SELECT r.*, d.dataset_name, d.symbol
            FROM alpha_research_runs r
            LEFT JOIN backtest_datasets d ON d.dataset_id = r.dataset_id
            WHERE r.user_id = ? AND r.run_id = ?
            """,
            (user_id, run_id),
        )
        if row is None:
            return None
        data = self._row(row)
        data["trials"] = self.list_trials(run_id, limit=20)
        data["iterations"] = self.list_iterations(run_id)
        data["signals"] = self.list_signals(run_id, limit=500)
        return data

    def claim_next(self) -> Optional[Dict]:
        with self.storage._lock, self.storage._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT * FROM alpha_research_runs WHERE status = 'queued' "
                "ORDER BY created_at LIMIT 1"
            ).fetchone()
            if row is None:
                conn.commit()
                return None
            now = int(time.time())
            conn.execute(
                "UPDATE alpha_research_runs SET status = 'running', started_at = ? "
                "WHERE run_id = ? AND status = 'queued'",
                (now, row["run_id"]),
            )
            conn.commit()
            data = dict(row)
            data["status"] = "running"
            data["started_at"] = now
            data["config"] = json.loads(data.pop("config_json"))
            return data

    def save_trial(
        self, run_id: str, number: int, status: str, score: Optional[float],
        params: Dict, metrics: Dict, duration_ms: int, error: str = "",
        iteration_number: int = 1,
    ) -> None:
        self.storage.execute(
            """
            INSERT OR REPLACE INTO alpha_research_trials(
                run_id, trial_number, iteration_number, status, score, params_json, metrics_json,
                duration_ms, error_message, created_at
            ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                run_id, number, iteration_number, status, score,
                json.dumps(params, ensure_ascii=False, separators=(",", ":")),
                json.dumps(metrics, ensure_ascii=False, separators=(",", ":")),
                duration_ms, error[:500], int(time.time()),
            ),
        )

    def save_iteration(
        self, run_id: str, iteration_number: int, status: str,
        candidate: Dict, expression: str = "", best_params: Optional[Dict] = None,
        metrics: Optional[Dict] = None, feedback_prompt: str = "",
        feedback_response: Optional[Dict] = None, llm_model: str = "",
        error: str = "", completed: bool = False,
    ) -> None:
        now = int(time.time())
        self.storage.execute(
            """
            INSERT INTO alpha_research_iterations(
                run_id, iteration_number, status, candidate_json, expression_text,
                best_params_json, metrics_json, feedback_prompt,
                feedback_response_json, llm_model, error_message, started_at,
                completed_at
            ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(run_id, iteration_number) DO UPDATE SET
                status = excluded.status,
                candidate_json = excluded.candidate_json,
                expression_text = excluded.expression_text,
                best_params_json = excluded.best_params_json,
                metrics_json = excluded.metrics_json,
                feedback_prompt = excluded.feedback_prompt,
                feedback_response_json = excluded.feedback_response_json,
                llm_model = excluded.llm_model,
                error_message = excluded.error_message,
                completed_at = excluded.completed_at
            """,
            (
                run_id, iteration_number, status,
                json.dumps(candidate, ensure_ascii=False, separators=(",", ":")),
                expression,
                json.dumps(best_params or {}, ensure_ascii=False, separators=(",", ":")),
                json.dumps(metrics or {}, ensure_ascii=False, separators=(",", ":")),
                feedback_prompt,
                json.dumps(feedback_response or {}, ensure_ascii=False, separators=(",", ":")),
                llm_model, error[:500], now, now if completed else None,
            ),
        )

    def update_progress(self, run_id: str, progress: float) -> None:
        self.storage.execute(
            "UPDATE alpha_research_runs SET progress = ? WHERE run_id = ?",
            (round(max(0, min(100, progress)), 1), run_id),
        )

    def complete(
        self, run_id: str, best_params: Dict, result: Dict,
        signals: List[Dict],
    ) -> None:
        now = int(time.time())
        with self.storage._lock, self.storage._connect() as conn:
            conn.execute("PRAGMA foreign_keys=ON")
            conn.execute("BEGIN IMMEDIATE")
            conn.execute(
                """
                UPDATE alpha_research_runs
                SET status = 'completed', progress = 100, best_params_json = ?,
                    result_json = ?, completed_at = ?, error_message = ''
                WHERE run_id = ?
                """,
                (
                    json.dumps(best_params, ensure_ascii=False, separators=(",", ":")),
                    json.dumps(result, ensure_ascii=False, separators=(",", ":")),
                    now, run_id,
                ),
            )
            conn.execute("DELETE FROM alpha_research_signals WHERE run_id = ?", (run_id,))
            conn.executemany(
                "INSERT INTO alpha_research_signals(run_id, bar_time, direction, alpha_value, close_price) "
                "VALUES(?, ?, ?, ?, ?)",
                [
                    (run_id, item["time"], item["direction"], item["alpha"], item["close"])
                    for item in signals
                ],
            )
            conn.commit()

    def fail(self, run_id: str, message: str, canceled: bool = False) -> None:
        self.storage.execute(
            """
            UPDATE alpha_research_runs
            SET status = ?, error_message = ?, completed_at = ?
            WHERE run_id = ?
            """,
            ("canceled" if canceled else "failed", message[:500], int(time.time()), run_id),
        )

    def request_cancel(self, user_id: int, run_id: str) -> bool:
        row = self.storage.fetchone(
            "SELECT status FROM alpha_research_runs WHERE user_id = ? AND run_id = ?",
            (user_id, run_id),
        )
        if row is None or row["status"] not in {"queued", "running"}:
            return False
        self.storage.execute(
            "UPDATE alpha_research_runs SET cancel_requested = 1 WHERE run_id = ?",
            (run_id,),
        )
        return True

    def is_cancel_requested(self, run_id: str) -> bool:
        row = self.storage.fetchone(
            "SELECT cancel_requested FROM alpha_research_runs WHERE run_id = ?",
            (run_id,),
        )
        return bool(row and row["cancel_requested"])

    def recover_stale(self) -> None:
        self.storage.execute(
            "UPDATE alpha_research_runs SET status = 'queued', started_at = NULL "
            "WHERE status = 'running'"
        )

    def list_trials(self, run_id: str, limit: int = 20) -> List[Dict]:
        rows = self.storage.fetchall(
            "SELECT * FROM alpha_research_trials WHERE run_id = ? "
            "ORDER BY score DESC, trial_number LIMIT ?",
            (run_id, limit),
        )
        return [self._trial_row(row) for row in rows]

    def list_iterations(self, run_id: str) -> List[Dict]:
        rows = self.storage.fetchall(
            "SELECT * FROM alpha_research_iterations WHERE run_id = ? "
            "ORDER BY iteration_number",
            (run_id,),
        )
        return [self._iteration_row(row) for row in rows]

    def list_signals(self, run_id: str, limit: int = 500) -> List[Dict]:
        rows = self.storage.fetchall(
            "SELECT bar_time AS time, direction, alpha_value AS alpha, close_price AS close "
            "FROM alpha_research_signals WHERE run_id = ? ORDER BY bar_time DESC LIMIT ?",
            (run_id, limit),
        )
        return [dict(row) for row in reversed(rows)]

    @staticmethod
    def _row(row) -> Dict:
        data = dict(row)
        data["config"] = json.loads(data.pop("config_json") or "{}")
        data["best_params"] = json.loads(data.pop("best_params_json") or "{}")
        data["result"] = json.loads(data.pop("result_json") or "{}")
        data["cancel_requested"] = bool(data["cancel_requested"])
        return data

    @staticmethod
    def _trial_row(row) -> Dict:
        data = dict(row)
        data["params"] = json.loads(data.pop("params_json") or "{}")
        data["metrics"] = json.loads(data.pop("metrics_json") or "{}")
        return data

    @staticmethod
    def _iteration_row(row) -> Dict:
        data = dict(row)
        data["candidate"] = json.loads(data.pop("candidate_json") or "{}")
        data["best_params"] = json.loads(data.pop("best_params_json") or "{}")
        data["metrics"] = json.loads(data.pop("metrics_json") or "{}")
        data["feedback_response"] = json.loads(
            data.pop("feedback_response_json") or "{}"
        )
        return data


class AlphaLibraryRepository:
    """Versioned, validated Alpha definitions available to strategy signals."""

    def __init__(self, storage: Optional[SQLiteStorage] = None):
        self.storage = storage or get_storage()

    def list_visible(self, user_id: int) -> List[Dict]:
        rows = self.storage.fetchall(
            """
            SELECT a.*, u.username AS owner_username
            FROM alpha_library a
            JOIN users u ON u.id = a.user_id
            WHERE a.user_id = ? OR a.visibility = 'shared'
            ORDER BY a.updated_at DESC
            """,
            (user_id,),
        )
        return [self._row(row, user_id) for row in rows]

    def get_visible(self, user_id: int, alpha_id: str) -> Optional[Dict]:
        row = self.storage.fetchone(
            """
            SELECT a.*, u.username AS owner_username
            FROM alpha_library a
            JOIN users u ON u.id = a.user_id
            WHERE a.alpha_id = ? AND (a.user_id = ? OR a.visibility = 'shared')
            """,
            (alpha_id, user_id),
        )
        return self._row(row, user_id) if row else None

    def get_runtime_definition(
        self, alpha_id: str, owner_user_id: int = 0,
    ) -> Optional[Dict]:
        row = self.storage.fetchone(
            """
            SELECT a.*, u.username AS owner_username
            FROM alpha_library a
            JOIN users u ON u.id = a.user_id
            WHERE a.alpha_id = ? AND a.status = 'validated'
              AND (a.visibility = 'shared' OR a.user_id = ?)
            """,
            (str(alpha_id), int(owner_user_id or 0)),
        )
        return self._row(row, int(owner_user_id or 0)) if row else None

    def publish_run(
        self, user_id: int, run: Dict, visibility: str = "private",
    ) -> Dict:
        if run.get("status") != "completed":
            raise ValueError("只有已完成的 Alpha 研究才能进入因子库")
        result = run.get("result") or {}
        definition = result.get("runtime_definition") or {}
        if not definition.get("factors") or not definition.get("params"):
            raise ValueError("研究结果缺少可执行 Alpha 定义")
        admission = self.admission_report(result)
        if not admission["passed"]:
            failed = "、".join(
                item["label"] for item in admission["checks"] if not item["passed"]
            )
            raise ValueError(f"Alpha 尚未通过准入检查: {failed}")
        visibility = "shared" if visibility == "shared" else "private"
        existing = self.storage.fetchone(
            "SELECT alpha_id FROM alpha_library WHERE source_run_id = ?",
            (run["run_id"],),
        )
        if existing:
            return self.get_visible(user_id, existing["alpha_id"])
        alpha_id = uuid.uuid4().hex[:12]
        now = int(time.time())
        metrics = {
            "admission": admission,
            "metrics": result.get("metrics") or {},
            "splits": result.get("splits") or {},
            "factor_diagnostics": result.get("factor_diagnostics") or [],
            "parameter_robustness": result.get("parameter_robustness") or [],
            "subperiod_robustness": result.get("subperiod_robustness") or [],
            "library_correlations": result.get("library_correlations") or [],
            "independent_evaluation": result.get("independent_evaluation") or {},
            "residual_evaluation": result.get("residual_evaluation") or {},
            "ablation_experiment": result.get("ablation_experiment") or {},
            "experiment_cost": result.get("experiment_cost") or {},
        }
        self.storage.execute(
            """
            INSERT INTO alpha_library(
                alpha_id, user_id, source_run_id, name, version, status,
                visibility, timeframe, definition_json, metrics_json,
                created_at, updated_at
            ) VALUES(?, ?, ?, ?, 1, 'validated', ?, ?, ?, ?, ?, ?)
            """,
            (
                alpha_id, user_id, run["run_id"], run["research_name"],
                visibility, definition["timeframe"],
                json.dumps(definition, ensure_ascii=False, separators=(",", ":")),
                json.dumps(metrics, ensure_ascii=False, separators=(",", ":")),
                now, now,
            ),
        )
        return self.get_visible(user_id, alpha_id)

    def retire(self, user_id: int, alpha_id: str) -> bool:
        exists = self.storage.fetchone(
            "SELECT 1 FROM alpha_library WHERE alpha_id = ? AND user_id = ?",
            (alpha_id, user_id),
        )
        if not exists:
            return False
        self.storage.execute(
            "UPDATE alpha_library SET status = 'retired', updated_at = ? "
            "WHERE alpha_id = ? AND user_id = ?",
            (int(time.time()), alpha_id, user_id),
        )
        return True

    def copy(self, user_id: int, alpha_id: str) -> Optional[Dict]:
        source = self.get_visible(user_id, alpha_id)
        if source is None:
            return None
        new_id = uuid.uuid4().hex[:12]
        now = int(time.time())
        self.storage.execute(
            """
            INSERT INTO alpha_library(
                alpha_id, user_id, source_run_id, name, version, status,
                visibility, timeframe, definition_json, metrics_json,
                created_at, updated_at
            ) VALUES(?, ?, ?, ?, 1, 'validated', 'private', ?, ?, ?, ?, ?)
            """,
            (
                new_id, int(user_id),
                f"{source.get('source_run_id', '')}:copy:{new_id}",
                f"{source.get('name') or 'Alpha'} 副本",
                source.get("timeframe") or (source.get("definition") or {}).get("timeframe", ""),
                json.dumps(source.get("definition") or {}, ensure_ascii=False, separators=(",", ":")),
                json.dumps(source.get("metrics") or {}, ensure_ascii=False, separators=(",", ":")),
                now, now,
            ),
        )
        return self.get_visible(user_id, new_id)

    @staticmethod
    def admission_report(result: Dict) -> Dict:
        metrics = result.get("metrics") or {}
        hidden = (result.get("splits") or {}).get("hidden_test") or {}
        quintile = metrics.get("quintile_analysis") or {}
        subperiods = result.get("subperiod_robustness") or []
        positive_subperiod_ratio = (
            sum(float(item.get("rank_ic", 0)) > 0 for item in subperiods)
            / len(subperiods) if subperiods else 1.0
        )
        correlations = result.get("library_correlations") or []
        max_library_correlation = max(
            (abs(float(item.get("correlation", 0))) for item in correlations),
            default=0.0,
        )
        ablation = result.get("ablation_experiment") or {}
        # Alpha discovery should not require every diagnostic to be perfect.
        # Four core checks protect against unusable signals; the remaining
        # diagnostics measure maturity and need a majority to pass.
        values = [
            ("coverage", "因子有效覆盖率", float(metrics.get("factor_coverage", 0)), 0.65, ">=", True),
            ("rolling_samples", "滚动 IC 样本", float(metrics.get("rolling_ic_count", 0)), 2, ">=", True),
            ("rank_ic", "Rank IC", float(metrics.get("rank_ic", 0)), 0.005, ">=", True),
            ("positive_ic", "正 Rank IC 比例", float(metrics.get("positive_rank_ic_ratio", 0)), 0.45, ">=", False),
            ("monotonicity", "五分组单调性", float(quintile.get("monotonicity", 0)), 0.35, ">=", False),
            ("spread", "最高组与最低组收益差", float(quintile.get("top_bottom_spread", 0)), 0, ">", True),
            ("hidden_rank_ic", "隐藏测试 Rank IC", float(hidden.get("rank_ic", 0)), -0.005, ">=", False),
            ("subperiods", "分时段正 Rank IC 比例", positive_subperiod_ratio, 0.5, ">=", False),
            ("orthogonality", "与已有 Alpha 最大相关", max_library_correlation, 0.95, "<=", False),
            ("ablation", "消融有效因子比例", float(ablation.get("useful_factor_ratio", 0)), 0.25, ">=", False),
        ]
        checks = [{
            "key": key, "label": label, "value": round(value, 8),
            "threshold": threshold, "operator": operator, "required": required,
            "passed": (
                value >= threshold if operator == ">="
                else value <= threshold if operator == "<="
                else value > threshold
            ),
        } for key, label, value, threshold, operator, required in values]
        required_checks = [item for item in checks if item["required"]]
        optional_checks = [item for item in checks if not item["required"]]
        required_passed = all(item["passed"] for item in required_checks)
        optional_passed_count = sum(item["passed"] for item in optional_checks)
        minimum_optional_passes = 3
        return {
            "passed": required_passed and optional_passed_count >= minimum_optional_passes,
            "checks": checks,
            "required_passed": required_passed,
            "passed_count": sum(item["passed"] for item in checks),
            "required_count": len(required_checks),
            "optional_passed_count": optional_passed_count,
            "minimum_optional_passes": minimum_optional_passes,
        }

    @staticmethod
    def _row(row, viewer_user_id: int) -> Dict:
        data = dict(row)
        data["definition"] = json.loads(data.pop("definition_json") or "{}")
        data["metrics"] = json.loads(data.pop("metrics_json") or "{}")
        data["is_owner"] = int(data["user_id"]) == int(viewer_user_id)
        return data


@dataclass
class BacktestResult:
    score: float
    metrics: Dict
    signals: List[Dict]


class AlphaBacktestEngine:
    def __init__(self, catalog: Optional[FactorCatalog] = None):
        self.catalog = catalog or FactorCatalog()

    @staticmethod
    def build_frame(bars: List[Dict], timeframe: str) -> pd.DataFrame:
        if timeframe not in ALPHA_PERIOD_SECONDS:
            raise ValueError(f"不支持的回测周期: {timeframe}")
        frame = pd.DataFrame(bars)
        if frame.empty:
            raise ValueError("历史数据集没有行情")
        frame = frame.sort_values("time").drop_duplicates("time")
        if timeframe != "M1":
            seconds = ALPHA_PERIOD_SECONDS[timeframe]
            frame["bucket"] = frame["time"].floordiv(seconds).mul(seconds)
            frame = frame.groupby("bucket", as_index=False).agg({
                "open": "first", "high": "max", "low": "min", "close": "last",
                "tick_volume": "sum", "spread": "last",
            }).rename(columns={"bucket": "time"})
        return frame.reset_index(drop=True)

    def calculate_alpha(self, frame: pd.DataFrame, config: Dict, params: Dict) -> pd.Series:
        components = self.calculate_factor_components(frame, config, params)
        if not components:
            raise ValueError("至少需要选择一个因子")
        alpha = sum(item["values"] * item["weight"] for item in components)
        weight_total = sum(abs(item["weight"]) for item in components)
        return alpha / max(weight_total, 1e-9)

    def calculate_factor_components(
        self, frame: pd.DataFrame, config: Dict, params: Dict,
    ) -> List[Dict]:
        """Return the exact causal factor series used by the composite Alpha."""
        components = []
        for index, factor in enumerate(config["factors"]):
            length = int(params.get(
                f"factor_{index}_length", factor["length_min"]
            ))
            weight = float(params.get(
                f"factor_{index}_weight", factor.get("weight_min", 1)
            ))
            raw = self.catalog.calculate(
                frame, factor["name"], length,
                config.get("time_zone", "Asia/Shanghai"),
            )
            components.append({
                "index": index,
                "name": factor["name"],
                "length": length,
                "weight": weight,
                "values": self._preprocess_factor(raw, length),
            })
        return components

    @staticmethod
    def _preprocess_factor(values: pd.Series, length: int) -> pd.Series:
        """Causal winsorization and standardization fitted on prior observations."""
        window = max(20, min(200, int(length) * 5))
        min_periods = max(5, window // 4)
        history = values.shift(1)
        lower = history.rolling(window, min_periods=min_periods).quantile(0.01)
        upper = history.rolling(window, min_periods=min_periods).quantile(0.99)
        winsorized = values.clip(lower=lower, upper=upper)
        mean = history.rolling(window, min_periods=min_periods).mean()
        std = history.rolling(window, min_periods=min_periods).std().replace(0, np.nan)
        return ((winsorized - mean) / std).clip(-5, 5)

    def run(
        self, frame: pd.DataFrame, config: Dict, params: Dict,
        evaluation_start: int = 0,
        alpha_override: Optional[pd.Series] = None,
    ) -> BacktestResult:
        alpha = (
            pd.Series(alpha_override).reset_index(drop=True)
            if alpha_override is not None
            else self.calculate_alpha(frame, config, params)
        )
        if len(alpha) != len(frame):
            raise ValueError("预计算 Alpha 长度与行情数据不一致")
        buy_threshold = float(params.get("buy_threshold", config["buy_threshold_min"]))
        sell_threshold = float(params.get("sell_threshold", config["sell_threshold_max"]))
        signals = pd.Series(0, index=frame.index, dtype="int8")
        signals[alpha >= buy_threshold] = 1
        signals[alpha <= sell_threshold] = -1
        confirmation = int(config.get("confirmation_bars", 1))
        if confirmation > 1:
            same = signals.eq(signals.shift(1))
            confirmed = same.rolling(confirmation - 1, min_periods=confirmation - 1).sum()
            signals = signals.where((signals == 0) | (confirmed >= confirmation - 1), 0)
        cooldown = int(config.get("cooldown_bars", 0))
        if cooldown:
            signals = self._apply_cooldown(signals, cooldown)

        horizon = int(config["prediction_horizon"])
        decay_horizons = sorted({1, 3, 5, 10, 20, horizon})
        future_returns = {
            item: frame["close"].shift(-item).div(frame["close"]).sub(1)
            for item in decay_horizons
        }
        evaluation_start = max(0, min(int(evaluation_start), len(frame)))
        evaluation_slice = slice(evaluation_start, None)
        metrics = self._metrics(
            alpha.iloc[evaluation_slice],
            signals.iloc[evaluation_slice],
            future_returns[horizon].iloc[evaluation_slice],
            {
                item: values.iloc[evaluation_slice]
                for item, values in future_returns.items()
            },
        )
        metrics["score"] = self._score(metrics)
        signal_rows = [
            {
                "time": int(frame.at[index, "time"]),
                "direction": int(signals.at[index]),
                "alpha": round(float(alpha.at[index]), 6),
                "close": float(frame.at[index, "close"]),
            }
            for index in frame.index[evaluation_start:]
            if signals.at[index] and pd.notna(alpha.at[index])
        ]
        # Alpha research measures factor predictiveness. Trade simulation belongs to strategy backtesting.
        return BacktestResult(metrics["score"], metrics, signal_rows)

    @staticmethod
    def _apply_cooldown(signals: pd.Series, cooldown: int) -> pd.Series:
        result = signals.copy()
        last_event = -cooldown - 1
        last_direction = 0
        for index, direction in enumerate(signals.tolist()):
            if direction == 0:
                continue
            if direction != last_direction or index - last_event > cooldown:
                last_event = index
                last_direction = direction
            else:
                result.iloc[index] = 0
        return result

    @staticmethod
    def _metrics(
        alpha: pd.Series, signals: pd.Series, future_return: pd.Series,
        decay_returns: Optional[Dict[int, pd.Series]] = None,
    ) -> Dict:
        valid = alpha.notna() & future_return.notna()
        active = valid & signals.ne(0)
        sample_count = int(valid.sum())
        signal_count = int(active.sum())
        base = {
            "sample_count": sample_count,
            "signal_count": signal_count,
            "coverage": round(signal_count / max(sample_count, 1), 6),
            "factor_coverage": round(
                sample_count / max(int(future_return.notna().sum()), 1), 6
            ),
            "ic": 0.0,
            "rank_ic": 0.0,
            "rolling_ic_mean": 0.0,
            "rolling_ic_std": 0.0,
            "rolling_rank_ic_mean": 0.0,
            "rolling_rank_ic_std": 0.0,
            "rolling_ic_count": 0,
            "ic_ir": 0.0,
            "rank_ic_ir": 0.0,
            "return_ir": 0.0,
            "hit_rate": 0.0,
            "mean_signal_return": 0.0,
            "turnover": 0.0,
            "autocorrelation": AlphaBacktestEngine._autocorrelation(alpha),
            "quintile_analysis": AlphaBacktestEngine._quintile_analysis(
                alpha, future_return
            ),
            "decay": AlphaBacktestEngine._decay_metrics(
                alpha, signals, decay_returns or {}
            ),
        }
        if sample_count < 20 or signal_count < 3:
            return base
        signal_returns = signals[active] * future_return[active]
        ic = alpha[valid].corr(future_return[valid])
        rank_ic = alpha[valid].rank().corr(future_return[valid].rank())
        std = float(signal_returns.std())
        return_ir = (
            float(signal_returns.mean()) / std * math.sqrt(signal_count)
            if std and math.isfinite(std) else 0.0
        )
        rolling = AlphaBacktestEngine._rolling_ic(alpha[valid], future_return[valid])
        base.update({
            "ic": round(float(ic) if pd.notna(ic) else 0.0, 6),
            "rank_ic": round(float(rank_ic) if pd.notna(rank_ic) else 0.0, 6),
            **rolling,
            "return_ir": round(return_ir, 6),
            "hit_rate": round(float((signal_returns > 0).mean()), 6),
            "mean_signal_return": round(float(signal_returns.mean()), 8),
            "turnover": round(float(signals.diff().abs().fillna(0).sum()) / sample_count, 6),
        })
        return base

    @staticmethod
    def _rolling_ic(alpha: pd.Series, future_return: pd.Series) -> Dict:
        values = pd.DataFrame({"alpha": alpha, "future": future_return}).dropna()
        sample_count = len(values)
        window = max(20, min(60, sample_count // 4))
        if sample_count < window * 2:
            return {
                "rolling_ic_mean": 0.0, "rolling_ic_std": 0.0,
                "rolling_rank_ic_mean": 0.0, "rolling_rank_ic_std": 0.0,
                "rolling_ic_count": 0, "ic_ir": 0.0, "rank_ic_ir": 0.0,
                "ic_t_stat": 0.0, "rank_ic_t_stat": 0.0,
                "positive_ic_ratio": 0.0, "positive_rank_ic_ratio": 0.0,
            }
        step = max(5, window // 4)
        ic_values = []
        rank_values = []
        for end in range(window, sample_count + 1, step):
            chunk = values.iloc[end - window:end]
            ic = chunk["alpha"].corr(chunk["future"])
            rank_ic = chunk["alpha"].rank().corr(chunk["future"].rank())
            if pd.notna(ic):
                ic_values.append(float(ic))
            if pd.notna(rank_ic):
                rank_values.append(float(rank_ic))

        def summary(items: List[float]) -> Tuple[float, float, float]:
            if not items:
                return 0.0, 0.0, 0.0
            mean = float(np.mean(items))
            std = float(np.std(items, ddof=1)) if len(items) > 1 else 0.0
            if std > 1e-12:
                ratio = max(-10.0, min(10.0, mean / std))
            else:
                ratio = 10.0 if mean > 0 else (-10.0 if mean < 0 else 0.0)
            return mean, std, ratio

        ic_mean, ic_std, ic_ir = summary(ic_values)
        rank_mean, rank_std, rank_ir = summary(rank_values)
        return {
            "rolling_ic_mean": round(ic_mean, 6),
            "rolling_ic_std": round(ic_std, 6),
            "rolling_rank_ic_mean": round(rank_mean, 6),
            "rolling_rank_ic_std": round(rank_std, 6),
            "rolling_ic_count": min(len(ic_values), len(rank_values)),
            "ic_ir": round(ic_ir, 6),
            "rank_ic_ir": round(rank_ir, 6),
            "ic_t_stat": round(
                ic_mean / (ic_std / math.sqrt(len(ic_values)))
                if ic_std > 1e-12 and len(ic_values) > 1 else 0.0, 6
            ),
            "rank_ic_t_stat": round(
                rank_mean / (rank_std / math.sqrt(len(rank_values)))
                if rank_std > 1e-12 and len(rank_values) > 1 else 0.0, 6
            ),
            "positive_ic_ratio": round(
                sum(value > 0 for value in ic_values) / max(1, len(ic_values)), 6
            ),
            "positive_rank_ic_ratio": round(
                sum(value > 0 for value in rank_values) / max(1, len(rank_values)), 6
            ),
        }

    @staticmethod
    def _autocorrelation(values: pd.Series) -> List[Dict]:
        valid = values.dropna()
        return [{
            "lag": lag,
            "correlation": round(
                float(valid.autocorr(lag=lag))
                if len(valid) > lag + 10 and pd.notna(valid.autocorr(lag=lag))
                else 0.0,
                6,
            ),
        } for lag in (1, 3, 5, 10, 20)]

    @staticmethod
    def _quintile_analysis(alpha: pd.Series, future_return: pd.Series) -> Dict:
        history = alpha.shift(1)
        window = min(500, max(60, len(alpha) // 3))
        min_periods = max(20, window // 3)
        boundaries = [
            history.rolling(window, min_periods=min_periods).quantile(value)
            for value in (0.2, 0.4, 0.6, 0.8)
        ]
        groups = pd.Series(np.nan, index=alpha.index)
        ready = alpha.notna() & future_return.notna() & boundaries[0].notna()
        groups[ready] = 1
        for boundary in boundaries:
            groups[ready & alpha.gt(boundary)] += 1
        returns = []
        counts = []
        for group in range(1, 6):
            selected = future_return[groups.eq(group)].dropna()
            returns.append(round(float(selected.mean()), 8) if len(selected) else 0.0)
            counts.append(int(len(selected)))
        comparisons = [returns[index] <= returns[index + 1] for index in range(4)]
        return {
            "group_returns": returns,
            "group_counts": counts,
            "monotonicity": round(sum(comparisons) / 4, 6),
            "top_bottom_spread": round(returns[-1] - returns[0], 8),
        }

    def factor_diagnostics(
        self, frame: pd.DataFrame, config: Dict, params: Dict,
    ) -> List[Dict]:
        horizons = sorted({1, 3, 5, 10, 20, int(config["prediction_horizon"])})
        diagnostics = []
        normalized_series = []
        for index, factor in enumerate(config["factors"]):
            length = int(params[f"factor_{index}_length"])
            raw = self.catalog.calculate(
                frame, factor["name"], length,
                config.get("time_zone", "Asia/Shanghai"),
            )
            normalized = self._preprocess_factor(raw, length)
            normalized_series.append((factor["name"], normalized))
            primary_return = frame["close"].shift(
                -int(config["prediction_horizon"])
            ).div(frame["close"]).sub(1)
            rolling = self._rolling_ic(normalized, primary_return)
            valid = normalized.notna()
            missing = (~valid).astype(int)
            streak = int(missing.groupby(valid.cumsum()).sum().max() or 0)
            diagnostics.append({
                "name": factor["name"],
                "length": length,
                "coverage": round(float(valid.mean()), 6),
                "missing_count": int((~valid).sum()),
                "max_missing_streak": streak,
                "autocorrelation": self._autocorrelation(normalized),
                **rolling,
                "quintile_analysis": self._quintile_analysis(
                    normalized, primary_return
                ),
                "decay": self._decay_metrics(
                    normalized,
                    pd.Series(np.where(normalized >= 0, 1, -1), index=frame.index),
                    {
                        horizon: frame["close"].shift(-horizon).div(
                            frame["close"]
                        ).sub(1)
                        for horizon in horizons
                    },
                ),
            })
        correlation = pd.DataFrame({name: values for name, values in normalized_series}).corr()
        for item in diagnostics:
            item["correlation_with_selected"] = {
                name: round(float(correlation.at[item["name"], name]), 6)
                for name, _ in normalized_series
                if name != item["name"] and pd.notna(correlation.at[item["name"], name])
            }
            item["max_peer_correlation"] = max(
                (abs(value) for value in item["correlation_with_selected"].values()),
                default=0.0,
            )
        return diagnostics

    def time_slot_report(self, frame: pd.DataFrame, config: Dict, params: Dict) -> Dict:
        """Summarize how the selected Alpha behaves at recurring local times."""
        if not any(item["name"] in FactorCatalog.NATIVE_FACTORS for item in config["factors"]):
            return {}
        time_zone = config.get("time_zone", "Asia/Shanghai")
        try:
            local_time = pd.to_datetime(frame["time"], unit="s", utc=True).dt.tz_convert(
                ZoneInfo(time_zone)
            )
        except Exception:
            return {}
        horizon = int(config["prediction_horizon"])
        alpha = self.calculate_alpha(frame, config, params)
        future_return = frame["close"].shift(-horizon).div(frame["close"]).sub(1)
        slots = local_time.dt.strftime("%H:%M")
        rows = []
        for slot, indices in slots.groupby(slots, sort=True).groups.items():
            values = alpha.loc[indices]
            future = future_return.loc[indices]
            valid = values.notna() & future.notna()
            if int(valid.sum()) < 5:
                continue
            rank_ic = values[valid].rank().corr(future[valid].rank())
            rows.append({
                "slot": slot,
                "sample_count": int(valid.sum()),
                "average_future_return": round(float(future[valid].mean()), 8),
                "up_probability": round(float((future[valid] > 0).mean()), 6),
                "rank_ic": round(float(rank_ic) if pd.notna(rank_ic) else 0.0, 6),
                "average_alpha": round(float(values[valid].mean()), 6),
            })
        rows.sort(key=lambda item: (abs(item["rank_ic"]), item["sample_count"]), reverse=True)
        return {
            "time_zone": time_zone,
            "lookback_days": int(config.get("same_slot_lookback_days", 5)),
            "slot_count": len(rows),
            "items": rows[:24],
        }

    @staticmethod
    def _decay_metrics(
        alpha: pd.Series, signals: pd.Series,
        decay_returns: Dict[int, pd.Series],
    ) -> List[Dict]:
        decay = []
        for horizon, future_return in sorted(decay_returns.items()):
            valid = alpha.notna() & future_return.notna()
            active = valid & signals.ne(0)
            ic = alpha[valid].corr(future_return[valid]) if int(valid.sum()) >= 20 else 0.0
            rank_ic = (
                alpha[valid].rank().corr(future_return[valid].rank())
                if int(valid.sum()) >= 20 else 0.0
            )
            signal_return = (
                float((signals[active] * future_return[active]).mean())
                if int(active.sum()) >= 3 else 0.0
            )
            decay.append({
                "horizon": int(horizon),
                "ic": round(float(ic) if pd.notna(ic) else 0.0, 6),
                "rank_ic": round(float(rank_ic) if pd.notna(rank_ic) else 0.0, 6),
                "mean_signal_return": round(signal_return, 8),
                "sample_count": int(valid.sum()),
            })
        return decay

    @staticmethod
    def _score(metrics: Dict) -> float:
        coverage = min(1.0, metrics.get("coverage", 0) / 0.2)
        stability = max(0.0, 1 - min(1.0, abs(metrics.get("turnover", 0))))
        score = (
            max(0.0, metrics.get("rank_ic", 0)) * 30
            + max(-2, min(2, metrics.get("ic_ir", 0))) * 8
            + max(-2, min(2, metrics.get("return_ir", 0))) * 2
            + max(0, metrics.get("hit_rate", 0) - 0.5) * 60
            + coverage * 5
            + stability * 5
        )
        if metrics.get("signal_count", 0) < 10:
            score -= 20
        return round(float(score), 6)


class AlphaOptimizationEngine:
    def __init__(
        self, repository: AlphaResearchRepository,
        progress_callback: Optional[Callable[[float], None]] = None,
        cancel_callback: Optional[Callable[[], bool]] = None,
        candidate_service=None,
    ):
        self.repository = repository
        self.progress_callback = progress_callback or (lambda value: None)
        self.cancel_callback = cancel_callback or (lambda: False)
        self.backtest = AlphaBacktestEngine()
        self.candidate_service = candidate_service or AlphaCandidateService()

    def independent_evaluation(
        self, frame: pd.DataFrame, config: Dict, params: Dict,
        evaluation_start: int = 0,
        components: Optional[List[Dict]] = None,
    ) -> Dict:
        """Cheap per-factor gate executed for every Optuna trial."""
        started = time.monotonic()
        horizon = int(config["prediction_horizon"])
        future = frame["close"].shift(-horizon).div(frame["close"]).sub(1)
        start = max(0, min(int(evaluation_start), len(frame)))
        future = future.iloc[start:]
        rows = []
        factor_components = components or self.backtest.calculate_factor_components(
            frame, config, params
        )
        for component in factor_components:
            direction = -1.0 if component["weight"] < 0 else 1.0
            values = (component["values"] * direction).iloc[start:]
            valid = values.notna() & future.notna()
            sample_count = int(valid.sum())
            coverage = sample_count / max(1, int(future.notna().sum()))
            rank_ic = values[valid].rank().corr(future[valid].rank())
            rank_ic = float(rank_ic) if pd.notna(rank_ic) else 0.0
            rolling = self.backtest._rolling_ic(values, future)
            stable_ratio = float(rolling.get("positive_rank_ic_ratio", 0))
            standard_deviation = float(values[valid].std()) if sample_count else 0.0
            passed = bool(
                sample_count >= 20
                and coverage >= 0.6
                and math.isfinite(standard_deviation)
                and standard_deviation > 1e-9
                and abs(rank_ic) >= 0.001
            )
            rows.append({
                "factor_index": component["index"],
                "name": component["name"],
                "length": component["length"],
                "weight": round(component["weight"], 6),
                "coverage": round(coverage, 6),
                "sample_count": sample_count,
                "rank_ic": round(rank_ic, 6),
                "positive_rank_ic_ratio": round(stable_ratio, 6),
                "standard_deviation": round(standard_deviation, 8),
                "passed": passed,
            })
        mean_rank_ic = float(np.mean([item["rank_ic"] for item in rows])) if rows else 0.0
        mean_positive_ratio = float(np.mean([
            item["positive_rank_ic_ratio"] for item in rows
        ])) if rows else 0.0
        adjustment = (
            float(np.clip(mean_rank_ic, -0.05, 0.05)) * 10
            + (mean_positive_ratio - 0.5) * 0.25
        )
        return {
            "passed": bool(rows) and all(item["passed"] for item in rows),
            "factor_count": len(rows),
            "mean_rank_ic": round(mean_rank_ic, 6),
            "mean_positive_rank_ic_ratio": round(mean_positive_ratio, 6),
            "score_adjustment": round(adjustment, 6),
            "duration_ms": int((time.monotonic() - started) * 1000),
            "factors": rows,
        }

    def residual_evaluation(
        self, frame: pd.DataFrame, train_end: int,
        config: Dict, params: Dict,
    ) -> Dict:
        """Fit factor residuals on train and measure incremental IC on validation."""
        started = time.monotonic()
        horizon = int(config["prediction_horizon"])
        future = frame["close"].shift(-horizon).div(frame["close"]).sub(1)
        components = self.backtest.calculate_factor_components(frame, config, params)
        oriented = [
            item["values"] * (-1.0 if item["weight"] < 0 else 1.0)
            for item in components
        ]
        rows = []
        for index, component in enumerate(components):
            target = oriented[index]
            peers = [values for peer_index, values in enumerate(oriented) if peer_index != index]
            residual = target.copy()
            if peers:
                peer_frame = pd.concat(peers, axis=1)
                train_data = pd.concat(
                    [target.rename("target"), peer_frame], axis=1
                ).iloc[:train_end].dropna()
                if len(train_data) >= max(20, len(peers) + 5):
                    train_x = train_data.iloc[:, 1:].to_numpy(dtype=float)
                    train_x = np.column_stack([np.ones(len(train_x)), train_x])
                    coefficients = np.linalg.lstsq(
                        train_x, train_data["target"].to_numpy(dtype=float),
                        rcond=None,
                    )[0]
                    available = pd.concat(
                        [target.rename("target"), peer_frame], axis=1
                    ).dropna()
                    apply_x = available.iloc[:, 1:].to_numpy(dtype=float)
                    apply_x = np.column_stack([np.ones(len(apply_x)), apply_x])
                    residual = pd.Series(np.nan, index=frame.index, dtype=float)
                    residual.loc[available.index] = (
                        available["target"].to_numpy(dtype=float)
                        - apply_x.dot(coefficients)
                    )
            validation_target = target.iloc[train_end:]
            validation_residual = residual.iloc[train_end:]
            validation_future = future.iloc[train_end:]
            raw_valid = validation_target.notna() & validation_future.notna()
            residual_valid = validation_residual.notna() & validation_future.notna()
            raw_rank_ic = validation_target[raw_valid].rank().corr(
                validation_future[raw_valid].rank()
            )
            residual_rank_ic = validation_residual[residual_valid].rank().corr(
                validation_future[residual_valid].rank()
            )
            raw_rank_ic = float(raw_rank_ic) if pd.notna(raw_rank_ic) else 0.0
            residual_rank_ic = (
                float(residual_rank_ic) if pd.notna(residual_rank_ic) else 0.0
            )
            rolling = self.backtest._rolling_ic(
                validation_residual, validation_future
            )
            target_std = float(validation_target[raw_valid].std()) if raw_valid.any() else 0.0
            residual_std = (
                float(validation_residual[residual_valid].std())
                if residual_valid.any() else 0.0
            )
            rows.append({
                "factor_index": component["index"],
                "name": component["name"],
                "raw_rank_ic": round(raw_rank_ic, 6),
                "residual_rank_ic": round(residual_rank_ic, 6),
                "incremental_rank_ic": round(residual_rank_ic, 6),
                "retained_variance_ratio": round(
                    residual_std / target_std if target_std > 1e-12 else 0.0, 6
                ),
                "positive_rank_ic_ratio": rolling.get(
                    "positive_rank_ic_ratio", 0.0
                ),
                "sample_count": int(residual_valid.sum()),
            })
        mean_incremental = float(np.mean([
            item["incremental_rank_ic"] for item in rows
        ])) if rows else 0.0
        mean_positive_ratio = float(np.mean([
            item["positive_rank_ic_ratio"] for item in rows
        ])) if rows else 0.0
        adjustment = (
            float(np.clip(mean_incremental, -0.05, 0.05)) * 20
            + (mean_positive_ratio - 0.5) * 0.5
        )
        return {
            "factor_count": len(rows),
            "mean_incremental_rank_ic": round(mean_incremental, 6),
            "mean_positive_rank_ic_ratio": round(mean_positive_ratio, 6),
            "score_adjustment": round(adjustment, 6),
            "duration_ms": int((time.monotonic() - started) * 1000),
            "factors": rows,
        }

    def ablation_experiment(
        self, frame: pd.DataFrame, evaluation_start: int,
        config: Dict, params: Dict, baseline: BacktestResult,
    ) -> Dict:
        """Run the final baseline plus one variant with each factor removed."""
        started = time.monotonic()
        variants = [{
            "variant": "baseline", "removed_factor": None,
            "score": baseline.score,
            "score_delta": 0.0,
            "rank_ic": baseline.metrics.get("rank_ic", 0),
            "rank_ic_delta": 0.0,
            "turnover": baseline.metrics.get("turnover", 0),
            "contribution": "baseline",
        }]
        factors = config.get("factors") or []
        for removed_index, removed_factor in enumerate(factors):
            reduced_factors = [
                factor for index, factor in enumerate(factors)
                if index != removed_index
            ]
            if not reduced_factors:
                variants.append({
                    "variant": "remove_factor",
                    "removed_factor": removed_factor["name"],
                    "score": None, "score_delta": None,
                    "rank_ic": None, "rank_ic_delta": None,
                    "turnover": None, "contribution": "sole_factor",
                })
                continue
            reduced_config = {**config, "factors": reduced_factors}
            reduced_params = {
                key: value for key, value in params.items()
                if not key.startswith("factor_")
            }
            next_index = 0
            for original_index in range(len(factors)):
                if original_index == removed_index:
                    continue
                reduced_params[f"factor_{next_index}_length"] = params[
                    f"factor_{original_index}_length"
                ]
                reduced_params[f"factor_{next_index}_weight"] = params[
                    f"factor_{original_index}_weight"
                ]
                next_index += 1
            result = self.backtest.run(
                frame, reduced_config, reduced_params,
                evaluation_start=evaluation_start,
            )
            score_delta = baseline.score - result.score
            rank_ic_delta = (
                float(baseline.metrics.get("rank_ic", 0))
                - float(result.metrics.get("rank_ic", 0))
            )
            variants.append({
                "variant": "remove_factor",
                "removed_factor": removed_factor["name"],
                "score": result.score,
                "score_delta": round(score_delta, 6),
                "rank_ic": result.metrics.get("rank_ic", 0),
                "rank_ic_delta": round(rank_ic_delta, 6),
                "turnover": result.metrics.get("turnover", 0),
                "contribution": (
                    "essential" if score_delta >= 1.0 or rank_ic_delta >= 0.02
                    else "useful" if score_delta >= 0.1 or rank_ic_delta >= 0.005
                    else "redundant"
                ),
            })
        removable = [item for item in variants if item["variant"] == "remove_factor"]
        useful = [
            item for item in removable
            if item["contribution"] in {"essential", "useful", "sole_factor"}
        ]
        return {
            "variant_count": len(variants),
            "useful_factor_ratio": round(
                len(useful) / max(1, len(removable)), 6
            ),
            "duration_ms": int((time.monotonic() - started) * 1000),
            "variants": variants,
        }

    def run(self, task: Dict) -> Tuple[Dict, Dict, List[Dict], List[Dict]]:
        try:
            import optuna
        except ImportError as exc:
            raise RuntimeError("缺少 Optuna，请先安装项目依赖") from exc
        optuna.logging.set_verbosity(optuna.logging.WARNING)
        base_config = task["config"]
        dataset = BacktestDatasetRepository(self.repository.storage).get_visible(
            int(task["user_id"]), base_config["dataset_id"]
        )
        if dataset is None or dataset["status"] != "ready":
            raise ValueError("历史数据集不存在或尚未就绪")
        bars = HistoricalBarReader.read(dataset["file_path"], dataset["data_format"])
        frame = self.backtest.build_frame(bars, base_config["timeframe"])
        if len(frame) < 100:
            raise ValueError("聚合后的行情不足 100 根，无法进行 Alpha 搜索")
        train_end = max(60, int(len(frame) * 0.6))
        validation_end = max(train_end + 20, int(len(frame) * 0.8))
        validation_end = min(validation_end, len(frame) - 1)
        optimization_frame = frame.iloc[:validation_end].reset_index(drop=True)
        trial_count = int(base_config["trial_count"])
        max_iterations = (
            int(base_config.get("llm_iteration_count", 3))
            if base_config.get("research_mode") == "ai" else 1
        )
        current_candidate = self._candidate_from_config(base_config)
        current_config = dict(base_config)
        history = []
        global_best = None
        completed_total = 0
        pruned_total = 0
        independent_duration_ms = 0
        residual_candidate_total = 0
        residual_duration_ms = 0
        no_improvement_rounds = 0
        stopped_reason = "达到配置的最大迭代轮次"

        for iteration in range(1, max_iterations + 1):
            if self.cancel_callback():
                raise AlphaResearchCanceled("用户已终止 Alpha 研究任务")
            self.repository.save_iteration(
                task["run_id"], iteration, "running", current_candidate
            )

            def objective(trial):
                if self.cancel_callback():
                    raise AlphaResearchCanceled("用户已终止 Alpha 研究任务")
                started = time.monotonic()
                params = self._suggest_params(trial, current_config)
                trial_number = (iteration - 1) * trial_count + trial.number
                try:
                    components = self.backtest.calculate_factor_components(
                        optimization_frame, current_config, params
                    )
                    independent = self.independent_evaluation(
                        optimization_frame, current_config, params,
                        evaluation_start=train_end,
                        components=components,
                    )
                    if not independent["passed"]:
                        metrics = {
                            "independent_evaluation": independent,
                            "gate_status": "pruned",
                        }
                        trial.set_user_attr("metrics", metrics)
                        self.repository.save_trial(
                            task["run_id"], trial_number, "pruned", None,
                            params, metrics,
                            int((time.monotonic() - started) * 1000),
                            "独立因子评估未通过", iteration,
                        )
                        raise optuna.TrialPruned("独立因子评估未通过")
                    weight_total = sum(
                        abs(item["weight"]) for item in components
                    )
                    trial_alpha = sum(
                        item["values"] * item["weight"]
                        for item in components
                    ) / max(weight_total, 1e-9)
                    train_result = self.backtest.run(
                        optimization_frame.iloc[:train_end].reset_index(drop=True),
                        current_config, params,
                        alpha_override=trial_alpha.iloc[:train_end],
                    )
                    validation_result = self.backtest.run(
                        optimization_frame, current_config, params,
                        evaluation_start=train_end,
                        alpha_override=trial_alpha,
                    )
                    gap = train_result.score - validation_result.score
                    base_objective_score = (
                        validation_result.score - max(0.0, gap) * 0.25
                    )
                    objective_score = round(
                        base_objective_score
                        + float(independent["score_adjustment"]), 6
                    )
                    metrics = {
                        "train": train_result.metrics,
                        "validation": validation_result.metrics,
                        "generalization_gap": round(gap, 6),
                        "base_objective_score": round(base_objective_score, 6),
                        "objective_score": objective_score,
                        "independent_evaluation": independent,
                        "gate_status": "passed",
                    }
                    trial.set_user_attr("metrics", metrics)
                    self.repository.save_trial(
                        task["run_id"], trial_number, "completed", objective_score,
                        params, metrics, int((time.monotonic() - started) * 1000),
                        iteration_number=iteration,
                    )
                    return objective_score
                except AlphaResearchCanceled:
                    raise
                except optuna.TrialPruned:
                    raise
                except Exception as exc:
                    self.repository.save_trial(
                        task["run_id"], trial_number, "failed", None, params, {},
                        int((time.monotonic() - started) * 1000), str(exc), iteration,
                    )
                    raise

            def after_trial(study, trial):
                nonlocal completed_total
                completed_total += 1
                self.progress_callback(
                    completed_total / (trial_count * max_iterations) * 90
                )
                if self.cancel_callback():
                    study.stop()

            study = optuna.create_study(
                direction="maximize",
                sampler=optuna.samplers.TPESampler(
                    seed=int(base_config.get("random_seed", 42)) + iteration - 1
                ),
            )
            study.optimize(
                objective, n_trials=trial_count, callbacks=[after_trial],
                catch=(ValueError, RuntimeError),
            )
            if self.cancel_callback():
                raise AlphaResearchCanceled("用户已终止 Alpha 研究任务")
            completed_trials = [
                trial for trial in study.trials
                if trial.state == optuna.trial.TrialState.COMPLETE
                and trial.value is not None
            ]
            if not completed_trials:
                pruned_count = sum(
                    trial.state == optuna.trial.TrialState.PRUNED
                    for trial in study.trials
                )
                if pruned_count == len(study.trials):
                    raise RuntimeError(
                        f"第 {iteration} 轮所有 Trial 均未通过独立因子门槛"
                    )
                raise RuntimeError(f"第 {iteration} 轮全部参数试验均失败")
            pruned_total += sum(
                trial.state == optuna.trial.TrialState.PRUNED
                for trial in study.trials
            )
            independent_duration_ms += sum(
                int(((trial.user_attrs.get("metrics") or {}).get(
                    "independent_evaluation"
                ) or {}).get("duration_ms", 0))
                for trial in study.trials
            )

            top_trials = sorted(
                completed_trials, key=lambda item: float(item.value), reverse=True
            )[:5]
            residual_ranked = []
            for candidate_trial in top_trials:
                residual = self.residual_evaluation(
                    optimization_frame, train_end, current_config,
                    dict(candidate_trial.params),
                )
                adjusted_score = round(
                    float(candidate_trial.value)
                    + float(residual["score_adjustment"]), 6
                )
                residual_ranked.append({
                    "trial": candidate_trial,
                    "residual_evaluation": residual,
                    "adjusted_score": adjusted_score,
                })
            residual_candidate_total += len(residual_ranked)
            residual_duration_ms += sum(
                int(item["residual_evaluation"].get("duration_ms", 0))
                for item in residual_ranked
            )
            selected_trial = max(
                residual_ranked, key=lambda item: item["adjusted_score"]
            )
            best_trial = selected_trial["trial"]
            best_params = dict(best_trial.params)
            best_metrics = dict(best_trial.user_attrs.get("metrics") or {})
            best_metrics["residual_evaluation"] = selected_trial[
                "residual_evaluation"
            ]
            best_metrics["adjusted_objective_score"] = selected_trial[
                "adjusted_score"
            ]
            detailed_train = self.backtest.run(
                optimization_frame.iloc[:train_end].reset_index(drop=True),
                current_config, best_params,
            )
            detailed_validation = self.backtest.run(
                optimization_frame, current_config, best_params,
                evaluation_start=train_end,
            )
            best_metrics["train"] = detailed_train.metrics
            best_metrics["validation"] = detailed_validation.metrics
            scores = sorted(float(item.value) for item in completed_trials)
            best_metrics.update({
                "top_trials": [
                    {
                        "score": round(float(item["trial"].value), 6),
                        "residual_adjusted_score": item["adjusted_score"],
                        "params": dict(item["trial"].params),
                        "residual_evaluation": item["residual_evaluation"],
                    }
                    for item in residual_ranked
                ],
                "median_score": round(float(np.median(scores)), 6),
                "successful_trials": len(completed_trials),
            })
            expression = self._expression(current_config, best_params)
            iteration_record = {
                "iteration": iteration,
                "candidate": current_candidate,
                "expression": expression,
                "best_params": best_params,
                "metrics": best_metrics,
            }
            history.append(iteration_record)
            iteration_score = float(best_metrics["adjusted_objective_score"])
            if global_best is None or iteration_score > global_best["score"] + 0.1:
                global_best = {
                    "score": iteration_score,
                    "iteration": iteration,
                    "candidate": current_candidate,
                    "config": dict(current_config),
                    "params": best_params,
                    "independent_evaluation": best_metrics.get(
                        "independent_evaluation", {}
                    ),
                    "residual_evaluation": best_metrics.get(
                        "residual_evaluation", {}
                    ),
                }
                no_improvement_rounds = 0
            else:
                no_improvement_rounds += 1

            self.repository.save_iteration(
                task["run_id"], iteration, "completed", current_candidate,
                expression, best_params, best_metrics, completed=True,
            )
            if iteration >= max_iterations:
                break
            if no_improvement_rounds >= 2:
                stopped_reason = "连续两轮验证集未显著改善，提前停止"
                break
            try:
                refinement = self.candidate_service.refine(
                    int(task["user_id"]), base_config.get("research_description", ""),
                    base_config["timeframe"], base_config["prediction_horizon"],
                    current_candidate, history,
                )
                self.repository.save_iteration(
                    task["run_id"], iteration, "completed", current_candidate,
                    expression, best_params, best_metrics,
                    refinement["prompt"], refinement["response"],
                    refinement["model"], completed=True,
                )
                current_candidate = refinement["candidate"]
                current_config = self._config_for_candidate(
                    base_config, current_candidate
                )
            except Exception as exc:
                stopped_reason = f"大模型改进失败，保留当前最佳结果: {exc}"
                self.repository.save_iteration(
                    task["run_id"], iteration, "completed", current_candidate,
                    expression, best_params, best_metrics, error=str(exc),
                    completed=True,
                )
                break

        if global_best is None:
            raise RuntimeError("没有可用的 Alpha 优化结果")
        selected_config = global_best["config"]
        best_params = global_best["params"]
        final_result = self.backtest.run(frame, selected_config, best_params)
        train_result = self.backtest.run(
            frame.iloc[:train_end].reset_index(drop=True), selected_config,
            best_params,
        )
        validation_result = self.backtest.run(
            frame.iloc[:validation_end].reset_index(drop=True), selected_config,
            best_params, evaluation_start=train_end,
        )
        ablation = self.ablation_experiment(
            frame.iloc[:validation_end].reset_index(drop=True), train_end,
            selected_config, best_params, validation_result,
        )
        # Hidden test is evaluated exactly once after optimization and ablation.
        test_result = self.backtest.run(
            frame, selected_config, best_params,
            evaluation_start=validation_end,
        )
        factor_diagnostics = self.backtest.factor_diagnostics(
            frame, selected_config, best_params
        )
        parameter_robustness = []
        for key, value in best_params.items():
            if not key.endswith("_length"):
                continue
            factor_index = int(key.split("_")[1])
            factor = selected_config["factors"][factor_index]
            for multiplier in (0.9, 1.1):
                perturbed = dict(best_params)
                perturbed[key] = max(
                    int(factor["length_min"]),
                    min(int(factor["length_max"]), round(int(value) * multiplier)),
                )
                evaluation = self.backtest.run(
                    frame.iloc[:validation_end].reset_index(drop=True),
                    selected_config, perturbed,
                    evaluation_start=train_end,
                )
                parameter_robustness.append({
                    "parameter": key,
                    "value": perturbed[key],
                    "validation_score": evaluation.score,
                    "rank_ic": evaluation.metrics.get("rank_ic", 0),
                    "ic_ir": evaluation.metrics.get("ic_ir", 0),
                })
        subperiod_robustness = []
        for segment_number, (segment_start, segment_end) in enumerate((
            (0, len(frame) // 3),
            (len(frame) // 3, len(frame) * 2 // 3),
            (len(frame) * 2 // 3, len(frame)),
        ), start=1):
            segment = self.backtest.run(
                frame.iloc[:segment_end].reset_index(drop=True), selected_config,
                best_params, evaluation_start=segment_start,
            )
            subperiod_robustness.append({
                "segment": segment_number,
                "start_time": int(frame.iloc[segment_start]["time"]),
                "end_time": int(frame.iloc[segment_end - 1]["time"]),
                "score": segment.score,
                "rank_ic": segment.metrics.get("rank_ic", 0),
                "hit_rate": segment.metrics.get("hit_rate", 0),
            })
        candidate_alpha = self.backtest.calculate_alpha(
            frame, selected_config, best_params
        )
        library_correlations = []
        try:
            visible_alphas = AlphaLibraryRepository(
                self.repository.storage
            ).list_visible(int(task["user_id"]))
        except (AttributeError, TypeError):
            visible_alphas = []
        for alpha in visible_alphas:
            definition = alpha.get("definition") or {}
            if (
                alpha.get("status") != "validated"
                or definition.get("timeframe") != selected_config["timeframe"]
            ):
                continue
            try:
                existing = self.backtest.calculate_alpha(
                    frame, definition, definition.get("params") or {}
                )
                correlation = candidate_alpha.corr(existing)
            except Exception:
                continue
            if pd.notna(correlation):
                library_correlations.append({
                    "alpha_id": alpha["alpha_id"],
                    "name": alpha["name"],
                    "correlation": round(float(correlation), 6),
                })
        result = {
            "best_score": global_best["score"],
            "trial_count": completed_total,
            "trials_per_iteration": trial_count,
            "completed_iterations": len(history),
            "selected_iteration": global_best["iteration"],
            "stopped_reason": stopped_reason,
            "selected_candidate": global_best["candidate"],
            "independent_evaluation": global_best.get(
                "independent_evaluation", {}
            ),
            "residual_evaluation": global_best.get(
                "residual_evaluation", {}
            ),
            "ablation_experiment": ablation,
            "experiment_cost": {
                "independent_runs": completed_total,
                "independent_pruned_trials": pruned_total,
                "independent_duration_ms": independent_duration_ms,
                "residual_candidates": residual_candidate_total,
                "residual_duration_ms": residual_duration_ms,
                "ablation_variants": ablation["variant_count"],
                "ablation_duration_ms": ablation["duration_ms"],
            },
            "factor_diagnostics": factor_diagnostics,
            "time_slot_report": self.backtest.time_slot_report(
                frame, selected_config, best_params
            ),
            "parameter_robustness": parameter_robustness,
            "subperiod_robustness": subperiod_robustness,
            "library_correlations": library_correlations,
            "runtime_definition": {
                "timeframe": selected_config["timeframe"],
                "factors": selected_config["factors"],
                "params": best_params,
                "buy_threshold": best_params["buy_threshold"],
                "sell_threshold": best_params["sell_threshold"],
                "prediction_horizon": selected_config["prediction_horizon"],
                "confirmation_bars": selected_config.get("confirmation_bars", 1),
                "preprocessing": "causal_winsorize_zscore",
            },
            "metrics": final_result.metrics,
            "splits": {
                "train": train_result.metrics,
                "validation": validation_result.metrics,
                "hidden_test": test_result.metrics,
            },
            "data": {
                "bar_count": len(frame), "timeframe": selected_config["timeframe"],
                "train_bars": train_end,
                "validation_bars": validation_end - train_end,
                "hidden_test_bars": len(frame) - validation_end,
            },
            "research_type": "factor_validity",
        }
        self.progress_callback(98)
        return result, best_params, final_result.signals

    @staticmethod
    def _suggest_params(trial, config: Dict) -> Dict:
        params = {}
        for index, factor in enumerate(config["factors"]):
            params[f"factor_{index}_length"] = trial.suggest_int(
                f"factor_{index}_length", factor["length_min"], factor["length_max"]
            )
            params[f"factor_{index}_weight"] = trial.suggest_float(
                f"factor_{index}_weight", factor["weight_min"], factor["weight_max"]
            )
        params["buy_threshold"] = trial.suggest_float(
            "buy_threshold", config["buy_threshold_min"], config["buy_threshold_max"]
        )
        params["sell_threshold"] = trial.suggest_float(
            "sell_threshold", config["sell_threshold_min"], config["sell_threshold_max"]
        )
        return params

    @staticmethod
    def _candidate_from_config(config: Dict) -> Dict:
        meta = config.get("candidate_meta") or {}
        return {
            "candidate_id": uuid.uuid4().hex[:10],
            "name": meta.get("name") or config.get("research_name") or "Alpha 候选",
            "theme": meta.get("theme") or "自定义",
            "hypothesis": meta.get("hypothesis") or config.get("research_description", ""),
            "buy_logic": meta.get("buy_logic") or "Alpha 高于买入阈值",
            "sell_logic": meta.get("sell_logic") or "Alpha 低于卖出阈值",
            "factors": [dict(item) for item in config["factors"]],
        }

    @staticmethod
    def _config_for_candidate(base_config: Dict, candidate: Dict) -> Dict:
        config = dict(base_config)
        factors = []
        for factor in candidate["factors"]:
            normalized = {
                key: factor[key] for key in (
                    "name", "length_min", "length_max", "weight_min", "weight_max"
                )
            }
            if normalized["name"] in FactorCatalog.NATIVE_FACTORS:
                if normalized["name"] in {"same_slot_mean_return", "same_slot_win_rate"}:
                    normalized["length_min"] = normalized["length_max"] = int(
                        base_config.get("same_slot_lookback_days", 5)
                    )
                else:
                    normalized["length_min"] = normalized["length_max"] = 2
            factors.append(normalized)
        config["factors"] = factors
        config["candidate_meta"] = {
            key: candidate.get(key, "")
            for key in ("name", "theme", "hypothesis", "buy_logic", "sell_logic")
        }
        return config

    @staticmethod
    def _expression(config: Dict, params: Dict) -> str:
        terms = []
        for index, factor in enumerate(config["factors"]):
            length = params.get(f"factor_{index}_length")
            weight = params.get(f"factor_{index}_weight")
            terms.append(f"zscore({factor['name']}({length})) * {float(weight):.4f}")
        return (
            f"Alpha = ({' + '.join(terms)}) / sum(abs(weights)); "
            f"buy >= {float(params['buy_threshold']):.4f}; "
            f"sell <= {float(params['sell_threshold']):.4f}"
        )


class AlphaCandidateService:
    """Use the approved platform LLM to turn a research goal into factor recipes."""

    SYSTEM_PROMPT = (
        "你是量化研究助手。只输出合法 JSON，不输出 Markdown。"
        "候选必须使用提供的技术分析或平台原生时段因子，解释研究假设，不承诺盈利。"
    )

    def __init__(self, catalog: Optional[FactorCatalog] = None):
        self.catalog = catalog or FactorCatalog()

    @staticmethod
    def _render_template(template: str, variables: Dict[str, object]) -> str:
        rendered = template
        for key, value in variables.items():
            rendered = rendered.replace(f"{{{{{key}}}}}", str(value))
        return rendered.strip()

    def _scene_prompts(self, service, user_id: int, scene_code: str) -> Dict:
        governance = getattr(service, "_llm_governance", None)
        if governance is None:
            return {}
        return governance.scene_options(user_id, scene_code)

    def generate(self, user_id: int, payload: Dict) -> List[Dict]:
        description = str(payload.get("research_description", "")).strip()
        if len(description) < 10:
            raise ValueError("请至少用 10 个字描述希望研究的行情规律")
        timeframe = str(payload.get("timeframe", "M5")).upper()
        horizon = max(1, min(500, int(payload.get("prediction_horizon", 15))))
        candidate_count = max(3, min(8, int(payload.get("candidate_count", 3))))
        factors = self.catalog.list()
        by_theme: Dict[str, List[str]] = {}
        for item in factors:
            by_theme.setdefault(item["research_theme"], []).append(item["name"])
        catalog_text = "\n".join(
            f"- {theme}: {', '.join(names)}" for theme, names in by_theme.items()
        )
        from market.services.llm_service import LLMService
        from market.store.llm_store import LLMStore

        from llm_governance import (
            ALPHA_CANDIDATE_GENERATION,
            ALPHA_CANDIDATE_PROMPT_TEMPLATE,
        )
        service = LLMService(LLMStore(user_id=user_id, account_id=0), None)
        scene = self._scene_prompts(service, user_id, ALPHA_CANDIDATE_GENERATION)
        prompt = self._render_template(
            scene.get("user_prompt_template") or ALPHA_CANDIDATE_PROMPT_TEMPLATE,
            {
                "research_description": description,
                "timeframe": timeframe,
                "prediction_horizon": horizon,
                "candidate_count": candidate_count,
                "factor_catalog": catalog_text,
            },
        )
        response = service.call_llm(
            prompt,
            system_prompt=scene.get("system_prompt") or self.SYSTEM_PROMPT,
            scene_code=ALPHA_CANDIDATE_GENERATION,
            object_type="alpha_research", object_id="candidate_generation",
        )
        if not response or not isinstance(response.get("candidates"), list):
            raise RuntimeError("大模型未返回有效的 Alpha 候选，请稍后重试")
        catalog_by_name = {item["name"]: item for item in factors}
        candidates = []
        for raw in response["candidates"][:candidate_count]:
            normalized = self._normalize_candidate(raw, catalog_by_name)
            if normalized:
                candidates.append(normalized)
        if not candidates:
            raise RuntimeError("大模型返回的候选没有使用可执行因子")
        return candidates

    def refine(
        self, user_id: int, research_description: str, timeframe: str,
        prediction_horizon: int, current_candidate: Dict,
        iteration_history: List[Dict],
    ) -> Dict:
        """Ask the LLM for one structural revision using validation-only evidence."""
        factors = self.catalog.list()
        catalog_by_name = {item["name"]: item for item in factors}
        by_theme: Dict[str, List[str]] = {}
        for item in factors:
            by_theme.setdefault(item["research_theme"], []).append(item["name"])
        catalog_text = "\n".join(
            f"- {theme}: {', '.join(names)}" for theme, names in by_theme.items()
        )
        safe_history = [self._prompt_iteration(item) for item in iteration_history]
        service = self._llm_service(user_id)
        from llm_governance import (
            ALPHA_ITERATIVE_REFINEMENT,
            ALPHA_REFINEMENT_PROMPT_TEMPLATE,
        )
        scene = self._scene_prompts(service, user_id, ALPHA_ITERATIVE_REFINEMENT)
        prompt = self._render_template(
            scene.get("user_prompt_template") or ALPHA_REFINEMENT_PROMPT_TEMPLATE,
            {
                "research_description": research_description,
                "timeframe": timeframe,
                "prediction_horizon": prediction_horizon,
                "current_candidate": json.dumps(
                    current_candidate, ensure_ascii=False, separators=(',', ':')
                ),
                "iteration_history": json.dumps(
                    safe_history, ensure_ascii=False, separators=(',', ':')
                ),
                "factor_catalog": catalog_text,
            },
        )
        response = service.call_llm(
            prompt,
            system_prompt=scene.get("system_prompt") or self.SYSTEM_PROMPT,
            scene_code=ALPHA_ITERATIVE_REFINEMENT,
            object_type="alpha_research", object_id="iterative_refinement",
        )
        raw_candidate = response.get("candidate") if isinstance(response, dict) else None
        candidate = self._normalize_candidate(raw_candidate, catalog_by_name)
        if candidate is None:
            raise RuntimeError("大模型未返回可执行的 Alpha 改进候选")
        return {
            "candidate": candidate,
            "prompt": prompt,
            "response": response,
            "model": str(scene.get("default_model_id") or ""),
        }

    @staticmethod
    def _prompt_iteration(item: Dict) -> Dict:
        """Keep prompts compact and explicitly exclude any hidden-test payload."""
        metrics = item.get("metrics") or {}
        top_trials = [{
            "score": trial.get("score"),
            "residual_adjusted_score": trial.get("residual_adjusted_score"),
            "params": trial.get("params") or {},
        } for trial in (metrics.get("top_trials") or [])[:5]]
        return {
            "iteration": item.get("iteration"),
            "expression": item.get("expression", ""),
            "hypothesis": (item.get("candidate") or {}).get("hypothesis", ""),
            "factors": [
                factor.get("name") for factor in (item.get("candidate") or {}).get("factors", [])
            ],
            "best_params": item.get("best_params") or {},
            "train": metrics.get("train") or {},
            "validation": metrics.get("validation") or {},
            "generalization_gap": metrics.get("generalization_gap", 0),
            "objective_score": metrics.get("objective_score", 0),
            "adjusted_objective_score": metrics.get(
                "adjusted_objective_score", 0
            ),
            "independent_evaluation": metrics.get(
                "independent_evaluation", {}
            ),
            "residual_evaluation": metrics.get("residual_evaluation", {}),
            "top_trials": top_trials,
            "median_score": metrics.get("median_score", 0),
        }

    @staticmethod
    def _llm_service(user_id: int):
        from market.services.llm_service import LLMService
        from market.store.llm_store import LLMStore

        return LLMService(LLMStore(user_id=user_id, account_id=0), None)

    @staticmethod
    def _normalize_candidate(raw: Dict, catalog_by_name: Dict[str, Dict]) -> Optional[Dict]:
        if not isinstance(raw, dict):
            return None
        normalized_factors = []
        seen = set()
        for factor in raw.get("factors", [])[:5]:
            name = str((factor or {}).get("name", "")).strip().lower()
            if name in seen or name not in catalog_by_name:
                continue
            seen.add(name)
            length_min = max(2, min(500, int(factor.get("length_min", 7))))
            length_max = max(length_min, min(500, int(factor.get("length_max", 30))))
            weight_min = max(-3.0, min(3.0, float(factor.get("weight_min", 0.2))))
            weight_max = max(weight_min, min(3.0, float(factor.get("weight_max", 1.0))))
            if weight_min == weight_max == 0:
                weight_max = 1.0
            meta = catalog_by_name[name]
            normalized_factors.append({
                "name": name,
                "length_min": length_min,
                "length_max": length_max,
                "weight_min": weight_min,
                "weight_max": weight_max,
                "display_name": meta["display_name"],
                "category": meta["category"],
                "category_label": meta["category_label"],
                "research_theme": meta["research_theme"],
            })
        if not normalized_factors:
            return None
        return {
            "candidate_id": uuid.uuid4().hex[:10],
            "name": str(raw.get("name") or "Alpha 候选")[:80],
            "theme": str(raw.get("theme") or normalized_factors[0]["research_theme"])[:30],
            "hypothesis": str(raw.get("hypothesis") or "待验证的因子组合")[:500],
            "buy_logic": str(raw.get("buy_logic") or "Alpha 高于买入阈值")[:200],
            "sell_logic": str(raw.get("sell_logic") or "Alpha 低于卖出阈值")[:200],
            "factors": normalized_factors,
        }


class AlphaResearchService:
    def __init__(self, repository: Optional[AlphaResearchRepository] = None):
        self.repository = repository or AlphaResearchRepository()
        self.datasets = BacktestDatasetRepository(self.repository.storage)
        self.candidates = AlphaCandidateService()
        self.library = AlphaLibraryRepository(self.repository.storage)

    def context(self, user_id: int) -> Dict:
        return {
            "datasets": [
                item for item in self.datasets.list_for_user(user_id)
                if item["status"] == "ready"
            ],
            "factors": FactorCatalog().list(),
            "alpha_library": self.library.list_visible(user_id),
        }

    def create(self, user_id: int, payload: Dict) -> Dict:
        config = self._validate(user_id, payload)
        return self.repository.create(user_id, config)

    def _validate(self, user_id: int, payload: Dict) -> Dict:
        dataset_id = str(payload.get("dataset_id", "")).strip()
        dataset = self.datasets.get_visible(user_id, dataset_id)
        if dataset is None or dataset["status"] != "ready":
            raise ValueError("请选择已就绪且有权使用的历史数据集")
        time_zone = str(payload.get("time_zone", "Asia/Shanghai"))
        try:
            ZoneInfo(time_zone)
        except Exception as exc:
            raise ValueError("研究时区无效") from exc
        same_slot_lookback_days = int(payload.get("same_slot_lookback_days", 5))
        if same_slot_lookback_days not in {3, 5, 10, 20}:
            raise ValueError("同期观察交易日仅支持 3、5、10 或 20 天")
        factors = payload.get("factors") or []
        if not 1 <= len(factors) <= 5:
            raise ValueError("每个研究任务请选择 1-5 个因子")
        catalog_by_name = {item["name"]: item for item in FactorCatalog().list()}
        normalized_factors = []
        for factor in factors:
            name = str(factor.get("name", "")).strip().lower()
            if name not in catalog_by_name:
                raise ValueError(f"不支持的因子: {name}")
            length_min = int(factor.get("length_min", 7))
            length_max = int(factor.get("length_max", 30))
            weight_min = float(factor.get("weight_min", 0.2))
            weight_max = float(factor.get("weight_max", 1.0))
            if not 2 <= length_min <= length_max <= 500:
                raise ValueError(f"因子 {name} 的周期范围无效")
            if not -3 <= weight_min <= weight_max <= 3 or weight_min == weight_max == 0:
                raise ValueError(f"因子 {name} 的权重范围无效")
            meta = catalog_by_name[name]
            if meta.get("is_native"):
                if meta.get("supports_length"):
                    length_min = length_max = same_slot_lookback_days
                else:
                    length_min = length_max = 2
            normalized_factors.append({
                "name": name, "length_min": length_min, "length_max": length_max,
                "weight_min": weight_min, "weight_max": weight_max,
            })
        trial_count = int(payload.get("trial_count", 50))
        if not 5 <= trial_count <= 500:
            raise ValueError("Optuna 试验次数必须在 5-500 之间")
        research_mode = "ai" if payload.get("research_mode") == "ai" else "advanced"
        llm_iteration_count = int(payload.get("llm_iteration_count", 3))
        if not 1 <= llm_iteration_count <= 5:
            raise ValueError("大模型研究轮次必须在 1-5 之间")
        if research_mode != "ai":
            llm_iteration_count = 1
        timeframe = str(payload.get("timeframe", "M5")).upper()
        if timeframe not in {"M1", "M5", "M15", "M30", "H1", "H4"}:
            raise ValueError("回测周期无效")
        buy_min = float(payload.get("buy_threshold_min", 0.3))
        buy_max = float(payload.get("buy_threshold_max", 2.0))
        sell_min = float(payload.get("sell_threshold_min", -2.0))
        sell_max = float(payload.get("sell_threshold_max", -0.3))
        if not 0 < buy_min <= buy_max <= 5:
            raise ValueError("买入阈值范围无效")
        if not -5 <= sell_min <= sell_max < 0:
            raise ValueError("卖出阈值范围无效")
        raw_candidate = payload.get("candidate_meta") or {}
        candidate_meta = {
            key: str(raw_candidate.get(key, ""))[:limit]
            for key, limit in {
                "name": 80, "theme": 30, "hypothesis": 500,
                "buy_logic": 200, "sell_logic": 200,
            }.items()
        } if isinstance(raw_candidate, dict) else {}
        return {
            "research_name": str(payload.get("research_name", "")).strip()[:80] or "Alpha 因子研究",
            "research_description": str(payload.get("research_description", "")).strip()[:500],
            "research_mode": research_mode,
            "llm_iteration_count": llm_iteration_count,
            "candidate_meta": candidate_meta,
            "dataset_id": dataset_id,
            "timeframe": timeframe,
            "time_zone": time_zone,
            "same_slot_lookback_days": same_slot_lookback_days,
            "factors": normalized_factors,
            "prediction_horizon": max(1, min(500, int(payload.get("prediction_horizon", 15)))),
            "confirmation_bars": max(1, min(10, int(payload.get("confirmation_bars", 1)))),
            "cooldown_bars": max(0, min(500, int(payload.get("cooldown_bars", 0)))),
            "buy_threshold_min": buy_min,
            "buy_threshold_max": buy_max,
            "sell_threshold_min": sell_min,
            "sell_threshold_max": sell_max,
            "trial_count": trial_count,
            "random_seed": int(payload.get("random_seed", 42)),
        }

class AlphaResearchWorker:
    def __init__(self, storage: Optional[SQLiteStorage] = None, poll_seconds: float = 1.0):
        self.repository = AlphaResearchRepository(storage)
        self.poll_seconds = max(0.1, poll_seconds)
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self.repository.recover_stale()
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="alpha-research-worker", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=5)

    def run_once(self) -> bool:
        task = self.repository.claim_next()
        if task is None:
            return False
        run_id = task["run_id"]
        try:
            optimizer = AlphaOptimizationEngine(
                self.repository,
                progress_callback=lambda value: self.repository.update_progress(run_id, value),
                cancel_callback=lambda: self.repository.is_cancel_requested(run_id),
            )
            result, params, signals = optimizer.run(task)
            self.repository.complete(run_id, params, result, signals)
        except AlphaResearchCanceled as exc:
            self.repository.fail(run_id, str(exc), canceled=True)
        except Exception as exc:
            self.repository.fail(run_id, str(exc))
        return True

    def _loop(self) -> None:
        while not self._stop.is_set():
            if not self.run_once():
                self._stop.wait(self.poll_seconds)

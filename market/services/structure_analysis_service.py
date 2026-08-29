"""Asynchronous LLM structure-segment analysis for configured symbols/periods."""
import asyncio
import json
import re
from datetime import datetime
from typing import Dict, List

from sqlite_storage import RuntimeStateRepository


_repo = RuntimeStateRepository(0, 0)
_tasks = set()
_last_bars: Dict[str, str] = {}


def _key(symbol: str, period: str) -> str:
    return f"{str(symbol).strip()}::{str(period).upper()}"


def _symbol_base(symbol: str) -> str:
    """Normalize common broker suffixes for configuration matching."""
    value = str(symbol or "").strip().upper()
    while value.endswith(("M", "#", "_")):
        value = value[:-1]
    return value


def list_configs() -> List[Dict]:
    return _repo.list_entities("llm_structure_config")


def save_configs(items: List[Dict]) -> List[Dict]:
    normalized = []
    for item in items or []:
        symbol = str(item.get("symbol") or "").strip()
        period = str(item.get("period") or "M5").upper()
        if not symbol or period not in {"M1", "M5", "M15", "H1", "H4"}:
            continue
        value = {
            "symbol": symbol, "period": period,
            "enabled": bool(item.get("enabled", True)),
            "kline_count": max(50, min(288, int(item.get("kline_count", 288) or 288))),
            "updated_at": datetime.now().isoformat(),
        }
        _repo.upsert_entity("llm_structure_config", _key(symbol, period), value)
        normalized.append(value)
    return normalized


def _enabled(symbol: str, period: str):
    for item in list_configs():
        if (_symbol_base(item.get("symbol", "")) == _symbol_base(symbol)
                and item.get("period", "").upper() == period.upper()):
            return item if item.get("enabled") else None
    return None


def _extract_json(text: str):
    match = re.search(r"\{.*\}", text or "", re.S)
    if not match:
        return None
    try:
        return json.loads(match.group(0))
    except Exception:
        return None


async def _run(engine, symbol: str, period: str, rows: List[Dict], config: Dict):
    key = _key(symbol, period)
    count = int(config.get("kline_count", 288))
    sample = rows[-count:]
    _repo.upsert_entity("llm_structure_result", key, {"symbol": symbol, "period": period, "status": "queued", "queued_at": datetime.now().isoformat(), "kline_count": len(sample)})
    try:
        prompt = """你是行情结构识别器。以下K线按时间正序排列，请识别最近最多5个连续、互不重叠的结构段。
结构类型只能使用 uptrend、downtrend、range_rising、range_falling、range、breakout、transition。
只有至少连续两根K线确认结构变化后才切换类型，短暂影线或单根异常不要单独成段。
只输出JSON，不要Markdown或解释，格式必须为：
{"symbol":"实际品种","period":"周期","segments":[{"type":"uptrend","label":"上涨趋势","start_time":"","end_time":"","bar_count":0,"support":0,"resistance":0,"reason":""}],"current_structure":"","confidence":0,"summary":""}
"""
        payload = "\n".join(json.dumps(x, ensure_ascii=False) for x in sample)
        request = prompt + f"品种: {symbol} 周期: {period}\nK线:\n{payload}"
        response = None
        last_error = None
        for attempt in range(1, 4):
            _repo.upsert_entity("llm_structure_result", key, {"symbol": symbol, "period": period, "status": "running", "attempt": attempt, "started_at": datetime.now().isoformat(), "kline_count": len(sample)})
            try:
                response = await asyncio.wait_for(asyncio.to_thread(engine.llm_service.call_llm, request, scene_code="ai_signal_analysis", object_type="structure_analysis", object_id=key, max_tokens=3000), timeout=90)
                break
            except Exception as exc:
                last_error = exc
                if attempt < 3:
                    await asyncio.sleep(2 ** (attempt - 1))
        if response is None:
            raise RuntimeError(f"大模型调用失败（已重试3次）：{last_error}")
        result = response if isinstance(response, dict) else _extract_json(str(response))
        if not isinstance(result, dict):
            raise ValueError("模型返回不是有效JSON")
        result.update({"symbol": symbol, "period": period, "status": "ok", "analyzed_at": datetime.now().isoformat(), "kline_count": len(sample)})
        _repo.upsert_entity("llm_structure_result", key, result)
    except Exception as exc:
        _repo.upsert_entity("llm_structure_result", key, {"symbol": symbol, "period": period, "status": "error", "error": str(exc), "analyzed_at": datetime.now().isoformat(), "kline_count": len(sample)})


def trigger_if_configured(engine, symbol: str, period: str, rows: List[Dict]):
    config = _enabled(symbol, period)
    if not config or not rows:
        return False
    latest = str(rows[-1].get("timestamp") or rows[-1].get("time") or "")
    key = _key(symbol, period)
    if _last_bars.get(key) == latest or any(getattr(t, "_structure_key", None) == (key, latest) for t in _tasks):
        return False
    _last_bars[key] = latest
    task = asyncio.create_task(_run(engine, symbol, period, rows, config))
    print(f"[StructureAnalysis] 已触发大模型结构分析: {symbol} {period}, bars={len(rows)}")
    task._structure_key = (key, latest)
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)
    return True


def get_results() -> List[Dict]:
    return list(_repo.list_entities("llm_structure_result"))

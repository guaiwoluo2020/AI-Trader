import json
import re
import time

import requests

from sqlite_storage import AISignalSourceRepository, LLMConfigRepository


repo = AISignalSourceRepository()
storage = repo.storage
config = LLMConfigRepository().get_effective_config(1)
rows = storage.fetchall(
    "SELECT * FROM ai_signal_sources ORDER BY user_id, created_at, signal_source_id"
)

SYSTEM_PROMPT = (
    "你是交易系统提示词设计助手。根据用户的分析目标和当前 AI 信号源配置，"
    "生成一份可执行的行情分析提示词。只返回合法 JSON，不输出 Markdown。"
)


def parse_content(content):
    text = str(content or "").strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.I)
    return json.loads(text)


def generate(row):
    source = repo._row_to_dict(row)
    params = dict(source.get("config") or {})
    context = {
        "name": source["name"],
        "symbol": source["symbol"],
        "period": source["period"],
        "analysis_interval_minutes": int(params.get("analysis_interval_minutes") or 0),
        "kline_count": int(params.get("kline_count") or 0),
        "reference_market_data": params.get("reference_market_data") or [],
    }
    intent = (
        "围绕 {symbol}/{period} 主行情进行独立分析，基于最近 {kline_count} 根K线识别趋势、"
        "关键位置与满足条件的入场机会；行情证据不足时必须返回空交易建议，不能猜测。"
    ).format(**context)
    prompt = """请为下面的 AI 信号源生成一份专属提示词候选。

信号源配置：
{context}

用户希望：
{intent}

硬性要求：
1. 输出 JSON 对象，字段必须为 system_prompt、analysis_prompt_template、summary、assumptions。
2. analysis_prompt_template 必须保留 {{{{market_data}}}}；如果配置了参考行情，应保留 {{{{reference_market_data}}}}。
3. 不得出现 {{{{strategy_context}}}}、strategy_id 或任何策略级约束。
4. 只分析主品种和主周期；参考行情只能辅助判断，不能单独生成交易建议。
5. 必须要求纯 JSON 输出，并要求 trade_suggestions 中的建议携带 signal_source_id 与主周期 period。""".format(
        context=json.dumps(context, ensure_ascii=False), intent=intent
    )
    response = requests.post(
        f"{config.api_base.rstrip('/')}/chat/completions",
        headers={
            "Authorization": f"Bearer {config.api_key}",
            "Content-Type": "application/json",
        },
        json={
            "model": config.model,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ],
            "temperature": 0.3,
            "max_tokens": 4000,
            "response_format": {"type": "json_object"},
        },
        timeout=120,
    )
    if response.status_code != 200:
        raise RuntimeError(f"HTTP {response.status_code}: {response.text[:300]}")
    result = parse_content(
        (response.json().get("choices") or [{}])[0].get("message", {}).get("content")
    )
    generated_system = str(result.get("system_prompt") or "").strip()
    template = str(result.get("analysis_prompt_template") or "").strip()
    if not generated_system or not template:
        raise ValueError("缺少 system_prompt 或 analysis_prompt_template")
    if "{{market_data}}" not in template:
        raise ValueError("缺少 {{market_data}}")
    if "{{strategy_context}}" in template:
        raise ValueError("包含废弃的 {{strategy_context}}")
    if context["reference_market_data"] and "{{reference_market_data}}" not in template:
        raise ValueError("缺少 {{reference_market_data}}")
    params.update({
        "prompt_mode": "custom",
        "system_prompt": generated_system[:10000],
        "analysis_prompt_template": template[:50000],
        "prompt_generated_at": int(time.time()),
        "prompt_generator_model": config.model,
    })
    storage.execute(
        "UPDATE ai_signal_sources SET config_json = ?, updated_at = ? "
        "WHERE signal_source_id = ? AND user_id = ?",
        (
            json.dumps(params, ensure_ascii=False),
            int(time.time()),
            source["signal_source_id"],
            source["user_id"],
        ),
    )
    return {
        "id": source["signal_source_id"],
        "name": source["name"],
        "status": "updated",
        "template_length": len(template),
    }


report = []
for row in rows:
    source = repo._row_to_dict(row)
    try:
        report.append(generate(row))
    except Exception as exc:
        report.append({
            "id": source.get("signal_source_id"),
            "name": source.get("name"),
            "status": "failed",
            "error": str(exc)[:400],
        })

print(json.dumps(report, ensure_ascii=False))

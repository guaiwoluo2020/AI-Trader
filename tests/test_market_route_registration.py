"""接口路由拆分后的注册契约测试。

这些测试不启动数据库或 TradingEngine，只检查路由模块的静态注册结果，
避免重构后旧路由和新模块同时注册，或迁移接口意外丢失。
"""

import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _registered_routes(path: Path):
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    routes = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for decorator in node.decorator_list:
            if not isinstance(decorator, ast.Call):
                continue
            if not isinstance(decorator.func, ast.Attribute):
                continue
            if decorator.func.attr not in {"get", "post", "put", "delete", "patch", "websocket"}:
                continue
            if not decorator.args or not isinstance(decorator.args[0], ast.Constant):
                continue
            routes.append((decorator.func.attr.upper(), decorator.args[0].value, node.name))
    return routes


def test_migrated_market_routes_are_registered_once():
    files = sorted(ROOT.glob("routes_market*.py")) + [ROOT / "routes_structure_plans.py"]
    routes = [route for path in files for route in _registered_routes(path)]
    keys = [(method, path) for method, path, _ in routes]

    duplicates = {key for key in keys if keys.count(key) > 1}
    assert not duplicates, f"发现重复注册路由: {sorted(duplicates)}"

    expected = {
        ("GET", "/market/structure/{symbol}"),
        ("GET", "/market/structure/{symbol}/trade-plans"),
        ("GET", "/market/structure/{symbol}/signal-reviews"),
        ("GET", "/market/pivots/{symbol}"),
        ("GET", "/admin/market-structure/config"),
        ("PUT", "/admin/market-structure/config"),
        ("WEBSOCKET", "/ws/market"),
        ("WEBSOCKET", "/ws/system-logs"),
    }
    assert expected <= set(keys)


def test_routes_market_has_no_migrated_legacy_handlers():
    tree = ast.parse((ROOT / "routes_market.py").read_text(encoding="utf-8"))
    names = {
        node.name
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    migrated = {
        "get_market_structure",
        "get_structure_trade_plans",
        "get_structure_signal_reviews",
        "get_market_structure_config",
        "put_market_structure_config",
        "get_pivots",
        "websocket_market",
        "websocket_system_logs",
    }
    assert not (migrated & names)

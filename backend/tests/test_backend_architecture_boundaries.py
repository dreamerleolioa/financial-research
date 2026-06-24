from __future__ import annotations

import ast
from pathlib import Path


BACKEND_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = BACKEND_ROOT / "src" / "ai_stock_sentinel"

PURE_DOMAIN_MODULES = (
    SRC_ROOT / "technical" / "metrics.py",
    SRC_ROOT / "technical" / "profile.py",
    SRC_ROOT / "analysis" / "metrics.py",
    SRC_ROOT / "analysis" / "confidence_scorer.py",
    SRC_ROOT / "analysis" / "position_scorer.py",
    SRC_ROOT / "daily_radar" / "cooldown.py",
    SRC_ROOT / "daily_radar" / "explanations.py",
    SRC_ROOT / "daily_radar" / "prefilter.py",
    SRC_ROOT / "daily_radar" / "relative_strength.py",
    SRC_ROOT / "daily_radar" / "scoring.py",
    SRC_ROOT / "portfolio" / "fees.py",
    SRC_ROOT / "portfolio" / "risk_summary.py",
)

REFACTORED_HTTP_BOUNDARIES = (
    SRC_ROOT / "api.py",
    SRC_ROOT / "analysis" / "router.py",
    SRC_ROOT / "daily_radar" / "router.py",
    SRC_ROOT / "portfolio" / "history_router.py",
    SRC_ROOT / "portfolio" / "router.py",
    SRC_ROOT / "watchlist" / "router.py",
)

PURE_DOMAIN_BANNED_IMPORTS = (
    "fastapi",
    "sqlalchemy",
    "requests",
    "httpx",
    "yfinance",
    "ai_stock_sentinel.data_sources",
    "ai_stock_sentinel.db",
)

DAILY_RADAR_ROUTER_PRESENTATION_HELPERS = {
    "_background_context_labels",
    "_candidate_response",
    "_date_mapping",
    "_history_response",
    "_matched_rules",
    "_ordered_candidates",
    "_public_run_response",
    "_run_data_dates",
    "_run_market_context",
    "_run_response",
    "_stored_display_name",
    "_trace_version",
}


def test_pure_domain_modules_do_not_import_framework_db_or_external_providers() -> None:
    violations: list[str] = []
    for path in PURE_DOMAIN_MODULES:
        imported = _imported_modules(path)
        banned = _matching_imports(imported, PURE_DOMAIN_BANNED_IMPORTS)
        if banned:
            violations.append(f"{_rel(path)} imports {', '.join(sorted(banned))}")

    assert violations == []


def test_refactored_http_boundaries_do_not_define_pydantic_schemas() -> None:
    violations: list[str] = []
    for path in REFACTORED_HTTP_BOUNDARIES:
        tree = _parse(path)
        base_model_names = _base_model_aliases(tree)
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef) and _inherits_from(node, base_model_names):
                violations.append(f"{_rel(path)} defines Pydantic schema class {node.name}")

    assert violations == []


def test_daily_radar_router_keeps_public_response_presenters_outside_router() -> None:
    router_path = SRC_ROOT / "daily_radar" / "router.py"
    tree = _parse(router_path)
    defined_functions = {node.name for node in ast.walk(tree) if isinstance(node, ast.FunctionDef)}
    leaked_helpers = sorted(defined_functions & DAILY_RADAR_ROUTER_PRESENTATION_HELPERS)

    assert leaked_helpers == []


def _parse(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def _imported_modules(path: Path) -> set[str]:
    tree = _parse(path)
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            modules.add(node.module)
    return modules


def _matching_imports(imported: set[str], banned_prefixes: tuple[str, ...]) -> set[str]:
    return {
        module
        for module in imported
        if any(module == banned or module.startswith(f"{banned}.") for banned in banned_prefixes)
    }


def _base_model_aliases(tree: ast.Module) -> set[str]:
    aliases = {"BaseModel"}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == "pydantic":
            for alias in node.names:
                if alias.name == "BaseModel":
                    aliases.add(alias.asname or alias.name)
    return aliases


def _inherits_from(node: ast.ClassDef, base_model_names: set[str]) -> bool:
    for base in node.bases:
        if isinstance(base, ast.Name) and base.id in base_model_names:
            return True
        if isinstance(base, ast.Attribute) and base.attr == "BaseModel":
            return True
    return False


def _rel(path: Path) -> str:
    return str(path.relative_to(BACKEND_ROOT))

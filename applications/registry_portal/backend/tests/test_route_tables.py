"""Lambda のルート表と、共通の経路表（infrastructure/modules/api_routes/routes.json）を照合する。

経路を足すときは、経路表（API Gateway のメソッドと統合）と app.py のルート表の両方を直す。
片方だけを直すと、API Gateway が Lambda に届けても 404 になるか、Lambda の経路に要求が届かない。
経路の一覧はこの 2 か所だけに置き、テストには経路を書き写さない。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast

from registry_portal.app import (
    CORS_ALLOWED_METHODS,
    create_curation_handler,
    create_management_handler,
)

from catalog_helpers import ENV

ROUTES_JSON = Path(__file__).resolve().parents[2] / "infrastructure/modules/api_routes/routes.json"
ROUTE_GROUPS: dict[str, dict[str, dict[str, str]]] = json.loads(ROUTES_JSON.read_text())


def _route_keys(*groups: str) -> set[str]:
    return {
        f"{route['method']} {route['path']}"
        for group in groups
        for route in ROUTE_GROUPS[group].values()
    }


def test_management_lambda_serves_exactly_the_main_and_management_routes() -> None:
    # 本体スタック（main）と管理スタック（management）の経路は、どちらも管理 Lambda に統合する
    handler = create_management_handler(ENV)

    assert handler.route_keys == _route_keys("main", "management")


def test_decision_lambda_serves_exactly_the_decision_routes() -> None:
    # AWS クライアントは作るだけで呼ばないため、ここでは中身のない値を渡す
    handler = create_curation_handler(
        {**ENV, "SUPPLEMENT_TABLE_NAME": "registry-portal-supplement"},
        registry_client=cast(Any, object()),
        store_client=cast(Any, object()),
    )

    assert handler.route_keys == _route_keys("decision")


def test_preflight_allows_exactly_the_methods_in_the_route_table() -> None:
    # プリフライトは管理 Lambda がすべてのリソースについて答えるため、決裁の経路のメソッドも含める
    allowed = {method.strip() for method in CORS_ALLOWED_METHODS.split(",")}
    used = {route["method"] for group in ROUTE_GROUPS.values() for route in group.values()}

    assert allowed == used | {"OPTIONS"}

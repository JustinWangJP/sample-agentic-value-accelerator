"""画面上の通知（#15）の Seam 1 テストで共有する EventBridge イベントと handler の補助。"""

from __future__ import annotations

from typing import Any

from catalog_helpers import REGISTRY_ARN, SUPPLEMENT_TABLE
from draft_helpers import RECORD_ARN, RECORD_ID
from registry_portal.app import create_event_handler

REGISTRY_ID = REGISTRY_ARN.rsplit("/", 1)[1]


def event_handler(registry: Any, db: Any) -> Any:
    """イベント処理 handler。認証設定は持たず、Registry と補助テーブルだけを設定する。"""
    return create_event_handler(
        {"REGISTRY_ARN": REGISTRY_ARN, "SUPPLEMENT_TABLE_NAME": SUPPLEMENT_TABLE},
        registry_client=registry,
        store_client=db,
    )


def registry_event(
    state: str,
    *,
    event_id: str,
    record_id: str = RECORD_ID,
    registry_id: str = REGISTRY_ID,
    source: str = "aws.agent-registry",
) -> dict[str, Any]:
    """Registry が EventBridge の default バスに送るレコードのイベント（AWS の例と同じ形）。"""
    return {
        "version": "0",
        "id": event_id,
        "detail-type": f"Registry Record State changed to {state}",
        "source": source,
        "account": "123456789012",
        "time": "2026-11-14T00:00:00Z",
        "region": "us-east-1",
        "resources": [RECORD_ARN.replace(RECORD_ID, record_id)],
        "detail": {"registryRecordId": record_id, "registryId": registry_id},
    }

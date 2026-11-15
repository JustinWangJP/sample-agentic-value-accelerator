"""Curator の管理レコード検索（GET /api/records/publication、#34）の Seam 1 テスト。

- Curator が管理下のレコードを名前の一部と状態で探し、公開管理を開く対象の一覧にする
- 状態の語彙は AWS の状態に ポータルの SUSPENDED（公開停止中）を加えた閉じた集合
- 未管理レコードは含めない（Admin の取り込み workflow の対象）
- Registry・補助ストアの読み取りの失敗は空の結果にせず 502 として区別する
"""

from datetime import datetime, timezone

from botocore.stub import Stubber
from moto import mock_aws

from catalog_helpers import ENV, REGISTRY_ARN, SUPPLEMENT_TABLE
from decision_helpers import CURATOR, put
from draft_helpers import call, clients, event
from registry_portal.app import create_curation_handler

# 一覧の項目のキー。record_summary（curation_reads.py）に suspended と publishedVersion を加えた形
ITEM_KEYS = {
    "recordId",
    "name",
    "displayName",
    "recordType",
    "recordVersion",
    "status",
    "managed",
    "ownerTeam",
    "suspended",
    "publishedVersion",
}


def listed_record(
    record_id,
    name,
    *,
    display_name=None,
    status="APPROVED",
    record_type="AGENT",
    record_version="1.0.0",
):
    """ListRegistryRecords の応答に並ぶ、レコードの要約。"""
    return {
        "registryArn": REGISTRY_ARN,
        "recordArn": f"{REGISTRY_ARN}/record/{record_id}",
        "recordId": record_id,
        "name": name,
        "displayName": display_name if display_name is not None else name,
        "description": "説明",
        "recordType": record_type,
        "recordVersion": record_version,
        "status": status,
        "createdAt": datetime(2026, 11, 1, tzinfo=timezone.utc),
        "updatedAt": datetime(2026, 11, 2, tzinfo=timezone.utc),
    }


def stub_publication_list(stub, records, *, status=None, token=None, next_token=None):
    """ListRegistryRecords の応答を積む。expected_params でポータル側の検索条件を検査する。"""
    filters = [{"name": "recordType", "values": ["AGENT", "MCP", "SKILL"]}]
    if status is not None:
        filters.append({"name": "status", "values": [status]})
    params = {"registryId": REGISTRY_ARN, "maxResults": 20, "filters": filters}
    if token is not None:
        params["nextToken"] = token
    response = {"registryRecords": records}
    if next_token is not None:
        response["nextToken"] = next_token
    stub.add_response("list_registry_records", response, params)


def managed(db, record_id, **changes):
    put(db, "MANAGEMENT", {"managed": True, "ownerTeam": "team-payments", **changes}, record_id)


def decision_for(registry, db):
    return create_curation_handler(
        {**ENV, "SUPPLEMENT_TABLE_NAME": SUPPLEMENT_TABLE},
        registry_client=registry,
        store_client=db,
    )


def search(handler, query=None, *, groups=CURATOR):
    request = event("GET", "/api/records/publication", groups=groups, key="", sub="carol")
    if query is not None:
        request["queryStringParameters"] = query
    return call(handler, request)


@mock_aws
def test_curator_lists_managed_records_with_suspension_and_published_version():
    """管理下のレコードだけを、公開停止の区別と発見できる版とともに並べる。"""
    registry, db = clients()
    with Stubber(registry) as stub:
        decision = decision_for(registry, db)
        adopted = listed_record("agentABC1234", "payments-agent", record_version="2.0.0")
        unmanaged = listed_record("mcpEFGH5678I", "outside-mcp", record_type="MCP")
        managed(db, "agentABC1234")
        stub_publication_list(stub, [adopted, unmanaged])
        status, body = search(decision)
        assert status == 200, body
        assert body["nextToken"] is None
        assert len(body["items"]) == 1
        item = body["items"][0]
        assert set(item) == ITEM_KEYS
        assert item["recordId"] == "agentABC1234"
        assert item["managed"] is True
        assert item["ownerTeam"] == "team-payments"
        assert item["suspended"] is False
        assert item["publishedVersion"] == "2.0.0"
        stub.assert_no_pending_responses()

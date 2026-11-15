"""Curator の管理レコード検索（GET /api/records/publication、#34）の Seam 1 テスト。

- Curator が管理下のレコードを名前の一部と状態で探し、公開管理を開く対象の一覧にする
- 状態の語彙は AWS の状態に ポータルの SUSPENDED（公開停止中）を加えた閉じた集合
- 未管理レコードは含めない（Admin の取り込み workflow の対象）
- Registry・補助ストアの読み取りの失敗は空の結果にせず 502 として区別する
"""

from datetime import datetime, timezone

from botocore.exceptions import EndpointConnectionError
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
    """管理下のレコードだけを、公開停止の区別と公開中の版とともに並べる。"""
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


@mock_aws
def test_name_filter_matches_name_and_display_name_case_insensitively():
    """name は name と displayName に対する、大文字小文字を区別しない部分一致。絞り込みはポータル側。"""
    registry, db = clients()
    with Stubber(registry) as stub:
        decision = decision_for(registry, db)
        by_name = listed_record("agentABC1234", "payments-agent")
        by_display = listed_record(
            "mcpEFGH5678I", "logging-mcp", display_name="Payments Logging", record_type="MCP"
        )
        unrelated = listed_record("sklEFGH5678I", "translate-skill", record_type="SKILL")
        for record_id in ("agentABC1234", "mcpEFGH5678I", "sklEFGH5678I"):
            managed(db, record_id)
        # AWS の name フィルターは一致方法が文書化されていないため、ポータル側で絞り込む
        stub_publication_list(stub, [by_name, by_display, unrelated])
        status, body = search(decision, {"name": "PAYMENTS"})
        assert status == 200, body
        assert [item["recordId"] for item in body["items"]] == ["agentABC1234", "mcpEFGH5678I"]
        stub.assert_no_pending_responses()


@mock_aws
def test_status_filter_distinguishes_suspension_from_rejection():
    """status は閉じた集合。SUSPENDED は公開停止中、REJECTED は却下済みだけに一致する。"""
    registry, db = clients()
    with Stubber(registry) as stub:
        decision = decision_for(registry, db)
        suspended = listed_record("agentABC1234", "payments-agent", status="REJECTED")
        rejected = listed_record(
            "mcpEFGH5678I", "logging-mcp", status="REJECTED", record_type="MCP"
        )
        draft = listed_record(
            "sklEFGH5678I", "translate-skill", status="DRAFT", record_type="SKILL"
        )
        managed(db, "agentABC1234", suspendedVersion="2.0.0")
        managed(db, "mcpEFGH5678I")
        managed(db, "sklEFGH5678I")
        # SUSPENDED は管理情報の印で判断するため、AWS には状態を渡さない
        stub_publication_list(stub, [suspended, rejected, draft])
        status, body = search(decision, {"status": "SUSPENDED"})
        assert status == 200, body
        assert [item["recordId"] for item in body["items"]] == ["agentABC1234"]
        assert body["items"][0]["suspended"] is True
        assert body["items"][0]["publishedVersion"] is None
        # REJECTED は AWS の状態で問い合わせ、公開停止中は除外する
        stub_publication_list(stub, [suspended, rejected], status="REJECTED")
        status, body = search(decision, {"status": "REJECTED"})
        assert status == 200, body
        assert [item["recordId"] for item in body["items"]] == ["mcpEFGH5678I"]
        assert body["items"][0]["suspended"] is False
        # それ以外は AWS の状態との完全一致
        stub_publication_list(stub, [draft], status="DRAFT")
        status, body = search(decision, {"status": "DRAFT"})
        assert status == 200, body
        assert [item["recordId"] for item in body["items"]] == ["sklEFGH5678I"]
        stub.assert_no_pending_responses()


@mock_aws
def test_record_deprecated_after_suspension_is_no_longer_suspended():
    """公開停止の後に廃止したレコードは、印が残っていても公開停止中に数えない（廃止は終端の状態）。"""
    registry, db = clients()
    with Stubber(registry) as stub:
        decision = decision_for(registry, db)
        retired = listed_record("agentABC1234", "payments-agent", status="DEPRECATED")
        # 廃止は公開停止の印を消さない（印を消すのはポータルの公開承認だけ）
        managed(db, "agentABC1234", suspendedVersion="2.0.0")
        stub_publication_list(stub, [retired])
        status, body = search(decision, {"status": "SUSPENDED"})
        assert status == 200, body
        assert body["items"] == []
        stub_publication_list(stub, [retired], status="DEPRECATED")
        status, body = search(decision, {"status": "DEPRECATED"})
        assert status == 200, body
        assert [item["recordId"] for item in body["items"]] == ["agentABC1234"]
        assert body["items"][0]["suspended"] is False
        assert body["items"][0]["publishedVersion"] is None
        stub.assert_no_pending_responses()


@mock_aws
def test_search_checks_role_and_rejects_unknown_keys_and_invalid_values():
    """Curator 以外は 403。未知のクエリキー・不正な値は 400（AWS を呼ばない）。"""
    registry, db = clients()
    with Stubber(registry) as stub:
        decision = decision_for(registry, db)
        for groups in ("", "registry-publisher team-payments", "registry-admin"):
            status, body = search(decision, groups=groups)
            assert status == 403, (groups, body)
        invalid = (
            {"team": "team-payments"},
            {"status": "PUBLISHED"},
            {"status": "suspended"},
            {"name": ""},
            {"name": "a" * 256},
            {"nextToken": "bad token"},
        )
        for query in invalid:
            status, body = search(decision, query)
            assert status == 400, (query, body)
            assert body["error"]["code"] == "VALIDATION_ERROR"
        stub.assert_no_pending_responses()


@mock_aws
def test_search_relays_the_aws_page_token_and_reports_read_failures_as_502():
    """ポータル側で絞り込むため、items が0件でも nextToken を保持する。読み取りの失敗は 502。"""
    registry, db = clients()
    with Stubber(registry) as stub:
        decision = decision_for(registry, db)
        matching = listed_record("agentABC1234", "payments-agent")
        managed(db, "agentABC1234")
        # 1ページ目は name が一致しないレコードを返し、次のページを指す
        stub_publication_list(
            stub, [listed_record("mcpEFGH5678I", "logging-mcp")], next_token="next-page"
        )
        status, body = search(decision, {"name": "payments"})
        assert status == 200, body
        assert body["items"] == []
        assert body["nextToken"] == "next-page"
        # 2ページ目は nextToken を AWS にそのまま渡す
        stub_publication_list(stub, [matching], token="next-page")
        status, body = search(decision, {"name": "payments", "nextToken": "next-page"})
        assert status == 200, body
        assert [item["recordId"] for item in body["items"]] == ["agentABC1234"]
        assert body["nextToken"] is None
        # Registry を読めないときは空の結果にせず 502
        stub.add_client_error("list_registry_records", "AccessDeniedException")
        status, body = search(decision)
        assert status == 502
        assert body["error"]["code"] == "REGISTRY_UNAVAILABLE"
        # 補助ストアを読めないときも 502
        stub_publication_list(stub, [matching])

        def unreachable(**kwargs):
            raise EndpointConnectionError(endpoint_url="https://dynamodb.example")

        db.get_item = unreachable
        status, body = search(decision)
        assert status == 502
        assert body["error"]["code"] == "REGISTRY_UNAVAILABLE"
        stub.assert_no_pending_responses()

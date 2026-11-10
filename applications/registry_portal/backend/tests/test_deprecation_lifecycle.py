"""廃止依頼・公開停止・廃止（#13）の Seam 1 テスト。

- 廃止依頼は、対象の所有チームを所属チームに持つ Publisher だけが行える
- 廃止依頼は審査待ちの一覧に申請と並んで表示される
- 公開停止は却下（REJECTED）による非公開化として実行する
- 廃止は元に戻せないことを明示し、確認（confirm）を必須にする
- Publisher と Viewer は公開停止や廃止を実行できない
"""

from datetime import datetime, timezone

from botocore.stub import Stubber
from moto import mock_aws

from catalog_helpers import ISSUER, REGISTRY_ARN, SUPPLEMENT_TABLE
from decision_helpers import (
    CURATOR,
    GET,
    RECORD,
    REASON,
    decide,
    put,
    setup_pending,
    stored,
    stub_status_update,
)
from draft_helpers import RECORD_ID, call, clients, event
from record_fixtures import CUSTOM_DRAFT


def deprecation_pk_item(db):
    from boto3.dynamodb.types import TypeDeserializer

    item = db.get_item(
        TableName=SUPPLEMENT_TABLE,
        Key={
            "pk": {"S": f"REGISTRY#{REGISTRY_ARN}#DEPRECATION"},
            "sk": {"S": RECORD_ID},
        },
    ).get("Item")
    return None if item is None else {k: TypeDeserializer().deserialize(v) for k, v in item.items()}


def stub_get(stub, status):
    stub.add_response("get_registry_record", {**RECORD, "status": status}, GET)


def request_deprecation(
    management,
    *,
    reason="利用が終わったため",
    groups="registry-publisher team-payments",
    sub="alice",
):
    return call(
        management,
        event(
            "POST",
            f"/api/records/{RECORD_ID}/deprecation-requests",
            {"reason": reason},
            groups=groups,
            sub=sub,
            key="",
        ),
    )


def approve(stub, state):
    """審査待ちの申請を公開承認して、公開中（APPROVED）の状態を作る。"""
    stub.add_response("get_registry_record", {**RECORD, "status": "PENDING_APPROVAL"}, GET)
    stub_status_update(stub, approved=True)
    stub.add_response("get_registry_record", {**RECORD, "status": "APPROVED"}, GET)
    status, body = decide(state["decision"], snapshot_hash=state["snapshot"]["snapshotHash"])
    assert status == 200 and body["operationState"] == "SUCCEEDED"


@mock_aws
def test_publisher_of_the_owner_team_requests_deprecation():
    registry, db = clients()
    with Stubber(registry) as stub:
        state = setup_pending(stub, registry, db)
        approve(stub, state)
        stub_get(stub, "APPROVED")
        status, body = request_deprecation(state["management"])
        assert status == 200
        assert body["state"] == "OPEN"
        assert body["requestedBy"] == {"issuer": ISSUER, "subject": "alice"}
        assert body["reason"] == "利用が終わったため"
        stored_request = deprecation_pk_item(db)
        assert stored_request["state"] == "OPEN"
        assert stored_request["recordId"] == RECORD_ID
        stub.assert_no_pending_responses()


@mock_aws
def test_deprecation_request_requires_owner_team_publisher():
    registry, db = clients()
    with Stubber(registry) as stub:
        state = setup_pending(stub, registry, db)
        approve(stub, state)
        stub_get(stub, "APPROVED")
        # 所属チームのない Publisher、他チームの Publisher、Curator、Viewer は 403
        for groups in (
            "registry-publisher",
            "registry-publisher team-other",
            CURATOR,
            "",
        ):
            status, body = request_deprecation(state["management"], groups=groups)
            assert status == 403, (groups, body)
            assert body["error"]["code"] == "FORBIDDEN"
        stub.assert_no_pending_responses()


@mock_aws
def test_deprecation_request_validates_body_and_rejects_conflicts():
    registry, db = clients()
    with Stubber(registry) as stub:
        state = setup_pending(stub, registry, db)
        approve(stub, state)
        # 本文の検査は AWS を呼ぶ前に行う
        for body in ({}, {"reason": ""}, {"reason": "x" * 256}, {"reason": "r", "extra": 1}):
            status, result = call(
                state["management"],
                event(
                    "POST",
                    f"/api/records/{RECORD_ID}/deprecation-requests",
                    body,
                    key="",
                ),
            )
            assert status == 400, result
        status, _ = call(
            state["management"],
            event(
                "POST",
                f"/api/records/{RECORD_ID}/deprecation-requests",
                {"reason": "password=hunter2 のため終了"},
                key="",
            ),
        )
        assert status == 400
        stub_get(stub, "APPROVED")
        status, _ = request_deprecation(state["management"])
        assert status == 200
        # 同じレコードへの未解決の依頼の重複は 409
        stub_get(stub, "APPROVED")
        status, body = request_deprecation(state["management"], reason="もう一度依頼")
        assert status == 409
        assert body["error"]["code"] == "CONTENT_CONFLICT"
        stub.assert_no_pending_responses()


@mock_aws
def test_deprecation_request_rejects_records_not_open_to_requests():
    registry, db = clients()
    with Stubber(registry) as stub:
        state = setup_pending(stub, registry, db)
        # 廃止済みは依頼できない
        stub_get(stub, "DEPRECATED")
        status, body = request_deprecation(state["management"])
        assert status == 409
        # ポータルの管理下にないレコードは 404
        put(db, "MANAGEMENT", {"managed": False, "ownerTeam": "team-payments"})
        stub_get(stub, "APPROVED")
        status, body = request_deprecation(state["management"])
        assert status == 404
        stub.assert_no_pending_responses()


@mock_aws
def test_review_list_shows_deprecation_requests_alongside_submissions():
    registry, db = clients()
    with Stubber(registry) as stub:
        state = setup_pending(stub, registry, db)
        approve(stub, state)
        stub_get(stub, "APPROVED")
        request_deprecation(state["management"])
        other = {
            "registryArn": REGISTRY_ARN,
            "recordArn": f"{REGISTRY_ARN}/record/mcpEFGH5678I",
            "recordId": "mcpEFGH5678I",
            "name": "payments-mcp",
            "displayName": "決済 MCP",
            "description": "説明",
            "recordType": "MCP",
            "recordVersion": "1.0.0",
            "status": "PENDING_APPROVAL",
            "createdAt": datetime(2026, 11, 1, tzinfo=timezone.utc),
            "updatedAt": datetime(2026, 11, 2, tzinfo=timezone.utc),
        }
        stub.add_response(
            "list_registry_records",
            {"registryRecords": [other]},
            {
                "registryId": REGISTRY_ARN,
                "maxResults": 20,
                "filters": [{"name": "status", "values": ["PENDING_APPROVAL"]}],
            },
        )
        stub_get(stub, "APPROVED")
        status, body = call_reviews(state["decision"])
        assert status == 200
        assert [item["reviewKind"] for item in body["items"]] == [
            "SUBMISSION",
            "DEPRECATION_REQUEST",
        ]
        submission, request = body["items"]
        assert submission["recordId"] == "mcpEFGH5678I"
        assert request["recordId"] == RECORD_ID
        assert request["status"] == "APPROVED"
        assert request["ownerTeam"] == "team-payments"
        assert request["reason"] == "利用が終わったため"
        assert request["requestedBy"] == {"issuer": ISSUER, "subject": "alice"}
        assert request["requestedAt"]
        assert request["managed"] is True
        stub.assert_no_pending_responses()


def call_reviews(decision):
    return call(decision, event("GET", "/api/reviews", groups=CURATOR, key="", sub="carol"))


def suspend(decision, body=None, *, groups=CURATOR, sub="carol", key="suspend-1"):
    return call(
        decision,
        event(
            "POST",
            f"/api/records/{RECORD_ID}/suspend",
            body if body is not None else {"reason": "問題が見つかったため"},
            groups=groups,
            sub=sub,
            key=key,
        ),
    )


def deprecate(decision, body=None, *, groups=CURATOR, sub="carol", key="deprecate-1"):
    return call(
        decision,
        event(
            "POST",
            f"/api/records/{RECORD_ID}/deprecate",
            body if body is not None else {"reason": "利用終了のため", "confirm": True},
            groups=groups,
            sub=sub,
            key=key,
        ),
    )


@mock_aws
def test_curator_suspends_the_published_record_as_a_rejection():
    registry, db = clients()
    with Stubber(registry) as stub:
        state = setup_pending(stub, registry, db)
        approve(stub, state)
        stub_get(stub, "APPROVED")
        request_deprecation(state["management"])
        stub_get(stub, "APPROVED")
        stub_status_update(stub, approved=False, reason="問題が見つかったため")
        stub_get(stub, "REJECTED")
        status, body = suspend(state["decision"])
        assert status == 200
        assert body["operationState"] == "SUCCEEDED"
        # 公開停止は却下による非公開化。再公開には編集と再申請が必要
        assert body["status"] == "REJECTED"
        assert body["publishedVersion"] == CUSTOM_DRAFT["recordVersion"]
        assert body["submissionId"] is None and body["snapshotHash"] is None
        # 未解決の廃止依頼はこの決裁で解決済みになる
        stored_request = deprecation_pk_item(db)
        assert stored_request["state"] == "RESOLVED"
        assert stored_request["resolution"] == "SUSPENDED"
        assert stored_request["resolvedBy"] == {"issuer": ISSUER, "subject": "carol"}
        # 公開版スナップショットは書き換えない
        assert (
            stored(
                db,
                f'PUBLISHED#{CUSTOM_DRAFT["recordVersion"]}',
            )
            is not None
        )
        stub.assert_no_pending_responses()


@mock_aws
def test_suspend_rejects_non_published_records_and_bad_bodies():
    registry, db = clients()
    with Stubber(registry) as stub:
        state = setup_pending(stub, registry, db)
        # 公開中でなければ 409（AWS の状態更新は呼ばない）
        stub_get(stub, "DRAFT")
        status, body = suspend(state["decision"])
        assert status == 409
        assert body["error"]["code"] == "CONTENT_CONFLICT"
        stub.assert_no_pending_responses()


@mock_aws
def test_suspend_and_deprecate_require_the_curator_role_and_a_key():
    registry, db = clients()
    with Stubber(registry) as stub:
        state = setup_pending(stub, registry, db)
        approve(stub, state)
        for groups in ("", "registry-publisher team-payments", "registry-admin"):
            for path in ("suspend", "deprecate"):
                status, body = call(
                    state["decision"],
                    event(
                        "POST",
                        f"/api/records/{RECORD_ID}/{path}",
                        {"reason": REASON},
                        groups=groups,
                        key="k-1",
                    ),
                )
                assert status == 403, (groups, path, body)
        # Idempotency-Key は必須
        status, body = call(
            state["decision"],
            event(
                "POST",
                f"/api/records/{RECORD_ID}/suspend",
                {"reason": REASON},
                groups=CURATOR,
                key="",
            ),
        )
        assert status == 400
        # 廃止は確認を必須にする。confirm がないときは項目の指定を、false のときは不可逆性を伝える
        status, body = call(
            state["decision"],
            event(
                "POST",
                f"/api/records/{RECORD_ID}/deprecate",
                {"reason": REASON},
                groups=CURATOR,
                key="k-2",
            ),
        )
        assert status == 400 and "confirm" in body["error"]["message"]
        status, body = call(
            state["decision"],
            event(
                "POST",
                f"/api/records/{RECORD_ID}/deprecate",
                {"reason": REASON, "confirm": False},
                groups=CURATOR,
                key="k-3",
            ),
        )
        assert status == 400
        assert "元に戻せません" in body["error"]["message"]
        stub.assert_no_pending_responses()


@mock_aws
def test_curator_deprecates_with_confirmation_and_it_is_irreversible():
    registry, db = clients()
    with Stubber(registry) as stub:
        state = setup_pending(stub, registry, db)
        approve(stub, state)
        stub_get(stub, "APPROVED")
        request_deprecation(state["management"])
        stub_get(stub, "APPROVED")
        stub.add_response(
            "update_registry_record_status",
            {
                "registryArn": REGISTRY_ARN,
                "recordArn": f"{REGISTRY_ARN}/record/{RECORD_ID}",
                "recordId": RECORD_ID,
                "status": "DEPRECATED",
                "statusReason": "利用終了のため",
                "updatedAt": RECORD["updatedAt"],
                "ResponseMetadata": {"RequestId": "aws-deprecate-1"},
            },
            {**GET, "status": "DEPRECATED", "statusReason": "利用終了のため"},
        )
        stub_get(stub, "DEPRECATED")
        status, body = deprecate(state["decision"])
        assert status == 200
        assert body["operationState"] == "SUCCEEDED"
        assert body["status"] == "DEPRECATED"
        stored_request = deprecation_pk_item(db)
        assert stored_request["state"] == "RESOLVED"
        assert stored_request["resolution"] == "DEPRECATED"
        stub.assert_no_pending_responses()


@mock_aws
def test_suspend_resolves_without_an_open_request():
    registry, db = clients()
    with Stubber(registry) as stub:
        state = setup_pending(stub, registry, db)
        approve(stub, state)
        stub_get(stub, "APPROVED")
        stub_status_update(stub, approved=False, reason="問題が見つかったため")
        stub_get(stub, "REJECTED")
        status, body = suspend(state["decision"])
        assert status == 200
        assert deprecation_pk_item(db) is None
        stub.assert_no_pending_responses()

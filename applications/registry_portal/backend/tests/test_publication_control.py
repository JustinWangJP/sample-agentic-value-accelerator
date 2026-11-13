"""廃止依頼・公開停止・廃止・依頼の見送り・公開管理の照会（#13）の Seam 1 テスト。

- 廃止依頼は、対象の所有チームを所属チームに持つ Publisher だけが行える
- 廃止依頼は審査待ちの一覧に申請と並んで表示される（最初のページだけ、古い順）
- 公開停止は却下（REJECTED）で発見できなくする。依頼は閉じず、再公開には編集と再申請が必要
- 廃止は元に戻せないことを明示し、確認（confirm）を必須にする。どの状態からも廃止できる
- 依頼は廃止か見送りでだけ閉じる。どちらも履歴に残る
- Publisher と Viewer は公開停止・廃止・見送りを実行できない
"""

from datetime import datetime, timezone

from botocore.exceptions import EndpointConnectionError
from botocore.stub import Stubber
from boto3.dynamodb.types import TypeDeserializer, TypeSerializer
from moto import mock_aws

from catalog_helpers import ENV, ISSUER, REGISTRY_ARN, SUPPLEMENT_TABLE
from decision_helpers import (
    CURATOR,
    GET,
    META,
    RECORD,
    REASON,
    decide,
    missing_published,
    put,
    setup_pending,
    stored,
    stub_status_update,
    stub_submit,
)
from draft_helpers import RECORD_ID, aws_record, call, clients, event
from record_fixtures import CUSTOM_DESCRIPTORS, CUSTOM_DRAFT
from registry_portal.app import create_management_handler
from registry_portal.domain.drafts import Management

PUBLISHED_VERSION = CUSTOM_DRAFT["recordVersion"]
DEPRECATION_PK = f"REGISTRY#{REGISTRY_ARN}#DEPRECATION"
SUBMISSION_KEYS = {
    "reviewKind",
    "recordId",
    "name",
    "displayName",
    "recordType",
    "recordVersion",
    "status",
    "managed",
    "ownerTeam",
    "submissionState",
    "submissionId",
    "snapshotHash",
    "reason",
    "submittedAt",
    "submittedBy",
    "invalidated",
    "operationId",
}
REQUEST_KEYS = {
    "reviewKind",
    "recordId",
    "name",
    "displayName",
    "recordType",
    "recordVersion",
    "status",
    "managed",
    "ownerTeam",
    "requestId",
    "reason",
    "requestedAt",
    "requestedBy",
    "suspended",
    "operationId",
}


def deprecation_item(db, record_id=RECORD_ID):
    item = db.get_item(
        TableName=SUPPLEMENT_TABLE,
        Key={"pk": {"S": DEPRECATION_PK}, "sk": {"S": record_id}},
    ).get("Item")
    return None if item is None else {k: TypeDeserializer().deserialize(v) for k, v in item.items()}


def put_deprecation_item(db, item, record_id=RECORD_ID):
    db.put_item(
        TableName=SUPPLEMENT_TABLE,
        Item={
            "pk": {"S": DEPRECATION_PK},
            "sk": {"S": record_id},
            **{k: TypeSerializer().serialize(v) for k, v in item.items()},
        },
    )


def stub_get(stub, status, **changes):
    stub.add_response("get_registry_record", {**RECORD, "status": status, **changes}, GET)


def stub_publication_update(stub, target, reason, error=None):
    params = {**GET, "status": target, "statusReason": reason}
    if error:
        stub.add_client_error("update_registry_record_status", error, expected_params=params)
        return
    stub.add_response(
        "update_registry_record_status",
        {
            "registryArn": REGISTRY_ARN,
            "recordArn": f"{REGISTRY_ARN}/record/{RECORD_ID}",
            "recordId": RECORD_ID,
            "status": target,
            "statusReason": reason,
            "updatedAt": RECORD["updatedAt"],
            "ResponseMetadata": {"RequestId": "aws-publication-1"},
        },
        params,
    )


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


def decline(decision, body, *, groups=CURATOR, sub="carol"):
    return call(
        decision,
        event(
            "POST",
            f"/api/records/{RECORD_ID}/deprecation-requests/decline",
            body,
            groups=groups,
            sub=sub,
            key="",
        ),
    )


def inspect(decision, *, groups=CURATOR, record_id=RECORD_ID):
    return call(
        decision, event("GET", f"/api/records/{record_id}/publication", groups=groups, key="")
    )


def history(management, *, groups="registry-publisher team-payments", sub="alice"):
    status, body = call(
        management,
        event("GET", f"/api/records/{RECORD_ID}/history", groups=groups, sub=sub, key=""),
    )
    assert status == 200, body
    return body["entries"]


def call_reviews(decision, token=None):
    request = event("GET", "/api/reviews", groups=CURATOR, key="", sub="carol")
    if token is not None:
        request["queryStringParameters"] = {"nextToken": token}
    return call(decision, request)


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


def approve(stub, state):
    """審査待ちの申請を公開承認して、公開中（APPROVED）の状態を作る。"""
    stub_get(stub, "PENDING_APPROVAL")
    stub_status_update(stub, approved=True)
    stub_get(stub, "APPROVED")
    status, body = decide(state["decision"], snapshot_hash=state["snapshot"]["snapshotHash"])
    assert status == 200 and body["operationState"] == "SUCCEEDED"


def published_and_suspended(stub, state):
    """公開承認の後に公開停止して、公開停止中（REJECTED）の状態を作る。"""
    approve(stub, state)
    stub_get(stub, "APPROVED")
    stub_status_update(stub, approved=False, reason="問題が見つかったため")
    stub_get(stub, "REJECTED")
    status, body = suspend(state["decision"])
    assert status == 200 and body["operationState"] == "SUCCEEDED", body


def other_pending_record():
    return {
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


def stub_pending_list(stub, records, *, token=None, next_token=None):
    params = {
        "registryId": REGISTRY_ARN,
        "maxResults": 20,
        "filters": [{"name": "status", "values": ["PENDING_APPROVAL"]}],
    }
    if token is not None:
        params["nextToken"] = token
    response = {"registryRecords": records}
    if next_token is not None:
        response["nextToken"] = next_token
    stub.add_response("list_registry_records", response, params)


# --- 廃止依頼（Publisher） ---


@mock_aws
def test_publisher_of_the_owner_team_requests_deprecation_and_it_is_recorded_in_history():
    registry, db = clients()
    with Stubber(registry) as stub:
        state = setup_pending(stub, registry, db)
        approve(stub, state)
        stub_get(stub, "APPROVED")
        status, body = request_deprecation(state["management"])
        assert status == 200
        assert body["state"] == "OPEN"
        assert body["resolution"] is None
        assert body["requestedBy"] == {"issuer": ISSUER, "subject": "alice"}
        assert body["reason"] == "利用が終わったため"
        stored_request = deprecation_item(db)
        assert stored_request["state"] == "OPEN"
        assert stored_request["recordId"] == RECORD_ID
        # 依頼は操作記録に残り、所有チームの Publisher が履歴で確認できる
        entry = history(state["management"])[-1]
        assert entry["kind"] == "REQUEST_DEPRECATION"
        assert entry["state"] == "SUCCEEDED"
        assert entry["reason"] == "利用が終わったため"
        assert entry["principal"] == {"issuer": ISSUER, "subject": "alice"}
        stub.assert_no_pending_responses()


@mock_aws
def test_deprecation_request_requires_owner_team_publisher():
    registry, db = clients()
    with Stubber(registry) as stub:
        state = setup_pending(stub, registry, db)
        approve(stub, state)
        # 所属チームのない Publisher、他チームの Publisher、Curator、Viewer は 403。
        # 所有チームの検査は AWS のレコードを読む前に行う（読み取りの応答を用意しない）
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
def test_deprecation_request_validates_body_and_rejects_duplicates():
    registry, db = clients()
    with Stubber(registry) as stub:
        state = setup_pending(stub, registry, db)
        approve(stub, state)
        # 本文の検査は AWS を呼ぶ前に行う
        for body in (
            {},
            {"reason": ""},
            {"reason": "x" * 256},
            {"reason": "r", "extra": 1},
            {"reason": "password=hunter2 のため終了"},
        ):
            status, result = call(
                state["management"],
                event("POST", f"/api/records/{RECORD_ID}/deprecation-requests", body, key=""),
            )
            assert status == 400, (body, result)
        stub_get(stub, "APPROVED")
        status, _ = request_deprecation(state["management"])
        assert status == 200
        # 同じレコードへの未解決の依頼の重複は 409
        stub_get(stub, "APPROVED")
        status, body = request_deprecation(state["management"], reason="もう一度依頼")
        assert status == 409
        assert body["error"]["code"] == "CONTENT_CONFLICT"
        assert "未解決の廃止依頼" in body["error"]["message"]
        stub.assert_no_pending_responses()


@mock_aws
def test_deprecation_request_rejects_deprecated_and_locked_records():
    registry, db = clients()
    with Stubber(registry) as stub:
        state = setup_pending(stub, registry, db)
        # 廃止済みは依頼できない
        stub_get(stub, "DEPRECATED")
        status, body = request_deprecation(state["management"])
        assert status == 409
        # 別の操作がロックを持つ間は保存しない（共通のロックの外では書き込まない）
        put(db, "LOCK", {"operationId": "other-operation"})
        stub_get(stub, "PENDING_APPROVAL")
        status, body = request_deprecation(state["management"])
        assert status == 409
        assert "別の操作が処理中" in body["error"]["message"]
        assert deprecation_item(db) is None
        stub.assert_no_pending_responses()


@mock_aws
def test_deprecation_request_hides_existence_and_management_from_non_owners():
    """所有チームに属さない Publisher には、レコードの有無や管理状態を見せずに 403 を返す。"""
    registry, db = clients()
    with Stubber(registry) as stub:
        state = setup_pending(stub, registry, db)
        # 所有チームの Publisher には、AWS から消えたレコードを 404 で知らせる
        stub.add_client_error(
            "get_registry_record", "ResourceNotFoundException", expected_params=GET
        )
        status, body = request_deprecation(state["management"])
        assert status == 404, body
        assert body["error"]["code"] == "RECORD_NOT_FOUND"
        # ポータルに管理情報がないレコードは、所有チームを判定できないため 403（AWS は読まない）
        status, body = call(
            state["management"],
            event(
                "POST",
                "/api/records/mcpEFGH5678I/deprecation-requests",
                {"reason": "利用が終わったため"},
                groups="registry-publisher team-other",
                key="",
            ),
        )
        assert status == 403, body
        assert body["error"]["code"] == "FORBIDDEN"
        # 管理下にないレコードも、下書きの操作と同じく所有チームによらず 403（AWS は読まない）
        put(db, "MANAGEMENT", {"managed": False, "ownerTeam": "team-payments"})
        for groups in ("registry-publisher team-payments", "registry-publisher team-other"):
            status, body = request_deprecation(state["management"], groups=groups)
            assert status == 403, (groups, body)
            assert body["error"]["code"] == "FORBIDDEN"
        assert deprecation_item(db) is None
        stub.assert_no_pending_responses()


@mock_aws
def test_deprecation_request_is_503_when_the_supplement_table_is_not_configured():
    handler = create_management_handler(ENV)
    # 権限のない呼び出し元には、設定の状態より先に 403 を返す
    status, body = call(
        handler,
        event("POST", f"/api/records/{RECORD_ID}/deprecation-requests", {"reason": "r"}, "", ""),
    )
    assert status == 403
    status, body = call(
        handler,
        event("POST", f"/api/records/{RECORD_ID}/deprecation-requests", {"reason": "r"}, key=""),
    )
    assert status == 503
    assert body["error"]["code"] == "DRAFTS_NOT_CONFIGURED"


# --- 審査待ちの一覧 ---


@mock_aws
def test_review_list_shows_requests_on_the_first_page_with_kind_specific_fields():
    registry, db = clients()
    with Stubber(registry) as stub:
        state = setup_pending(stub, registry, db)
        approve(stub, state)
        stub_get(stub, "APPROVED")
        request_deprecation(state["management"])
        stub_pending_list(stub, [other_pending_record()], next_token="page-2")
        stub_get(stub, "APPROVED")
        status, body = call_reviews(state["decision"])
        assert status == 200
        assert [item["reviewKind"] for item in body["items"]] == [
            "DEPRECATION_REQUEST",
            "SUBMISSION",
        ]
        request, submission = body["items"]
        # 種別ごとに項目の形が決まっている（SPA の isReviewItem と揃える）
        assert set(request) == REQUEST_KEYS
        assert set(submission) == SUBMISSION_KEYS
        assert submission["recordId"] == "mcpEFGH5678I"
        assert request["recordId"] == RECORD_ID
        assert request["status"] == "APPROVED"
        assert request["ownerTeam"] == "team-payments"
        assert request["reason"] == "利用が終わったため"
        assert request["requestedBy"] == {"issuer": ISSUER, "subject": "alice"}
        assert request["suspended"] is False
        assert request["managed"] is True
        # 2ページ目以降には依頼を重ねて表示しない
        stub_pending_list(stub, [], token="page-2")
        status, body = call_reviews(state["decision"], token="page-2")
        assert status == 200
        assert body["items"] == []
        stub.assert_no_pending_responses()


@mock_aws
def test_review_list_orders_requests_oldest_first():
    registry, db = clients()
    with Stubber(registry) as stub:
        state = setup_pending(stub, registry, db)
        older = {
            "requestId": "a" * 64,
            "recordId": "mcpEFGH5678I",
            "reason": "古い依頼",
            "requestedBy": {"issuer": ISSUER, "subject": "dave"},
            "requestedAt": "2026-10-01T00:00:00+00:00",
            "state": "OPEN",
        }
        newer = {**older, "requestId": "b" * 64, "recordId": RECORD_ID, "reason": "新しい依頼"}
        newer["requestedAt"] = "2026-11-01T00:00:00+00:00"
        put_deprecation_item(db, newer)
        put_deprecation_item(db, older, record_id="mcpEFGH5678I")
        stub_pending_list(stub, [])
        stub.add_response(
            "get_registry_record",
            {**other_pending_record(), "status": "APPROVED"},
            {"registryId": REGISTRY_ARN, "recordId": "mcpEFGH5678I"},
        )
        stub_get(stub, "PENDING_APPROVAL")
        status, body = call_reviews(state["decision"])
        assert status == 200
        assert [item["reason"] for item in body["items"]] == ["古い依頼", "新しい依頼"]
        stub.assert_no_pending_responses()


@mock_aws
def test_review_list_reports_a_broken_or_unreadable_request_store_as_502():
    registry, db = clients()
    with Stubber(registry) as stub:
        state = setup_pending(stub, registry, db)
        # 保存内容の形式の異常は、空の一覧にせず 502 として区別する
        put_deprecation_item(db, {"recordId": RECORD_ID, "state": "UNKNOWN"})
        stub_pending_list(stub, [])
        status, body = call_reviews(state["decision"])
        assert status == 502
        assert "形式" in body["error"]["message"]
        # 補助ストアを読めないときも 502
        stub_pending_list(stub, [])

        def unreachable(**kwargs):
            raise EndpointConnectionError(endpoint_url="https://dynamodb.example")

        db.query = unreachable
        status, body = call_reviews(state["decision"])
        assert status == 502
        assert body["error"]["code"] == "REGISTRY_UNAVAILABLE"
        stub.assert_no_pending_responses()


# --- 公開停止 ---


@mock_aws
def test_curator_suspends_as_a_rejection_and_the_request_stays_open():
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
        # 公開停止は却下で発見できなくする。公開中の版はなくなる
        assert body["status"] == "REJECTED"
        assert body["publishedVersion"] is None
        assert body["submissionId"] is None and body["snapshotHash"] is None
        # 公開版スナップショットと publishedVersion は書き換えず、再申請の semver の下限に使う
        assert stored(db, f"PUBLISHED#{PUBLISHED_VERSION}") is not None
        management = Management(stored(db, "MANAGEMENT"))
        rejected = {**RECORD, "status": "REJECTED"}
        assert management.suspended_version == PUBLISHED_VERSION
        assert management.published_version(rejected) is None
        assert management.last_published_version(rejected) == PUBLISHED_VERSION
        # 公開停止は依頼を閉じない。廃止するか見送るかの判断待ちとして一覧に残る
        assert deprecation_item(db)["state"] == "OPEN"
        stub_pending_list(stub, [])
        stub_get(stub, "REJECTED")
        status, body = call_reviews(state["decision"])
        assert [item["suspended"] for item in body["items"]] == [True]
        stub.assert_no_pending_responses()


@mock_aws
def test_suspend_rejects_records_without_a_published_version_or_with_a_newer_revision():
    registry, db = clients()
    with Stubber(registry) as stub:
        state = setup_pending(stub, registry, db)
        # 公開版がない審査待ちは、公開停止ではなく却下で扱う（AWS の状態更新は呼ばない）
        stub_get(stub, "PENDING_APPROVAL")
        status, body = suspend(state["decision"])
        assert status == 409
        assert "却下" in body["error"]["message"]
        assert stored(db, "LOCK") is None
        approve(stub, state)
        # 公開中の版があり新しい版が審査待ち（二重リビジョン）の間は、挙動が未確認のため受け付けない
        stub_get(stub, "PENDING_APPROVAL", recordVersion="1.0.1")
        status, body = suspend(state["decision"], key="suspend-2")
        assert status == 409
        assert f"公開中の版 {PUBLISHED_VERSION}" in body["error"]["message"]
        stub.assert_no_pending_responses()


@mock_aws
def test_suspend_explains_that_a_failed_aws_write_blocks_it():
    registry, db = clients()
    with Stubber(registry) as stub:
        state = setup_pending(stub, registry, db)
        approve(stub, state)
        # 公開中の版が残ったまま AWS の更新が失敗した状態は、「公開中の版がない」ではなく失敗を理由に示す
        for index, failed in enumerate(("UPDATE_FAILED", "CREATE_FAILED")):
            stub_get(stub, failed, recordVersion="1.0.1")
            status, body = suspend(state["decision"], key=f"suspend-failed-{index}")
            assert status == 409
            assert "失敗" in body["error"]["message"]
            assert "公開中の版がない" not in body["error"]["message"]
            assert stored(db, "LOCK") is None
        stub.assert_no_pending_responses()


@mock_aws
def test_suspension_mark_is_ignored_once_aws_shows_the_record_as_approved():
    registry, db = clients()
    with Stubber(registry) as stub:
        state = setup_pending(stub, registry, db)
        published_and_suspended(stub, state)
        stub_get(stub, "REJECTED")
        request_deprecation(state["management"])
        # ポータルの外で再承認されて AWS が公開中を返すなら、残った印で「公開停止済み」と表示しない
        stub_get(stub, "APPROVED")
        status, body = inspect(state["decision"])
        assert status == 200
        assert body["suspended"] is False
        assert body["publishedVersion"] == PUBLISHED_VERSION
        assert body["actions"]["suspend"]["allowed"] is True
        stub_pending_list(stub, [])
        stub_get(stub, "APPROVED")
        status, body = call_reviews(state["decision"])
        assert [item["suspended"] for item in body["items"]] == [False]
        stub.assert_no_pending_responses()


@mock_aws
def test_suspended_record_cannot_be_approved_directly():
    registry, db = clients()
    with Stubber(registry) as stub:
        state = setup_pending(stub, registry, db)
        published_and_suspended(stub, state)
        # 再公開には編集と再申請が必要。却下済みの直接承認（AWS は許す）はポータルで拒否する
        stub_get(stub, "REJECTED")
        status, body = decide(
            state["decision"], snapshot_hash=state["snapshot"]["snapshotHash"], key="decide-2"
        )
        assert status == 409
        assert "再申請" in body["error"]["message"]
        stub.assert_no_pending_responses()


@mock_aws
def test_suspended_record_cannot_be_resubmitted_without_editing():
    registry, db = clients()
    with Stubber(registry) as stub:
        state = setup_pending(stub, registry, db)
        published_and_suspended(stub, state)
        management_handler = state["management"]
        rejected = {**RECORD, "status": "REJECTED"}
        stub.add_response("get_registry_record", rejected, GET)
        status, current = call(management_handler, event("GET", f"/api/records/{RECORD_ID}"))
        assert status == 200
        before = stored(db, "MANAGEMENT")
        # 再公開には編集が必要。公開停止中（REJECTED）のまま再申請しても、申請 API を呼ばずに 409
        stub.add_response("get_registry_record", rejected, GET)
        status, body = call(
            management_handler,
            event(
                "POST",
                f"/api/records/{RECORD_ID}/submit",
                {
                    "expectedContentHash": current["contentHash"],
                    "reason": "編集せずに再申請する",
                    "relatedReferences": [],
                },
                sub="bob",
                key="submit-2",
            ),
        )
        assert status == 409, body
        assert body["error"]["code"] == "CONTENT_CONFLICT"
        # 申請は保存されず、公開停止の印も残る
        after = stored(db, "MANAGEMENT")
        assert after["activeSubmissionId"] == before["activeSubmissionId"]
        assert Management(after).suspended_version == PUBLISHED_VERSION
        stub.assert_no_pending_responses()


@mock_aws
def test_suspended_record_is_republished_by_editing_resubmitting_and_approving():
    registry, db = clients()
    with Stubber(registry) as stub:
        state = setup_pending(stub, registry, db)
        published_and_suspended(stub, state)
        management_handler = state["management"]
        rejected = {**RECORD, "status": "REJECTED"}
        stub.add_response("get_registry_record", rejected, GET)
        status, current = call(management_handler, event("GET", f"/api/records/{RECORD_ID}"))
        assert status == 200
        assert current["publishedVersion"] is None
        # Publisher が版を上げて編集する
        edited = aws_record(
            {**CUSTOM_DRAFT, "recordVersion": "1.0.1"},
            descriptors=CUSTOM_DESCRIPTORS,
            customMetadata=META,
        )
        stub.add_response("get_registry_record", rejected, GET)
        stub.add_response("update_registry_record", {**edited, "status": "UPDATING"}, None)
        stub.add_response("get_registry_record", edited, GET)
        status, updated = call(
            management_handler,
            event(
                "PATCH",
                f"/api/records/{RECORD_ID}",
                {
                    "draft": {**CUSTOM_DRAFT, "recordVersion": "1.0.1"},
                    "expectedContentHash": current["contentHash"],
                },
            ),
        )
        assert status == 200, updated
        # 再申請。公開停止中は発見 API が版を返さないが、補助ストアの公開済みの版が semver の下限になる
        state["transport"].registry.append(missing_published())
        stub_submit(stub, edited)
        status, submitted = call(
            management_handler,
            event(
                "POST",
                f"/api/records/{RECORD_ID}/submit",
                {
                    "expectedContentHash": updated["contentHash"],
                    "reason": "問題を修正した",
                    "relatedReferences": [],
                },
                sub="bob",
                key="submit-2",
            ),
        )
        assert status == 200, submitted
        snapshot = stored(db, f'SUBMISSION#{submitted["submissionId"]}')
        stub.add_response("get_registry_record", {**edited, "status": "PENDING_APPROVAL"}, GET)
        stub_status_update(stub, approved=True, record=edited)
        stub.add_response("get_registry_record", {**edited, "status": "APPROVED"}, GET)
        status, body = decide(
            state["decision"], snapshot_hash=snapshot["snapshotHash"], key="decide-2"
        )
        assert status == 200 and body["operationState"] == "SUCCEEDED", body
        assert body["publishedVersion"] == "1.0.1"
        # 再承認で公開停止の印は消える
        management = Management(stored(db, "MANAGEMENT"))
        assert management.suspended_version is None
        assert management.item["publishedVersion"] == "1.0.1"
        stub.assert_no_pending_responses()


@mock_aws
def test_suspend_waits_for_the_change_to_appear_instead_of_failing():
    registry, db = clients()
    with Stubber(registry) as stub:
        state = setup_pending(stub, registry, db)
        approve(stub, state)
        # AWS が受け付けた後も公開中のままに見えるときは、失敗にせず結果確認中のままロックを持つ
        stub_get(stub, "APPROVED")
        stub_status_update(stub, approved=False, reason="問題が見つかったため")
        stub_get(stub, "APPROVED")
        status, body = suspend(state["decision"])
        assert status == 202
        assert body["operationState"] == "VERIFYING"
        assert stored(db, "LOCK") is not None
        # 反映が見えたら、操作照会（管理 handler）で成功を確定する
        stub_get(stub, "REJECTED")
        status, body = call(
            state["management"],
            event("GET", f"/api/operations/{body['operationId']}", groups=CURATOR, sub="carol"),
        )
        assert status == 200
        assert body["operationState"] == "SUCCEEDED"
        assert body["status"] == "REJECTED"
        assert stored(db, "LOCK") is None
        stub.assert_no_pending_responses()


@mock_aws
def test_suspend_resends_when_the_first_call_did_not_reach_aws():
    registry, db = clients()
    with Stubber(registry) as stub:
        state = setup_pending(stub, registry, db)
        approve(stub, state)
        # 届いたか不明なエラーは結果確認中。受け付けを確認できていないため、同じキーの再送で送り直す
        stub_get(stub, "APPROVED")
        stub_publication_update(
            stub, "REJECTED", "問題が見つかったため", error="InternalServerException"
        )
        status, body = suspend(state["decision"])
        assert status == 202
        stub_get(stub, "APPROVED")
        stub_publication_update(stub, "REJECTED", "問題が見つかったため")
        stub_get(stub, "REJECTED")
        status, body = suspend(state["decision"])
        assert status == 200
        assert body["operationState"] == "SUCCEEDED"
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
        status, body = deprecate(state["decision"], {"reason": REASON}, key="k-2")
        assert status == 400 and "confirm" in body["error"]["message"]
        status, body = deprecate(state["decision"], {"reason": REASON, "confirm": False}, key="k-3")
        assert status == 400
        assert "元に戻せません" in body["error"]["message"]
        stub.assert_no_pending_responses()


# --- 廃止 ---


@mock_aws
def test_curator_deprecates_with_confirmation_and_the_request_is_resolved():
    registry, db = clients()
    with Stubber(registry) as stub:
        state = setup_pending(stub, registry, db)
        published_and_suspended(stub, state)
        stub_get(stub, "REJECTED")
        request_deprecation(state["management"])
        # 公開停止中（REJECTED）から廃止する
        stub_get(stub, "REJECTED")
        stub_publication_update(stub, "DEPRECATED", "利用終了のため")
        stub_get(stub, "DEPRECATED")
        status, body = deprecate(state["decision"])
        assert status == 200
        assert body["operationState"] == "SUCCEEDED"
        assert body["status"] == "DEPRECATED"
        assert body["publishedVersion"] is None
        stored_request = deprecation_item(db)
        assert stored_request["state"] == "RESOLVED"
        assert stored_request["resolution"] == "DEPRECATED"
        assert stored_request["resolutionReason"] == "利用終了のため"
        assert stored_request["resolvedBy"] == {"issuer": ISSUER, "subject": "carol"}
        # 依頼の正本は書き換えない
        assert stored_request["reason"] == "利用が終わったため"
        stub.assert_no_pending_responses()


@mock_aws
def test_deprecate_accepts_any_settled_status_and_is_irreversible():
    registry, db = clients()
    with Stubber(registry) as stub:
        state = setup_pending(stub, registry, db)
        # AWS はどの状態からも廃止できる。審査待ちへの依頼も、ここで処理できる
        stub_get(stub, "PENDING_APPROVAL")
        status, _ = request_deprecation(state["management"])
        assert status == 200
        stub_get(stub, "PENDING_APPROVAL")
        stub_publication_update(stub, "DEPRECATED", "利用終了のため")
        stub_get(stub, "DEPRECATED")
        status, body = deprecate(state["decision"])
        assert status == 200, body
        assert deprecation_item(db)["resolution"] == "DEPRECATED"
        # 廃止は元に戻せない。公開停止・廃止・廃止依頼はどれも受け付けない
        stub_get(stub, "DEPRECATED")
        status, body = suspend(state["decision"], key="suspend-2")
        assert status == 409
        stub_get(stub, "DEPRECATED")
        status, body = deprecate(state["decision"], key="deprecate-2")
        assert status == 409
        assert "すでに廃止" in body["error"]["message"]
        stub_get(stub, "DEPRECATED")
        status, body = request_deprecation(state["management"])
        assert status == 409
        stub.assert_no_pending_responses()


@mock_aws
def test_deprecate_without_a_request_writes_no_request():
    registry, db = clients()
    with Stubber(registry) as stub:
        state = setup_pending(stub, registry, db)
        approve(stub, state)
        stub_get(stub, "APPROVED")
        stub_publication_update(stub, "DEPRECATED", "利用終了のため")
        stub_get(stub, "DEPRECATED")
        status, body = deprecate(state["decision"])
        assert status == 200
        assert deprecation_item(db) is None
        stub.assert_no_pending_responses()


# --- 見送り ---


@mock_aws
def test_curator_declines_a_request_with_a_reason_and_it_is_recorded():
    registry, db = clients()
    with Stubber(registry) as stub:
        state = setup_pending(stub, registry, db)
        published_and_suspended(stub, state)
        stub_get(stub, "REJECTED")
        _, request = request_deprecation(state["management"])
        status, body = decline(
            state["decision"], {"requestId": request["requestId"], "reason": "利用者が残っている"}
        )
        assert status == 200, body
        assert body["state"] == "RESOLVED"
        assert body["resolution"] == "DECLINED"
        assert body["resolutionReason"] == "利用者が残っている"
        assert body["resolvedBy"] == {"issuer": ISSUER, "subject": "carol"}
        # レコードの状態は変えない（公開停止中のまま）。一覧からは消える
        assert Management(stored(db, "MANAGEMENT")).suspended_version == PUBLISHED_VERSION
        stub_pending_list(stub, [])
        status, listed = call_reviews(state["decision"])
        assert listed["items"] == []
        # 見送りも履歴に残り、Publisher が理由を確認できる
        entry = history(state["management"])[-1]
        assert entry["kind"] == "DECLINE_DEPRECATION"
        assert entry["reason"] == "利用者が残っている"
        assert entry["state"] == "SUCCEEDED"
        # 見送った後は、Publisher が新しい依頼を出せる
        stub_get(stub, "REJECTED")
        status, _ = request_deprecation(state["management"], reason="改めて依頼する")
        assert status == 200
        stub.assert_no_pending_responses()


@mock_aws
def test_decline_checks_role_body_target_and_lock():
    registry, db = clients()
    with Stubber(registry) as stub:
        state = setup_pending(stub, registry, db)
        stub_get(stub, "PENDING_APPROVAL")
        _, request = request_deprecation(state["management"])
        body = {"requestId": request["requestId"], "reason": "見送る"}
        for groups in ("", "registry-publisher team-payments", "registry-admin"):
            status, result = decline(state["decision"], body, groups=groups)
            assert status == 403, (groups, result)
        for invalid in ({"reason": "見送る"}, {"requestId": request["requestId"], "reason": ""}):
            status, _ = decline(state["decision"], invalid)
            assert status == 400
        # 画面で確認した依頼と一致しなければ見送らない
        status, result = decline(state["decision"], {**body, "requestId": "0" * 64})
        assert status == 409
        # 公開停止・廃止の処理中（ロックあり）は見送らない
        put(db, "LOCK", {"operationId": "other-operation"})
        status, result = decline(state["decision"], body)
        assert status == 409
        assert "別の操作が処理中" in result["error"]["message"]
        assert deprecation_item(db)["state"] == "OPEN"
        stub.assert_no_pending_responses()


# --- 公開管理の照会 ---


@mock_aws
def test_publication_view_shows_state_request_and_available_actions():
    registry, db = clients()
    with Stubber(registry) as stub:
        state = setup_pending(stub, registry, db)
        approve(stub, state)
        stub_get(stub, "APPROVED")
        request_deprecation(state["management"])
        stub_get(stub, "APPROVED")
        status, body = inspect(state["decision"])
        assert status == 200
        assert body["status"] == "APPROVED"
        assert body["publishedVersion"] == PUBLISHED_VERSION
        assert body["suspended"] is False
        assert body["operationId"] is None
        assert body["deprecationRequest"]["reason"] == "利用が終わったため"
        assert body["actions"] == {
            "suspend": {"allowed": True, "message": None},
            "deprecate": {"allowed": True, "message": None},
        }
        # 新しい版が審査待ち（二重リビジョン）なら、公開停止できない理由を示す
        stub_get(stub, "PENDING_APPROVAL", recordVersion="1.0.1")
        status, body = inspect(state["decision"])
        assert body["actions"]["suspend"]["allowed"] is False
        assert "新しい版" in body["actions"]["suspend"]["message"]
        assert body["actions"]["deprecate"]["allowed"] is True
        # 別の操作の処理中は、どちらも実行できない
        put(db, "LOCK", {"operationId": "other-operation"})
        stub_get(stub, "APPROVED")
        status, body = inspect(state["decision"])
        assert body["operationId"] == "other-operation"
        assert not body["actions"]["suspend"]["allowed"]
        assert not body["actions"]["deprecate"]["allowed"]
        stub.assert_no_pending_responses()


@mock_aws
def test_publication_view_checks_role_and_distinguishes_missing_records():
    registry, db = clients()
    with Stubber(registry) as stub:
        state = setup_pending(stub, registry, db)
        for groups in ("", "registry-publisher team-payments", "registry-admin"):
            status, _ = inspect(state["decision"], groups=groups)
            assert status == 403
        status, _ = inspect(state["decision"], record_id="bad")
        assert status == 400
        stub.add_client_error(
            "get_registry_record", "ResourceNotFoundException", expected_params=GET
        )
        status, body = inspect(state["decision"])
        assert status == 404
        assert body["error"]["code"] == "RECORD_NOT_FOUND"
        stub.add_client_error("get_registry_record", "AccessDeniedException", expected_params=GET)
        status, body = inspect(state["decision"])
        assert status == 502
        # 管理下にないレコードは、操作できない理由を示す
        put(db, "MANAGEMENT", {"managed": False, "ownerTeam": "team-payments"})
        stub_get(stub, "APPROVED")
        status, body = inspect(state["decision"])
        assert body["managed"] is False
        assert "管理されていない" in body["actions"]["deprecate"]["message"]
        stub.assert_no_pending_responses()


@mock_aws
def test_review_detail_warns_about_an_open_deprecation_request():
    registry, db = clients()
    with Stubber(registry) as stub:
        state = setup_pending(stub, registry, db)
        stub_get(stub, "PENDING_APPROVAL")
        request_deprecation(state["management"])
        stub_get(stub, "PENDING_APPROVAL")
        status, body = call(
            state["decision"],
            event("GET", f"/api/reviews/{RECORD_ID}", groups=CURATOR, sub="carol", key=""),
        )
        assert status == 200
        assert body["deprecationRequest"]["reason"] == "利用が終わったため"
        stub.assert_no_pending_responses()

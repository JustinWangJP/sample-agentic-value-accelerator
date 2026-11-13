"""Admin の回復操作（結果確認中の操作を Registry の再取得で確定・解除する）の外部結果を検証する Seam 1 テスト。"""

from collections import deque
from datetime import datetime, timedelta, timezone

from botocore.stub import Stubber
from moto import mock_aws
import pytest

from catalog_helpers import ENV, REGISTRY_ARN, SUPPLEMENT_TABLE, FakeSecrets, FakeTransport
from decision_helpers import (
    CURATOR,
    GET,
    RECORD,
    META,
    REASON,
    decide,
    missing_published,
    put,
    setup_pending,
    stored,
    stub_status_update,
)
from draft_helpers import RECORD_ARN, RECORD_ID, aws_record, call, clients, event
from record_fixtures import CUSTOM_DESCRIPTORS, CUSTOM_DRAFT, stub_create
from registry_portal import recovery
from registry_portal.app import create_curation_handler, create_management_handler
from registry_portal.services.discovery import DiscoveryDependencies

ADMIN = "registry-admin"
RECOVERY_PATH = f"/api/records/{RECORD_ID}/recovery"


@pytest.fixture(autouse=True)
def long_after_start(monkeypatch):
    """回復の対象にできる経過時間（開始から5分）を過ぎた時刻にする。"""
    later = datetime.now(timezone.utc) + timedelta(hours=1)
    monkeypatch.setattr(recovery, "_utcnow", lambda: later)


def recover(handler, *, groups=ADMIN, sub="admin-1", release=False, reason="Registry を確認した"):
    body = {"reason": reason}
    if release:
        body["release"] = True
    return call(handler, event("POST", RECOVERY_PATH, body, groups=groups, sub=sub, key=""))


def stuck_decision(stub, registry, db):
    """決裁の AWS 呼び出しが 5xx で結果不明になり、結果確認中でロックが残った状態を作る。"""
    state = setup_pending(stub, registry, db)
    stub.add_response("get_registry_record", {**RECORD, "status": "PENDING_APPROVAL"}, GET)
    stub_status_update(stub, approved=True, error="InternalServerException")
    status, pending = decide(state["decision"], snapshot_hash=state["snapshot"]["snapshotHash"])
    assert (status, pending["operationState"]) == (202, "VERIFYING")
    return state, pending["operationId"]


@mock_aws
def test_admin_reconciles_a_stuck_decision_from_the_registry_and_records_the_recovery():
    registry, db = clients()
    with Stubber(registry) as stub:
        state, operation_id = stuck_decision(stub, registry, db)
        # 決裁は AWS に届いていた。Admin が Registry を再取得すると承認済みと分かる
        stub.add_response("get_registry_record", {**RECORD, "status": "APPROVED"}, GET)
        status, body = recover(state["decision"])
        assert status == 200
        assert body["operationState"] == "SUCCEEDED"
        # 回復の応答は、操作の中身（スナップショット・下書きの内容）と操作 ID を含めず、結果の要約だけを返す。
        # 結果確認中の決裁の操作 ID は、決裁した Curator 以外に返さない（ADR-0010）
        assert set(body) == {"recordId", "operationState", "message", "recovery"}
        assert body["recovery"]["mode"] == "RECONCILED"
        assert body["recovery"]["by"]["subject"] == "admin-1"
        assert body["recovery"]["reason"] == "Registry を確認した"
        assert stored(db, "LOCK") is None
        assert stored(db, f"PUBLISHED#{CUSTOM_DRAFT['recordVersion']}") is not None
        # 回復した主体は、操作記録（追記記録の RESULT）に残る
        result = stored(db, f"AUDIT#{operation_id}#RESULT")
        assert result["recovery"]["by"]["subject"] == "admin-1"
        stub.assert_no_pending_responses()


@mock_aws
def test_recovery_is_admin_only_and_validated_before_any_aws_call():
    registry, db = clients()
    with Stubber(registry) as stub:
        state, operation_id = stuck_decision(stub, registry, db)
        for groups in ("", "registry-publisher team-payments", CURATOR):
            assert recover(state["decision"], groups=groups)[0] == 403
        for body in (
            {},
            {"reason": ""},
            {"reason": "x" * 256},
            {"reason": "確認済み", "release": "yes"},
            {"reason": "確認済み", "operationId": operation_id},
        ):
            request = event("POST", RECOVERY_PATH, body, groups=ADMIN, key="")
            assert call(state["decision"], request)[0] == 400
        # 拒否した要求は、ロックも操作も変えない
        assert stored(db, "LOCK")["operationId"] == operation_id
        stub.assert_no_pending_responses()


@mock_aws
def test_recovery_needs_a_locked_operation_that_is_old_enough_to_be_idle(monkeypatch):
    registry, db = clients()
    with Stubber(registry) as stub:
        state, operation_id = stuck_decision(stub, registry, db)
        # 開始直後の操作は、実行中の Lambda が続けている可能性があるため回復できない
        now = datetime.now(timezone.utc)
        monkeypatch.setattr(recovery, "_utcnow", lambda: now)
        status, body = recover(state["decision"])
        assert (status, body["error"]["code"]) == (409, "OPERATION_STILL_RUNNING")
        assert stored(db, "LOCK")["operationId"] == operation_id
        # ロックのないレコードには回復する操作がない
        status, body = call(
            state["decision"],
            event("POST", "/api/records/otherREC1234/recovery", {"reason": "確認"}, groups=ADMIN),
        )
        assert (status, body["error"]["code"]) == (409, "NO_RECOVERABLE_OPERATION")
        stub.assert_no_pending_responses()


@mock_aws
def test_admin_can_release_an_operation_the_registry_cannot_settle():
    registry, db = clients()
    with Stubber(registry) as stub:
        state, operation_id = stuck_decision(stub, registry, db)
        # 再取得しても決裁が反映されたか分からない（承認待ちのまま）。AWS の書き込みは呼び直さない
        stub.add_response("get_registry_record", {**RECORD, "status": "PENDING_APPROVAL"}, GET)
        status, body = recover(state["decision"])
        assert (status, body["operationState"], body["recovery"]) == (202, "VERIFYING", None)
        assert stored(db, "LOCK")["operationId"] == operation_id
        # 強制解除は、Registry を再取得できたうえで、確定できないときだけ失敗として確定する
        stub.add_response("get_registry_record", {**RECORD, "status": "PENDING_APPROVAL"}, GET)
        stub.add_response("get_registry_record", {**RECORD, "status": "PENDING_APPROVAL"}, GET)
        status, body = recover(state["decision"], release=True)
        assert status == 200
        assert body["operationState"] == "FAILED"
        assert body["recovery"]["mode"] == "RELEASED"
        assert stored(db, "LOCK") is None
        assert stored(db, f"PUBLISHED#{CUSTOM_DRAFT['recordVersion']}") is None
        result = stored(db, f"AUDIT#{operation_id}#RESULT")
        assert result["errorCode"] == "ADMIN_RELEASED"
        assert result["recovery"]["by"]["subject"] == "admin-1"
        stub.assert_no_pending_responses()


@mock_aws
def test_release_is_refused_while_the_registry_cannot_be_read():
    registry, db = clients()
    with Stubber(registry) as stub:
        state, operation_id = stuck_decision(stub, registry, db)
        stub.add_client_error("get_registry_record", "InternalServerException", expected_params=GET)
        stub.add_client_error("get_registry_record", "InternalServerException", expected_params=GET)
        status, body = recover(state["decision"], release=True)
        assert (status, body["error"]["code"]) == (502, "REGISTRY_UNAVAILABLE")
        assert stored(db, "LOCK")["operationId"] == operation_id
        stub.assert_no_pending_responses()


def setup_draft(stub, registry, db):
    """申請前の下書きを作り、管理 handler と決裁 handler（回復の入口）を返す。"""
    transport = FakeTransport(registry=deque([missing_published()]))
    management = create_management_handler(
        {**ENV, "SUPPLEMENT_TABLE_NAME": SUPPLEMENT_TABLE},
        registry_client=registry,
        supplement_store_client=db,
        discovery=DiscoveryDependencies(transport=transport, secrets_client=FakeSecrets().client),
    )
    decision = create_curation_handler(
        {**ENV, "SUPPLEMENT_TABLE_NAME": SUPPLEMENT_TABLE},
        registry_client=registry,
        store_client=db,
    )
    stub_create(stub, CUSTOM_DRAFT, CUSTOM_DESCRIPTORS, META)
    status, created = call(management, event(body=CUSTOM_DRAFT))
    assert status == 201
    return management, decision, created


@mock_aws
def test_admin_reconciles_a_stuck_update_the_registry_shows_as_applied():
    registry, db = clients()
    with Stubber(registry) as stub:
        management, decision, created = setup_draft(stub, registry, db)
        changed = {**CUSTOM_DRAFT, "description": "結果確認を待つ説明"}
        stub.add_response("get_registry_record", RECORD, GET)
        stub.add_client_error(
            "update_registry_record", "InternalServerException", http_status_code=500
        )
        status, pending = call(
            management,
            event(
                "PATCH",
                f"/api/records/{RECORD_ID}",
                {"draft": changed, "expectedContentHash": created["contentHash"]},
            ),
        )
        assert (status, pending["operationState"]) == (202, "VERIFYING")
        # 他の書き込みは止まったまま。Admin が Registry を再取得すると更新が反映済みと分かる
        stub.add_response(
            "get_registry_record",
            aws_record(changed, descriptors=CUSTOM_DESCRIPTORS, customMetadata=META),
            GET,
        )
        status, body = recover(decision)
        assert (status, body["operationState"]) == (200, "SUCCEEDED")
        assert "operationId" not in body
        assert body["recovery"]["mode"] == "RECONCILED"
        assert stored(db, "LOCK") is None
        assert stored(db, "MANAGEMENT")["lastEditedBy"]["subject"] == "alice"
        stub.assert_no_pending_responses()


@mock_aws
def test_recovery_never_calls_the_submission_api_for_a_stuck_submission():
    registry, db = clients()
    with Stubber(registry) as stub:
        management, decision, created = setup_draft(stub, registry, db)
        stub.add_response("get_registry_record", RECORD, GET)
        stub.add_response(
            "get_registry",
            {
                "name": "registry",
                "registryId": "abcdEFGH1234",
                "registryArn": REGISTRY_ARN,
                "status": "ACTIVE",
                "createdAt": RECORD["createdAt"],
                "updatedAt": RECORD["updatedAt"],
                "approvalConfiguration": {"autoApprovalRules": []},
            },
            {"registryId": REGISTRY_ARN},
        )
        stub.add_client_error(
            "submit_registry_record_for_approval", "InternalServerException", http_status_code=500
        )
        status, pending = call(
            management,
            event(
                "POST",
                f"/api/records/{RECORD_ID}/submit",
                {
                    "expectedContentHash": created["contentHash"],
                    "reason": "利用者向けに公開する",
                    "relatedReferences": [],
                },
                key="submit-1",
            ),
        )
        assert (status, pending["operationState"]) == (202, "VERIFYING")
        # 申請 API の権限を持たない決裁 handler は、DRAFT のままの現物を見ても申請を呼び直さない
        stub.add_response("get_registry_record", RECORD, GET)
        status, body = recover(decision)
        assert (status, body["operationState"], body["recovery"]) == (202, "VERIFYING", None)
        stub.add_response("get_registry_record", RECORD, GET)
        stub.add_response("get_registry_record", RECORD, GET)
        status, body = recover(decision, release=True)
        assert (status, body["operationState"]) == (200, "FAILED")
        assert body["recovery"]["mode"] == "RELEASED"
        assert stored(db, "LOCK") is None
        stub.assert_no_pending_responses()


@mock_aws
def test_admin_can_inspect_the_operation_holding_the_lock_without_calling_aws(monkeypatch):
    registry, db = clients()
    with Stubber(registry) as stub:
        state, operation_id = stuck_decision(stub, registry, db)

        def inspect(groups=ADMIN, record_id=RECORD_ID):
            request = event("GET", f"/api/records/{record_id}/recovery", groups=groups, key="")
            return call(state["decision"], request)

        assert inspect(groups=CURATOR)[0] == 403
        status, body = inspect()
        assert status == 200
        assert body["recordId"] == RECORD_ID
        assert "operationId" not in body["operation"]
        assert body["operation"]["kind"] == "APPROVE"
        assert body["operation"]["operationState"] == "VERIFYING"
        assert body["operation"]["principal"]["subject"] == "carol"
        assert body["operation"]["recoverable"] is True
        # 回復できる時刻は開始時刻から MIN_OPERATION_AGE 後。SPA は待ち時間を自分で持たない
        started = datetime.fromisoformat(body["operation"]["startedAt"])
        recoverable_at = datetime.fromisoformat(body["operation"]["recoverableAt"])
        assert recoverable_at - started == recovery.MIN_OPERATION_AGE
        # 理由・ハッシュなど、決裁の中身は返さない
        assert set(body["operation"]) == {
            "kind",
            "operationState",
            "principal",
            "startedAt",
            "recoverableAt",
            "recoverable",
        }
        monkeypatch.setattr(recovery, "_utcnow", lambda: datetime.now(timezone.utc))
        assert inspect()[1]["operation"]["recoverable"] is False
        # 結果確認中の操作がないレコードは null。取得できないことと区別する
        assert inspect(record_id="otherREC1234") == (
            200,
            {"recordId": "otherREC1234", "operation": None},
        )
        stub.assert_no_pending_responses()


@mock_aws
def test_operation_lookup_shows_the_recovery_result_after_an_admin_recovered_it():
    registry, db = clients()
    with Stubber(registry) as stub:
        state, operation_id = stuck_decision(stub, registry, db)
        stub.add_response("get_registry_record", {**RECORD, "status": "APPROVED"}, GET)
        assert recover(state["decision"])[0] == 200
        # 決裁した Curator は、元の操作照会で回復結果（回復した Admin と理由）を確認できる
        status, body = call(
            state["management"],
            event("GET", f"/api/operations/{operation_id}", groups=CURATOR, key="", sub="carol"),
        )
        assert (status, body["operationState"]) == (200, "SUCCEEDED")
        assert body["recovery"]["mode"] == "RECONCILED"
        assert body["recovery"]["by"]["subject"] == "admin-1"
        stub.assert_no_pending_responses()


@mock_aws
def test_operation_lookup_shows_a_released_operation_as_failed_with_the_recovery():
    registry, db = clients()
    with Stubber(registry) as stub:
        state, operation_id = stuck_decision(stub, registry, db)
        for _ in range(2):
            stub.add_response("get_registry_record", {**RECORD, "status": "PENDING_APPROVAL"}, GET)
        assert recover(state["decision"], release=True)[0] == 200
        status, body = call(
            state["management"],
            event("GET", f"/api/operations/{operation_id}", groups=CURATOR, key="", sub="carol"),
        )
        assert (status, body["operationState"]) == (200, "FAILED")
        assert body["recovery"]["mode"] == "RELEASED"
        # 画面が FAILED の message をそのまま表示するため、強制解除されたことと理由が message に出る
        assert "強制解除" in body["message"]
        assert "Registry を確認した" in body["message"]
        stub.assert_no_pending_responses()


UPDATE_STATUS_PARAMS = {**GET, "status": "APPROVED", "statusReason": REASON}


@pytest.mark.parametrize(
    ("fault", "http_status", "state", "lock_held"),
    [
        ("AccessDeniedException", 403, "FAILED", False),
        ("ThrottlingException", 429, "FAILED", False),
        ("InternalServerException", 500, "VERIFYING", True),
        ("ServiceUnavailableException", 503, "VERIFYING", True),
        ("timeout", None, "VERIFYING", True),
    ],
)
@mock_aws
def test_decision_faults_never_record_a_success_and_only_unknown_outcomes_keep_the_lock(
    fault, http_status, state, lock_held
):
    from botocore.exceptions import ReadTimeoutError

    registry, db = clients()
    with Stubber(registry) as stub:
        handler = setup_pending(stub, registry, db)["decision"]
        snapshot_hash = stored(db, "MANAGEMENT")["activeSubmissionId"]
        snapshot_hash = stored(db, f"SUBMISSION#{snapshot_hash}")["snapshotHash"]
        stub.add_response("get_registry_record", {**RECORD, "status": "PENDING_APPROVAL"}, GET)
        if fault == "timeout":

            def timeout(**params):
                raise ReadTimeoutError(endpoint_url="https://example.com")

            registry.update_registry_record_status = timeout
        else:
            stub.add_client_error(
                "update_registry_record_status",
                fault,
                http_status_code=http_status,
                expected_params=UPDATE_STATUS_PARAMS,
            )
        status, body = decide(handler, snapshot_hash=snapshot_hash)
        operation_state = body.get("operationState", "FAILED")
        assert operation_state == state
        assert (stored(db, "LOCK") is not None) is lock_held
        # どの失敗でも、承認済みの公開版や成功の操作記録は残らない
        assert stored(db, f"PUBLISHED#{CUSTOM_DRAFT['recordVersion']}") is None
        assert stored(db, "MANAGEMENT").get("publishedVersion") is None
        assert status in {202, 400, 429}
        stub.assert_no_pending_responses()


@pytest.mark.parametrize(
    ("fault", "http_status", "state", "lock_held"),
    [
        ("AccessDeniedException", 403, "FAILED", False),
        ("ThrottlingException", 429, "FAILED", False),
        ("InternalServerException", 500, "VERIFYING", True),
        ("ServiceUnavailableException", 503, "VERIFYING", True),
        ("timeout", None, "VERIFYING", True),
    ],
)
@mock_aws
def test_submission_faults_never_record_a_submission_and_only_unknown_outcomes_keep_the_lock(
    fault, http_status, state, lock_held
):
    from botocore.exceptions import ReadTimeoutError

    registry, db = clients()
    with Stubber(registry) as stub:
        management, _, created = setup_draft(stub, registry, db)
        stub.add_response("get_registry_record", RECORD, GET)
        stub.add_response(
            "get_registry",
            {
                "name": "registry",
                "registryId": "abcdEFGH1234",
                "registryArn": REGISTRY_ARN,
                "status": "ACTIVE",
                "createdAt": RECORD["createdAt"],
                "updatedAt": RECORD["updatedAt"],
                "approvalConfiguration": {"autoApprovalRules": []},
            },
            {"registryId": REGISTRY_ARN},
        )
        if fault == "timeout":

            def timeout(**params):
                raise ReadTimeoutError(endpoint_url="https://example.com")

            registry.submit_registry_record_for_approval = timeout
        else:
            stub.add_client_error(
                "submit_registry_record_for_approval", fault, http_status_code=http_status
            )
        request = event(
            "POST",
            f"/api/records/{RECORD_ID}/submit",
            {
                "expectedContentHash": created["contentHash"],
                "reason": "利用者向けに公開する",
                "relatedReferences": [],
            },
            key="submit-1",
        )
        status, body = call(management, request)
        assert body.get("operationState", "FAILED") == state
        assert (stored(db, "LOCK") is not None) is lock_held
        # どの失敗でも、審査待ちの申請は管理情報に記録されない
        assert stored(db, "MANAGEMENT").get("activeSubmissionId") is None
        assert status in {202, 400, 429}
        stub.assert_no_pending_responses()


@mock_aws
def test_a_restarted_lambda_still_finds_the_verifying_operation_and_recovers_it():
    registry, db = clients()
    with Stubber(registry) as stub:
        state, operation_id = stuck_decision(stub, registry, db)
        # インスタンスの状態を持たない新しい handler（Lambda の再起動）を、同じ補助テーブルで作り直す
        restarted = create_curation_handler(
            {**ENV, "SUPPLEMENT_TABLE_NAME": SUPPLEMENT_TABLE},
            registry_client=registry,
            store_client=db,
        )
        request = event("GET", f"/api/records/{RECORD_ID}/recovery", groups=ADMIN, key="")
        status, body = call(restarted, request)
        assert (status, body["operation"]["kind"]) == (200, "APPROVE")
        stub.add_response("get_registry_record", {**RECORD, "status": "APPROVED"}, GET)
        status, body = recover(restarted)
        assert (status, body["operationState"]) == (200, "SUCCEEDED")
        assert stored(db, "LOCK") is None
        stub.assert_no_pending_responses()


@mock_aws
def test_release_is_refused_while_the_registry_is_still_processing_the_record():
    registry, db = clients()
    with Stubber(registry) as stub:
        state, operation_id = stuck_decision(stub, registry, db)
        # AWS がまだ書き込みを処理中のレコードは、結果が出る前に失敗として確定しない
        for _ in range(2):
            stub.add_response("get_registry_record", {**RECORD, "status": "UPDATING"}, GET)
        status, body = recover(state["decision"], release=True)
        assert (status, body["error"]["code"]) == (409, "RECORD_STILL_PROCESSING")
        assert stored(db, "LOCK")["operationId"] == operation_id
        stub.assert_no_pending_responses()


def stub_submission_accepted(stub):
    """申請 API までの応答を積む。申請 API は受け付けを返すが、再取得では DRAFT のまま（反映待ち）。"""
    stub.add_response("get_registry_record", RECORD, GET)
    stub.add_response(
        "get_registry",
        {
            "name": "registry",
            "registryId": "abcdEFGH1234",
            "registryArn": REGISTRY_ARN,
            "status": "ACTIVE",
            "createdAt": RECORD["createdAt"],
            "updatedAt": RECORD["updatedAt"],
            "approvalConfiguration": {"autoApprovalRules": []},
        },
        {"registryId": REGISTRY_ARN},
    )
    stub.add_response(
        "submit_registry_record_for_approval",
        {
            "registryArn": REGISTRY_ARN,
            "recordArn": RECORD_ARN,
            "recordId": RECORD_ID,
            "status": "PENDING_APPROVAL",
            "updatedAt": RECORD["updatedAt"],
        },
        GET,
    )
    stub.add_response("get_registry_record", RECORD, GET)


@mock_aws
def test_release_is_refused_for_a_decision_the_registry_accepted():
    registry, db = clients()
    with Stubber(registry) as stub:
        state = setup_pending(stub, registry, db)
        # AWS は決裁を受け付けたが、再取得ではまだ承認待ちに見える（反映待ち）
        stub.add_response("get_registry_record", {**RECORD, "status": "PENDING_APPROVAL"}, GET)
        stub_status_update(stub, approved=True)
        stub.add_response("get_registry_record", {**RECORD, "status": "PENDING_APPROVAL"}, GET)
        status, pending = decide(state["decision"], snapshot_hash=state["snapshot"]["snapshotHash"])
        assert (status, pending["operationState"]) == (202, "VERIFYING")
        operation_id = pending["operationId"]
        # 受け付けた決裁は AWS に届いている。失敗として強制解除せず、ロックを残す
        stub.add_response("get_registry_record", {**RECORD, "status": "PENDING_APPROVAL"}, GET)
        status, body = recover(state["decision"], release=True)
        assert (status, body["error"]["code"]) == (409, "ACCEPTED_BY_REGISTRY")
        assert stored(db, "LOCK")["operationId"] == operation_id
        assert stored(db, f"AUDIT#{operation_id}#RESULT") is None
        # 反映が見えたら、照合で成功として確定できる
        stub.add_response("get_registry_record", {**RECORD, "status": "APPROVED"}, GET)
        status, body = recover(state["decision"])
        assert (status, body["operationState"]) == (200, "SUCCEEDED")
        assert stored(db, "LOCK") is None
        stub.assert_no_pending_responses()


def submit(management, created):
    return call(
        management,
        event(
            "POST",
            f"/api/records/{RECORD_ID}/submit",
            {
                "expectedContentHash": created["contentHash"],
                "reason": "利用者向けに公開する",
                "relatedReferences": [],
            },
            key="submit-1",
        ),
    )


@mock_aws
def test_release_is_refused_for_a_submission_the_registry_accepted():
    registry, db = clients()
    with Stubber(registry) as stub:
        management, decision, created = setup_draft(stub, registry, db)
        stub_submission_accepted(stub)
        status, pending = submit(management, created)
        assert (status, pending["operationState"]) == (202, "VERIFYING")
        # 申請 API の受け付けを確認済みの申請は、DRAFT が見えても強制解除しない
        stub.add_response("get_registry_record", RECORD, GET)
        status, body = recover(decision, release=True)
        assert (status, body["error"]["code"]) == (409, "ACCEPTED_BY_REGISTRY")
        assert stored(db, "LOCK")["operationId"] == pending["operationId"]
        stub.assert_no_pending_responses()


@mock_aws
def test_publisher_sees_the_recovery_in_the_operation_lookup_and_the_history():
    registry, db = clients()
    with Stubber(registry) as stub:
        management, decision, created = setup_draft(stub, registry, db)
        changed = {**CUSTOM_DRAFT, "description": "結果確認を待つ説明"}
        stub.add_response("get_registry_record", RECORD, GET)
        stub.add_client_error(
            "update_registry_record", "InternalServerException", http_status_code=500
        )
        status, pending = call(
            management,
            event(
                "PATCH",
                f"/api/records/{RECORD_ID}",
                {"draft": changed, "expectedContentHash": created["contentHash"]},
            ),
        )
        assert (status, pending["operationState"]) == (202, "VERIFYING")
        stub.add_response(
            "get_registry_record",
            aws_record(changed, descriptors=CUSTOM_DESCRIPTORS, customMetadata=META),
            GET,
        )
        assert recover(decision)[0] == 200
        # 更新した Publisher の操作照会（成功の結果）に、回復した Admin と理由が載る
        status, body = call(
            management, event("GET", f"/api/operations/{pending['operationId']}", key="")
        )
        assert (status, body["operationState"]) == (200, "SUCCEEDED")
        assert body["recovery"]["mode"] == "RECONCILED"
        assert body["recovery"]["by"]["subject"] == "admin-1"
        # 操作と審査の履歴にも、回復結果が載る
        status, history = call(
            management, event("GET", f"/api/records/{RECORD_ID}/history", key="")
        )
        assert status == 200
        entry = next(e for e in history["entries"] if e["operationId"] == pending["operationId"])
        assert entry["state"] == "SUCCEEDED"
        assert entry["recovery"]["reason"] == "Registry を確認した"
        stub.assert_no_pending_responses()


@mock_aws
def test_a_lock_without_its_operation_is_reported_as_a_supplement_failure():
    registry, db = clients()
    with Stubber(registry) as stub:
        state, _ = stuck_decision(stub, registry, db)
        # 補助ストアの異常（ロックが指す操作がない）は、Registry の障害とは別のコードで返す
        put(db, "LOCK", {**stored(db, "LOCK"), "operationId": "f" * 64})
        request = event("GET", RECOVERY_PATH, groups=ADMIN, key="")
        status, body = call(state["decision"], request)
        assert (status, body["error"]["code"]) == (502, "SUPPLEMENT_UNAVAILABLE")
        status, body = recover(state["decision"])
        assert (status, body["error"]["code"]) == (502, "SUPPLEMENT_UNAVAILABLE")
        stub.assert_no_pending_responses()

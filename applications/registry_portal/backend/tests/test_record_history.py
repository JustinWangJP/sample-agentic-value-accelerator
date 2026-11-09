"""操作履歴 API（GET /api/records/{recordId}/history）の外部結果を検証する Seam 1 テスト。

操作記録（補助テーブルの追記記録）が上書き禁止の条件式付きで追記され、
履歴 API がロールと所有チームで権限を検査することを確認する。
"""

from __future__ import annotations

from typing import Any

from botocore.stub import Stubber
from moto import mock_aws
import pytest

from catalog_helpers import ENV, ISSUER, REGISTRY_ARN, SUPPLEMENT_TABLE
from draft_helpers import RECORD_ID, call, clients, event
from record_fixtures import CUSTOM_DESCRIPTORS, CUSTOM_DRAFT, METADATA, aws_record, stub_create
from decision_helpers import (
    CURATOR,
    GET,
    META,
    REASON,
    RECORD,
    decide,
    put,
    setup_pending,
    stored,
    stub_status_update,
)
from registry_portal.app import create_management_handler
from registry_portal.services.draft_store import DraftStore, StoreConflict

ADMIN = "registry-admin"
OPERATION_ID = "0123456789abcdef0123456789abcdef"
CAROL = {"issuer": ISSUER, "subject": "carol"}
ALICE = {"issuer": ISSUER, "subject": "alice"}


def history(
    handler, record_id=RECORD_ID, *, groups="registry-publisher team-payments", sub="alice"
):
    return call(
        handler,
        event("GET", f"/api/records/{record_id}/history", groups=groups, key="", sub=sub),
    )


def history_handler(registry, db):
    return create_management_handler(
        {**ENV, "SUPPLEMENT_TABLE_NAME": SUPPLEMENT_TABLE},
        registry_client=registry,
        draft_store_client=db,
    )


def store(db) -> DraftStore:
    return DraftStore(SUPPLEMENT_TABLE, REGISTRY_ARN, db)


def adopt_published(db, record_id=RECORD_ID):
    """取り込み前から公開されていたレコードとして、取り込み（#16）が書く管理情報を置く。"""
    put(
        db,
        "MANAGEMENT",
        {
            **{k: v for k, v in METADATA.items() if k != "ownerTeam"},
            "managed": True,
            "ownerTeam": "team-payments",
            "publishedVersion": "1.0.0",
            "adoptedPublishedVersion": "1.0.0",
        },
        record_id,
    )


def add_adopted_version(db, version):
    """ポータル経由のレコードに、取り込み前の公開版があった状態を管理情報に書き足す。"""
    put(db, "MANAGEMENT", {**stored(db, "MANAGEMENT"), "adoptedPublishedVersion": version})


def put_intent(db, *, op_id=OPERATION_ID, sk=None, **overrides):
    """結果確認中のまま残った公開承認の INTENT（決裁 handler が開始時に書く内容）を置く。"""
    put(
        db,
        sk or f"AUDIT#{op_id}#INTENT",
        {
            "operationId": op_id,
            "ownerTeam": "team-payments",
            "principal": CAROL,
            "kind": "APPROVE",
            "state": "IN_PROGRESS",
            "startedAt": "2999-01-01T00:00:00+00:00",
            "reason": REASON,
            "expectedContentHash": "c" * 64,
            "snapshotHash": "d" * 64,
            "recordVersion": "1.0.0",
            **overrides,
        },
    )


def appended_puts(recording: RecordingClient) -> list[dict[str, Any]]:
    """アプリが送った書き込みのうち、操作記録（AUDIT 項目）の追記だけを取り出す。"""
    return [
        item["Put"]
        for transaction in recording.transactions
        for item in transaction
        if "Put" in item and item["Put"]["Item"]["sk"]["S"].startswith("AUDIT#")
    ]


class RecordingClient:
    """DynamoDB クライアントへの書き込みを記録し、Query を1件ずつのページに分けて返す。"""

    def __init__(self, client: Any, *, page_size: int | None = None) -> None:
        self._client = client
        self._page_size = page_size
        self.transactions: list[list[dict[str, Any]]] = []
        self.queries = 0

    def __getattr__(self, name: str) -> Any:
        return getattr(self._client, name)

    def transact_write_items(self, *, TransactItems):
        self.transactions.append(TransactItems)
        return self._client.transact_write_items(TransactItems=TransactItems)

    def query(self, **params):
        self.queries += 1
        if self._page_size is not None:
            params = {**params, "Limit": self._page_size}
        return self._client.query(**params)


@mock_aws
def test_history_checks_role_and_owner_team_before_reading():
    registry, db = clients()
    with Stubber(registry) as stub:
        state = setup_pending(stub, registry, db)
        # Viewer（ロールなし）と所属チームのない Publisher は読めない
        assert history(state["management"], groups="", sub="dave")[0] == 403
        assert history(state["management"], groups="registry-publisher", sub="dave")[0] == 403
        # 他チームの Publisher も読めない
        assert (
            history(state["management"], groups="registry-publisher team-cards", sub="dave")[0]
            == 403
        )
        # 所有チームの Publisher、所属チームによらず Curator と Admin は読める
        assert history(state["management"])[0] == 200
        assert history(state["management"], groups=CURATOR, sub="carol")[0] == 200
        assert history(state["management"], groups=ADMIN, sub="erin")[0] == 200
        stub.assert_no_pending_responses()


@mock_aws
def test_history_of_unknown_record_is_not_found():
    registry, db = clients()
    handler = history_handler(registry, db)
    assert history(handler)[0] == 404
    assert history(handler, "short!")[0] == 400


def approve(state):
    """審査待ちの申請を Curator（carol）が公開承認する。"""
    stub = state["stub"]
    stub.add_response("get_registry_record", {**RECORD, "status": "PENDING_APPROVAL"}, GET)
    stub_status_update(stub)
    stub.add_response(
        "get_registry_record", {**RECORD, "status": "APPROVED", "statusReason": REASON}, GET
    )
    status, _ = decide(state["decision"], snapshot_hash=state["snapshot"]["snapshotHash"])
    assert status == 200


@mock_aws
def test_history_entries_contain_principal_hash_time_result_and_aws_request_id():
    registry, db = clients()
    with Stubber(registry) as stub:
        state = setup_pending(stub, registry, db)
        approve({**state, "stub": stub})

        status, body = history(state["management"])
        assert status == 200
        assert body["recordId"] == RECORD_ID
        assert body["ownerTeam"] == "team-payments"
        entries = body["entries"]
        kinds = [entry["kind"] for entry in entries]
        assert kinds == ["CREATE", "SUBMIT", "APPROVE"]
        for entry in entries:
            assert entry["principal"] is not None
            assert entry["startedAt"]
            assert entry["targetHash"]
            assert entry["operationId"]
        approved = entries[-1]
        assert approved["state"] == "SUCCEEDED"
        assert approved["awsRequestId"] == "aws-decide-1"
        assert approved["reason"] == REASON
        assert entries[0]["state"] == "SUCCEEDED"
        # 作成の対象のハッシュは、作成した内容の内容ハッシュ（依頼の照合用の requestHash ではない）
        create_op = entries[0]["operationId"]
        created = stored(db, f"AUDIT#{create_op}#RESULT")["result"]
        assert entries[0]["targetHash"] == created["contentHash"]
        assert entries[0]["targetHash"] != stored(db, f"AUDIT#{create_op}#INTENT")["requestHash"]


@mock_aws
def test_every_appended_record_is_written_with_no_overwrite_condition():
    """作成・申請・公開承認でアプリが送る操作記録の追記は、すべて上書き禁止の条件式を持つ。"""
    registry, db = clients()
    recording = RecordingClient(db)
    with Stubber(registry) as stub:
        state = setup_pending(stub, registry, recording)
        approve({**state, "stub": stub})

    puts = appended_puts(recording)
    record_pk = store(db).record_pk(RECORD_ID)
    assert {put["Item"]["pk"]["S"] for put in puts} == {
        record_pk,
        store(db).name_lock_pk(CUSTOM_DRAFT["name"]),
    }
    phases = sorted(put["Item"]["sk"]["S"].rsplit("#", 1)[1] for put in puts)
    # 作成は名前ロック側の正本とレコード側の写しの2組、申請と公開承認はレコード側の1組ずつ
    assert phases == ["INTENT"] * 4 + ["RESULT"] * 4
    assert all(put["ConditionExpression"] == "attribute_not_exists(pk)" for put in puts)


@mock_aws
def test_settled_operation_cannot_be_appended_again():
    """確定済みの操作の結果を追記し直すと、補助ストアの条件式で拒否され、既存の記録は変わらない。"""
    registry, db = clients()
    with Stubber(registry) as stub:
        state = setup_pending(stub, registry, db)
    operation = store(db).get_operation(state["submitted"]["submissionId"])
    assert operation is not None and operation["state"] == "SUCCEEDED"
    with pytest.raises(StoreConflict):
        store(db).abort(operation, "OVERWRITTEN")
    result = stored(db, f"AUDIT#{operation['operationId']}#RESULT")
    assert "errorCode" not in result


@mock_aws
def test_create_copies_its_intent_to_the_record_without_later_fields_or_the_definition():
    """作成の INTENT のレコード側の写しは、開始時の意図に揃え、定義の全文を重ねて保存しない。"""
    registry, db = clients()
    with Stubber(registry) as stub:
        setup_pending(stub, registry, db)
    (create,) = [
        record
        for record in store(db).list_appended_records(RECORD_ID)
        if record.phase == "INTENT" and record.item["kind"] == "CREATE"
    ]
    origin = store(db).get(
        store(db).name_lock_pk(CUSTOM_DRAFT["name"]), f"AUDIT#{create.operation_id}#INTENT"
    )
    assert origin is not None
    keys = {"pk", "sk"}
    # 写しは、正本から定義の全文を除いたものと一致し、開始後に書き足した項目を含まない
    assert {k: v for k, v in create.item.items() if k not in keys} == {
        k: v for k, v in origin.items() if k not in keys | {"draft"}
    }
    assert {"recordId", "awsRequestId", "draft"}.isdisjoint(create.item)
    assert create.item["principal"] == ALICE


@mock_aws
def test_create_awaiting_its_result_already_appears_in_the_history():
    """作成が結果確認中のまま止まっても、管理情報と同時にレコードの履歴へ現れる。"""
    registry, db = clients()
    with Stubber(registry) as stub:
        stub_create(
            stub,
            CUSTOM_DRAFT,
            CUSTOM_DESCRIPTORS,
            META,
            returned={
                **aws_record(CUSTOM_DRAFT, descriptors=CUSTOM_DESCRIPTORS, customMetadata=META),
                "status": "CREATING",
            },
        )
        handler = history_handler(registry, db)
        status, _ = call(handler, event(body=CUSTOM_DRAFT))
        assert status == 202
        status, body = history(handler)
    assert status == 200
    (entry,) = body["entries"]
    assert entry["kind"] == "CREATE"
    assert entry["state"] == "VERIFYING"
    assert entry["principal"] == ALICE
    # 作成した内容の内容ハッシュはまだ確定していない
    assert entry["targetHash"] is None


@mock_aws
def test_history_shows_unknown_decider_for_pre_existing_published_records():
    """取り込み前から公開されていたカタログ項目は、公開承認した主体を「不明」として返す。"""
    registry, db = clients()
    adopt_published(db)
    status, body = history(history_handler(registry, db))
    assert status == 200
    (entry,) = body["entries"]
    assert entry["kind"] == "APPROVE"
    assert entry["principal"] is None
    assert entry["state"] == "UNKNOWN"
    assert entry["operationId"] is None
    assert entry["recordVersion"] == "1.0.0"


@mock_aws
def test_history_of_record_without_operations_is_empty():
    """管理下にあり操作記録がないレコードは、404 ではなく 0件の 200 を返す。"""
    registry, db = clients()
    put(db, "MANAGEMENT", {"managed": True, "ownerTeam": "team-payments"})
    status, body = history(history_handler(registry, db))
    assert status == 200
    assert body["entries"] == []


@mock_aws
def test_unknown_decider_remains_after_a_later_portal_approval():
    """取り込み後にポータルで別の版を公開承認しても、取り込み前の版の「不明」は履歴から消えない。"""
    registry, db = clients()
    with Stubber(registry) as stub:
        state = setup_pending(stub, registry, db)
        add_adopted_version(db, "0.9.0")
        approve({**state, "stub": stub})

        status, body = history(state["management"])
    assert status == 200
    states = [(entry["kind"], entry["state"]) for entry in body["entries"]]
    assert states[0] == ("APPROVE", "UNKNOWN")
    assert body["entries"][0]["recordVersion"] == "0.9.0"
    assert ("APPROVE", "SUCCEEDED") in states


@mock_aws
def test_unsettled_decision_is_shown_in_full_only_to_the_deciding_curator():
    """結果確認中の決裁の中身と操作 ID は、決裁した Curator 本人にだけ返す（ADR-0010）。"""
    registry, db = clients()
    with Stubber(registry) as stub:
        state = setup_pending(stub, registry, db)
        put_intent(db)
        status, own = history(state["management"], groups=CURATOR, sub="carol")
        assert status == 200
        decided = own["entries"][-1]
        assert decided["kind"] == "APPROVE"
        assert decided["state"] == "VERIFYING"
        assert decided["operationId"] == OPERATION_ID
        assert decided["reason"] == REASON
        assert decided["recordVersion"] == "1.0.0"
        assert "withheld" not in decided

        for viewer in (
            {},  # 所有チームの Publisher（alice）
            {"groups": CURATOR, "sub": "dave"},  # 決裁していない Curator
            {"groups": ADMIN, "sub": "erin"},
        ):
            status, body = history(state["management"], **viewer)
            assert status == 200
            assert body["entries"][-1] == {
                "operationId": None,
                "kind": "APPROVE",
                "principal": CAROL,
                "startedAt": "2999-01-01T00:00:00+00:00",
                "targetHash": None,
                "state": "VERIFYING",
                "withheld": True,
            }


@mock_aws
def test_history_reads_every_query_page():
    """Query の応答が複数ページに分かれても、すべての操作記録を履歴に含める。"""
    registry, db = clients()
    with Stubber(registry) as stub:
        setup_pending(stub, registry, db)
    paged = RecordingClient(db, page_size=1)
    status, body = history(history_handler(registry, paged))
    assert status == 200
    assert [entry["kind"] for entry in body["entries"]] == ["CREATE", "SUBMIT"]
    assert paged.queries > 1


@pytest.mark.parametrize(
    "malformed",
    [
        pytest.param({"sk": "AUDIT#broken"}, id="ソートキーの形式違反"),
        pytest.param({"principal": None}, id="主体の欠落"),
        pytest.param({"kind": "DELETE"}, id="未知の種別"),
        pytest.param({"startedAt": 1}, id="開始時刻の型違反"),
        pytest.param({"sk": f"AUDIT#{OPERATION_ID}#RESULT"}, id="INTENT のない RESULT"),
    ],
)
@mock_aws
def test_malformed_operation_record_is_reported_instead_of_hidden(malformed):
    """形式の崩れた操作記録は捨てたり欠けた項目のまま返したりせず、保存内容の異常として返す。"""
    registry, db = clients()
    with Stubber(registry) as stub:
        state = setup_pending(stub, registry, db)
        put_intent(db, **malformed)
        status, body = history(state["management"])
    assert status == 502
    assert body["error"]["code"] == "HISTORY_UNAVAILABLE"
    assert "形式が正しくありません" in body["error"]["message"]


@mock_aws
def test_history_without_supplement_table_reports_not_configured():
    """補助テーブルが未設定なら、500 ではなく未設定のエラーを返す。"""
    registry, _ = clients()
    handler = create_management_handler({**ENV}, registry_client=registry)
    status, body = history(handler, groups=CURATOR, sub="carol")
    assert status == 503
    assert body["error"]["code"] == "DRAFTS_NOT_CONFIGURED"

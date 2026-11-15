"""検証スクリプト（scripts/verify_suspend_dual_revision.py）のロジックのテスト（#33）。

スクリプトは人間が dev 環境で実行するため、このテストでは AWS に接続しない。
AWS に触る部分は薄く保ち、ペイロード構築・応答の解釈・レポート整形などの純粋なロジックは、
botocore Stubber とフェイクの transport に差し替えて検証する。
"""

import json
import os

import boto3
from botocore.stub import ANY, Stubber

import verify_suspend_dual_revision as verify

REGISTRY_ARN = "arn:aws:agent-registry:us-east-1:123456789012:registry/abcd0123efgh4567"
REGISTRY_ID = "abcd0123efgh4567"
RECORD_ID = "test0123abcd"


def registry_client():
    return boto3.client(
        "agent-registry-control",
        region_name="us-east-1",
        aws_access_key_id="test",
        aws_secret_access_key="test",
    )


# --- URL とペイロードの構築 ---


def test_batch_and_mcp_urls_follow_aws_documented_shapes():
    # 発見 API の宛先は、ポータルの DiscoverySettings と同じ構成（config.py）
    assert verify.discovery_batch_url("us-east-1") == (
        "https://agent-registry.us-east-1.api.aws/discoverable-records-batch"
    )
    # MCP endpoint の宛先は、AWS 文書（Using the Registry MCP endpoint）の例と同じ構成
    assert verify.mcp_endpoint_url(REGISTRY_ARN) == (
        f"https://agent-registry.us-east-1.api.aws/registry/{REGISTRY_ID}/mcp"
    )


def test_create_record_payload_matches_create_registry_record():
    payload = verify.create_record_payload(REGISTRY_ARN, "suspend-verify-20261115", "1.0.0")
    assert payload["registryId"] == REGISTRY_ARN
    assert payload["name"] == "suspend-verify-20261115"
    assert payload["recordType"] == "CUSTOM"
    assert payload["recordVersion"] == "1.0.0"
    # custom descriptor は canonical JSON を data に持つ（ポータルの下書きと同じ形）
    data = json.loads(payload["descriptors"]["custom"]["data"])
    assert data["purpose"] != ""
    assert "clientToken" in payload
    assert payload["customMetadata"]["environment"] == "dev"


def test_edit_record_payload_matches_update_registry_record_shape():
    """編集の payload は、ポータルの RecordDraft.update_payload と同じ構造にする。

    ポータルの実装は AWS の UpdateRegistryRecord の契約に沿って動いているため、
    optionalValue の入れ子などの構造が一致することが独立した根拠になる。
    """
    payload = verify.edit_record_payload("1.0.1")
    # recordVersion はプレーン、他は optionalValue で包む
    assert payload["recordVersion"] == "1.0.1"
    assert set(payload) == {
        "displayName",
        "description",
        "recordVersion",
        "customMetadata",
        "descriptors",
    }
    assert payload["displayName"] == {"optionalValue": verify.DISPLAY_NAME}
    assert payload["description"] == {"optionalValue": verify.DESCRIPTION}
    assert set(payload["customMetadata"]["optionalValue"]) == {
        "environment",
        "dataClassification",
    }
    descriptor = payload["descriptors"]["optionalValue"]["custom"]["optionalValue"]
    assert set(descriptor) == {"data"}
    data = json.loads(descriptor["data"]["optionalValue"])
    assert data == verify.custom_definition()


def test_mcp_tool_call_is_jsonrpc_tools_call():
    payload = verify.mcp_batch_get_call(call_id=7, record_id=RECORD_ID)
    # AWS 文書の curl 例と同じ形（JSON-RPC 2.0 の tools/call）
    assert payload == {
        "jsonrpc": "2.0",
        "id": 7,
        "method": "tools/call",
        "params": {
            "name": "batch_get_discoverable_registry_record",
            "arguments": {"recordIds": [RECORD_ID]},
        },
    }


def test_discovery_batch_payload_identifies_the_registry_and_record():
    assert verify.batch_get_payload(REGISTRY_ARN, RECORD_ID) == {
        "entries": [{"registryId": REGISTRY_ARN, "recordIds": [RECORD_ID]}]
    }


# --- 応答の解釈 ---


def batch_body(version="1.0.0"):
    record = {
        "recordId": RECORD_ID,
        "recordVersion": version,
        "status": "APPROVED",
        "name": "suspend-verify-20261115",
    }
    return {"registryRecords": [record], "errors": []}


def test_batch_sighting_reads_the_returned_version():
    sighting = verify.parse_batch_response(batch_body("1.0.0"), RECORD_ID)
    assert sighting.found is True
    assert sighting.record_version == "1.0.0"


def test_batch_sighting_reports_resource_not_found():
    body = {
        "registryRecords": [],
        "errors": [
            {"recordId": RECORD_ID, "errorCode": "RESOURCE_NOT_FOUND", "errorMessage": "not found"}
        ],
    }
    sighting = verify.parse_batch_response(body, RECORD_ID)
    assert sighting.found is False
    assert sighting.record_version is None
    assert "RESOURCE_NOT_FOUND" in sighting.detail


def test_batch_sighting_reports_unexpected_shapes_and_http_errors():
    # 形の異なる応答は「見つからない」にせず、想定外として区別する
    sighting = verify.parse_batch_response({"unexpected": True}, RECORD_ID)
    assert sighting.found is False and "想定外" in sighting.detail
    # bytes（HTTP 応答本文）も同じ扱い
    sighting = verify.parse_batch_response(b"not json", RECORD_ID)
    assert sighting.found is False and "想定外" in sighting.detail


def test_mcp_sighting_reads_the_tool_result_text():
    # MCP endpoint は result.content[].text に JSON を返す。batch と同じ中身と仮定して解釈する
    body = {
        "jsonrpc": "2.0",
        "id": 1,
        "result": {
            "content": [
                {
                    "type": "text",
                    "text": json.dumps(batch_body("1.0.0"), ensure_ascii=False),
                }
            ],
            "isError": False,
        },
    }
    sighting = verify.parse_mcp_response(body, RECORD_ID)
    assert sighting.found is True
    assert sighting.record_version == "1.0.0"


def test_mcp_sighting_reports_tool_errors_and_plain_text():
    # ツールがエラーを返した場合（isError: true）は本文をそのまま記録する
    body = {
        "jsonrpc": "2.0",
        "id": 1,
        "result": {
            "content": [{"type": "text", "text": "Error: Invalid request parameters"}],
            "isError": True,
        },
    }
    sighting = verify.parse_mcp_response(body, RECORD_ID)
    assert sighting.found is False
    assert "Error: Invalid request parameters" in sighting.detail
    # JSON-RPC の error も記録する
    rpc_error = {"jsonrpc": "2.0", "id": 1, "error": {"code": -32000, "message": "x"}}
    sighting = verify.parse_mcp_response(rpc_error, RECORD_ID)
    assert sighting.found is False
    # text が JSON でなければ生の本文を記録する
    body["result"]["isError"] = False
    sighting = verify.parse_mcp_response(body, RECORD_ID)
    assert sighting.found is False
    assert "Error: Invalid request parameters" in sighting.detail


# --- M2M トークンの取得 ---


def test_token_request_is_client_credentials_with_basic_auth():
    headers, body = verify.token_request("client-id", "secret", "")
    assert headers["authorization"].startswith("Basic ")
    assert headers["content-type"] == "application/x-www-form-urlencoded"
    assert body == b"grant_type=client_credentials"
    _, body_with_scope = verify.token_request("client-id", "secret", "registry/read")
    assert b"scope=registry%2Fread" in body_with_scope


def test_parse_token_response_returns_the_access_token():
    token = verify.parse_token_response(b'{"access_token": "abc.def", "expires_in": 3600}')
    assert token == "abc.def"
    import pytest

    with pytest.raises(ValueError):
        verify.parse_token_response(b'{"error": "invalid_client"}')


# --- ケース A/B/C のシーケンス（boto3 は Stubber、HTTP はフェイクに差し替える） ---

NAME = "suspend-verify-20261115"
RECORD_ARN = f"{REGISTRY_ARN}/record/{RECORD_ID}"
GET_PARAMS = {"registryId": REGISTRY_ARN, "recordId": RECORD_ID}
APPROVE_REASON = "Issue #33 の検証のため承認する"
REJECT_REASON = "Issue #33 の検証のため却下する"


def aws_record(status, version):
    return {
        "registryArn": REGISTRY_ARN,
        "recordArn": RECORD_ARN,
        "recordId": RECORD_ID,
        "name": NAME,
        "displayName": verify.DISPLAY_NAME,
        "description": verify.DESCRIPTION,
        "recordType": "CUSTOM",
        "recordVersion": version,
        "status": status,
        "descriptors": {"custom": {"data": json.dumps(verify.custom_definition())}},
        "customMetadata": {"environment": "dev", "dataClassification": "private"},
        "createdAt": "2026-11-15T00:00:00Z",
        "updatedAt": "2026-11-15T00:00:00Z",
    }


def registry_info(rules=None):
    return {
        "name": "registry",
        "registryId": REGISTRY_ID,
        "registryArn": REGISTRY_ARN,
        "status": "ACTIVE",
        "createdAt": "2026-11-15T00:00:00Z",
        "updatedAt": "2026-11-15T00:00:00Z",
        "approvalConfiguration": {"autoApprovalRules": rules or []},
    }


def status_response(target, reason):
    return {
        "registryArn": REGISTRY_ARN,
        "recordArn": RECORD_ARN,
        "recordId": RECORD_ID,
        "status": target,
        "statusReason": reason,
        "updatedAt": "2026-11-15T00:00:00Z",
    }


def mutate_response(status="UPDATING"):
    return {
        "registryArn": REGISTRY_ARN,
        "recordArn": RECORD_ARN,
        "recordId": RECORD_ID,
        "name": NAME,
        "recordType": "CUSTOM",
        "status": status,
        "createdAt": "2026-11-15T00:00:00Z",
        "updatedAt": "2026-11-15T00:00:00Z",
    }


class FakeTransport:
    """URL ごとに並べた応答を順に返し、呼び出しを記録するフェイク。"""

    def __init__(self):
        self.calls: list[tuple[str, str, dict, bytes]] = []
        self.responses: dict[str, list[verify.TransportResponse]] = {}

    def queue(self, url_suffix: str, response: verify.TransportResponse) -> "FakeTransport":
        self.responses.setdefault(url_suffix, []).append(response)
        return self

    def __call__(self, method, url, headers, body, timeout):
        self.calls.append((method, url, dict(headers), body))
        for suffix, pending in self.responses.items():
            if url.endswith(suffix) and pending:
                return pending.pop(0)
        raise AssertionError(f"フェイクが想定していない URL です: {url}")


def json_http_response(payload) -> verify.TransportResponse:
    return verify.TransportResponse(200, json.dumps(payload, ensure_ascii=False).encode())


def sighting_responses(transport, version):
    """発見 API と MCP endpoint が承認済み版を返す応答を並べる。"""
    transport.queue("/discoverable-records-batch", json_http_response(batch_body(version)))
    transport.queue(
        "/mcp",
        json_http_response(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "result": {
                    "content": [
                        {
                            "type": "text",
                            "text": json.dumps(batch_body(version), ensure_ascii=False),
                        }
                    ],
                    "isError": False,
                },
            }
        ),
    )


def stub_status(stub, target, reason, error=None):
    params = {**GET_PARAMS, "status": target, "statusReason": reason}
    if error:
        stub.add_client_error("update_registry_record_status", error, expected_params=params)
        return
    stub.add_response("update_registry_record_status", status_response(target, reason), params)


def stub_edit(stub, version):
    stub.add_response(
        "update_registry_record",
        mutate_response(),
        {
            **GET_PARAMS,
            "displayName": {"optionalValue": verify.DISPLAY_NAME},
            "description": {"optionalValue": verify.DESCRIPTION},
            "recordVersion": version,
            "customMetadata": {
                "optionalValue": {"environment": "dev", "dataClassification": "private"}
            },
            "descriptors": {
                "optionalValue": {"custom": {"optionalValue": {"data": {"optionalValue": ANY}}}}
            },
        },
    )


def stub_submit(stub):
    stub.add_response(
        "submit_registry_record_for_approval",
        {
            "registryArn": REGISTRY_ARN,
            "recordArn": RECORD_ARN,
            "recordId": RECORD_ID,
            "status": "PENDING_APPROVAL",
            "updatedAt": "2026-11-15T00:00:00Z",
        },
        GET_PARAMS,
    )


def stub_setup(stub):
    """検証用レコードを作り、承認してベースラインの観測を作る応答を並べる。"""
    stub.add_response("get_registry", registry_info(), {"registryId": REGISTRY_ARN})
    stub.add_response(
        "create_registry_record",
        {"recordArn": RECORD_ARN, "status": "CREATING"},
        {
            "registryId": REGISTRY_ARN,
            "name": NAME,
            "displayName": verify.DISPLAY_NAME,
            "description": verify.DESCRIPTION,
            "recordType": "CUSTOM",
            "recordVersion": "1.0.0",
            "descriptors": {"custom": {"data": ANY}},
            "customMetadata": {"environment": "dev", "dataClassification": "private"},
            "clientToken": ANY,
        },
    )
    stub.add_response("get_registry_record", aws_record("DRAFT", "1.0.0"), GET_PARAMS)
    stub_submit(stub)
    stub.add_response("get_registry_record", aws_record("PENDING_APPROVAL", "1.0.0"), GET_PARAMS)
    stub_status(stub, "APPROVED", APPROVE_REASON)
    stub.add_response("get_registry_record", aws_record("APPROVED", "1.0.0"), GET_PARAMS)


def stub_reapprove(stub, version):
    """承認済み版を作り直す応答を並べる（編集 → 申請 → 承認）。"""
    stub_edit(stub, version)
    stub.add_response("get_registry_record", aws_record("DRAFT", version), GET_PARAMS)
    stub_submit(stub)
    stub.add_response("get_registry_record", aws_record("PENDING_APPROVAL", version), GET_PARAMS)
    stub_status(stub, "APPROVED", APPROVE_REASON)
    stub.add_response("get_registry_record", aws_record("APPROVED", version), GET_PARAMS)


def make_control():
    registry = registry_client()
    stub = Stubber(registry)
    control = verify.RegistryControl(registry, REGISTRY_ARN)
    return registry, stub, control


def test_run_verification_executes_cases_when_rejections_are_accepted():
    registry, stub, control = make_control()
    transport = FakeTransport()
    stub_setup(stub)
    sighting_responses(transport, "1.0.0")  # ベースライン
    # ケース A: 編集して DRAFT にする
    stub_edit(stub, "1.0.1")
    stub.add_response("get_registry_record", aws_record("DRAFT", "1.0.1"), GET_PARAMS)
    sighting_responses(transport, "1.0.0")  # REJECT 前
    stub_status(stub, "REJECTED", REJECT_REASON)
    stub.add_response("get_registry_record", aws_record("REJECTED", "1.0.1"), GET_PARAMS)
    sighting_responses(transport, "1.0.0")  # REJECT 後（承認済み版が消えるか）
    # 復旧: 新しい版を承認し直す
    stub_reapprove(stub, "1.0.2")
    # ケース B: 編集して申請する（PENDING_APPROVAL）
    stub_edit(stub, "1.0.3")
    stub.add_response("get_registry_record", aws_record("DRAFT", "1.0.3"), GET_PARAMS)
    stub_submit(stub)
    stub.add_response("get_registry_record", aws_record("PENDING_APPROVAL", "1.0.3"), GET_PARAMS)
    sighting_responses(transport, "1.0.2")  # REJECT 前
    stub_status(stub, "REJECTED", REJECT_REASON)
    stub.add_response("get_registry_record", aws_record("REJECTED", "1.0.3"), GET_PARAMS)
    sighting_responses(transport, "1.0.2")  # REJECT 後（ケース C の観測を兼ねる）

    with stub:
        report = verify.run_verification(control, transport, NAME, token="token-value")
        stub.assert_no_pending_responses()

    # ベースライン: 承認した版が発見 API と MCP endpoint の両方に現れる
    assert report.baseline.after[0].record_version == "1.0.0"
    assert report.baseline.after[1].record_version == "1.0.0"
    # ケース A: 最新版 DRAFT への REJECT は受け付けられ、承認済み版の観測を記録する
    case_a = report.cases[0]
    assert "DRAFT" in case_a.label
    assert case_a.attempt is not None and case_a.attempt.accepted is True
    assert case_a.before[0].record_version == "1.0.0"
    assert case_a.after is not None and case_a.after[0].record_version == "1.0.0"
    # ケース B: 最新版 PENDING_APPROVAL への REJECT
    case_b = report.cases[1]
    assert "PENDING_APPROVAL" in case_b.label
    assert case_b.attempt is not None and case_b.attempt.accepted is True
    assert case_b.before[0].record_version == "1.0.2"
    assert case_b.after is not None and case_b.after[1].record_version == "1.0.2"
    # ケース C: 最新版 REJECTED のときの観測（B の受理後に見たもの）
    case_c = report.cases[2]
    assert "REJECTED" in case_c.label
    assert case_c.attempt is None
    assert case_c.after is not None and case_c.after[0].record_version == "1.0.2"
    # 発見 API と MCP endpoint の宛先と認証
    batch_calls = [c for c in transport.calls if c[1].endswith("/discoverable-records-batch")]
    mcp_calls = [c for c in transport.calls if c[1].endswith("/mcp")]
    assert len(batch_calls) == 5 and len(mcp_calls) == 5
    assert all(c[2]["authorization"] == "Bearer token-value" for c in transport.calls)


def test_run_verification_records_rejected_status_updates_without_after_sightings():
    registry, stub, control = make_control()
    transport = FakeTransport()
    stub_setup(stub)
    sighting_responses(transport, "1.0.0")
    # ケース A: REJECT が受け付けられない（ConflictException）
    stub_edit(stub, "1.0.1")
    stub.add_response("get_registry_record", aws_record("DRAFT", "1.0.1"), GET_PARAMS)
    sighting_responses(transport, "1.0.0")
    stub_status(stub, "REJECTED", REJECT_REASON, error="ConflictException")
    # 復旧: DRAFT のままなので、編集して承認し直す
    stub_reapprove(stub, "1.0.2")
    # ケース B: 申請してから REJECT を試みるが、やはり受け付けられない
    stub_edit(stub, "1.0.3")
    stub.add_response("get_registry_record", aws_record("DRAFT", "1.0.3"), GET_PARAMS)
    stub_submit(stub)
    stub.add_response("get_registry_record", aws_record("PENDING_APPROVAL", "1.0.3"), GET_PARAMS)
    sighting_responses(transport, "1.0.2")
    stub_status(stub, "REJECTED", REJECT_REASON, error="ConflictException")

    with stub:
        report = verify.run_verification(control, transport, NAME, token="token-value")
        stub.assert_no_pending_responses()

    case_a, case_b, case_c = report.cases
    assert case_a.attempt is not None and case_a.attempt.accepted is False
    assert case_a.attempt.error_code == "ConflictException"
    assert case_a.after is None  # 受け付けられていないため、REJECT 後の観測はない
    assert case_b.attempt is not None and case_b.attempt.accepted is False
    assert case_b.after is None
    # ケース C の状態（最新版 REJECTED）は作れなかったことを記録する
    assert case_c.after is None
    assert any("作れませんでした" in note for note in report.notes)


def test_run_verification_stops_when_auto_approval_is_enabled():
    registry, stub, control = make_control()
    transport = FakeTransport()
    stub.add_response(
        "get_registry", registry_info(rules=["APPROVE_ALL"]), {"registryId": REGISTRY_ARN}
    )
    import pytest

    with stub, pytest.raises(RuntimeError) as excinfo:
        verify.run_verification(control, transport, NAME, token="token-value")
    assert "自動承認" in str(excinfo.value)


# --- 後始末 ---


def test_cleanup_deletes_the_record_when_allowed():
    registry, stub, control = make_control()
    stub.add_response("delete_registry_record", {}, GET_PARAMS)
    with stub:
        result = verify.cleanup_record(control, RECORD_ID)
        stub.assert_no_pending_responses()
    assert "削除" in result


def test_cleanup_deprecates_the_record_when_delete_is_rejected():
    registry, stub, control = make_control()
    stub.add_client_error("delete_registry_record", "ConflictException", expected_params=GET_PARAMS)
    stub_status(stub, "DEPRECATED", "Issue #33 の検証の後始末")
    with stub:
        result = verify.cleanup_record(control, RECORD_ID)
        stub.assert_no_pending_responses()
    assert "DEPRECATED" in result


# --- レポート整形 ---


def sighting(source, found, version, detail=""):
    return verify.Sighting(source, found, version, detail)


def make_report(**case_overrides) -> verify.Report:
    defaults = dict(
        label="A: 最新版 DRAFT",
        latest_status="DRAFT",
        latest_version="1.0.1",
        approved_version="1.0.0",
        attempt=verify.StatusAttempt(True, None, "受け付けられました"),
        before=(sighting("発見 API", True, "1.0.0"), sighting("MCP endpoint", True, "1.0.0")),
        after=(sighting("発見 API", True, "1.0.0"), sighting("MCP endpoint", True, "1.0.0")),
    )
    case = verify.CaseResult(**{**defaults, **case_overrides})
    return verify.Report(
        registry_arn=REGISTRY_ARN,
        record_name=NAME,
        record_id=RECORD_ID,
        executed_at="2026-11-15T05:30:00Z",
        auto_approval="無効（手動審査）",
        baseline=verify.CaseResult(
            label="事前確認（承認済みのみ）",
            latest_status="APPROVED",
            latest_version="1.0.0",
            approved_version="1.0.0",
            attempt=None,
            before=(sighting("発見 API", True, "1.0.0"), sighting("MCP endpoint", True, "1.0.0")),
            after=(sighting("発見 API", True, "1.0.0"), sighting("MCP endpoint", True, "1.0.0")),
        ),
        cases=(case,),
    )


def test_render_report_produces_a_markdown_table_for_readme():
    report = make_report()
    text = verify.render_report(report, cleanup_note="削除しました")
    lines = text.strip().splitlines()
    # README に貼れる Markdown: 見出し・メタ情報・表・後始末
    assert lines[0].startswith("# ")
    assert any(REGISTRY_ARN in line for line in lines)
    assert any(RECORD_ID in line for line in lines)
    assert any(NAME in line for line in lines)
    header = next(line for line in lines if line.startswith("| ケース"))
    assert "REJECT の受け付け" in header
    assert any(line.startswith("| --- ") for line in lines)
    case_line = next(line for line in lines if line.startswith("| A: 最新版 DRAFT"))
    assert "DRAFT 1.0.1" in case_line
    assert "受け付けられた" in case_line
    assert "1.0.0 を返した" in case_line
    assert "削除しました" in text


def test_render_report_distinguishes_all_outcomes():
    # 承認済み版と異なる版・見つからない・観測なし・拒否された REJECT を区別して並べる
    report = make_report(
        attempt=verify.StatusAttempt(False, "ConflictException", "AWS が拒否しました"),
        after=(sighting("発見 API", False, None, "RESOURCE_NOT_FOUND"), None),
    )
    text = verify.render_report(report, cleanup_note="DEPRECATED にしました")
    case_line = next(line for line in text.splitlines() if line.startswith("| A: 最新版 DRAFT"))
    assert "拒否された（ConflictException）" in case_line
    assert "返さなかった（RESOURCE_NOT_FOUND）" in case_line
    assert "観測なし" in case_line
    # 承認済み版と異なる版を返した場合は注記を付ける
    report = make_report(
        before=(sighting("発見 API", True, "1.0.1"), sighting("MCP endpoint", True, "1.0.0")),
    )
    text = verify.render_report(report, cleanup_note="削除しました")
    case_line = next(line for line in text.splitlines() if line.startswith("| A: 最新版 DRAFT"))
    assert "1.0.1 を返した" in case_line and "承認済み版" in case_line


def test_render_report_lists_notes_when_a_case_could_not_be_set_up():
    report = make_report(
        after=None,
        attempt=verify.StatusAttempt(False, "ConflictException", "AWS が拒否しました"),
    )
    import dataclasses

    report = dataclasses.replace(
        report,
        notes=(
            "ケース B の REJECT が受け付けられなかったため、C（最新版 REJECTED）の状態は作れませんでした。",
        ),
    )
    text = verify.render_report(report, cleanup_note="削除しました")
    assert "備考" in text
    assert "作れませんでした" in text


# --- 引数解析 ---


def test_parse_args_reads_the_registry_and_token_sources():
    args = verify.parse_args(
        ["--registry-arn", REGISTRY_ARN, "--token", "direct-token", "--name-prefix", "ops-verify"]
    )
    assert args.registry_arn == REGISTRY_ARN
    assert args.token == "direct-token"
    assert args.name_prefix == "ops-verify"
    assert args.keep_record is False


def test_parse_args_reads_credentials_from_the_environment(monkeypatch):
    monkeypatch.setenv("SUSPEND_VERIFY_TOKEN", "env-token")
    args = verify.parse_args(["--registry-arn", REGISTRY_ARN])
    assert args.token == "env-token"
    monkeypatch.delenv("SUSPEND_VERIFY_TOKEN")
    monkeypatch.setenv("SUSPEND_VERIFY_CLIENT_ID", "id")
    monkeypatch.setenv("SUSPEND_VERIFY_CLIENT_SECRET", "secret")
    monkeypatch.setenv("SUSPEND_VERIFY_TOKEN_URL", "https://example.com/token")
    args = verify.parse_args(["--registry-arn", REGISTRY_ARN])
    assert args.client_id == "id" and args.client_secret == "secret"
    assert args.token_url == "https://example.com/token"


def test_help_and_missing_required_options_exit_cleanly(capsys):
    import pytest

    with pytest.raises(SystemExit) as exit_info:
        verify.parse_args(["--help"])
    assert exit_info.value.code == 0
    assert "--registry-arn" in capsys.readouterr().out
    # トークンの指定がなければ使い方を示して止める
    for stale in (
        "SUSPEND_VERIFY_TOKEN",
        "SUSPEND_VERIFY_TOKEN_URL",
        "SUSPEND_VERIFY_CLIENT_ID",
        "SUSPEND_VERIFY_CLIENT_SECRET",
    ):
        os.environ.pop(stale, None)
    with pytest.raises(SystemExit) as exit_info:
        verify.parse_args(["--registry-arn", REGISTRY_ARN])
    assert exit_info.value.code == 2
    assert "--token" in capsys.readouterr().err

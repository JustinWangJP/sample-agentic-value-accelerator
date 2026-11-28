"""検証スクリプト（scripts/verify_suspend_dual_revision.py）のロジックのテスト（#33）。

スクリプトは人間が dev 環境で実行するため、このテストでは AWS に接続しない。
AWS に触る部分は薄く保ち、ペイロード構築・応答の解釈・レポート整形などの純粋なロジックは、
botocore Stubber とフェイクの transport に差し替えて検証する。
"""

import dataclasses
import json

import boto3
import pytest
from botocore.exceptions import EndpointConnectionError
from botocore.stub import ANY, Stubber

import verify_suspend_dual_revision as verify
from registry_portal.domain.drafts import RecordDraft
from registry_portal.services.http import TransportError, TransportResponse

REGISTRY_ARN = "arn:aws:agent-registry:us-east-1:123456789012:registry/abcd0123efgh4567"
REGISTRY_ID = "abcd0123efgh4567"
RECORD_ID = "test0123abcd"
NAME = "suspend-verify-20261115"


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


def test_verification_record_is_a_portal_custom_agent_draft():
    """検証用レコードは、ポータルが作る custom Agent と同じ形（RecordDraft）にする。

    種別・データ分類・environment はポータルの定義に従い、スクリプトに独自の値を持たない。
    """
    draft = verify.verification_draft(NAME, "1.0.0")
    assert isinstance(draft, RecordDraft)
    payload = verify.create_record_payload(REGISTRY_ARN, draft)
    assert payload["registryId"] == REGISTRY_ARN
    assert {
        key: value for key, value in payload.items() if key not in ("registryId", "clientToken")
    } == (draft.create_payload())
    assert payload["recordType"] == "AGENT"
    assert set(payload["descriptors"]) == {"custom"}
    assert json.loads(payload["descriptors"]["custom"]["data"]) == verify.CUSTOM_DEFINITION
    assert payload["customMetadata"]["environment"] == "dev"
    assert payload["customMetadata"]["dataClassification"] == "private"
    # AWS の clientToken は 33〜256 文字
    assert 33 <= len(payload["clientToken"]) <= 256


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


def batch_body(version="1.0.0", status="APPROVED"):
    record = {"recordId": RECORD_ID, "recordVersion": version, "status": status, "name": NAME}
    return {"registryRecords": [record], "errors": []}


def error_body(code):
    return {
        "registryRecords": [],
        "errors": [{"recordId": RECORD_ID, "errorCode": code, "errorMessage": "x"}],
    }


def mcp_body(inner):
    return {
        "jsonrpc": "2.0",
        "id": 1,
        "result": {
            "content": [{"type": "text", "text": json.dumps(inner, ensure_ascii=False)}],
            "isError": False,
        },
    }


def test_batch_sighting_reads_the_returned_version_and_status():
    sighting = verify.parse_batch_response(batch_body("1.0.0"), RECORD_ID)
    assert sighting.outcome == verify.FOUND
    assert sighting.record_version == "1.0.0"
    assert sighting.status == "APPROVED"
    assert sighting.source == verify.DISCOVERY_SOURCE


def test_batch_sighting_treats_only_resource_not_found_as_absent():
    # ポータルの発見アダプターと同じく、RESOURCE_NOT_FOUND だけを「発見できない」とする
    sighting = verify.parse_batch_response(error_body("RESOURCE_NOT_FOUND"), RECORD_ID)
    assert sighting.outcome == verify.ABSENT
    assert sighting.record_version is None
    assert "RESOURCE_NOT_FOUND" in sighting.detail
    # それ以外のエラーコードは判定できなかったものとして区別する
    sighting = verify.parse_batch_response(error_body("ACCESS_DENIED"), RECORD_ID)
    assert sighting.outcome == verify.FAILED
    assert "ACCESS_DENIED" in sighting.detail


def test_batch_sighting_reports_unexpected_shapes_as_failed():
    # 形の異なる応答・対象を含まない応答は「見つからない」にせず、判定できなかったとして区別する
    for body in ({"unexpected": True}, b"not json", {"registryRecords": [], "errors": []}):
        sighting = verify.parse_batch_response(body, RECORD_ID)
        assert sighting.outcome == verify.FAILED


def test_mcp_sighting_reads_the_tool_result_text():
    # MCP endpoint は result.content[].text に JSON を返す。batch と同じ中身と仮定して解釈する
    sighting = verify.parse_mcp_response(mcp_body(batch_body("1.0.0")), RECORD_ID)
    assert sighting.outcome == verify.FOUND
    assert sighting.record_version == "1.0.0"
    assert sighting.source == verify.MCP_SOURCE
    sighting = verify.parse_mcp_response(mcp_body(error_body("RESOURCE_NOT_FOUND")), RECORD_ID)
    assert sighting.outcome == verify.ABSENT


def test_mcp_sighting_reports_tool_errors_and_plain_text_as_failed():
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
    assert sighting.outcome == verify.FAILED
    assert "Error: Invalid request parameters" in sighting.detail
    # JSON-RPC の error も記録する
    rpc_error = {"jsonrpc": "2.0", "id": 1, "error": {"code": -32000, "message": "x"}}
    sighting = verify.parse_mcp_response(rpc_error, RECORD_ID)
    assert sighting.outcome == verify.FAILED and "JSON-RPC" in sighting.detail
    # text が JSON でなければ生の本文を記録する
    body["result"]["isError"] = False
    sighting = verify.parse_mcp_response(body, RECORD_ID)
    assert sighting.outcome == verify.FAILED
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

    with pytest.raises(ValueError):
        verify.parse_token_response(b'{"error": "invalid_client"}')


# --- 発見 API と MCP endpoint の観測（フェイクの transport） ---


class FakeTransport:
    """URL ごとに並べた応答（または例外）を順に返し、呼び出しを記録するフェイク。"""

    def __init__(self):
        self.calls: list[tuple[str, str, dict, bytes]] = []
        self.responses: dict[str, list[TransportResponse | Exception]] = {}

    def queue(self, url_suffix: str, response: TransportResponse | Exception) -> "FakeTransport":
        self.responses.setdefault(url_suffix, []).append(response)
        return self

    def __call__(self, method, url, headers, body, timeout):
        self.calls.append((method, url, dict(headers), body))
        for suffix, pending in self.responses.items():
            if url.endswith(suffix) and pending:
                response = pending.pop(0)
                if isinstance(response, Exception):
                    raise response
                return response
        raise AssertionError(f"フェイクが想定していない URL です: {url}")

    def calls_to(self, suffix):
        return [call for call in self.calls if call[1].endswith(suffix)]


def json_http_response(payload) -> TransportResponse:
    return TransportResponse(200, json.dumps(payload, ensure_ascii=False).encode())


def sighting_responses(transport, version):
    """発見 API と MCP endpoint が指定の版を返す応答を1回分並べる。"""
    transport.queue("/discoverable-records-batch", json_http_response(batch_body(version)))
    transport.queue("/mcp", json_http_response(mcp_body(batch_body(version))))


def settled_sightings(transport, version):
    """観測が安定する（2回続けて同じ結果になる）だけの応答を並べる。"""
    sighting_responses(transport, version)
    sighting_responses(transport, version)


def make_probes(transport, sleeps=None):
    return verify.Probes(
        transport,
        "token-value",
        REGISTRY_ARN,
        settle_seconds=7.0,
        sleep=(sleeps.append if sleeps is not None else lambda _: None),
    )


def test_observe_waits_and_repeats_until_two_observations_agree():
    transport = FakeTransport()
    sleeps: list[float] = []
    # 反映の途中で 1 回目だけ古い版が返り、2・3 回目が一致する
    sighting_responses(transport, "1.0.0")
    settled_sightings(transport, "1.0.1")
    observation = make_probes(transport, sleeps).observe(RECORD_ID)
    assert observation.stable is True
    assert observation.discovery.record_version == "1.0.1"
    # 観測の前ごとに settle_seconds 待つ
    assert sleeps == [7.0, 7.0, 7.0]
    assert all(call[2]["authorization"] == "Bearer token-value" for call in transport.calls)


def test_observe_marks_unstable_observations():
    transport = FakeTransport()
    for version in ("1.0.0", "1.0.1", "1.0.2"):
        sighting_responses(transport, version)
    observation = make_probes(transport).observe(RECORD_ID)
    assert observation.stable is False
    assert observation.discovery.record_version == "1.0.2"


def test_probes_turn_transport_failures_and_http_errors_into_failed_sightings():
    transport = FakeTransport()
    transport.queue("/discoverable-records-batch", TransportError("URLError"))
    transport.queue("/mcp", TransportResponse(406, b"Not Acceptable"))
    probes = make_probes(transport)
    discovery = probes.discovery_api(RECORD_ID)
    assert discovery.outcome == verify.FAILED and "URLError" in discovery.detail
    mcp = probes.mcp_endpoint(RECORD_ID)
    # 200 以外は本文も捨てずに記録する
    assert mcp.outcome == verify.FAILED and mcp.detail == "HTTP 406: Not Acceptable"


def test_http_error_detail_prefers_json_messages_and_truncates_long_bodies():
    body = json.dumps({"message": "Session ID required"}).encode()
    assert verify.http_error_detail(400, body) == "HTTP 400: Session ID required"
    long_text = ("x" * (verify.ERROR_EXCERPT_LIMIT + 50)).encode()
    detail = verify.http_error_detail(500, long_text)
    assert detail.startswith("HTTP 500: xxx") and detail.endswith("…")
    assert len(detail) == len("HTTP 500: ") + verify.ERROR_EXCERPT_LIMIT + 1
    assert verify.http_error_detail(502, b"") == "HTTP 502"


# --- ケース A/B/C のシーケンス（boto3 は Stubber、HTTP はフェイクに差し替える） ---

RECORD_ARN = f"{REGISTRY_ARN}/record/{RECORD_ID}"
GET_PARAMS = {"registryId": REGISTRY_ARN, "recordId": RECORD_ID}


def aws_record(status, version):
    payload = verify.verification_draft(NAME, version).create_payload()
    return {
        **payload,
        "registryArn": REGISTRY_ARN,
        "recordArn": RECORD_ARN,
        "recordId": RECORD_ID,
        "status": status,
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
        "recordType": "AGENT",
        "status": status,
        "createdAt": "2026-11-15T00:00:00Z",
        "updatedAt": "2026-11-15T00:00:00Z",
    }


def stub_get(stub, status, version):
    stub.add_response("get_registry_record", aws_record(status, version), GET_PARAMS)


def stub_status(stub, target, reason, error=None):
    params = {**GET_PARAMS, "status": target, "statusReason": reason}
    if error:
        stub.add_client_error("update_registry_record_status", error, expected_params=params)
        return
    stub.add_response("update_registry_record_status", status_response(target, reason), params)


def stub_new_revision(stub, version):
    """編集して最新版を DRAFT にする（ポータルの update_payload と同じ引数）。"""
    stub.add_response(
        "update_registry_record",
        mutate_response(),
        {**GET_PARAMS, **verify.verification_draft(NAME, version).update_payload()},
    )
    stub_get(stub, "DRAFT", version)


def stub_submit(stub, version):
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
    stub_get(stub, "PENDING_APPROVAL", version)


def stub_approve(stub, version):
    stub_status(stub, "APPROVED", verify.APPROVE_REASON)
    stub_get(stub, "APPROVED", version)


def stub_reject(stub, version, error=None, resulting_status="REJECTED"):
    """REJECT の試行。受理後の読み直しは resulting_status になるまで（ならなければ上限まで）続く。"""
    stub_status(stub, "REJECTED", verify.REJECT_REASON, error=error)
    if error is not None:
        return
    reads = 1 if resulting_status == "REJECTED" else verify.WAIT_ATTEMPTS
    for _ in range(reads):
        stub_get(stub, resulting_status, version)


def stub_baseline(stub, transport):
    """作成済みのレコード（1.0.0）を公開承認し、事前確認の観測までの応答を並べる。"""
    stub_get(stub, "DRAFT", "1.0.0")
    stub_submit(stub, "1.0.0")
    stub_approve(stub, "1.0.0")
    settled_sightings(transport, "1.0.0")


def stub_through_case_b(stub, transport, *, a_error=None, b_error=None, a_status="REJECTED"):
    """事前確認 → ケース A → 公開承認済みの版の作り直し → ケース B までの応答を並べる。

    版は 1.0.0（事前確認）、1.0.1（A）、1.0.2（作り直し）、1.0.3（B）。
    a_status は、A の REJECT が受理された後に管理 API が返す最新版の状態。
    """
    stub_baseline(stub, transport)
    # ケース A: 編集して DRAFT にし、REJECT を試みる
    stub_new_revision(stub, "1.0.1")
    settled_sightings(transport, "1.0.0")  # REJECT 前
    stub_reject(stub, "1.0.1", error=a_error, resulting_status=a_status)
    if a_error is None:
        settled_sightings(transport, "1.0.0")  # REJECT 後
    # 公開承認済みの版を作り直す
    stub_new_revision(stub, "1.0.2")
    stub_submit(stub, "1.0.2")
    stub_approve(stub, "1.0.2")
    # ケース B: 編集して申請し、REJECT を試みる
    stub_new_revision(stub, "1.0.3")
    stub_submit(stub, "1.0.3")
    settled_sightings(transport, "1.0.2")  # REJECT 前
    stub_reject(stub, "1.0.3", error=b_error)
    if b_error is None:
        settled_sightings(transport, "1.0.2")  # REJECT 後


def run(stub, control, transport):
    with stub:
        report = verify.run_verification(control, make_probes(transport), NAME, RECORD_ID)
        stub.assert_no_pending_responses()
    return report


def make_control():
    registry = registry_client()
    return Stubber(registry), verify.RegistryControl(registry, REGISTRY_ARN, sleep=lambda _: None)


def test_create_verification_record_checks_manual_approval_then_creates():
    stub, control = make_control()
    stub.add_response("get_registry", registry_info(), {"registryId": REGISTRY_ARN})
    stub.add_response(
        "create_registry_record",
        {"recordArn": RECORD_ARN, "status": "CREATING"},
        {
            "registryId": REGISTRY_ARN,
            **verify.verification_draft(NAME, verify.INITIAL_VERSION).create_payload(),
            "clientToken": ANY,
        },
    )
    with stub:
        assert verify.create_verification_record(control, NAME) == RECORD_ID
        stub.assert_no_pending_responses()


def test_run_verification_attempts_reject_in_every_case_when_accepted():
    stub, control = make_control()
    transport = FakeTransport()
    stub_through_case_b(stub, transport)
    # ケース C: B の受理で最新版 REJECTED。公開承認済みの版の発見を見て、さらに REJECT を試みる
    settled_sightings(transport, "1.0.2")  # REJECT 前（新しい版を却下した後）
    stub_reject(stub, "1.0.3")
    settled_sightings(transport, "1.0.2")  # REJECT 後

    report = run(stub, control, transport)

    # ベースライン: 公開承認した版が発見 API と MCP endpoint の両方に現れる
    assert report.baseline.before.discovery.record_version == "1.0.0"
    assert report.baseline.before.mcp.record_version == "1.0.0"
    assert report.baseline.after is None
    assert report.baseline is not None
    case_a, case_b, case_c = report.cases
    # 公開承認済みの版は、観測ではなくスクリプトが公開承認した版
    assert case_a.revision == verify.RevisionState("DRAFT", "1.0.1", "1.0.0")
    assert case_a.attempt == verify.StatusAttempt(True, resulting_status="REJECTED")
    assert case_a.after is not None and case_a.after.discovery.record_version == "1.0.0"
    assert case_b.revision == verify.RevisionState("PENDING_APPROVAL", "1.0.3", "1.0.2")
    assert case_b.attempt is not None and case_b.attempt.reached_rejected is True
    # ケース C も REJECT（公開停止）を試み、受付可否を記録する
    assert case_c.revision == verify.RevisionState("REJECTED", "1.0.3", "1.0.2")
    assert case_c.attempt is not None and case_c.attempt.accepted is True
    assert case_c.before.discovery.record_version == "1.0.2"
    assert case_c.after is not None
    assert report.notes == ()
    # 観測は 7 回（ベースライン・A 前後・B 前後・C 前後）、それぞれ安定するまで 2 回ずつ問い合わせる
    assert len(transport.calls_to("/discoverable-records-batch")) == 14
    assert len(transport.calls_to("/mcp")) == 14


def test_run_verification_recreates_case_c_by_case_a_route_when_only_b_is_rejected():
    """B の REJECT が拒否されても A が受理されていれば、A と同じ手順で C の状態を作って試す。"""
    stub, control = make_control()
    transport = FakeTransport()
    stub_through_case_b(stub, transport, b_error="ConflictException")
    # B の版（審査待ち）を公開承認し、下書きの版を作って却下する
    stub_approve(stub, "1.0.3")
    stub_new_revision(stub, "1.0.4")
    stub_reject(stub, "1.0.4")
    # ケース C
    settled_sightings(transport, "1.0.3")
    stub_reject(stub, "1.0.4", error="ConflictException")

    report = run(stub, control, transport)

    case_a, case_b, case_c = report.cases
    assert case_a.attempt is not None and case_a.attempt.accepted is True
    assert case_b.attempt is not None and case_b.attempt.accepted is False
    assert case_b.after is None
    assert case_c.revision == verify.RevisionState("REJECTED", "1.0.4", "1.0.3")
    assert case_c.attempt is not None and case_c.attempt.error_code == "ConflictException"
    assert case_c.after is None
    assert any("A と同じ手順" in note for note in report.notes)


def test_run_verification_records_when_case_c_cannot_be_recreated():
    stub, control = make_control()
    transport = FakeTransport()
    stub_through_case_b(stub, transport, b_error="ConflictException")
    stub_approve(stub, "1.0.3")
    stub_new_revision(stub, "1.0.4")
    stub_reject(stub, "1.0.4", error="ValidationException")

    report = run(stub, control, transport)

    # 状態を作れなかったケース C は行にせず、備考に理由を残す
    assert [case.label for case in report.cases] == [
        "A: 最新版 DRAFT",
        "B: 最新版 PENDING_APPROVAL",
    ]
    assert any(
        "作れませんでした" in note and "ValidationException" in note for note in report.notes
    )


def test_run_verification_skips_case_c_when_both_rejections_are_refused():
    stub, control = make_control()
    transport = FakeTransport()
    stub_through_case_b(stub, transport, a_error="ConflictException", b_error="ConflictException")

    report = run(stub, control, transport)

    case_a, case_b = report.cases
    assert case_a.attempt is not None and case_a.attempt.error_code == "ConflictException"
    assert case_a.after is None  # 受け付けられていないため、REJECT 後の観測はない
    assert case_b.attempt is not None and case_b.attempt.accepted is False
    assert any("作れませんでした" in note for note in report.notes)


def test_run_verification_records_acceptance_that_leaves_latest_not_rejected(monkeypatch):
    """REJECT が受理されても最新版が REJECTED にならない（公開承認済みの版の側が却下された可能性）。

    これは検証で知りたい結果の一つのため、例外で止めずに記録して先へ進む。
    """
    monkeypatch.setattr(verify, "WAIT_ATTEMPTS", 2)
    stub, control = make_control()
    transport = FakeTransport()
    stub_through_case_b(stub, transport, a_status="DRAFT", b_error="ConflictException")

    report = run(stub, control, transport)

    case_a, case_b = report.cases
    assert case_a.attempt == verify.StatusAttempt(True, resulting_status="DRAFT")
    assert case_a.attempt.reached_rejected is False
    assert case_a.after is not None  # 受理されたため、受け付け後の観測は残す
    assert case_b.attempt is not None and case_b.attempt.accepted is False
    # A・B のどちらでも最新版が REJECTED にならなければ、ケース C の状態は作れない
    assert any("REJECTED にならなかった" in note for note in report.notes)
    line = next(line for line in verify.render_report(report).splitlines() if "| A:" in line)
    assert "受け付けられた（最新版の状態は DRAFT。REJECTED になりませんでした）" in line


def test_run_verification_keeps_partial_results_when_interrupted():
    """途中で例外が起きても、それまでの結果と中断の理由を Report に残す。"""
    stub, control = make_control()
    transport = FakeTransport()
    stub_baseline(stub, transport)
    stub.add_client_error("update_registry_record", "ThrottlingException")  # ケース A の編集

    report = run(stub, control, transport)

    assert report.baseline is not None
    assert report.baseline.before.discovery.record_version == "1.0.0"
    assert report.cases == ()
    assert any(
        "途中で中断しました" in note and "ThrottlingException" in note for note in report.notes
    )
    # 中断しても表は出力できる
    assert "| 事前確認" in verify.render_report(report)


def test_run_verification_warns_when_baseline_does_not_show_the_approved_version():
    stub, control = make_control()
    transport = FakeTransport()
    stub_get(stub, "DRAFT", "1.0.0")
    stub_submit(stub, "1.0.0")
    stub_approve(stub, "1.0.0")
    for _ in range(2):  # 事前確認: 発見 API は返すが、MCP endpoint は判定できない
        transport.queue("/discoverable-records-batch", json_http_response(batch_body("1.0.0")))
        transport.queue("/mcp", TransportResponse(406, b"Not Acceptable"))
    stub.add_client_error("update_registry_record", "ThrottlingException")

    report = run(stub, control, transport)

    baseline_notes = [note for note in report.notes if note.startswith("事前確認で")]
    assert len(baseline_notes) == 1
    assert "MCP endpoint" in baseline_notes[0] and "断定できません" in baseline_notes[0]


def test_create_verification_record_stops_when_auto_approval_is_enabled():
    stub, control = make_control()
    stub.add_response(
        "get_registry", registry_info(rules=["APPROVE_ALL"]), {"registryId": REGISTRY_ARN}
    )

    with stub, pytest.raises(RuntimeError) as excinfo:
        verify.create_verification_record(control, NAME)
    assert "自動承認" in str(excinfo.value)


# --- 後始末 ---


def test_cleanup_deletes_the_record_when_allowed():
    stub, control = make_control()
    stub.add_response("delete_registry_record", {}, GET_PARAMS)
    with stub:
        result = verify.cleanup_record(control, RECORD_ID)
        stub.assert_no_pending_responses()
    assert "削除" in result


def test_cleanup_deprecates_the_record_when_delete_is_rejected():
    stub, control = make_control()
    stub.add_client_error("delete_registry_record", "ConflictException", expected_params=GET_PARAMS)
    stub_status(stub, "DEPRECATED", verify.CLEANUP_REASON)
    with stub:
        result = verify.cleanup_record(control, RECORD_ID)
        stub.assert_no_pending_responses()
    assert "DEPRECATED" in result


def test_cleanup_asks_for_manual_cleanup_instead_of_raising():
    # 削除も DEPRECATED も拒否されたら、例外にせず手動での後始末を促す（結果の出力を妨げない）
    stub, control = make_control()
    stub.add_client_error("delete_registry_record", "ConflictException", expected_params=GET_PARAMS)
    stub_status(stub, "DEPRECATED", verify.CLEANUP_REASON, error="AccessDeniedException")
    with stub:
        result = verify.cleanup_record(control, RECORD_ID)
        stub.assert_no_pending_responses()
    assert "ConflictException" in result and "AccessDeniedException" in result
    assert RECORD_ID in result and "手動" in result


def test_cleanup_reports_connection_failures_for_manual_cleanup():
    class Unreachable:
        def delete_registry_record(self, **_):
            raise EndpointConnectionError(endpoint_url="https://example.com")

    result = verify.cleanup_record(verify.RegistryControl(Unreachable(), REGISTRY_ARN), RECORD_ID)
    assert "EndpointConnectionError" in result and RECORD_ID in result


# --- main（出力と後始末の順序） ---


def stub_main(monkeypatch, run_verification):
    """main の外部依存を差し替え、出力と後始末の呼び出しを記録する。"""
    events: list[str] = []
    monkeypatch.setattr(verify.boto3, "client", lambda *args, **kwargs: object())
    monkeypatch.setattr(verify, "create_verification_record", lambda control, name: RECORD_ID)
    monkeypatch.setattr(verify, "run_verification", run_verification)
    monkeypatch.setattr(verify, "render_report", lambda report: events.append("report") or "表")
    monkeypatch.setattr(
        verify,
        "cleanup_record",
        lambda control, record_id: events.append("cleanup") or "削除しました",
    )
    return events


def test_main_prints_the_report_before_cleanup(monkeypatch, capsys):
    events = stub_main(monkeypatch, lambda *args: make_report())
    assert verify.main(["--registry-arn", REGISTRY_ARN, "--token", "t"]) == 0
    # 後始末が失敗・中断しても結果を失わないよう、表を先に出力する
    assert events == ["report", "cleanup"]
    out = capsys.readouterr().out
    assert out.index("表") < out.index(f"後始末（レコード {RECORD_ID}）: 削除しました")


def test_main_cleans_up_even_when_interrupted(monkeypatch):
    def interrupted(*args):
        raise KeyboardInterrupt

    events = stub_main(monkeypatch, interrupted)
    with pytest.raises(KeyboardInterrupt):
        verify.main(["--registry-arn", REGISTRY_ARN, "--token", "t"])
    assert events == ["cleanup"]


# --- レポート整形 ---


def found(source, version, status="APPROVED"):
    return verify.Sighting(source, verify.FOUND, version, status)


def observation(version="1.0.0", stable=True) -> verify.Observation:
    """発見 API と MCP endpoint が同じ版を返す観測。"""
    return verify.Observation(
        discovery=found(verify.DISCOVERY_SOURCE, version),
        mcp=found(verify.MCP_SOURCE, version),
        stable=stable,
    )


def make_report(**case_overrides) -> verify.Report:
    defaults = dict(
        label="A: 最新版 DRAFT",
        revision=verify.RevisionState("DRAFT", "1.0.1", "1.0.0"),
        attempt=verify.StatusAttempt(True, resulting_status="REJECTED"),
        before=observation("1.0.0"),
        after=observation("1.0.0"),
    )
    case = verify.CaseResult(**{**defaults, **case_overrides})
    return verify.Report(
        registry_arn=REGISTRY_ARN,
        record_name=NAME,
        record_id=RECORD_ID,
        executed_at="2026-11-15T05:30:00Z",
        auto_approval="無効（手動審査）",
        settle_seconds=15.0,
        baseline=verify.CaseResult(
            label="事前確認（公開承認済みの版のみ）",
            revision=verify.RevisionState("APPROVED", "1.0.0", "1.0.0"),
            attempt=None,
            before=observation("1.0.0"),
            after=None,
        ),
        cases=(case,),
    )


def case_line(text):
    return next(line for line in text.splitlines() if line.startswith("| A: 最新版 DRAFT"))


def test_render_report_produces_a_markdown_table_for_readme():
    text = verify.render_report(make_report())
    lines = text.strip().splitlines()
    # README に貼れる Markdown: 見出し・メタ情報・表（後始末の結果は main が続けて出力する）
    assert lines[0].startswith("# ")
    assert any(REGISTRY_ARN in line for line in lines)
    assert any(RECORD_ID in line for line in lines)
    assert any(NAME in line for line in lines)
    assert any("15 秒待ち" in line for line in lines)
    header = next(line for line in lines if line.startswith("| ケース"))
    assert "REJECT の受け付け" in header and "公開承認済みの版" in header
    assert any(line.startswith("| --- ") for line in lines)
    line = case_line(text)
    assert "DRAFT 1.0.1 | 1.0.0 |" in line
    assert "受け付けられた" in line
    assert "1.0.0 を返した" in line
    # 「返さなかった」と「確認できなかった」の意味を表の下に示す
    assert "確認できなかった" in text and "RESOURCE_NOT_FOUND" in text


def test_render_report_distinguishes_absent_from_failed_sightings():
    report = make_report(
        attempt=verify.StatusAttempt(False, error_code="ConflictException"),
        before=verify.Observation(
            discovery=verify.Sighting(
                verify.DISCOVERY_SOURCE, verify.ABSENT, detail="RESOURCE_NOT_FOUND"
            ),
            mcp=verify.Sighting.failed(verify.MCP_SOURCE, "HTTP 406"),
        ),
        after=None,
    )
    line = case_line(verify.render_report(report))
    assert "拒否された（ConflictException）" in line
    assert "返さなかった（RESOURCE_NOT_FOUND）" in line
    # 通信や形式の異常は「返さなかった」にしない
    assert "確認できなかった（HTTP 406）" in line


def test_render_report_annotates_other_versions_statuses_and_unstable_observations():
    report = make_report(
        before=verify.Observation(
            discovery=found(verify.DISCOVERY_SOURCE, "1.0.1", status="DRAFT"),
            mcp=found(verify.MCP_SOURCE, "1.0.0"),
        ),
        after=observation("1.0.0", stable=False),
    )
    line = case_line(verify.render_report(report))
    assert "1.0.1 を返した（公開承認済みの版 1.0.0 と異なる、status=DRAFT）" in line
    assert "観測が安定しませんでした" in line


def test_render_report_keeps_response_text_inside_one_cell():
    # 応答由来の文字列に改行や | があっても、表の1行・1セルに収める
    report = make_report(
        before=verify.Observation(
            discovery=found(verify.DISCOVERY_SOURCE, "1.0.0"),
            mcp=verify.Sighting.failed(verify.MCP_SOURCE, "HTTP 400: a | b\nc"),
        ),
    )
    line = case_line(verify.render_report(report))
    assert "確認できなかった（HTTP 400: a \\| b c）" in line
    assert line.count(" | ") == 7  # 8 列の区切り


def test_render_report_lists_notes():
    report = dataclasses.replace(
        make_report(), notes=("ケース C の状態（最新版 REJECTED）は作れませんでした。",)
    )
    text = verify.render_report(report)
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
    assert args.settle_seconds == verify.DEFAULT_SETTLE_SECONDS
    assert args.keep_record is False


def test_parse_args_reads_credentials_from_the_environment(monkeypatch):
    monkeypatch.setenv("SUSPEND_VERIFY_TOKEN", "env-token")
    args = verify.parse_args(["--registry-arn", REGISTRY_ARN, "--settle-seconds", "30"])
    assert args.token == "env-token"
    assert args.settle_seconds == 30.0
    monkeypatch.delenv("SUSPEND_VERIFY_TOKEN")
    monkeypatch.setenv("SUSPEND_VERIFY_CLIENT_ID", "id")
    monkeypatch.setenv("SUSPEND_VERIFY_CLIENT_SECRET", "secret")
    monkeypatch.setenv("SUSPEND_VERIFY_TOKEN_URL", "https://example.com/token")
    args = verify.parse_args(["--registry-arn", REGISTRY_ARN])
    assert args.client_id == "id" and args.client_secret == "secret"
    assert args.token_url == "https://example.com/token"


def test_help_and_invalid_options_exit_cleanly(capsys, monkeypatch):
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
        monkeypatch.delenv(stale, raising=False)
    with pytest.raises(SystemExit) as exit_info:
        verify.parse_args(["--registry-arn", REGISTRY_ARN])
    assert exit_info.value.code == 2
    assert "--token" in capsys.readouterr().err
    # 負の待ち時間は受け付けない
    with pytest.raises(SystemExit) as exit_info:
        verify.parse_args(
            ["--registry-arn", REGISTRY_ARN, "--token", "t", "--settle-seconds", "-1"]
        )
    assert exit_info.value.code == 2

#!/usr/bin/env python3
"""Issue #33 の実機検証スクリプト: 二重リビジョンでの却下の挙動を確認する。

人間が dev 環境で実行する runbook であり、ポータルの外から直接 AWS を操作する。

- 管理 API（agent-registry-control）は boto3 で呼ぶ
- 発見 API（BatchGetDiscoverableRegistryRecord）と MCP endpoint（InvokeRegistryMcp 相当の
  tools/call）は、発見用 M2M トークンで HTTP を直接呼ぶ。宛先はポータルの
  DiscoverySettings と同じ構成（backend/src/registry_portal/config.py）
- ケース A（最新版 DRAFT）/ B（最新版 PENDING_APPROVAL）/ C（最新版 REJECTED）を順に実行し、
  UpdateRegistryRecordStatus(REJECTED) の受け付け可否と、承認済み版が発見に残るかを確認する
- 結果は README に貼れる Markdown の表で stdout に出力する

使い方は「--help」または README の「公開停止・廃止・廃止依頼」の節を参照。
AWS に触る部分は薄く保ち、ペイロード構築・応答の解釈・レポート整形は純粋な関数として
切り出してある（backend/tests/test_suspend_verification_script.py が検証する）。
"""

from __future__ import annotations

import argparse
import base64
import itertools
import json
import os
import time
import urllib.error
import urllib.request
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any, Protocol
from urllib.parse import urlencode

import boto3
from botocore.exceptions import ClientError

RECORD_TYPE = "CUSTOM"
DISPLAY_NAME = "公開停止の検証"
DESCRIPTION = "Issue #33 の検証用レコード。スクリプトが自動的に作り、検証後に削除します。"
DEFAULT_NAME_PREFIX = "suspend-verify"
APPROVE_REASON = "Issue #33 の検証のため承認する"
REJECT_REASON = "Issue #33 の検証のため却下する"
CLEANUP_REASON = "Issue #33 の検証の後始末"
HTTP_TIMEOUT_SECONDS = 10.0
# AWS の応答が 202（非同期）のことがあるため、状態の確定を待つ間隔と回数
WAIT_INTERVAL_SECONDS = 5.0
WAIT_ATTEMPTS = 12


def parse_registry_arn(registry_arn: str) -> tuple[str, str]:
    """Registry の ARN から（region, registryId）を取り出す。

    arn:aws:agent-registry:<region>:<account>:registry/<registryId> を想定する。
    """
    region = registry_arn.split(":")[3]
    registry_id = registry_arn.rsplit("/", 1)[1]
    return region, registry_id


def discovery_endpoint(region: str) -> str:
    """発見 API と MCP endpoint のホスト。ポータルの DiscoverySettings と同じ構成。"""
    return f"https://agent-registry.{region}.api.aws"


def discovery_batch_url(region: str) -> str:
    """BatchGetDiscoverableRegistryRecord の宛先。"""
    return f"{discovery_endpoint(region)}/discoverable-records-batch"


def mcp_endpoint_url(registry_arn: str) -> str:
    """MCP endpoint の宛先。registryId は ARN の最後の path 要素。

    AWS 文書「Using the Registry MCP endpoint」:
    https://agent-registry.<region>.api.aws/registry/<registryId>/mcp
    """
    region, registry_id = parse_registry_arn(registry_arn)
    return f"{discovery_endpoint(region)}/registry/{registry_id}/mcp"


def _canonical(value: Mapping[str, Any]) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def custom_definition() -> dict[str, str]:
    """検証用レコードの custom descriptor の中身。"""
    return {
        "purpose": "Issue #33 の二重リビジョンの検証",
        "connectionReference": "https://example.com/suspend-verify",
        "authentication": "なし",
        "contact": "ops@example.com",
        "termsOfUse": "検証専用",
    }


def create_record_payload(registry_arn: str, name: str, version: str) -> dict[str, Any]:
    """CreateRegistryRecord の引数。ポータルの下書き（custom descriptor）と同じ形にする。"""
    return {
        "registryId": registry_arn,
        "name": name,
        "displayName": DISPLAY_NAME,
        "description": DESCRIPTION,
        "recordType": RECORD_TYPE,
        "recordVersion": version,
        "descriptors": {"custom": {"data": _canonical(custom_definition())}},
        "customMetadata": {"environment": "dev", "dataClassification": "private"},
        "clientToken": str(uuid.uuid4()),  # AWS は最小 33 文字を要求する
    }


def edit_record_payload(version: str) -> dict[str, Any]:
    """UpdateRegistryRecord の引数（新しい版の編集）。ポータルの update_payload と同じ形にする。

    descriptors は descriptor ごと・項目ごとに optionalValue で包む
    （domain/drafts.py の update_payload と同じ構造）。
    """
    data = _canonical(custom_definition())
    return {
        "displayName": {"optionalValue": DISPLAY_NAME},
        "description": {"optionalValue": DESCRIPTION},
        "recordVersion": version,
        "customMetadata": {
            "optionalValue": {"environment": "dev", "dataClassification": "private"}
        },
        "descriptors": {
            "optionalValue": {"custom": {"optionalValue": {"data": {"optionalValue": data}}}}
        },
    }


def batch_get_payload(registry_arn: str, record_id: str) -> dict[str, Any]:
    """発見 API（BatchGetDiscoverableRegistryRecord）の本文。"""
    return {"entries": [{"registryId": registry_arn, "recordIds": [record_id]}]}


def mcp_batch_get_call(call_id: int, record_id: str) -> dict[str, Any]:
    """MCP endpoint の tools/call（JSON-RPC 2.0）。AWS 文書の curl 例と同じ形。"""
    return {
        "jsonrpc": "2.0",
        "id": call_id,
        "method": "tools/call",
        "params": {
            "name": "batch_get_discoverable_registry_record",
            "arguments": {"recordIds": [record_id]},
        },
    }


# --- HTTP トランスポート（ポータルの services/http.py と同じ関数型。runbook として単体で動かす） ---
# services/http.py との重複はレビューで確認済みで、backend パッケージに依存せず単体で
# 動かすという設計意図のため、意図的に共有せず独立させている。


@dataclass(frozen=True)
class TransportResponse:
    """HTTP 応答。4xx・5xx も例外にせず、そのまま返す。"""

    status: int
    body: bytes


class TransportError(Exception):
    """接続できない、タイムアウトしたなど、HTTP 応答を得られなかった。"""


class Transport(Protocol):
    def __call__(
        self,
        method: str,
        url: str,
        headers: Mapping[str, str],
        body: bytes | None,
        timeout: float,
    ) -> TransportResponse: ...


def urllib_transport(
    method: str,
    url: str,
    headers: Mapping[str, str],
    body: bytes | None,
    timeout: float,
) -> TransportResponse:
    """urllib による実装。"""
    request = urllib.request.Request(url, data=body, method=method, headers=dict(headers))
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return TransportResponse(status=response.status, body=response.read())
    except urllib.error.HTTPError as error:
        with error:
            return TransportResponse(status=error.code, body=error.read())
    except (urllib.error.URLError, TimeoutError, OSError, ValueError) as error:
        # 例外の詳細（URL やヘッダーの値）を外へ出さない。種類だけを残す
        raise TransportError(type(error).__name__) from None


# --- 観測（発見 API・MCP endpoint の応答の解釈） ---


@dataclass(frozen=True)
class Sighting:
    """発見 API か MCP endpoint でレコードを観測した結果。"""

    source: str  # 「発見 API」/「MCP endpoint」
    found: bool
    record_version: str | None
    detail: str  # 人間が読む補足（エラーコード・想定外の理由）


@dataclass(frozen=True)
class Observation:
    """ある時点での発見 API と MCP endpoint の観測の組。"""

    discovery: Sighting  # 発見 API（BatchGetDiscoverableRegistryRecord）
    mcp: Sighting  # MCP endpoint（tools/call）


def _json_object(raw: bytes | Mapping[str, Any] | None) -> Mapping[str, Any] | None:
    if isinstance(raw, Mapping):
        return raw
    if raw is None:
        return None
    try:
        value = json.loads(raw)
    except ValueError:
        return None
    return value if isinstance(value, Mapping) else None


def _sighting_from_batch(body: Mapping[str, Any] | None, record_id: str, source: str) -> Sighting:
    """BatchGetDiscoverableRegistryRecord 形の本文（MCP ツールの結果も同じ形と仮定）から観測を作る。

    ポータルの発見アダプター（services/discovery.py）と同じ解釈にする。
    """
    if body is None:
        return Sighting(source, False, None, "応答が想定外の形式です")
    records, errors = body.get("registryRecords"), body.get("errors")
    if not isinstance(records, list) or not isinstance(errors, list):
        return Sighting(source, False, None, "応答が想定外の形式です")
    for record in records:
        if isinstance(record, Mapping) and record.get("recordId") == record_id:
            version = record.get("recordVersion")
            return Sighting(
                source,
                True,
                version if isinstance(version, str) else None,
                f"status={record.get('status')}",
            )
    for error in errors:
        if isinstance(error, Mapping) and error.get("recordId") == record_id:
            return Sighting(source, False, None, str(error.get("errorCode") or "不明なエラー"))
    return Sighting(source, False, None, "応答に対象のレコードがありませんでした")


def parse_batch_response(body: bytes | Mapping[str, Any], record_id: str) -> Sighting:
    """発見 API（BatchGetDiscoverableRegistryRecord）の応答を解釈する。"""
    return _sighting_from_batch(_json_object(body), record_id, "発見 API")


def parse_mcp_response(body: bytes | Mapping[str, Any], record_id: str) -> Sighting:
    """MCP endpoint の JSON-RPC 応答を解釈する。

    ツールの結果は result.content[].text に JSON で返る。AWS 文書は text の中身の形を
    明記しないため、JSON なら batch と同じ形と仮定して解釈し、そうでなければ本文をそのまま記録する。
    """
    value = _json_object(body)
    source = "MCP endpoint"
    if value is None:
        return Sighting(source, False, None, "応答が想定外の形式です")
    if isinstance(value.get("error"), Mapping):
        error = value["error"]
        return Sighting(source, False, None, f"JSON-RPC エラー: {error.get('message')}")
    result = value.get("result")
    if not isinstance(result, Mapping):
        return Sighting(source, False, None, "応答に result がありません")
    content = result.get("content")
    texts = (
        [
            item.get("text")
            for item in content
            if isinstance(item, Mapping) and isinstance(item.get("text"), str)
        ]
        if isinstance(content, list)
        else []
    )
    if not texts:
        return Sighting(source, False, None, "応答の content に text がありません")
    text = "\n".join(texts)
    if result.get("isError") is True:
        return Sighting(source, False, None, text)
    inner = _json_object(text)
    if inner is not None:
        return _sighting_from_batch(inner, record_id, source)
    return Sighting(source, False, None, f"ツールの結果が JSON ではありません: {text}")


# --- M2M トークンの取得（発見 API と MCP endpoint の認可は同じ JWT） ---


def token_request(client_id: str, client_secret: str, scope: str) -> tuple[dict[str, str], bytes]:
    """トークンエンドポイントへの要求。client credentials で Basic 認証する。"""
    credentials = f"{client_id}:{client_secret}".encode()
    form = {"grant_type": "client_credentials"}
    if scope:
        form["scope"] = scope
    headers = {
        "authorization": f"Basic {base64.b64encode(credentials).decode()}",
        "content-type": "application/x-www-form-urlencoded",
        "accept": "application/json",
    }
    return headers, urlencode(form).encode()


def parse_token_response(body: bytes) -> str:
    """トークンエンドポイントの応答から access_token を取り出す。不正なら ValueError。"""
    value = _json_object(body)
    token = value.get("access_token") if value is not None else None
    if not isinstance(token, str) or not token:
        raise ValueError("トークンエンドポイントの応答に access_token がありません")
    return token


def fetch_access_token(
    transport: Transport, token_url: str, client_id: str, client_secret: str, scope: str
) -> str:
    """発見用 M2M トークンを client credentials で取得する。"""
    headers, body = token_request(client_id, client_secret, scope)
    response = transport("POST", token_url, headers, body, HTTP_TIMEOUT_SECONDS)
    if response.status != 200:
        raise RuntimeError(
            f"トークンの取得に失敗しました（HTTP {response.status}）。"
            "client_id・client_secret・token_url を確認してください。"
        )
    try:
        return parse_token_response(response.body)
    except ValueError as error:
        raise RuntimeError(f"トークンの取得に失敗しました: {error}") from None


# --- 検証の記録 ---


def client_error_code(error: ClientError) -> str:
    """ClientError からエラーコードを取り出す。なければ「不明なエラー」にする。"""
    return str(error.response.get("Error", {}).get("Code", "不明なエラー"))


@dataclass(frozen=True)
class StatusAttempt:
    """UpdateRegistryRecordStatus(REJECTED) の試行結果。"""

    accepted: bool
    error_code: str | None
    detail: str


@dataclass(frozen=True)
class CaseResult:
    """ケース1つ分の記録。before は REJECT 試行前、after は受理後の観測。"""

    label: str
    latest_status: str
    latest_version: str
    approved_version: str | None
    attempt: StatusAttempt | None
    before: Observation
    after: Observation | None


@dataclass(frozen=True)
class Report:
    """検証全体の記録。README に貼る Markdown は render_report が組み立てる。"""

    registry_arn: str
    record_name: str
    record_id: str
    executed_at: str
    auto_approval: str
    baseline: CaseResult
    cases: tuple[CaseResult, ...]
    notes: tuple[str, ...] = ()


# --- 管理 API（agent-registry-control）の薄いラッパー ---


class RegistryControl:
    """管理 API を呼ぶ。検証の判断（状態の解釈・順序）は持たない。"""

    def __init__(
        self, client: Any, registry_arn: str, sleep: Callable[[float], None] = time.sleep
    ) -> None:
        self._client = client
        self._sleep = sleep
        self.registry = registry_arn

    def get_registry(self) -> Mapping[str, Any]:
        return self._client.get_registry(registryId=self.registry)

    def create_record(self, name: str, version: str) -> str:
        """レコードを作り、recordId を返す（ポータルの drafts と同じ導出）。"""
        response = self._client.create_registry_record(
            **create_record_payload(self.registry, name, version)
        )
        return str(response["recordArn"]).rsplit("/", 1)[1]

    def edit(self, record_id: str, version: str) -> None:
        self._client.update_registry_record(
            registryId=self.registry, recordId=record_id, **edit_record_payload(version)
        )

    def submit(self, record_id: str) -> None:
        self._client.submit_registry_record_for_approval(
            registryId=self.registry, recordId=record_id
        )

    def set_status(self, record_id: str, status: str, reason: str) -> Mapping[str, Any]:
        return self._client.update_registry_record_status(
            registryId=self.registry, recordId=record_id, status=status, statusReason=reason
        )

    def get(self, record_id: str) -> Mapping[str, Any]:
        return self._client.get_registry_record(registryId=self.registry, recordId=record_id)

    def try_delete(self, record_id: str) -> None:
        self._client.delete_registry_record(registryId=self.registry, recordId=record_id)

    def wait_status(self, record_id: str, expected: set[str]) -> Mapping[str, Any]:
        """状態が確定するまで読み直す。AWS は 202（非同期）を返すことがあるため。"""
        record: Mapping[str, Any] = {}
        for _ in range(WAIT_ATTEMPTS):
            record = self.get(record_id)
            if record.get("status") in expected:
                return record
            self._sleep(WAIT_INTERVAL_SECONDS)
        raise RuntimeError(
            f"レコードの状態が {' または '.join(sorted(expected))} になりません"
            f"（現在: {record.get('status')}）。時間をおいて再実行してください。"
        )


# --- 発見 API と MCP endpoint への観測 ---


class Probes:
    """発見用 M2M トークンで、発見 API と MCP endpoint の観測を問い合わせる。"""

    def __init__(self, transport: Transport, token: str, registry_arn: str, region: str) -> None:
        self._transport = transport
        self._token = token
        self._registry_arn = registry_arn
        self._batch_url = discovery_batch_url(region)
        self._mcp_url = mcp_endpoint_url(registry_arn)
        self._call_id = 0

    def _post(self, url: str, payload: Mapping[str, Any]) -> TransportResponse:
        headers = {
            "authorization": f"Bearer {self._token}",
            "content-type": "application/json",
            "accept": "application/json",
        }
        return self._transport(
            "POST", url, headers, json.dumps(payload).encode(), HTTP_TIMEOUT_SECONDS
        )

    def batch(self, record_id: str) -> Sighting:
        """発見 API（BatchGetDiscoverableRegistryRecord）で承認済み版を観測する。"""
        response = self._post(self._batch_url, batch_get_payload(self._registry_arn, record_id))
        if response.status != 200:
            return Sighting("発見 API", False, None, f"HTTP {response.status}")
        return parse_batch_response(response.body, record_id)

    def mcp(self, record_id: str) -> Sighting:
        """MCP endpoint（tools/call の batch_get_discoverable_registry_record）で観測する。"""
        self._call_id += 1
        response = self._post(self._mcp_url, mcp_batch_get_call(self._call_id, record_id))
        if response.status != 200:
            return Sighting("MCP endpoint", False, None, f"HTTP {response.status}")
        return parse_mcp_response(response.body, record_id)

    def observe(self, record_id: str) -> Observation:
        """発見 API と MCP endpoint の両方を観測する。"""
        return Observation(discovery=self.batch(record_id), mcp=self.mcp(record_id))


def _attempt_reject(
    control: RegistryControl, probes: Probes, record_id: str, reason: str
) -> tuple[StatusAttempt, Observation | None]:
    """REJECT を試み、受け付け可否と（受理された場合の）観測を返す。"""
    try:
        control.set_status(record_id, "REJECTED", reason)
    except ClientError as error:
        code = client_error_code(error)
        return StatusAttempt(False, code, f"AWS が拒否しました（{code}）"), None
    control.wait_status(record_id, {"REJECTED"})
    return StatusAttempt(True, None, "受け付けられました"), probes.observe(record_id)


def _approve(control: RegistryControl, record_id: str, version: str, reason: str) -> None:
    """編集 → 申請 → 承認を通し、指定した版を承認済みにする（復旧とベースライン作り）。"""
    control.edit(record_id, version)
    control.wait_status(record_id, {"DRAFT"})
    control.submit(record_id)
    control.wait_status(record_id, {"PENDING_APPROVAL"})
    control.set_status(record_id, "APPROVED", reason)
    control.wait_status(record_id, {"APPROVED"})


def run_verification(
    control: RegistryControl, transport: Transport, name: str, *, token: str
) -> Report:
    """ケース A（最新版 DRAFT）/ B（最新版 PENDING_APPROVAL）/ C（最新版 REJECTED）を実行する。

    前提: 対象の Registry は自動承認が無効（手動審査）。スクリプト自身が承認して進める。
    """
    configuration = control.get_registry().get("approvalConfiguration", {})
    rules = configuration.get("autoApprovalRules") if isinstance(configuration, Mapping) else None
    if not isinstance(rules, list) or rules:
        raise RuntimeError(
            "Registry の自動承認が有効（または確認できません）。検証には手動審査の Registry が"
            "必要です。自動承認を無効にしてから再実行してください。"
        )

    region, _ = parse_registry_arn(control.registry)
    probes = Probes(transport, token, control.registry, region)
    record_id = control.create_record(name, "1.0.0")
    control.wait_status(record_id, {"DRAFT"})
    control.submit(record_id)
    control.wait_status(record_id, {"PENDING_APPROVAL"})
    control.set_status(record_id, "APPROVED", APPROVE_REASON)
    control.wait_status(record_id, {"APPROVED"})
    baseline_observation = probes.observe(record_id)
    baseline = CaseResult(
        label="事前確認（承認済みのみ）",
        latest_status="APPROVED",
        latest_version="1.0.0",
        approved_version=baseline_observation.discovery.record_version,
        attempt=None,
        before=baseline_observation,
        after=baseline_observation,
    )

    notes: list[str] = []
    versions = itertools.count(1)
    cases: list[CaseResult] = []

    # ケース A: 公開中の版を残して編集し、最新版を DRAFT にする
    version_a = f"1.0.{next(versions)}"
    control.edit(record_id, version_a)
    control.wait_status(record_id, {"DRAFT"})
    before_a = probes.observe(record_id)
    attempt_a, after_a = _attempt_reject(control, probes, record_id, REJECT_REASON)
    cases.append(
        CaseResult(
            label="A: 最新版 DRAFT",
            latest_status="DRAFT",
            latest_version=version_a,
            approved_version=before_a.discovery.record_version,
            attempt=attempt_a,
            before=before_a,
            after=after_a,
        )
    )
    # 次のケースに備えて承認済み版を作り直す（REJECT が拒否されて DRAFT のままでも通る手順）
    _approve(control, record_id, f"1.0.{next(versions)}", APPROVE_REASON)

    # ケース B: 編集して申請し、最新版を PENDING_APPROVAL にする
    version_b = f"1.0.{next(versions)}"
    control.edit(record_id, version_b)
    control.wait_status(record_id, {"DRAFT"})
    control.submit(record_id)
    control.wait_status(record_id, {"PENDING_APPROVAL"})
    before_b = probes.observe(record_id)
    attempt_b, after_b = _attempt_reject(control, probes, record_id, REJECT_REASON)
    cases.append(
        CaseResult(
            label="B: 最新版 PENDING_APPROVAL",
            latest_status="PENDING_APPROVAL",
            latest_version=version_b,
            approved_version=before_b.discovery.record_version,
            attempt=attempt_b,
            before=before_b,
            after=after_b,
        )
    )

    # ケース C: 最新版を REJECTED にした後、承認済み版が発見されるか。
    # REJECTED への遷移として文書化されている経路（B の受理）を優先し、B が拒否された
    # ときは A の受理で同じ状態（最新版 REJECTED + 承認済み版）が観測できていれば使う。
    if attempt_b.accepted and after_b is not None:
        cases.append(
            CaseResult(
                label="C: 最新版 REJECTED",
                latest_status="REJECTED",
                latest_version=version_b,
                approved_version=before_b.discovery.record_version,
                attempt=None,
                before=before_b,
                after=after_b,
            )
        )
    elif attempt_a.accepted and after_a is not None:
        cases.append(
            CaseResult(
                label="C: 最新版 REJECTED",
                latest_status="REJECTED",
                latest_version=version_a,
                approved_version=before_a.discovery.record_version,
                attempt=None,
                before=before_a,
                after=after_a,
            )
        )
        notes.append(
            "ケース C は A の却下で作られた状態です（B の REJECT は受け付けられなかったため）。"
        )
    else:
        notes.append(
            "ケース A・B のどちらの REJECT も受け付けられなかったため、"
            "C（最新版 REJECTED）の状態は作れませんでした。"
        )
        cases.append(
            CaseResult(
                label="C: 最新版 REJECTED",
                latest_status="PENDING_APPROVAL",
                latest_version=version_b,
                approved_version=before_b.discovery.record_version,
                attempt=None,
                before=before_b,
                after=None,
            )
        )

    return Report(
        registry_arn=control.registry,
        record_name=name,
        record_id=record_id,
        executed_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        auto_approval="無効（手動審査）",
        baseline=baseline,
        cases=tuple(cases),
        notes=tuple(notes),
    )


# --- 後始末 ---


def cleanup_record(control: RegistryControl, record_id: str) -> str:
    """後始末。レコードの物理削除（DeleteRegistryRecord）を試み、拒否されたら DEPRECATED にする。

    削除は状態によって ConflictException を返しうる。DEPRECATED は元に戻せない終端の状態のため、
    再実行には新しいレコード名（--name-prefix を変える）を使う。
    """
    try:
        control.try_delete(record_id)
    except ClientError as error:
        code = client_error_code(error)
        control.set_status(record_id, "DEPRECATED", CLEANUP_REASON)
        return f"削除は拒否されました（{code}）。DEPRECATED にしました"
    return "削除しました"


# --- レポート整形 ---


def _format_sighting(sighting: Sighting, approved_version: str | None) -> str:
    """観測を表の单元格の文字列にする。承認済み版と異なる版は注記を付ける。"""
    if not sighting.found:
        return f"返さなかった（{sighting.detail}）"
    version = sighting.record_version or "不明な版"
    if approved_version and version != approved_version:
        return f"{version} を返した（承認済み版 {approved_version} と異なる）"
    return f"{version} を返した"


def _format_attempt(attempt: StatusAttempt | None) -> str:
    if attempt is None:
        return "—"
    if attempt.accepted:
        return "受け付けられた"
    return f"拒否された（{attempt.error_code}）"


def render_report(report: Report, *, cleanup_note: str) -> str:
    """検証結果を、README に貼れる Markdown で組み立てる。"""
    lines = [
        "# 公開停止（二重リビジョンでの却下）の実機検証結果（#33）",
        "",
        f"- 実行日時: {report.executed_at}",
        f"- Registry: {report.registry_arn}",
        f"- 対象レコード: {report.record_name}（{report.record_id}）",
        f"- 自動承認: {report.auto_approval}",
        f"- 後始末: {cleanup_note}",
        "",
        "| ケース | 最新版 | REJECT 試行前の発見 API | 同 MCP endpoint "
        "| REJECT の受け付け | 受け付け後の発見 API | 同 MCP endpoint |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ]
    for case in (report.baseline, *report.cases):
        before_api = _format_sighting(case.before.discovery, case.approved_version)
        before_mcp = _format_sighting(case.before.mcp, case.approved_version)
        if case.after is None:
            after_api = after_mcp = "—"
        else:
            after_api = _format_sighting(case.after.discovery, case.approved_version)
            after_mcp = _format_sighting(case.after.mcp, case.approved_version)
        lines.append(
            f"| {case.label} | {case.latest_status} {case.latest_version} "
            f"| {before_api} | {before_mcp} | {_format_attempt(case.attempt)} "
            f"| {after_api} | {after_mcp} |"
        )
    if report.notes:
        lines.append("")
        lines.append("備考:")
        lines.extend(f"- {note}" for note in report.notes)
    return "\n".join(lines) + "\n"


# --- 引数解析と組み立て ---


ENV_TOKEN = "SUSPEND_VERIFY_TOKEN"
ENV_TOKEN_URL = "SUSPEND_VERIFY_TOKEN_URL"
ENV_CLIENT_ID = "SUSPEND_VERIFY_CLIENT_ID"
ENV_CLIENT_SECRET = "SUSPEND_VERIFY_CLIENT_SECRET"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """コマンドライン引数と環境変数を読む。トークンは直接か、client credentials で取得する。"""
    parser = argparse.ArgumentParser(
        description=(
            "Issue #33: 二重リビジョン（承認済み版を残したまま最新版が DRAFT・PENDING_APPROVAL・"
            "REJECTED）での却下の挙動を確認する。対象の Registry は自動承認が無効であること。"
        )
    )
    parser.add_argument(
        "--registry-arn",
        required=True,
        help="対象 Registry の ARN（arn:aws:agent-registry:<region>:<account>:registry/<id>）",
    )
    parser.add_argument(
        "--token",
        default=None,
        help=f"発見 API と MCP endpoint 用の M2M アクセストークン（環境変数 {ENV_TOKEN} も可）",
    )
    parser.add_argument(
        "--token-url",
        default=None,
        help="トークンを取得する場合のエンドポイント（環境変数も可）",
    )
    parser.add_argument(
        "--client-id", default=None, help="client credentials 用の client_id（環境変数も可）"
    )
    parser.add_argument(
        "--client-secret",
        default=None,
        help="client credentials 用の client_secret（環境変数も可。ログに出ません）",
    )
    parser.add_argument("--scope", default="", help="（任意）トークン取得時に要求する scope")
    parser.add_argument(
        "--name-prefix",
        default=DEFAULT_NAME_PREFIX,
        help=f"検証用レコード名の prefix（既定: {DEFAULT_NAME_PREFIX}）。実行ごとに日時を付します",
    )
    parser.add_argument(
        "--keep-record",
        action="store_true",
        help="後始末（削除・DEPRECATED）を行わない。障害時の調査に使います",
    )
    args = parser.parse_args(argv)
    environ = os.environ
    args.token = args.token or environ.get(ENV_TOKEN, "").strip() or None
    args.token_url = args.token_url or environ.get(ENV_TOKEN_URL, "").strip() or None
    args.client_id = args.client_id or environ.get(ENV_CLIENT_ID, "").strip() or None
    args.client_secret = args.client_secret or environ.get(ENV_CLIENT_SECRET, "").strip() or None
    if args.token is None and not (args.token_url and args.client_id and args.client_secret):
        parser.error(
            f"--token（または {ENV_TOKEN}）か、--token-url と --client-id と --client-secret"
            f"（または {ENV_TOKEN_URL}・{ENV_CLIENT_ID}・{ENV_CLIENT_SECRET}）を指定してください"
        )
    return args


def main(argv: list[str] | None = None) -> int:
    """検証を実行し、README に貼れる結果を stdout に出す。実行手順は README を参照。"""
    args = parse_args(argv)
    region, _ = parse_registry_arn(args.registry_arn)
    client = boto3.client("agent-registry-control", region_name=region)
    control = RegistryControl(client, args.registry_arn)
    if args.token is not None:
        token = args.token
    else:
        token = fetch_access_token(
            urllib_transport,
            str(args.token_url),
            str(args.client_id),
            str(args.client_secret),
            args.scope,
        )
    name = f"{args.name_prefix}-{time.strftime('%Y%m%d%H%M%S')}"
    report = run_verification(control, urllib_transport, name, token=token)
    cleanup_note = (
        "スキップしました（--keep-record）"
        if args.keep_record
        else cleanup_record(control, report.record_id)
    )
    print(render_report(report, cleanup_note=cleanup_note))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Issue #33 の実機検証スクリプト: 二重リビジョンでの却下の挙動を確認する。

人間が dev 環境で実行する runbook であり、ポータルの外から直接 AWS を操作する。
ADR-0001 により、Registry への書き込み（作成・編集・申請・状態更新）は break-glass 用の
管理ロールでしか行えない。実行は「ポータル外の変更」として Admin に通知され、監査の対象になる。

- 管理 API（agent-registry-control）は boto3 で呼ぶ
- 発見 API（BatchGetDiscoverableRegistryRecord）と MCP endpoint（InvokeRegistryMcp 相当の
  tools/call）は、発見用 M2M トークンで HTTP を直接呼ぶ。宛先はポータルの
  DiscoverySettings と同じ構成（backend/src/registry_portal/config.py）
- ケース A（最新版 DRAFT）/ B（最新版 PENDING_APPROVAL）/ C（最新版 REJECTED）を順に実行し、
  それぞれで UpdateRegistryRecordStatus(REJECTED) の受け付け可否と、公開承認済みの版が
  発見に残るかを確認する
- 結果は README に貼れる Markdown の表で stdout に出力する

使い方は「--help」または README の「二重リビジョンの実機検証（#33）」の節を参照。
レコードの形・状態の値・HTTP トランスポートはポータルのバックエンド（registry_portal）の定義を
そのまま使うため、backend ディレクトリで PYTHONPATH=src を付けて実行する。
AWS に触る部分は薄く保ち、ペイロード構築・応答の解釈・レポート整形は純粋な関数として
切り出してある（backend/tests/test_suspend_verification_script.py が検証する）。
"""

from __future__ import annotations

import argparse
import base64
import dataclasses
import itertools
import json
import os
import time
import uuid
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass
from typing import Any, Literal
from urllib.parse import urlencode

import boto3
from botocore.exceptions import BotoCoreError, ClientError

try:
    from registry_portal.domain.catalog import (
        AGENT_RECORD_TYPE,
        CUSTOM_DESCRIPTOR_TYPE,
        DataClassification,
    )
    from registry_portal.domain.drafts import (
        DEPRECATED_STATUS,
        DRAFT_STATUS,
        PENDING_APPROVAL_STATUS,
        PUBLISHED_STATUS,
        REJECTED_STATUS,
        RecordDraft,
        RecordStatus,
    )
    from registry_portal.services.http import (
        Transport,
        TransportError,
        TransportResponse,
        urllib_transport,
    )
except ModuleNotFoundError as error:  # pragma: no cover - 実行手順の誤りを案内する
    raise SystemExit(
        "registry_portal を import できません。applications/registry_portal/backend で "
        "PYTHONPATH=src を付けて実行してください（README「二重リビジョンの実機検証（#33）」）。"
    ) from error

DISPLAY_NAME = "公開停止の検証"
DESCRIPTION = "Issue #33 の検証用レコード。スクリプトが自動的に作り、検証後に削除します。"
OWNER_TEAM = "suspend-verify"
DATA_CLASSIFICATION: DataClassification = "private"
# custom descriptor の定義（ポータルの custom Agent の下書きと同じ項目）
CUSTOM_DEFINITION = {
    "purpose": "Issue #33 の二重リビジョンの検証",
    "authentication": "なし",
    "connectionReference": "https://example.com/suspend-verify",
    "contact": "ops@example.com",
    "termsOfUse": "検証専用",
}
DEFAULT_NAME_PREFIX = "suspend-verify"
# 作成時の版。以降の版はこの後ろに 1.0.1、1.0.2 … と続ける
INITIAL_VERSION = "1.0.0"
APPROVE_REASON = "Issue #33 の検証のため公開承認する"
REJECT_REASON = "Issue #33 の検証のため却下する"
CLEANUP_REASON = "Issue #33 の検証の後始末"
HTTP_TIMEOUT_SECONDS = 10.0
# 管理 API の書き込みは 202（非同期）を返すため、状態の確定を待つ間隔と回数
# （根拠は README「二重リビジョンの実機検証（#33）」）
WAIT_INTERVAL_SECONDS = 5.0
WAIT_ATTEMPTS = 12
# 発見 API・MCP endpoint への反映の遅れに備え、観測の前に待つ秒数（既定）と、
# 結果が2回続けて一致するまで観測を繰り返す上限
DEFAULT_SETTLE_SECONDS = 15.0
SETTLE_ATTEMPTS = 3
# 200 以外の応答の本文を記録するときの上限（表のセルを読める長さに保つ）
ERROR_EXCERPT_LIMIT = 200


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


def verification_draft(name: str, version: str) -> RecordDraft:
    """検証用レコードの下書き。ポータルが作る custom Agent と同じ形（RecordDraft）にする。

    種別・データ分類・environment の値と検査は、ポータルの定義（domain/catalog.py・drafts.py）に従う。
    """
    return RecordDraft.parse(
        {
            "name": name,
            "displayName": DISPLAY_NAME,
            "description": DESCRIPTION,
            "recordVersion": version,
            "recordType": AGENT_RECORD_TYPE,
            "descriptorType": CUSTOM_DESCRIPTOR_TYPE,
            "ownerTeam": OWNER_TEAM,
            "dataClassification": DATA_CLASSIFICATION,
            **CUSTOM_DEFINITION,
        }
    )


def create_record_payload(registry_arn: str, draft: RecordDraft) -> dict[str, Any]:
    """CreateRegistryRecord の引数。ポータルの RecordDraft.create_payload と同じ中身にする。"""
    return {
        "registryId": registry_arn,
        **draft.create_payload(),
        # clientToken は 33〜256 文字（README「二重リビジョンの実機検証（#33）」の根拠）
        "clientToken": str(uuid.uuid4()),
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


# --- 観測（発見 API・MCP endpoint の応答の解釈） ---

SightingSource = Literal["発見 API", "MCP endpoint"]
DISCOVERY_SOURCE: SightingSource = "発見 API"
MCP_SOURCE: SightingSource = "MCP endpoint"

# found: 対象のレコードを返した
# absent: 応答は正常で、対象のレコードが RESOURCE_NOT_FOUND だった（発見できない）
# failed: 通信・HTTP・形式の異常で、発見できるかを判定できなかった
SightingOutcome = Literal["found", "absent", "failed"]
FOUND: SightingOutcome = "found"
ABSENT: SightingOutcome = "absent"
FAILED: SightingOutcome = "failed"

# 発見 API が「対象のレコードは発見できない」ことを示すエラーコード（services/discovery.py と同じ）
NOT_FOUND_ERROR_CODE = "RESOURCE_NOT_FOUND"


@dataclass(frozen=True)
class Sighting:
    """発見 API か MCP endpoint でレコードを観測した結果。"""

    source: SightingSource
    outcome: SightingOutcome
    record_version: str | None = None
    status: str | None = None  # 発見側が返したレコードの status
    detail: str = ""  # 人間が読む補足（エラーコード・想定外の理由）

    @classmethod
    def failed(cls, source: SightingSource, detail: str) -> Sighting:
        return cls(source, FAILED, detail=detail)


@dataclass(frozen=True)
class Observation:
    """ある時点での発見 API と MCP endpoint の観測の組。

    stable は、繰り返し観測して結果が2回続けて一致したか（反映の途中の可能性を示す）。
    """

    discovery: Sighting  # 発見 API（BatchGetDiscoverableRegistryRecord）
    mcp: Sighting  # MCP endpoint（tools/call）
    stable: bool = True


def _json_object(raw: bytes | str | Mapping[str, Any] | None) -> Mapping[str, Any] | None:
    if isinstance(raw, Mapping):
        return raw
    if raw is None:
        return None
    try:
        value = json.loads(raw)
    except ValueError:
        return None
    return value if isinstance(value, Mapping) else None


def _sighting_from_batch(
    body: Mapping[str, Any] | None, record_id: str, source: SightingSource
) -> Sighting:
    """BatchGetDiscoverableRegistryRecord 形の本文（MCP ツールの結果も同じ形と仮定）から観測を作る。

    ポータルの発見アダプター（services/discovery.py）と同じく、RESOURCE_NOT_FOUND だけを
    「発見できない」とし、それ以外の異常は判定できなかった（failed）として区別する。
    """
    if body is None:
        return Sighting.failed(source, "応答が想定外の形式です")
    records, errors = body.get("registryRecords"), body.get("errors")
    if not isinstance(records, list) or not isinstance(errors, list):
        return Sighting.failed(source, "応答が想定外の形式です")
    for record in records:
        if isinstance(record, Mapping) and record.get("recordId") == record_id:
            version, status = record.get("recordVersion"), record.get("status")
            return Sighting(
                source,
                FOUND,
                version if isinstance(version, str) else None,
                status if isinstance(status, str) else None,
            )
    for error in errors:
        if isinstance(error, Mapping) and error.get("recordId") == record_id:
            code = str(error.get("errorCode") or "不明なエラー")
            if code == NOT_FOUND_ERROR_CODE:
                return Sighting(source, ABSENT, detail=code)
            return Sighting.failed(source, code)
    return Sighting.failed(source, "応答に対象のレコードがありませんでした")


def parse_batch_response(body: bytes | Mapping[str, Any], record_id: str) -> Sighting:
    """発見 API（BatchGetDiscoverableRegistryRecord）の応答を解釈する。"""
    return _sighting_from_batch(_json_object(body), record_id, DISCOVERY_SOURCE)


def parse_mcp_response(body: bytes | Mapping[str, Any], record_id: str) -> Sighting:
    """MCP endpoint の JSON-RPC 応答を解釈する。

    ツールの結果は result.content[].text に JSON で返る。AWS 文書は text の中身の形を
    明記しないため、JSON なら batch と同じ形と仮定して解釈し、そうでなければ本文をそのまま記録する。
    """
    value = _json_object(body)
    if value is None:
        return Sighting.failed(MCP_SOURCE, "応答が想定外の形式です")
    if isinstance(value.get("error"), Mapping):
        error = value["error"]
        return Sighting.failed(MCP_SOURCE, f"JSON-RPC エラー: {error.get('message')}")
    result = value.get("result")
    if not isinstance(result, Mapping):
        return Sighting.failed(MCP_SOURCE, "応答に result がありません")
    texts = _content_texts(result.get("content"))
    if not texts:
        return Sighting.failed(MCP_SOURCE, "応答の content に text がありません")
    text = "\n".join(texts)
    if result.get("isError") is True:
        return Sighting.failed(MCP_SOURCE, f"ツールのエラー: {text}")
    inner = _json_object(text)
    if inner is not None:
        return _sighting_from_batch(inner, record_id, MCP_SOURCE)
    return Sighting.failed(MCP_SOURCE, f"ツールの結果が JSON ではありません: {text}")


def _content_texts(content: object) -> list[str]:
    """MCP のツール結果の content から text の要素だけを取り出す。"""
    if not isinstance(content, list):
        return []
    texts: list[str] = []
    for item in content:
        text = item.get("text") if isinstance(item, Mapping) else None
        if isinstance(text, str):
            texts.append(text)
    return texts


def http_error_detail(status: int, body: bytes) -> str:
    """200 以外の応答を、ステータスと本文の要約にする（本文を捨てずに記録するため）。

    JSON で message 系の項目があればそれを、なければ本文の先頭を ERROR_EXCERPT_LIMIT 文字まで使う。
    """
    value = _json_object(body)
    message = None
    if value is not None:
        message = next(
            (
                value[key]
                for key in ("message", "Message", "errorMessage", "error_description", "error")
                if isinstance(value.get(key), str) and value[key]
            ),
            None,
        )
    excerpt = message if message is not None else body.decode("utf-8", "replace").strip()
    if len(excerpt) > ERROR_EXCERPT_LIMIT:
        excerpt = excerpt[:ERROR_EXCERPT_LIMIT] + "…"
    return f"HTTP {status}: {excerpt}" if excerpt else f"HTTP {status}"


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


def aws_error_code(error: Exception) -> str:
    """AWS 呼び出しの例外を短い識別子にする。ClientError はエラーコード、それ以外は例外の型名。"""
    if isinstance(error, ClientError):
        return str(error.response.get("Error", {}).get("Code", "不明なエラー"))
    return type(error).__name__


@dataclass(frozen=True)
class StatusAttempt:
    """UpdateRegistryRecordStatus(REJECTED) の試行結果。

    resulting_status は、受理された後に管理 API が返した最新版の状態（拒否されたときは None）。
    受理されても最新版が REJECTED にならない（公開承認済みの版の側が却下された可能性がある）
    場合を、失敗にせず記録するために持つ。
    """

    accepted: bool
    error_code: str | None = None
    resulting_status: str | None = None

    @property
    def reached_rejected(self) -> bool:
        """受理され、最新版が REJECTED になったか（ケース C の状態を作れたか）。"""
        return self.accepted and self.resulting_status == REJECTED_STATUS


@dataclass(frozen=True)
class RevisionState:
    """REJECT を試す時点のレコードの版の組。

    approved_version はスクリプト自身が公開承認した版（観測から推定しない）。
    """

    latest_status: RecordStatus
    latest_version: str
    approved_version: str


@dataclass(frozen=True)
class CaseResult:
    """ケース1つ分の記録。before は REJECT 試行前、after は受理後の観測。"""

    label: str
    revision: RevisionState
    attempt: StatusAttempt | None
    before: Observation
    after: Observation | None


@dataclass(frozen=True)
class Report:
    """検証全体の記録。README に貼る Markdown は render_report が組み立てる。

    途中で中断した場合も、それまでの結果を残す（baseline が None のことがある）。
    """

    registry_arn: str
    record_name: str
    record_id: str
    executed_at: str
    auto_approval: str
    settle_seconds: float
    baseline: CaseResult | None
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
        registry: Mapping[str, Any] = self._client.get_registry(registryId=self.registry)
        return registry

    def create_record(self, draft: RecordDraft) -> str:
        """レコードを作り、recordId を返す（ポータルの drafts と同じ導出）。"""
        response = self._client.create_registry_record(
            **create_record_payload(self.registry, draft)
        )
        return str(response["recordArn"]).rsplit("/", 1)[1]

    def edit(self, record_id: str, draft: RecordDraft) -> None:
        """新しい版を編集する。引数はポータルの RecordDraft.update_payload と同じ。"""
        self._client.update_registry_record(
            registryId=self.registry, recordId=record_id, **draft.update_payload()
        )

    def submit(self, record_id: str) -> None:
        self._client.submit_registry_record_for_approval(
            registryId=self.registry, recordId=record_id
        )

    def set_status(self, record_id: str, status: RecordStatus, reason: str) -> None:
        self._client.update_registry_record_status(
            registryId=self.registry, recordId=record_id, status=status, statusReason=reason
        )

    def get(self, record_id: str) -> Mapping[str, Any]:
        record: Mapping[str, Any] = self._client.get_registry_record(
            registryId=self.registry, recordId=record_id
        )
        return record

    def delete(self, record_id: str) -> None:
        """レコードを物理削除する。拒否されたら ClientError のまま返す。"""
        self._client.delete_registry_record(registryId=self.registry, recordId=record_id)

    def poll_status(self, record_id: str, expected: RecordStatus) -> Mapping[str, Any]:
        """状態が expected になるまで読み直し、最後に読んだレコードを返す（ならなくても例外にしない）。

        管理 API の書き込みは 202（非同期）を返すため。
        """
        record: Mapping[str, Any] = {}
        for attempt in range(WAIT_ATTEMPTS):
            if attempt:
                self._sleep(WAIT_INTERVAL_SECONDS)
            record = self.get(record_id)
            if record.get("status") == expected:
                break
        return record

    def wait_status(self, record_id: str, expected: RecordStatus) -> Mapping[str, Any]:
        """状態が expected になるまで読み直す。ならなければ手順の前提が崩れたため例外にする。"""
        record = self.poll_status(record_id, expected)
        if record.get("status") == expected:
            return record
        raise RuntimeError(
            f"レコードの状態が {expected} になりません（現在: {record.get('status')}）。"
            "時間をおいて再実行してください。"
        )


# --- 発見 API と MCP endpoint への観測 ---


class Probes:
    """発見用 M2M トークンで、発見 API と MCP endpoint の観測を問い合わせる。"""

    def __init__(
        self,
        transport: Transport,
        token: str,
        registry_arn: str,
        *,
        settle_seconds: float = DEFAULT_SETTLE_SECONDS,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        region, _ = parse_registry_arn(registry_arn)
        self._transport = transport
        self._token = token
        self._registry_arn = registry_arn
        self._batch_url = discovery_batch_url(region)
        self._mcp_url = mcp_endpoint_url(registry_arn)
        self._sleep = sleep
        self.settle_seconds = settle_seconds
        self._call_id = 0

    def _send(
        self,
        source: SightingSource,
        url: str,
        payload: Mapping[str, Any],
        parse: Callable[[bytes], Sighting],
    ) -> Sighting:
        """POST して応答を解釈する。通信の失敗と 200 以外は、判定できなかった観測にする。

        200 以外のときは、ステータスに加えて本文の要約も記録する（http_error_detail）。
        """
        headers = {
            "authorization": f"Bearer {self._token}",
            "content-type": "application/json",
            "accept": "application/json",
        }
        try:
            response: TransportResponse = self._transport(
                "POST", url, headers, json.dumps(payload).encode(), HTTP_TIMEOUT_SECONDS
            )
        except TransportError as error:
            return Sighting.failed(source, f"接続できませんでした（{error}）")
        if response.status != 200:
            return Sighting.failed(source, http_error_detail(response.status, response.body))
        return parse(response.body)

    def discovery_api(self, record_id: str) -> Sighting:
        """発見 API（BatchGetDiscoverableRegistryRecord）で観測する。"""
        return self._send(
            DISCOVERY_SOURCE,
            self._batch_url,
            batch_get_payload(self._registry_arn, record_id),
            lambda body: parse_batch_response(body, record_id),
        )

    def mcp_endpoint(self, record_id: str) -> Sighting:
        """MCP endpoint（tools/call の batch_get_discoverable_registry_record）で観測する。"""
        self._call_id += 1
        return self._send(
            MCP_SOURCE,
            self._mcp_url,
            mcp_batch_get_call(self._call_id, record_id),
            lambda body: parse_mcp_response(body, record_id),
        )

    def _observe_once(self, record_id: str) -> Observation:
        self._sleep(self.settle_seconds)
        return Observation(
            discovery=self.discovery_api(record_id), mcp=self.mcp_endpoint(record_id)
        )

    def observe(self, record_id: str) -> Observation:
        """発見 API と MCP endpoint の両方を観測する。

        管理 API の状態の確定と発見側への反映には遅れがありうるため、settle_seconds 待ってから
        観測し、結果が2回続けて一致するまで繰り返す（最大 SETTLE_ATTEMPTS 回）。
        一致しなければ最後の観測を stable=False で返す。
        """
        current = self._observe_once(record_id)
        for _ in range(SETTLE_ATTEMPTS - 1):
            following = self._observe_once(record_id)
            if following == current:
                return following
            current = following
        return dataclasses.replace(current, stable=False)


# --- 検証の手順 ---


def ensure_manual_approval(control: RegistryControl) -> None:
    """対象の Registry の自動承認が無効（手動審査）か確かめる。スクリプト自身が公開承認して進めるため。"""
    configuration = control.get_registry().get("approvalConfiguration", {})
    rules = configuration.get("autoApprovalRules") if isinstance(configuration, Mapping) else None
    if not isinstance(rules, list) or rules:
        raise RuntimeError(
            "Registry の自動承認が有効（または確認できません）。検証には手動審査の Registry が"
            "必要です。自動承認を無効にしてから再実行してください。"
        )


def create_verification_record(control: RegistryControl, name: str) -> str:
    """自動承認が無効なことを確かめ、検証用レコードを作って recordId を返す。

    作成だけを run_verification から分け、後始末の対象を呼び出し側が確実に知れるようにする。
    """
    ensure_manual_approval(control)
    return control.create_record(verification_draft(name, INITIAL_VERSION))


def _baseline_notes(baseline: Observation, approved_version: str) -> list[str]:
    """事前確認で公開承認済みの版が発見できなければ、以降の結果の読み方の注意を返す。"""
    return [
        f"事前確認で {sighting.source} が公開承認済みの版 {approved_version} を返しませんでした。"
        f"以降の {sighting.source} の「返さなかった」は、却下の影響と断定できません。"
        for sighting in (baseline.discovery, baseline.mcp)
        if sighting.outcome != FOUND or sighting.record_version != approved_version
    ]


class _Verification:
    """1つの検証用レコードで、事前確認とケース A/B/C を順に進める。

    途中経過（baseline・cases・notes）を属性に残し、中断しても run_verification が
    それまでの結果を Report にできるようにする。approved はスクリプトが最後に公開承認した版。
    """

    def __init__(self, control: RegistryControl, probes: Probes, name: str, record_id: str) -> None:
        self._control = control
        self._probes = probes
        self._name = name
        self._record_id = record_id
        self._versions: Iterator[str] = (f"1.0.{n}" for n in itertools.count(1))
        self.approved = INITIAL_VERSION
        self.baseline: CaseResult | None = None
        self.cases: list[CaseResult] = []
        self.notes: list[str] = []

    def _new_revision(self) -> str:
        """公開承認済みの版を残したまま新しい版を編集し（最新版 DRAFT）、その版を返す。"""
        version = next(self._versions)
        self._control.edit(self._record_id, verification_draft(self._name, version))
        self._control.wait_status(self._record_id, DRAFT_STATUS)
        return version

    def _submit(self) -> None:
        """最新版を申請し、PENDING_APPROVAL にする。"""
        self._control.submit(self._record_id)
        self._control.wait_status(self._record_id, PENDING_APPROVAL_STATUS)

    def _approve(self, version: str) -> None:
        """審査待ちの最新版（version）を公開承認する。"""
        self._control.set_status(self._record_id, PUBLISHED_STATUS, APPROVE_REASON)
        self._control.wait_status(self._record_id, PUBLISHED_STATUS)
        self.approved = version

    def _reject(self) -> StatusAttempt:
        """UpdateRegistryRecordStatus(REJECTED) を試み、受け付け可否と受理後の最新版の状態を返す。

        受理されても最新版が REJECTED にならないこと（公開承認済みの版の側が却下された可能性）は、
        この検証で知りたい結果の一つのため、例外にせず resulting_status に記録する。
        """
        try:
            self._control.set_status(self._record_id, REJECTED_STATUS, REJECT_REASON)
        except ClientError as error:
            return StatusAttempt(False, error_code=aws_error_code(error))
        record = self._control.poll_status(self._record_id, REJECTED_STATUS)
        return StatusAttempt(True, resulting_status=str(record.get("status")))

    def _run_case(self, label: str, latest_status: RecordStatus, latest_version: str) -> CaseResult:
        """現在の状態で観測し、REJECT を試み、受理されたら再び観測して記録する。"""
        revision = RevisionState(latest_status, latest_version, self.approved)
        before = self._probes.observe(self._record_id)
        attempt = self._reject()
        after = self._probes.observe(self._record_id) if attempt.accepted else None
        case = CaseResult(label, revision, attempt, before, after)
        self.cases.append(case)
        return case

    def run(self) -> None:
        # 事前確認: 作成した版を公開承認し、発見 API と MCP endpoint に現れることを確かめる
        self._control.wait_status(self._record_id, DRAFT_STATUS)
        self._submit()
        self._approve(INITIAL_VERSION)
        before = self._probes.observe(self._record_id)
        self.baseline = CaseResult(
            "事前確認（公開承認済みの版のみ）",
            RevisionState(PUBLISHED_STATUS, self.approved, self.approved),
            None,
            before,
            None,
        )
        self.notes.extend(_baseline_notes(before, self.approved))

        # ケース A: 公開承認済みの版を残して編集し、最新版を DRAFT にする
        version_a = self._new_revision()
        case_a = self._run_case("A: 最新版 DRAFT", DRAFT_STATUS, version_a)

        # 次のケースに備えて公開承認済みの版を作り直す（REJECT が拒否されて DRAFT のままでも通る手順）
        rebuilt = self._new_revision()
        self._submit()
        self._approve(rebuilt)

        # ケース B: 編集して申請し、最新版を PENDING_APPROVAL にする
        version_b = self._new_revision()
        self._submit()
        case_b = self._run_case("B: 最新版 PENDING_APPROVAL", PENDING_APPROVAL_STATUS, version_b)

        # ケース C: 新しい版を却下した後（最新版 REJECTED）に、公開承認済みの版が発見されるか。
        # その状態でさらに REJECT（公開停止）を受け付けるかも確かめる。
        version_c = self._prepare_case_c(case_a, case_b)
        if version_c is not None:
            self._run_case("C: 最新版 REJECTED", REJECTED_STATUS, version_c)

    def _prepare_case_c(self, case_a: CaseResult, case_b: CaseResult) -> str | None:
        """ケース C の状態（最新版 REJECTED）を用意し、却下された版を返す。

        文書化された経路（審査待ちの版の却下 = B の受理）を優先し、B で作れなかったときは
        A と同じ手順（下書きの版の却下）で作り直す。作れなければ備考に理由を残して None を返す。
        """
        if case_b.attempt is not None and case_b.attempt.reached_rejected:
            return case_b.revision.latest_version
        if case_a.attempt is None or not case_a.attempt.reached_rejected:
            self.notes.append(
                "ケース A・B のどちらの REJECT でも最新版が REJECTED にならなかったため、"
                "ケース C の状態（最新版 REJECTED）は作れませんでした。"
            )
            return None
        # B の版は審査待ちのまま残っているため、公開承認してから下書きの版を作って却下する
        self._approve(case_b.revision.latest_version)
        candidate = self._new_revision()
        setup = self._reject()
        if not setup.reached_rejected:
            self.notes.append(
                "ケース C の状態（最新版 REJECTED）は作れませんでした。A と同じ手順の REJECT で"
                f"最新版が REJECTED になりませんでした（{_format_attempt(setup)}）。"
            )
            return None
        self.notes.append(
            "ケース C の状態は、B の REJECT で最新版が REJECTED にならなかったため、A と同じ手順"
            "（下書きの版の却下）で作りました。審査待ちの版の却下とは経路が異なります。"
        )
        return candidate


def run_verification(control: RegistryControl, probes: Probes, name: str, record_id: str) -> Report:
    """作成済みの検証用レコード（create_verification_record）で、事前確認とケース A/B/C を実行する。

    途中で例外が起きても、それまでの結果と中断の理由を備考に残した Report を返す。
    結果の出力と後始末を、呼び出し側が確実に行えるようにするため。
    """
    verification = _Verification(control, probes, name, record_id)
    try:
        verification.run()
    except Exception as error:  # 途中までの観測を失わないよう、種類を問わず記録して返す
        verification.notes.append(
            f"途中で中断しました（{type(error).__name__}: {error}）。"
            "ここまでの結果だけを記録しています。"
        )
    return Report(
        registry_arn=control.registry,
        record_name=name,
        record_id=record_id,
        executed_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        auto_approval="無効（手動審査）",
        settle_seconds=probes.settle_seconds,
        baseline=verification.baseline,
        cases=tuple(verification.cases),
        notes=tuple(verification.notes),
    )


# --- 後始末 ---


def cleanup_record(control: RegistryControl, record_id: str) -> str:
    """後始末。レコードの物理削除（DeleteRegistryRecord）を試み、拒否されたら DEPRECATED にする。

    DeleteRegistryRecord は ConflictException を返しうるが、どの状態で拒否するかは文書に記載がない
    （根拠は README「二重リビジョンの実機検証（#33）」）。どちらもできなければ例外にせず、
    手動での後始末を促す文を返す（検証結果の出力を妨げないため）。
    DEPRECATED は元に戻せない終端の状態だが、実行ごとに日時を付けた新しい名前のレコードを作るため、
    再実行には影響しない。
    """
    try:
        control.delete(record_id)
        return "削除しました"
    except ClientError as error:
        refused = aws_error_code(error)
    except BotoCoreError as error:
        return _manual_cleanup(record_id, f"削除できませんでした（{aws_error_code(error)}）")
    try:
        control.set_status(record_id, DEPRECATED_STATUS, CLEANUP_REASON)
    except (ClientError, BotoCoreError) as error:
        return _manual_cleanup(
            record_id,
            f"削除は拒否され（{refused}）、DEPRECATED にもできませんでした（{aws_error_code(error)}）",
        )
    return f"削除は拒否されました（{refused}）。DEPRECATED にしました"


def _manual_cleanup(record_id: str, reason: str) -> str:
    return f"{reason}。レコード {record_id} を手動で削除するか DEPRECATED にしてください"


# --- レポート整形 ---


def _inline(text: str) -> str:
    """応答由来の文字列を、Markdown の表のセルや箇条書きの1行に収める（改行と | を無害にする）。"""
    return " ".join(text.split()).replace("|", "\\|")


def _format_sighting(sighting: Sighting, approved_version: str) -> str:
    """観測を表のセルの文字列にする。公開承認済みの版と異なる版や、APPROVED 以外の status は注記する。"""
    if sighting.outcome == FAILED:
        return f"確認できなかった（{sighting.detail}）"
    if sighting.outcome == ABSENT:
        return f"返さなかった（{sighting.detail}）"
    version = sighting.record_version or "不明な版"
    remarks = []
    if version != approved_version:
        remarks.append(f"公開承認済みの版 {approved_version} と異なる")
    if sighting.status != PUBLISHED_STATUS:
        remarks.append(f"status={sighting.status}")
    suffix = f"（{'、'.join(remarks)}）" if remarks else ""
    return f"{version} を返した{suffix}"


def _format_observation(observation: Observation | None, approved_version: str) -> tuple[str, str]:
    """観測を（発見 API, MCP endpoint）のセルにする。安定しなかった観測は注記する。"""
    if observation is None:
        return "—", "—"
    suffix = "" if observation.stable else "（観測が安定しませんでした）"
    return (
        _format_sighting(observation.discovery, approved_version) + suffix,
        _format_sighting(observation.mcp, approved_version) + suffix,
    )


def _format_attempt(attempt: StatusAttempt | None) -> str:
    if attempt is None:
        return "—"
    if not attempt.accepted:
        return f"拒否された（{attempt.error_code}）"
    if attempt.reached_rejected:
        return "受け付けられた"
    return (
        f"受け付けられた（最新版の状態は {attempt.resulting_status}。REJECTED になりませんでした）"
    )


def render_report(report: Report) -> str:
    """検証結果を、README に貼れる Markdown で組み立てる。後始末の結果は main が続けて出力する。"""
    lines = [
        "# 公開停止（二重リビジョンでの却下）の実機検証結果（#33）",
        "",
        f"- 実行日時: {report.executed_at}",
        f"- Registry: {report.registry_arn}",
        f"- 対象レコード: {report.record_name}（{report.record_id}）",
        f"- 自動承認: {report.auto_approval}",
        f"- 観測: 各観測の前に {report.settle_seconds:g} 秒待ち、結果が2回続けて一致するまで"
        f"繰り返す（最大{SETTLE_ATTEMPTS}回）",
        "",
        "| ケース | 最新版 | 公開承認済みの版 | REJECT 試行前の発見 API | 同 MCP endpoint "
        "| REJECT の受け付け | 受け付け後の発見 API | 同 MCP endpoint |",
        "| --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    baseline = (report.baseline,) if report.baseline is not None else ()
    for case in (*baseline, *report.cases):
        revision = case.revision
        before_api, before_mcp = _format_observation(case.before, revision.approved_version)
        after_api, after_mcp = _format_observation(case.after, revision.approved_version)
        cells = (
            case.label,
            f"{revision.latest_status} {revision.latest_version}",
            revision.approved_version,
            before_api,
            before_mcp,
            _format_attempt(case.attempt),
            after_api,
            after_mcp,
        )
        lines.append("| " + " | ".join(_inline(cell) for cell in cells) + " |")
    lines.extend(
        [
            "",
            "「返さなかった」は応答が正常で対象のレコードが RESOURCE_NOT_FOUND だったこと、"
            "「確認できなかった」は通信・HTTP・応答の形式の異常で判定できなかったことを示します。",
        ]
    )
    if report.notes:
        lines.append("")
        lines.append("備考:")
        lines.extend(f"- {_inline(note)}" for note in report.notes)
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
            "Issue #33: 二重リビジョン（公開承認済みの版を残したまま最新版が DRAFT・"
            "PENDING_APPROVAL・REJECTED）での却下の挙動を確認する。対象の Registry は自動承認が"
            "無効であること。ADR-0001 により break-glass 用の管理ロールで実行する"
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
        "--settle-seconds",
        type=float,
        default=DEFAULT_SETTLE_SECONDS,
        help=(
            f"発見 API・MCP endpoint を観測する前に待つ秒数（既定: {DEFAULT_SETTLE_SECONDS:g}）。"
            "観測が安定しない場合は長くして再実行します"
        ),
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
    if args.settle_seconds < 0:
        parser.error("--settle-seconds は 0 以上で指定してください")
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
    probes = Probes(urllib_transport, token, args.registry_arn, settle_seconds=args.settle_seconds)
    name = f"{args.name_prefix}-{time.strftime('%Y%m%d%H%M%S')}"
    record_id = create_verification_record(control, name)
    try:
        report = run_verification(control, probes, name, record_id)
        # 後始末より先に出力し、後始末が失敗・中断しても検証結果を失わないようにする
        print(render_report(report), flush=True)
    finally:
        # 中断（Ctrl-C を含む）でも、作ったレコードを残さないよう必ず後始末する
        cleanup_note = (
            "スキップしました（--keep-record）"
            if args.keep_record
            else cleanup_record(control, record_id)
        )
        print(f"後始末（レコード {record_id}）: {cleanup_note}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

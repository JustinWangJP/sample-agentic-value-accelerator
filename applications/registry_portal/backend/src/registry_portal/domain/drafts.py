from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Literal, TypedDict, get_args

from registry_portal.domain.catalog import AGENT_RECORD_TYPE, DATA_CLASSIFICATIONS

DEFINITION_FIELDS = (
    "purpose",
    "connectionReference",
    "authentication",
    "contact",
    "termsOfUse",
)
# custom descriptor の中身を正本とし、補助ストアの管理情報（MANAGEMENT）にも写しを置く項目。
# 写しは公開版スナップショットを作るために使い、下書きの読み取りには使わない（ADR-0007）
SUPPLEMENT_FIELDS = ("contact", "termsOfUse", "connectionReference")
TEXT_LIMITS = {
    "name": 255,
    "displayName": 255,
    "description": 4096,
    "recordVersion": 255,
    "ownerTeam": 128,
    "environment": 3,
    "dataClassification": 7,
    "purpose": 4096,
    "connectionReference": 2048,
    "authentication": 1024,
    "contact": 1024,
    "termsOfUse": 4096,
}
SEMVER = re.compile(
    r"(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)"
    r"(?:-((?:0|[1-9][0-9]*|[0-9]*[a-zA-Z-][0-9a-zA-Z-]*)"
    r"(?:\.(?:0|[1-9][0-9]*|[0-9]*[a-zA-Z-][0-9a-zA-Z-]*))*))?"
    r"(?:\+[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?"
)

# AWS の Registry レコードの状態（ADR-0002）。
# SPA の `AWS_RECORD_STATUSES`（frontend/src/drafts/api.ts）と同じ値に揃える
RecordStatus = Literal[
    "DRAFT",
    "PENDING_APPROVAL",
    "APPROVED",
    "REJECTED",
    "DEPRECATED",
    "CREATING",
    "UPDATING",
    "CREATE_FAILED",
    "UPDATE_FAILED",
]
RECORD_STATUSES: tuple[RecordStatus, ...] = get_args(RecordStatus)
# AWS が書き込みを処理中で、結果がまだ確定していない状態
IN_FLIGHT_STATUSES: frozenset[RecordStatus] = frozenset({"CREATING", "UPDATING"})
# AWS が書き込みの失敗を確定した状態
AWS_FAILED_STATUSES: frozenset[RecordStatus] = frozenset({"CREATE_FAILED", "UPDATE_FAILED"})
# 公開版だけがあり、下書きを持たない状態
NO_DRAFT_STATUSES: frozenset[RecordStatus] = frozenset({"APPROVED", "DEPRECATED"})
# 編集を受け付けない状態
NOT_EDITABLE_STATUSES: frozenset[RecordStatus] = IN_FLIGHT_STATUSES | {"DEPRECATED"}

# ポータルの書き込み操作の状態。SPA の `DraftOperation`（frontend/src/drafts/api.ts）と揃える
OperationState = Literal["IN_PROGRESS", "VERIFYING", "FAILED", "SUCCEEDED"]
OperationKind = Literal["CREATE", "UPDATE"]


class PrincipalRecord(TypedDict):
    issuer: str
    subject: str


class _DraftOperationRequired(TypedDict):
    operationId: str
    ownerTeam: str
    principal: PrincipalRecord
    kind: OperationKind
    state: OperationState
    draft: dict[str, str]
    lockPk: str
    startedAt: str


class DraftOperation(_DraftOperationRequired, total=False):
    """補助ストアに保存する、下書きの書き込み操作。"""

    # 作成: 同じ Idempotency-Key に異なる内容が来たことを検出する
    requestHash: str
    # 更新: 取得時の内容ハッシュ。AWS に適用されなかったことの判定にも使う
    expectedContentHash: str
    recordId: str
    management: dict[str, Any]
    result: dict[str, Any]
    errorCode: str


def has_draft(status: str) -> bool:
    return status not in NO_DRAFT_STATUSES


def canonical(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def digest(value: object) -> str:
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def content_hash(record: Mapping[str, Any], management: Mapping[str, Any] | None = None) -> str:
    content = {
        key: record.get(key)
        for key in (
            "name",
            "displayName",
            "description",
            "recordType",
            "recordVersion",
            "descriptors",
            "customMetadata",
        )
    }
    if management is not None:
        content["supplement"] = {
            key: management.get(key) for key in ("ownerTeam", *SUPPLEMENT_FIELDS)
        }
    return digest(content)


@dataclass(frozen=True)
class AgentDraft:
    fields: Mapping[str, str]

    @classmethod
    def parse(cls, value: Mapping[str, Any]) -> AgentDraft:
        if set(value) - TEXT_LIMITS.keys():
            raise ValueError("下書きに指定できない項目が含まれています。")
        fields = {"environment": "dev", **value}
        for key, limit in TEXT_LIMITS.items():
            text = fields.get(key)
            if not isinstance(text, str) or not text.strip() or len(text) > limit:
                raise ValueError(f"{key} は1〜{limit}文字で指定してください。")
        if not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_./-]*", fields["name"]):
            raise ValueError("name は英数字で始め、英数字と _ . / - で指定してください。")
        if not SEMVER.fullmatch(fields["recordVersion"]):
            raise ValueError("recordVersion は semver（例 1.0.0）で指定してください。")
        if fields["environment"] != "dev":
            raise ValueError("environment は dev のみ指定できます。")
        if fields["dataClassification"] not in DATA_CLASSIFICATIONS:
            raise ValueError(
                f"データ分類は {' または '.join(DATA_CLASSIFICATIONS)} を指定してください。"
            )
        return cls(fields)

    def supplement(self) -> dict[str, str]:
        """補助ストアの管理情報に置く写し。"""
        return {key: self.fields[key] for key in SUPPLEMENT_FIELDS}

    def create_payload(self) -> dict[str, Any]:
        return {
            **{
                key: self.fields[key]
                for key in ("name", "displayName", "description", "recordVersion")
            },
            "recordType": AGENT_RECORD_TYPE,
            "descriptors": {
                "custom": {"data": canonical({key: self.fields[key] for key in DEFINITION_FIELDS})}
            },
            "customMetadata": {
                **{
                    key: self.fields[key]
                    for key in ("ownerTeam", "environment", "dataClassification")
                },
                "isA2A": False,
            },
        }

    def update_payload(self) -> dict[str, Any]:
        create = self.create_payload()
        return {
            "displayName": {"optionalValue": self.fields["displayName"]},
            "description": {"optionalValue": self.fields["description"]},
            "recordVersion": self.fields["recordVersion"],
            "customMetadata": {"optionalValue": create["customMetadata"]},
            "descriptors": {
                "optionalValue": {
                    "custom": {
                        "optionalValue": {
                            "data": {"optionalValue": create["descriptors"]["custom"]["data"]},
                        }
                    }
                }
            },
        }

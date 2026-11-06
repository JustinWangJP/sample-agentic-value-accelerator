from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Collection, Mapping
from dataclasses import dataclass
from typing import Any, Literal, TypedDict, get_args

from registry_portal.domain.catalog import AGENT_RECORD_TYPE, DATA_CLASSIFICATIONS

# Registry レコードの共通項目のうち、フォームから入力する項目
RECORD_FIELDS = ("name", "displayName", "description", "recordVersion")
# custom metadata に保存する項目（isA2A は定義から導出するため含めない）
METADATA_FIELDS = ("ownerTeam", "environment", "dataClassification")
# custom descriptor の data に canonical JSON として保存する定義
DEFINITION_FIELDS = (
    "purpose",
    "connectionReference",
    "authentication",
    "contact",
    "termsOfUse",
)
# custom descriptor の中身を正本とし、補助ストアの管理情報（MANAGEMENT）にも写しを置く項目。
# 写しは公開版スナップショットを作るために使い、下書きの読み取りには使わない（ADR-0008）
SUPPLEMENT_FIELDS = ("contact", "termsOfUse", "connectionReference")

# 下書きを保存できる environment。SPA の `DRAFT_ENVIRONMENTS`（frontend/src/drafts/api.ts）と揃える
Environment = Literal["dev"]
ENVIRONMENTS: tuple[Environment, ...] = get_args(Environment)
DEFAULT_ENVIRONMENT: Environment = ENVIRONMENTS[0]

# 項目ごとの文字数上限。SPA の `DRAFT_TEXT_FIELDS`（frontend/src/drafts/api.ts）と揃える
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
# semver 2.0.0 の版。ただし AWS の RegistryRecordVersion は `[a-zA-Z0-9.-]+` で `+` を受け付けないため、
# ビルドメタデータ（`+build`）は許可しない
SEMVER = re.compile(
    r"(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)"
    r"(?:-((?:0|[1-9][0-9]*|[0-9]*[a-zA-Z-][0-9a-zA-Z-]*)"
    r"(?:\.(?:0|[1-9][0-9]*|[0-9]*[a-zA-Z-][0-9a-zA-Z-]*))*))?"
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
DRAFT_STATUS: RecordStatus = "DRAFT"
# 公開承認済みで、AWS が公開中の版を返している状態
PUBLISHED_STATUS: RecordStatus = "APPROVED"
# AWS が書き込みを処理中で、結果がまだ確定していない状態
IN_FLIGHT_STATUSES: frozenset[RecordStatus] = frozenset({"CREATING", "UPDATING"})
# AWS が書き込みの失敗を確定した状態
AWS_FAILED_STATUSES: frozenset[RecordStatus] = frozenset({"CREATE_FAILED", "UPDATE_FAILED"})
# 公開版だけがあり、下書きを持たない状態
NO_DRAFT_STATUSES: frozenset[RecordStatus] = frozenset({PUBLISHED_STATUS, "DEPRECATED"})
# 編集を受け付けない状態
NOT_EDITABLE_STATUSES: frozenset[RecordStatus] = IN_FLIGHT_STATUSES | {"DEPRECATED"}

# ポータルの書き込み操作の状態。API が返すのは IN_PROGRESS 以外の3つで、
# SPA の `OPERATION_STATES`（frontend/src/drafts/api.ts）と揃える
OperationState = Literal["IN_PROGRESS", "VERIFYING", "FAILED", "SUCCEEDED"]
OperationKind = Literal["CREATE", "UPDATE"]

# 確定した操作（実行制御の項目）を補助テーブルの TTL で削除するまでの秒数（設計書「補助テーブルに保存するもの」）。
# この間は同じ Idempotency-Key に同じ結果を返す。未確定の操作とロックには TTL を付けない
SETTLED_OPERATION_RETENTION_SECONDS = 7 * 24 * 60 * 60


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
    # 確定時に保存する管理情報（MANAGEMENT の項目）
    management: dict[str, Any]
    result: DraftResult
    errorCode: str
    # AWS の書き込み API が返した request ID（設計書「操作記録」）
    awsRequestId: str


class DraftView(TypedDict):
    """API が返す下書きの詳細。"""

    recordId: str
    draft: dict[str, str]
    contentHash: str
    status: str
    hasDraft: bool
    publishedVersion: str | None


class DraftResult(DraftView):
    """結果が確定した書き込み操作の応答。"""

    operationId: str
    operationState: OperationState


class OperationStatus(TypedDict):
    """結果確認中、または失敗が確定した書き込み操作の応答。"""

    operationId: str
    operationState: OperationState
    message: str


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


def _is_principal(value: object) -> bool:
    return (
        isinstance(value, Mapping)
        and isinstance(value.get("issuer"), str)
        and isinstance(value.get("subject"), str)
    )


@dataclass(frozen=True)
class Management:
    """補助ストアの管理情報（MANAGEMENT）。所有チームの正本と、公開版スナップショット用の写しを持つ。"""

    item: Mapping[str, Any]

    @classmethod
    def parse(cls, item: Mapping[str, Any]) -> Management:
        """保存された管理情報を検査する。型が契約と異なれば ValueError。"""
        owner_team = item.get("ownerTeam")
        if (
            not isinstance(item.get("managed"), bool)
            or not isinstance(owner_team, str)
            or not owner_team
            or any(key in item and not isinstance(item[key], str) for key in SUPPLEMENT_FIELDS)
            or (
                item.get("publishedVersion") is not None
                and not isinstance(item["publishedVersion"], str)
            )
            or ("lastEditedBy" in item and not _is_principal(item["lastEditedBy"]))
        ):
            raise ValueError("保存された管理情報の形式が正しくありません。")
        return cls(item)

    @classmethod
    def after_edit(
        cls,
        base: Mapping[str, Any],
        owner_team: str,
        draft: AgentDraft,
        editor: PrincipalRecord,
        edited_at: str,
    ) -> Management:
        """下書きを保存した後の管理情報。最終編集者は自己承認の禁止（設計書「ロールと所有」）に使う。"""
        return cls(
            {
                **base,
                "managed": True,
                "ownerTeam": owner_team,
                **draft.supplement(),
                "lastEditedBy": editor,
                "lastEditedAt": edited_at,
            }
        )

    @property
    def owner_team(self) -> str:
        return str(self.item["ownerTeam"])

    def is_owned_by(self, teams: Collection[str]) -> bool:
        """ポータルの管理下にあり、所有チームが指定の所属チームに含まれるか。"""
        return self.item.get("managed") is True and self.owner_team in teams

    def published_version(self, record: Mapping[str, Any]) -> str | None:
        """公開中の版。AWS が新しい下書きを返している間は、補助ストアに保持した版を返す。"""
        if record["status"] == PUBLISHED_STATUS:
            return str(record["recordVersion"])
        version = self.item.get("publishedVersion")
        return version if isinstance(version, str) else None

    def hashed_fields(self) -> dict[str, Any]:
        """内容ハッシュに含める補助情報。"""
        return {key: self.item.get(key) for key in ("ownerTeam", *SUPPLEMENT_FIELDS)}


def content_hash(record: Mapping[str, Any], management: Management | None = None) -> str:
    content = {
        key: record.get(key)
        for key in (*RECORD_FIELDS, "recordType", "descriptors", "customMetadata")
    }
    if management is not None:
        content["supplement"] = management.hashed_fields()
    return digest(content)


@dataclass(frozen=True)
class AgentDraft:
    fields: Mapping[str, str]

    @classmethod
    def parse(cls, value: Mapping[str, Any]) -> AgentDraft:
        if set(value) - TEXT_LIMITS.keys():
            raise ValueError("下書きに指定できない項目が含まれています。")
        fields = {"environment": DEFAULT_ENVIRONMENT, **value}
        for key, limit in TEXT_LIMITS.items():
            text = fields.get(key)
            if not isinstance(text, str) or not text.strip() or len(text) > limit:
                raise ValueError(f"{key} は1〜{limit}文字で指定してください。")
        if not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_./-]*", fields["name"]):
            raise ValueError("name は英数字で始め、英数字と _ . / - で指定してください。")
        if not SEMVER.fullmatch(fields["recordVersion"]):
            raise ValueError(
                "recordVersion は semver（例 1.0.0）で指定してください。"
                "ビルドメタデータ（+ 以降）は指定できません。"
            )
        if fields["environment"] not in ENVIRONMENTS:
            raise ValueError(f"environment は {' または '.join(ENVIRONMENTS)} のみ指定できます。")
        if fields["dataClassification"] not in DATA_CLASSIFICATIONS:
            raise ValueError(
                f"データ分類は {' または '.join(DATA_CLASSIFICATIONS)} を指定してください。"
            )
        return cls(fields)

    @classmethod
    def from_record(cls, record: Mapping[str, Any], owner_team: str) -> AgentDraft:
        """Registry レコードを custom Agent フォームの下書きとして読む。読めなければ ValueError。

        定義は custom descriptor の中身を正本とし、所有チームだけは補助ストアの値を使う（ADR-0002）。
        """
        try:
            if record["recordType"] != AGENT_RECORD_TYPE or set(record["descriptors"]) != {
                "custom"
            }:
                raise ValueError("custom Agent ではありません。")
            definition = json.loads(record["descriptors"]["custom"]["data"])
            if not isinstance(definition, Mapping) or set(definition) != set(DEFINITION_FIELDS):
                raise ValueError("custom descriptor の定義がフォームの項目と一致しません。")
            metadata = record["customMetadata"]
            return cls.parse(
                {
                    **{key: record[key] for key in RECORD_FIELDS},
                    "ownerTeam": owner_team,
                    "environment": metadata["environment"],
                    "dataClassification": metadata["dataClassification"],
                    **definition,
                }
            )
        except (KeyError, TypeError) as error:
            raise ValueError("Registry レコードに必要な項目がありません。") from error

    def supplement(self) -> dict[str, str]:
        """補助ストアの管理情報に置く写し。"""
        return {key: self.fields[key] for key in SUPPLEMENT_FIELDS}

    def _definition_data(self) -> str:
        """custom descriptor の data に保存する canonical JSON。"""
        return canonical({key: self.fields[key] for key in DEFINITION_FIELDS})

    def create_payload(self) -> dict[str, Any]:
        return {
            **{key: self.fields[key] for key in RECORD_FIELDS},
            "recordType": AGENT_RECORD_TYPE,
            "descriptors": {"custom": {"data": self._definition_data()}},
            "customMetadata": self._custom_metadata(),
        }

    def update_payload(self) -> dict[str, Any]:
        return {
            "displayName": {"optionalValue": self.fields["displayName"]},
            "description": {"optionalValue": self.fields["description"]},
            "recordVersion": self.fields["recordVersion"],
            "customMetadata": {"optionalValue": self._custom_metadata()},
            "descriptors": {
                "optionalValue": {
                    "custom": {
                        "optionalValue": {"data": {"optionalValue": self._definition_data()}}
                    }
                }
            },
        }

    def _custom_metadata(self) -> dict[str, Any]:
        # isA2A は利用者の入力ではなく descriptor から導出する。custom descriptor の Agent は常に false
        return {**{key: self.fields[key] for key in METADATA_FIELDS}, "isA2A": False}


def draft_view(record: Mapping[str, Any], management: Management, draft: AgentDraft) -> DraftView:
    return {
        "recordId": record["recordId"],
        "draft": dict(draft.fields),
        "contentHash": content_hash(record, management),
        "status": record["status"],
        "hasDraft": has_draft(record["status"]),
        "publishedVersion": management.published_version(record),
    }

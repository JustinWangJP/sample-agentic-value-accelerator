from __future__ import annotations

import hashlib
import re
from collections.abc import Collection, Mapping
from dataclasses import dataclass
from typing import Any, Literal, TypedDict, TypeIs, get_args

from registry_portal.domain.catalog import (
    AGENT_RECORD_TYPE,
    CATALOG_RECORD_TYPES,
    DATA_CLASSIFICATIONS,
    DescriptorType,
)
from registry_portal.domain.descriptors import (
    ALL_SUPPLEMENT_FIELDS,
    DEFINITION_LIMITS,
    DESCRIPTOR_FIELDS,
    OPTIONAL_FIELDS,
    STRUCTURED_FIELDS,
    SUPPLEMENT_FIELDS,
    canonical,
    check_kind,
    create_descriptors,
    custom_metadata_extra,
    default_descriptor_type,
    descriptors_applied,
    normalize_and_check,
    read_descriptor_fields,
    supplement_fields,
    update_descriptors,
)
from registry_portal.domain.secrets import scan_text

# Registry レコードの共通項目のうち、フォームから入力する項目
RECORD_FIELDS = ("name", "displayName", "description", "recordVersion")
# 種別（recordType）と primary descriptor の種類。省略すると custom Agent
KIND_FIELDS = ("recordType", "descriptorType")
# custom metadata に保存する項目（isA2A は定義から導出するため含めない）
METADATA_FIELDS = ("ownerTeam", "environment", "dataClassification")
# 連絡先・利用条件・接続先の参照（SUPPLEMENT_FIELDS）は domain/descriptors.py に定義する
COMMON_FIELDS = (*RECORD_FIELDS, *KIND_FIELDS, *METADATA_FIELDS, *SUPPLEMENT_FIELDS)

# 下書きを保存できる environment。SPA の `DRAFT_ENVIRONMENTS`（frontend/src/drafts/api.ts）と揃える
Environment = Literal["dev"]
ENVIRONMENTS: tuple[Environment, ...] = get_args(Environment)
DEFAULT_ENVIRONMENT: Environment = ENVIRONMENTS[0]

# 許可値から選ぶ項目。文字数ではなく許可値で検査する（許可値の定義は catalog.py と上の Environment）
CHOICE_FIELDS = (*KIND_FIELDS, "environment", "dataClassification")
# 自由入力の項目ごとの文字数上限。SPA の `COMMON_TEXT_FIELDS` と `DESCRIPTOR_FIELD_SPECS`
# （frontend/src/drafts/descriptors.ts）と揃える
TEXT_LIMITS = {
    "name": 255,
    "displayName": 255,
    "description": 4096,
    "recordVersion": 255,
    "ownerTeam": 128,
    "purpose": 4096,
    "connectionReference": 2048,
    "authentication": 1024,
    "contact": 1024,
    "termsOfUse": 4096,
    "schemaVersion": 32,
    "toolsSchemaVersion": 32,
    "skillRepositoryUrl": 2048,
    "skillRepositorySource": 64,
    "skillCommit": 64,
    "skillVersion": 128,
    "skillDigest": 71,
    **DEFINITION_LIMITS,
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
# 申請を AWS が受け付け、Curator の審査待ちになった状態
PENDING_APPROVAL_STATUS: RecordStatus = "PENDING_APPROVAL"
# 公開承認済みで、AWS が公開中の版を返している状態
PUBLISHED_STATUS: RecordStatus = "APPROVED"
REJECTED_STATUS: RecordStatus = "REJECTED"
# 申請が AWS に受け付けられた後にだけ取りうる状態（審査待ち、または審査で決裁済み）
SUBMITTED_STATUSES: frozenset[RecordStatus] = frozenset(
    {PENDING_APPROVAL_STATUS, PUBLISHED_STATUS, REJECTED_STATUS}
)
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
OperationKind = Literal["CREATE", "UPDATE", "SUBMIT", "APPROVE", "REJECT"]
CREATE_KIND: OperationKind = "CREATE"
UPDATE_KIND: OperationKind = "UPDATE"
SUBMIT_KIND: OperationKind = "SUBMIT"
APPROVE_KIND: OperationKind = "APPROVE"
REJECT_KIND: OperationKind = "REJECT"
# 決裁 handler が行う操作（公開承認・却下）。操作照会は種別ごとに参照できる主体を変える
DECISION_KINDS: frozenset[OperationKind] = frozenset({APPROVE_KIND, REJECT_KIND})

# Registry レコード ID の形式。SPA の `RECORD_ID_PATTERN` と `RECORD_ID_LENGTH`
# （frontend/src/drafts/api.ts）と揃える
RECORD_ID_LENGTH = 12
RECORD_ID_PATTERN = re.compile(rf"[A-Za-z0-9]{{{RECORD_ID_LENGTH}}}")

# 確定した操作（実行制御の項目）を補助テーブルの TTL で削除するまでの秒数（設計書「補助テーブルに保存するもの」）。
# この間は同じ Idempotency-Key に同じ結果を返す。未確定の操作とロックには TTL を付けない
SETTLED_OPERATION_RETENTION_SECONDS = 7 * 24 * 60 * 60


class PrincipalRecord(TypedDict):
    issuer: str
    subject: str


class _OperationRequired(TypedDict):
    operationId: str
    ownerTeam: str
    principal: PrincipalRecord
    kind: OperationKind
    state: OperationState
    lockPk: str
    startedAt: str


class OperationRecord(_OperationRequired, total=False):
    """補助ストアに保存する書き込み操作のうち、下書きの操作と決裁の操作に共通する項目。

    補助ストア（DraftStore）のロック・確定・追記記録は、この項目だけを使う。
    """

    # 同じ Idempotency-Key に異なる内容が来たことを検出する
    requestHash: str
    # 更新: 取得時の内容ハッシュ。決裁: 申請時の内容ハッシュ。AWS に適用されたかの判定に使う
    expectedContentHash: str
    recordId: str
    # 確定時に保存する管理情報（MANAGEMENT の項目）
    management: dict[str, Any]
    errorCode: str
    # AWS の書き込み API が返した request ID（設計書「操作記録」）
    awsRequestId: str
    # 申請: 保存した申請スナップショットのハッシュ。決裁: Curator が審査したスナップショットのハッシュ
    snapshotHash: str


class _DraftOperationRequired(OperationRecord):
    draft: dict[str, str]


class DraftOperation(_DraftOperationRequired, total=False):
    """補助ストアに保存する、下書きの書き込み操作（作成・更新・申請）。"""

    result: DraftResult
    # 申請: 標準の申請 API が受け付けを返した。未設定なら、照合時に申請 API を呼び直す
    submissionAccepted: bool


class _DecisionOperationRequired(OperationRecord):
    # 承認・却下の両方で必須の理由。AWS の statusReason にそのまま送る
    reason: str


class DecisionOperation(_DecisionOperationRequired, total=False):
    """補助ストアに保存する、決裁の操作（公開承認・却下）。"""

    result: dict[str, Any]
    # 対象とした申請スナップショットの ID と、その時点のレコードの版
    submissionId: str
    recordVersion: str
    # AWS の状態更新 API が受け付けを返した。未設定なら、決裁 handler の再送で呼び直す
    decisionAccepted: bool


# 補助ストアに保存された操作。種別（kind）で下書きの操作か決裁の操作かが決まる
StoredOperation = DraftOperation | DecisionOperation


def is_decision_operation(operation: StoredOperation) -> TypeIs[DecisionOperation]:
    """決裁の操作か。操作照会は、種別ごとに参照できる主体を変える。"""
    return operation["kind"] in DECISION_KINDS


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


class SubmissionResult(DraftResult):
    submissionId: str
    snapshotHash: str


class OperationStatus(TypedDict):
    """結果確認中、または失敗が確定した書き込み操作の応答。"""

    operationId: str
    operationState: OperationState
    message: str


def has_draft(status: str) -> bool:
    return status not in NO_DRAFT_STATUSES


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
            or any(key in item and not isinstance(item[key], str) for key in ALL_SUPPLEMENT_FIELDS)
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
        draft: RecordDraft,
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
                "activeSubmissionId": None,
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
        return {key: self.item.get(key) for key in ("ownerTeam", *ALL_SUPPLEMENT_FIELDS)}


def content_hash(record: Mapping[str, Any], management: Management | None = None) -> str:
    content = {
        key: record.get(key)
        for key in (*RECORD_FIELDS, "recordType", "descriptors", "customMetadata")
    }
    if management is not None:
        content["supplement"] = management.hashed_fields()
    return digest(content)


@dataclass(frozen=True)
class RecordDraft:
    """Registry レコードの下書き。custom / A2A の Agent、MCP サーバー、Skill を同じ形で扱う。

    fields は文字列だけの平らな辞書で、種別（recordType）と primary descriptor（descriptorType）に応じて
    入力項目が異なる（domain/descriptors.py の DESCRIPTOR_FIELDS）。
    """

    fields: Mapping[str, str]

    @property
    def record_type(self) -> str:
        return self.fields["recordType"]

    @property
    def descriptor_type(self) -> DescriptorType:
        return check_kind(self.record_type, self.fields["descriptorType"])[1]

    @classmethod
    def parse(cls, value: Mapping[str, Any]) -> RecordDraft:
        record_type = value.get("recordType", AGENT_RECORD_TYPE)
        descriptor_type = value.get("descriptorType")
        if descriptor_type is None and record_type in CATALOG_RECORD_TYPES:
            descriptor_type = default_descriptor_type(record_type)
        kind, descriptor = check_kind(str(record_type), str(descriptor_type))
        allowed = (*COMMON_FIELDS, *DESCRIPTOR_FIELDS[descriptor])
        if set(value) - set(allowed):
            raise ValueError("下書きに指定できない項目が含まれています。")
        fields = {
            "environment": DEFAULT_ENVIRONMENT,
            **{key: "" for key in OPTIONAL_FIELDS if key in allowed},
            **value,
            "recordType": kind,
            "descriptorType": descriptor,
        }
        for key in allowed:
            if key in CHOICE_FIELDS:
                continue
            limit = TEXT_LIMITS[key]
            text = fields.get(key)
            if (
                not isinstance(text, str)
                or len(text) > limit
                or (not text.strip() and key not in OPTIONAL_FIELDS)
            ):
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
        if fields.get("dataClassification") not in DATA_CLASSIFICATIONS:
            raise ValueError(
                f"データ分類は {' または '.join(DATA_CLASSIFICATIONS)} を指定してください。"
            )
        findings = normalize_and_check(descriptor, fields) + [
            finding
            for key in allowed
            if key not in STRUCTURED_FIELDS
            for finding in scan_text(key, fields[key])
        ]
        if findings:
            shown = "、".join(dict.fromkeys(str(finding) for finding in findings[:5]))
            raise ValueError(
                f"秘密情報らしき値が含まれているため保存できません: {shown}。"
                "値は入力せず、Secrets Manager などの保管先への参照を記載してください。"
            )
        return cls(fields)

    @classmethod
    def from_record(
        cls, record: Mapping[str, Any], owner_team: str, supplement: Mapping[str, Any]
    ) -> RecordDraft:
        """Registry レコードをフォームの下書きとして読む。読めなければ ValueError。

        定義は primary descriptor の中身を正本とし、所有チームは補助ストアの値を使う（ADR-0002）。
        descriptor に置けない連絡先などは補助ストアの管理情報（supplement）を正本として読む（ADR-0009）。
        """
        try:
            descriptors = record["descriptors"]
            if len(descriptors) != 1:
                raise ValueError("primary descriptor が1個ではありません。")
            kind, descriptor = check_kind(record["recordType"], next(iter(descriptors)))
            metadata = record["customMetadata"]
            specific = read_descriptor_fields(descriptor, descriptors[descriptor], supplement)
            return cls.parse(
                {
                    **{key: record[key] for key in RECORD_FIELDS},
                    "recordType": kind,
                    "descriptorType": descriptor,
                    "ownerTeam": owner_team,
                    "environment": metadata["environment"],
                    "dataClassification": metadata["dataClassification"],
                    **specific,
                }
            )
        except (KeyError, TypeError) as error:
            raise ValueError("Registry レコードに必要な項目がありません。") from error

    def supplement(self) -> dict[str, str]:
        """補助ストアの管理情報に置く項目。"""
        return {key: self.fields[key] for key in supplement_fields(self.descriptor_type)}

    def create_payload(self) -> dict[str, Any]:
        return {
            **{key: self.fields[key] for key in RECORD_FIELDS},
            "recordType": self.record_type,
            "descriptors": create_descriptors(self.descriptor_type, self.fields),
            "customMetadata": self._custom_metadata(),
        }

    def update_payload(self, existing_descriptors: Collection[str] = ()) -> dict[str, Any]:
        """UpdateRegistryRecord の payload。

        primary descriptor を切り替えるときは、古い descriptor を空のオブジェクトで削除して1個に保つ。
        """
        descriptor = self.descriptor_type
        patches = update_descriptors(descriptor, self.fields)
        removed: dict[str, Any] = {name: {} for name in existing_descriptors if name != descriptor}
        return {
            "displayName": {"optionalValue": self.fields["displayName"]},
            "description": {"optionalValue": self.fields["description"]},
            "recordVersion": self.fields["recordVersion"],
            "customMetadata": {"optionalValue": self._custom_metadata()},
            "descriptors": {"optionalValue": {**removed, **patches}},
        }

    def is_applied_to(self, record: Mapping[str, Any]) -> bool:
        """AWS のレコードが、この下書きの内容を適用済みか。

        AWS が受理後に補う項目（descriptor の source など）や、JSON の空白・並びの違いは無視する。
        """
        payload = self.create_payload()
        metadata = record.get("customMetadata", {})
        return (
            all(record.get(key) == payload[key] for key in (*RECORD_FIELDS, "recordType"))
            and all(metadata.get(key) == value for key, value in payload["customMetadata"].items())
            and isinstance(record.get("descriptors"), Mapping)
            and descriptors_applied(payload["descriptors"], record["descriptors"])
        )

    def _custom_metadata(self) -> dict[str, Any]:
        # isA2A は利用者の入力ではなく descriptor から導出する（ADR-0006）
        return {
            **{key: self.fields[key] for key in METADATA_FIELDS},
            **custom_metadata_extra(self.record_type, self.fields["descriptorType"]),
        }


def draft_view(record: Mapping[str, Any], management: Management, draft: RecordDraft) -> DraftView:
    return {
        "recordId": record["recordId"],
        "draft": dict(draft.fields),
        "contentHash": content_hash(record, management),
        "status": record["status"],
        "hasDraft": has_draft(record["status"]),
        "publishedVersion": management.published_version(record),
    }

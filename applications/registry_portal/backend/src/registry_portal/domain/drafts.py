from __future__ import annotations

import hashlib
import re
from collections.abc import Collection, Mapping
from dataclasses import dataclass
from typing import Any, Literal, NotRequired, TypedDict, TypeIs, cast, get_args

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
# 廃止済み。どの状態からも遷移でき、元に戻せない終端の状態（README「公開停止・廃止・廃止依頼」）
DEPRECATED_STATUS: RecordStatus = "DEPRECATED"
# 申請が AWS に受け付けられた後にだけ取りうる状態（審査待ち、または審査で決裁済み）
SUBMITTED_STATUSES: frozenset[RecordStatus] = frozenset(
    {PENDING_APPROVAL_STATUS, PUBLISHED_STATUS, REJECTED_STATUS}
)
# AWS が書き込みを処理中で、結果がまだ確定していない状態
IN_FLIGHT_STATUSES: frozenset[RecordStatus] = frozenset({"CREATING", "UPDATING"})
# AWS が書き込みの失敗を確定した状態
AWS_FAILED_STATUSES: frozenset[RecordStatus] = frozenset({"CREATE_FAILED", "UPDATE_FAILED"})
# 公開版だけがあり、下書きを持たない状態
NO_DRAFT_STATUSES: frozenset[RecordStatus] = frozenset({PUBLISHED_STATUS, DEPRECATED_STATUS})
# 編集を受け付けない状態
NOT_EDITABLE_STATUSES: frozenset[RecordStatus] = IN_FLIGHT_STATUSES | {DEPRECATED_STATUS}

# ポータルの書き込み操作の状態。API が返すのは IN_PROGRESS 以外の3つで、
# SPA の `OPERATION_STATES`（frontend/src/drafts/api.ts）と揃える
OperationState = Literal["IN_PROGRESS", "VERIFYING", "FAILED", "SUCCEEDED"]
# SPA の `OPERATION_KINDS`（frontend/src/drafts/api.ts）と揃える
OperationKind = Literal[
    "CREATE",
    "UPDATE",
    "SUBMIT",
    "APPROVE",
    "REJECT",
    "SUSPEND",
    "DEPRECATE",
    "REQUEST_DEPRECATION",
    "DECLINE_DEPRECATION",
    "ADOPT",
    "REASSIGN_OWNER",
]
OPERATION_KINDS: tuple[OperationKind, ...] = get_args(OperationKind)
CREATE_KIND: OperationKind = "CREATE"
UPDATE_KIND: OperationKind = "UPDATE"
SUBMIT_KIND: OperationKind = "SUBMIT"
APPROVE_KIND: OperationKind = "APPROVE"
REJECT_KIND: OperationKind = "REJECT"
# 公開停止（却下で発見できなくする）と廃止（元に戻せない DEPRECATED）。申請への決裁ではないが、
# AWS の状態更新 API を呼ぶため、決裁と同じ決裁 handler が行う
SUSPEND_KIND: OperationKind = "SUSPEND"
DEPRECATE_KIND: OperationKind = "DEPRECATE"
# 廃止依頼（Publisher）と、その見送り（Curator）。AWS の状態を変えず、補助テーブルへの書き込みだけで完結する。
# 実行制御（CONTROL）やロックを残さず、1つのトランザクションで INTENT と RESULT を同時に追記する
REQUEST_DEPRECATION_KIND: OperationKind = "REQUEST_DEPRECATION"
DECLINE_DEPRECATION_KIND: OperationKind = "DECLINE_DEPRECATION"
# 決裁 handler が行う操作（決裁である公開承認・却下と、公開停止・廃止）。操作照会は種別ごとに参照できる主体を変える
DECISION_KINDS: frozenset[OperationKind] = frozenset(
    {APPROVE_KIND, REJECT_KIND, SUSPEND_KIND, DEPRECATE_KIND}
)

# 履歴 API が返す操作記録の結果。SPA の `HISTORY_STATES`（frontend/src/drafts/api.ts）と揃える。
# 結果確認中は、操作照会（OperationState）が API で返す値と同じ VERIFYING で表す。
# 結果の追記記録（RESULT）がない操作は結果確認中（VERIFYING）、取り込み前の公開承認は不明（UNKNOWN）
HistoryState = Literal["SUCCEEDED", "FAILED", "VERIFYING", "UNKNOWN"]
HISTORY_SUCCEEDED: HistoryState = "SUCCEEDED"
HISTORY_FAILED: HistoryState = "FAILED"
HISTORY_VERIFYING: HistoryState = "VERIFYING"
HISTORY_UNKNOWN: HistoryState = "UNKNOWN"

# 追記記録の段階。操作の開始時に INTENT、結果の確定時に RESULT を追記する
AuditPhase = Literal["INTENT", "RESULT"]
AUDIT_INTENT: AuditPhase = "INTENT"
AUDIT_RESULT: AuditPhase = "RESULT"
# 追記記録だけを Query で読むためのソートキーの接頭辞
AUDIT_SK_PREFIX = "AUDIT#"
_AUDIT_SK = re.compile(r"AUDIT#([^#]+)#(INTENT|RESULT)")


def audit_sk(op_id: str, phase: AuditPhase) -> str:
    """追記記録のソートキー（AUDIT#<操作 ID>#<段階>）。"""
    return f"{AUDIT_SK_PREFIX}{op_id}#{phase}"


def parse_audit_sk(sk: object) -> tuple[str, AuditPhase]:
    """追記記録のソートキーを操作 ID と段階に分ける。形式が契約と異なれば ValueError。"""
    match = _AUDIT_SK.fullmatch(sk) if isinstance(sk, str) else None
    if match is None:
        raise ValueError("保存された操作記録のキーの形式が正しくありません。")
    return match.group(1), cast(AuditPhase, match.group(2))


# Registry レコード ID の形式。SPA の `RECORD_ID_PATTERN` と `RECORD_ID_LENGTH`
# （frontend/src/drafts/api.ts）と揃える
RECORD_ID_LENGTH = 12
RECORD_ID_PATTERN = re.compile(rf"[A-Za-z0-9]{{{RECORD_ID_LENGTH}}}")

# 公開停止中であることを示す管理情報（MANAGEMENT）の項目。値は公開停止した時点の公開中の版で、
# 公開停止の成功で書き込み、公開承認の成功で消す（README「公開停止・廃止・廃止依頼」）
SUSPENDED_VERSION_KEY = "suspendedVersion"

# 管理情報（MANAGEMENT）が持ちうる項目。取り込み・所有変更は、読取時から変わっていないことを全項目で検査する
MANAGEMENT_FIELDS = (
    "managed",
    "ownerTeam",
    "publishedVersion",
    "adoptedPublishedVersion",
    "adoptedAt",
    SUSPENDED_VERSION_KEY,
    "lastEditedBy",
    "lastEditedAt",
    "activeSubmissionId",
    *ALL_SUPPLEMENT_FIELDS,
)

# 確定した操作（実行制御の項目）を補助テーブルの TTL で削除するまでの秒数（設計書「補助テーブルに保存するもの」）。
# この間は同じ Idempotency-Key に同じ結果を返す。未確定の操作とロックには TTL を付けない
SETTLED_OPERATION_RETENTION_SECONDS = 7 * 24 * 60 * 60


class PrincipalRecord(TypedDict):
    issuer: str
    subject: str


RecoveryMode = Literal["RECONCILED", "RELEASED"]
# SPA の `RECOVERY_MODES`（frontend/src/drafts/api.ts）と揃える
RECOVERY_RECONCILED: RecoveryMode = "RECONCILED"
RECOVERY_RELEASED: RecoveryMode = "RELEASED"


class RecoveryRecord(TypedDict):
    """Admin の回復操作が結果を確定した、または強制解除したときの記録（設計書「ロールと所有」）。"""

    # RECONCILED: Registry を再取得した結果から確定した。RELEASED: 確定できないまま Admin が強制解除した
    mode: RecoveryMode
    by: PrincipalRecord
    at: str
    reason: str


def is_recovery_record(value: object) -> TypeIs[RecoveryRecord]:
    """保存された回復の記録の形式か。SPA の isRecoveryRecord（frontend/src/drafts/api.ts）と揃える。"""
    return (
        isinstance(value, Mapping)
        and value.get("mode") in get_args(RecoveryMode)
        and is_principal(value.get("by"))
        and isinstance(value.get("at"), str)
        and isinstance(value.get("reason"), str)
    )


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

    補助ストア（SupplementStore）のロック・確定・追記記録は、この項目だけを使う。
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
    # Admin の回復操作が確定・解除した操作にだけ付く
    recovery: RecoveryRecord


class _DraftOperationRequired(OperationRecord):
    draft: dict[str, str]


class DraftOperation(_DraftOperationRequired, total=False):
    """補助ストアに保存する、下書きの書き込み操作（作成・更新・申請）。"""

    result: StoredDraftResult
    # 申請: 標準の申請 API が受け付けを返した。未設定なら、照合時に申請 API を呼び直す
    submissionAccepted: bool


class _DecisionOperationRequired(OperationRecord):
    # 承認・却下の両方で必須の理由。AWS の statusReason にそのまま送る
    reason: str


class DecisionOperation(_DecisionOperationRequired, total=False):
    """補助ストアに保存する、決裁 handler の操作（決裁である公開承認・却下と、公開停止・廃止）。"""

    result: dict[str, Any]
    # 対象とした申請スナップショットの ID と、その時点のレコードの版
    submissionId: str
    recordVersion: str
    # 操作の対象を固定した時点の AWS の状態。照合で同じ状態が見えている間は、操作が未反映と判断する。
    # 記録より前の操作（公開承認・却下だけ）では、決裁の前提である PENDING_APPROVAL とみなす
    observedStatus: RecordStatus
    # AWS の状態更新 API が受け付けを返した。未設定なら、決裁 handler の再送で呼び直す
    decisionAccepted: bool


# 補助ストアに保存された操作。種別（kind）で下書きの操作か決裁の操作かが決まる
StoredOperation = DraftOperation | DecisionOperation


def is_decision_operation(operation: StoredOperation) -> TypeIs[DecisionOperation]:
    """決裁の操作か。操作照会は、種別ごとに参照できる主体を変える。"""
    return operation["kind"] in DECISION_KINDS


def aws_accepted(operation: Mapping[str, Any]) -> bool:
    """AWS の書き込み API が受け付け（成功応答）を返したと記録済みの操作か。

    受け付けた操作は、Registry に反映が見えていなくても AWS に届いている。Admin の回復操作は、
    このような操作を「確定できない」として強制解除しない（反映を待って照合する）。
    """
    return bool(operation.get("decisionAccepted") or operation.get("submissionAccepted"))


class DraftView(TypedDict):
    """API が返す下書きの詳細。"""

    recordId: str
    draft: dict[str, str]
    contentHash: str
    status: str
    hasDraft: bool
    publishedVersion: str | None
    initializationNotice: NotRequired[str]


class DraftResult(DraftView):
    """結果が確定した書き込み操作の応答。"""

    operationId: str
    operationState: OperationState


class StoredDraftResult(DraftResult, total=False):
    """補助ストアに保存した、確定した下書きの操作の結果。操作照会はこれをそのまま返す。"""

    # Admin の回復操作で確定した操作にだけ付く。補助ストアが確定時に結果へ写す（SupplementStore._close）
    recovery: RecoveryRecord


class SubmissionResult(DraftResult):
    submissionId: str
    snapshotHash: str


class OperationStatus(TypedDict):
    """結果確認中、または失敗が確定した書き込み操作の応答。"""

    operationId: str
    operationState: OperationState
    message: str
    recovery: NotRequired[RecoveryRecord]


def has_draft(status: str) -> bool:
    return status not in NO_DRAFT_STATUSES


def digest(value: object) -> str:
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def is_principal(value: object) -> TypeIs[PrincipalRecord]:
    """保存された主体（issuer と subject の組）の形式か。"""
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
            or any(
                item.get(key) is not None and not isinstance(item[key], str)
                for key in (
                    "publishedVersion",
                    "adoptedPublishedVersion",
                    "adoptedAt",
                    SUSPENDED_VERSION_KEY,
                )
            )
            or ("lastEditedBy" in item and not is_principal(item["lastEditedBy"]))
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

    @property
    def is_managed(self) -> bool:
        """ポータルの管理下にあるか。未管理（取り込み前）のレコードは決裁できず、履歴もない。"""
        return self.item.get("managed") is True

    def is_owned_by(self, teams: Collection[str]) -> bool:
        """ポータルの管理下にあり、所有チームが指定の所属チームに含まれるか。"""
        return self.is_managed and self.owner_team in teams

    def published_version(self, record: Mapping[str, Any]) -> str | None:
        """公開中の版。AWS が新しい下書きを返している間は、補助ストアに保持した版を返す。

        公開停止中（再承認まで）と廃止済みのレコードには、発見できる版がないため None を返す。
        「新しい版の却下で、旧版は公開中」の場合と区別するため、公開停止は管理情報の印で判断する。
        """
        if record["status"] == PUBLISHED_STATUS:
            return str(record["recordVersion"])
        if record["status"] == DEPRECATED_STATUS or self.is_suspended(record):
            return None
        return self.last_published_version(record)

    def last_published_version(self, record: Mapping[str, Any]) -> str | None:
        """最後に公開承認された版。公開停止中でも保持し、再申請の semver の下限に使う。"""
        if record["status"] == PUBLISHED_STATUS:
            return str(record["recordVersion"])
        version = self.item.get("publishedVersion")
        return version if isinstance(version, str) else None

    @property
    def suspended_version(self) -> str | None:
        """公開停止した時点で公開中だった版。公開停止中でなければ None（再承認で消す）。"""
        version = self.item.get(SUSPENDED_VERSION_KEY)
        return version if isinstance(version, str) else None

    def is_suspended(self, record: Mapping[str, Any]) -> bool:
        """公開停止中か。公開停止の印があり、AWS が公開中（APPROVED）を返していない。

        AWS が公開中を返すなら、印が残っていても公開停止中ではない
        （ポータルの外で再承認された場合。印はポータルの公開承認でしか消えない）。
        """
        return self.suspended_version is not None and record["status"] != PUBLISHED_STATUS

    @property
    def is_adopted(self) -> bool:
        """取り込み（#16）でポータルの管理下に置いたレコードか。ポータル外で作られた項目の欠落を許す。"""
        return isinstance(self.item.get("adoptedAt"), str)

    @property
    def adopted_published_version(self) -> str | None:
        """取り込み（#16）の時点で既に公開されていた版。ポータルに公開承認の操作記録がない。

        取り込みが管理情報に書き込み、以後の編集や決裁でも `**base` で引き継ぐ。
        ポータルで作成したレコードには存在しない。
        """
        version = self.item.get("adoptedPublishedVersion")
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
        return cls._parse(value)

    @classmethod
    def _parse(
        cls,
        value: Mapping[str, Any],
        *,
        unset: Collection[str] = (),
        legacy_version: bool = False,
    ) -> RecordDraft:
        """下書きを検査する。unset と legacy_version は取り込みレコードの読み取りだけが使う。

        unset は Registry レコードに存在しなかった項目で、空のまま読むことを許す。legacy_version は
        ポータル導入前の semver でない版をそのまま読むことを許す。保存・申請は parse で検査し直す。
        """
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
                or (not text.strip() and key not in OPTIONAL_FIELDS and key not in unset)
            ):
                raise ValueError(f"{key} は1〜{limit}文字で指定してください。")
        if not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_./-]*", fields["name"]):
            raise ValueError("name は英数字で始め、英数字と _ . / - で指定してください。")
        if not SEMVER.fullmatch(fields["recordVersion"]) and not (
            legacy_version and re.fullmatch(r"[a-zA-Z0-9.-]+", fields["recordVersion"])
        ):
            raise ValueError(
                "recordVersion は semver（例 1.0.0）で指定してください。"
                "ビルドメタデータ（+ 以降）は指定できません。"
            )
        if fields["environment"] not in ENVIRONMENTS:
            raise ValueError(f"environment は {' または '.join(ENVIRONMENTS)} のみ指定できます。")
        if fields.get("dataClassification") not in DATA_CLASSIFICATIONS and not (
            "dataClassification" in unset and fields.get("dataClassification") == ""
        ):
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
            metadata = record.get("customMetadata", {})
            record_fields = {key: record[key] for key in RECORD_FIELDS if key in record}
            adopted = isinstance(supplement.get("adoptedAt"), str)
            unset: set[str] = set()
            if adopted:
                # ポータル外で作られた項目の欠落は推測で埋めない。表示名は name、環境は設計書の既定値 dev を
                # 初期値とし、説明とデータ分類は空のまま所有チームに入力させる（保存時に必須を検査する）
                if "description" not in record:
                    unset.add("description")
                if "dataClassification" not in metadata:
                    unset.add("dataClassification")
                record_fields = {"displayName": record["name"], "description": "", **record_fields}
                metadata = {
                    "environment": DEFAULT_ENVIRONMENT,
                    "dataClassification": "",
                    **metadata,
                }
            specific = read_descriptor_fields(descriptor, descriptors[descriptor], supplement)
            return cls._parse(
                {
                    **record_fields,
                    "recordType": kind,
                    "descriptorType": descriptor,
                    "ownerTeam": owner_team,
                    "environment": metadata["environment"],
                    "dataClassification": metadata["dataClassification"],
                    **specific,
                },
                unset=unset,
                legacy_version=adopted,
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

    def update_payload(
        self,
        existing_descriptors: Collection[str] = (),
        preserved_metadata: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        """UpdateRegistryRecord の payload。

        primary descriptor を切り替えるときは、古い descriptor を空のオブジェクトで削除して1個に保つ。
        preserved_metadata は取り込みレコードの既存 metadata で、ポータルが管理しない項目を残す。
        ポータルで作成したレコードは渡さず、ポータルの項目だけで置き換える。
        """
        descriptor = self.descriptor_type
        patches = update_descriptors(descriptor, self.fields)
        removed: dict[str, Any] = {name: {} for name in existing_descriptors if name != descriptor}
        return {
            "displayName": {"optionalValue": self.fields["displayName"]},
            "description": {"optionalValue": self.fields["description"]},
            "recordVersion": self.fields["recordVersion"],
            "customMetadata": {
                "optionalValue": {**(preserved_metadata or {}), **self._custom_metadata()}
            },
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
    view: DraftView = {
        "recordId": record["recordId"],
        "draft": dict(draft.fields),
        "contentHash": content_hash(record, management),
        "status": record["status"],
        "hasDraft": has_draft(record["status"]),
        "publishedVersion": management.published_version(record),
    }
    if management.is_adopted:
        notices = []
        metadata = record.get("customMetadata", {})
        if "environment" not in metadata:
            notices.append(f"未設定の環境は {DEFAULT_ENVIRONMENT} を初期値として表示しています。")
        if "dataClassification" not in metadata:
            notices.append(
                "データ分類が未設定です。"
                f"{' または '.join(DATA_CLASSIFICATIONS)} を選択してください。"
            )
        if "displayName" not in record or "description" not in record:
            notices.append("未設定の表示名は name、説明は空欄を初期値として表示しています。")
        if not SEMVER.fullmatch(draft.fields["recordVersion"]):
            notices.append(
                "既存の版をそのまま表示しています。保存前に semver の版を指定してください。"
            )
        if notices:
            view["initializationNotice"] = (
                "取り込みレコードです。" + "".join(notices) + "内容を確認して保存してください。"
            )
    return view

"""申請入力、semver の順序、変更できない申請スナップショット。"""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any, Literal

from registry_portal.domain.catalog import (
    AGENT_RECORD_TYPE,
    MCP_RECORD_TYPE,
    SKILL_RECORD_TYPE,
    CatalogDetail,
)
from registry_portal.domain.descriptors import SUPPLEMENT_FIELDS, a2a_metadata_matches
from registry_portal.domain.drafts import (
    RECORD_ID_PATTERN,
    SEMVER,
    TEXT_LIMITS,
    Management,
    PrincipalRecord,
    RecordDraft,
    content_hash,
    digest,
)
from registry_portal.domain.secrets import scan_json, scan_text

# 申請入力の上限。SPA の申請フォーム（frontend/src/drafts/api.ts）と揃える
REASON_MAX_LENGTH = 4096
# 自動承認の規則の観測結果。SPA の接続情報（frontend/src/administration/api.ts）と揃える
AutoApprovalState = Literal["DISABLED", "ENABLED", "UNKNOWN"]
RELATED_REFERENCES_MAX = 20
# 関連参照の参照先にできる種別（設計書「関連参照」: Agent と、それが使う Skill・MCP サーバーとの関係）
REFERENCE_TARGET_RECORD_TYPES = frozenset({MCP_RECORD_TYPE, SKILL_RECORD_TYPE})

# スナップショットに残す AWS 上のレコードの項目
SNAPSHOT_RECORD_FIELDS = (
    "recordId",
    "name",
    "displayName",
    "description",
    "recordType",
    "recordVersion",
    "descriptors",
    "customMetadata",
    "updatedAt",
)


def version_key(value: str) -> tuple[int, int, int, tuple[tuple[int, int | str], ...]]:
    match = SEMVER.fullmatch(value)
    if match is None:
        raise ValueError("recordVersion は semver（例 1.2.3）で指定してください。")
    pre = match[4]
    parts = (
        tuple((0, int(p)) if p.isdigit() else (1, p) for p in pre.split(".")) if pre else ((2, ""),)
    )
    return int(match[1]), int(match[2]), int(match[3]), parts


def check_a2a_metadata(record: Mapping[str, Any], references: list[dict[str, str]]) -> None:
    """Agent は metadata の isA2A が descriptor と一致すること。関連参照は Agent だけに許す。"""
    if record["recordType"] != AGENT_RECORD_TYPE:
        if references:
            raise ValueError("関連参照は Agent の申請にのみ指定できます。")
        return
    if not a2a_metadata_matches(record):
        raise ValueError("isA2A が定義と一致しません。下書きを保存し直してください。")


def auto_approval_state(registry: Mapping[str, Any]) -> AutoApprovalState:
    """GetRegistry の自動承認の規則を、無効・有効・未取得に分ける。

    AWS の ApprovalConfiguration は「autoApprovalRules が省略または空なら手動審査」と定めるため、
    設定や規則の欠落は無効とする。型が契約と異なる応答だけを未取得とし、無効と推測しない。
    接続情報の表示と申請の停止は、この判定を共有する。
    """
    configuration = registry.get("approvalConfiguration", {})
    if not isinstance(configuration, Mapping):
        return "UNKNOWN"
    rules = configuration.get("autoApprovalRules", [])
    if not isinstance(rules, list):
        return "UNKNOWN"
    return "ENABLED" if rules else "DISABLED"


def check_newer_than_published(
    version: str, known_published: list[str | None], adopted_version: str | None = None
) -> str | None:
    """recordVersion が公開中の版より大きいことを検査し、公開中の版を返す。

    公開中の版は、知っている版（補助ストアと発見 API）の大きい方とする。
    却下後の再申請は公開中の版と比べるだけなので、同じ値を許す。
    取り込み時に記録した公開版（adopted_version）が semver でなければ、ポータル導入前の版として
    semver のどの版よりも前とみなす。それ以外の semver でない版は順序を決められないため拒否する。
    """
    candidates = [known for known in known_published if known is not None]
    ordered = [known for known in candidates if SEMVER.fullmatch(known)]
    if any(known != adopted_version for known in candidates if known not in ordered):
        raise ValueError(
            "既存の公開版が semver ではないため、新しい版との順序を比較できません。"
            "管理者が公開版の移行方針を確認するまで申請できません。"
        )
    if not ordered:
        return candidates[0] if candidates else None
    published = max(ordered, key=version_key)
    if version_key(version) <= version_key(published):
        raise ValueError("recordVersion は公開中の版より大きくしてください。")
    return published


def check_reference_target(
    record_id: str,
    reference: Mapping[str, str],
    catalog_item_of: Callable[[str], CatalogDetail | None],
) -> None:
    """関連参照の参照先が、指定した版の Skill・MCP サーバーのカタログ項目であることを検査する。

    catalog_item_of は、レコード ID から公開承認済みの版を取得する（自己参照なら呼ばない）。
    """
    if reference["recordId"] == record_id:
        raise ValueError("関連参照に申請するレコード自身は指定できません。")
    target = catalog_item_of(reference["recordId"])
    if target is None or target.item.record_version != reference["recordVersion"]:
        raise ValueError("関連参照の指定した版はカタログ項目ではありません。")
    if target.item.record_type not in REFERENCE_TARGET_RECORD_TYPES:
        raise ValueError("関連参照には Skill または MCP サーバーのカタログ項目を指定してください。")


@dataclass(frozen=True)
class SubmissionInput:
    expected_hash: str
    reason: str
    references: list[dict[str, str]]

    @classmethod
    def parse(cls, body: Mapping[str, Any]) -> SubmissionInput:
        if set(body) != {"expectedContentHash", "reason", "relatedReferences"}:
            raise ValueError("expectedContentHash、reason、relatedReferences を指定してください。")
        expected, reason, references = (
            body[k] for k in ("expectedContentHash", "reason", "relatedReferences")
        )
        if not isinstance(expected, str) or not re.fullmatch(r"[a-f0-9]{64}", expected):
            raise ValueError("取得した内容ハッシュを指定してください。")
        if not isinstance(reason, str) or not 1 <= len(reason.strip()) <= REASON_MAX_LENGTH:
            raise ValueError(f"申請理由は1〜{REASON_MAX_LENGTH}文字で指定してください。")
        if scan_text("reason", reason):
            raise ValueError(
                "申請理由に秘密情報らしき値が含まれています。保管先への参照を記載してください。"
            )
        if not isinstance(references, list) or len(references) > RELATED_REFERENCES_MAX:
            raise ValueError(
                f"関連参照は{RELATED_REFERENCES_MAX}件以内のリストで指定してください。"
            )
        normalized = []
        for reference in references:
            if not isinstance(reference, dict) or set(reference) != {"recordId", "recordVersion"}:
                raise ValueError("関連参照には recordId と recordVersion を指定してください。")
            record_id, version = reference["recordId"], reference["recordVersion"]
            if not isinstance(record_id, str) or not RECORD_ID_PATTERN.fullmatch(record_id):
                raise ValueError("関連参照の recordId の形式が正しくありません。")
            if not isinstance(version, str) or len(version) > TEXT_LIMITS["recordVersion"]:
                raise ValueError("関連参照の recordVersion の形式が正しくありません。")
            version_key(version)
            if reference in normalized:
                raise ValueError("同じ関連参照を重複して指定できません。")
            normalized.append(dict(reference))
        return cls(expected, reason.strip(), normalized)


@dataclass(frozen=True)
class Submission:
    """1回の申請。申請 ID は申請操作の操作 ID と同じ。"""

    submission_id: str
    submitted_by: PrincipalRecord
    submitted_at: str
    request: SubmissionInput


def submission_snapshot(
    submission: Submission,
    record: Mapping[str, Any],
    management: Management,
    draft: RecordDraft,
) -> dict[str, Any]:
    """申請時点の内容を固定したスナップショット。canonical JSON のハッシュを付ける。"""
    aws_record = {key: record.get(key) for key in SNAPSHOT_RECORD_FIELDS}
    if scan_json("record", aws_record):
        raise ValueError(
            "AWS の定義や metadata に秘密情報らしき値が含まれています。管理者に連絡してください。"
        )
    if isinstance(aws_record["updatedAt"], datetime):
        aws_record["updatedAt"] = aws_record["updatedAt"].isoformat()
    pinned = draft.supplement()
    if any(not isinstance(pinned.get(key), str) for key in SUPPLEMENT_FIELDS):
        # 欠けた値を空文字で補うと、公開版へ写すときに「未入力」と区別できなくなる
        raise ValueError(
            "下書きに連絡先・利用条件・接続先の参照がありません。下書きを保存し直してください。"
        )
    supplement = {**management.hashed_fields(), **pinned}
    snapshot = {
        "submissionId": submission.submission_id,
        "submittedBy": submission.submitted_by,
        "lastEditedBy": management.item["lastEditedBy"],
        "submittedAt": submission.submitted_at,
        "reason": submission.request.reason,
        "record": aws_record,
        "ownerTeam": management.owner_team,
        "supplement": supplement,
        "relatedReferences": submission.request.references,
        **{key: pinned[key] for key in SUPPLEMENT_FIELDS},
        "contentHash": content_hash(record, management),
    }
    return {**snapshot, "snapshotHash": digest(snapshot)}


# 保存した申請スナップショットのうち、ハッシュの対象でない項目（キー、ハッシュ自身、読み取り時に付ける無効化の有無）
_SNAPSHOT_UNHASHED_KEYS = frozenset({"pk", "sk", "snapshotHash", "invalidated"})


def _from_store(value: Any) -> Any:
    """補助ストアの数値（Decimal）を、申請時と同じ JSON の数値に戻す。"""
    if isinstance(value, Decimal):
        return int(value) if value == value.to_integral_value() else float(value)
    if isinstance(value, Mapping):
        return {key: _from_store(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_from_store(item) for item in value]
    return value


def snapshot_fields(stored: Mapping[str, Any]) -> dict[str, Any]:
    """保存した申請スナップショットから、ハッシュの対象の項目だけを取り出す。"""
    return {
        key: _from_store(value)
        for key, value in stored.items()
        if key not in _SNAPSHOT_UNHASHED_KEYS
    }


def recompute_snapshot_hash(stored: Mapping[str, Any]) -> str:
    """保存した申請スナップショットの内容から、申請時と同じ方法でハッシュを計算し直す。

    保存後にスナップショットの項目（連絡先や関連参照など）が書き換えられていれば、
    申請時に記録した snapshotHash と一致しなくなる（設計書「内容を確認してから決裁する」）。
    """
    return digest(snapshot_fields(stored))

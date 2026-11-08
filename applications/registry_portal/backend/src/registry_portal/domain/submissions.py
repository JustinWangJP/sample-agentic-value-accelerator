"""申請入力、semver の順序、変更できない申請スナップショット。"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from registry_portal.domain.descriptors import SUPPLEMENT_FIELDS
from registry_portal.domain.drafts import (
    RECORD_ID_PATTERN,
    SEMVER,
    Management,
    PrincipalRecord,
    RecordDraft,
    content_hash,
    digest,
)
from registry_portal.domain.secrets import scan_json, scan_text

# 申請入力の上限。SPA の申請フォーム（frontend/src/drafts/api.ts）と揃える
REASON_MAX_LENGTH = 4096
RELATED_REFERENCES_MAX = 20
# 関連参照の recordVersion の上限。下書きの recordVersion（TEXT_LIMITS）と同じ
REFERENCE_VERSION_MAX_LENGTH = 255

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
            if not isinstance(version, str) or len(version) > REFERENCE_VERSION_MAX_LENGTH:
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

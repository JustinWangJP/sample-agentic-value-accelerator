"""申請入力、semver の順序、変更できない申請スナップショット。"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from registry_portal.domain.secrets import scan_json, scan_text

from registry_portal.domain.drafts import (
    SEMVER,
    Management,
    PrincipalRecord,
    RecordDraft,
    content_hash,
    digest,
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
        if not isinstance(reason, str) or not 1 <= len(reason.strip()) <= 4096:
            raise ValueError("申請理由は1〜4096文字で指定してください。")
        if scan_text("reason", reason):
            raise ValueError(
                "申請理由に秘密情報らしき値が含まれています。保管先への参照を記載してください。"
            )
        if not isinstance(references, list) or len(references) > 20:
            raise ValueError("関連参照は20件以内のリストで指定してください。")
        normalized = []
        for reference in references:
            if not isinstance(reference, dict) or set(reference) != {"recordId", "recordVersion"}:
                raise ValueError("関連参照には recordId と recordVersion を指定してください。")
            record_id, version = reference["recordId"], reference["recordVersion"]
            if not isinstance(record_id, str) or not re.fullmatch(r"[A-Za-z0-9]{12}", record_id):
                raise ValueError("関連参照の recordId の形式が正しくありません。")
            if not isinstance(version, str) or len(version) > 255:
                raise ValueError("関連参照の recordVersion の形式が正しくありません。")
            version_key(version)
            if reference in normalized:
                raise ValueError("同じ関連参照を重複して指定できません。")
            normalized.append(dict(reference))
        return cls(expected, reason.strip(), normalized)


def submission_snapshot(
    submission_id: str,
    record: Mapping[str, Any],
    management: Management,
    principal: PrincipalRecord,
    request: SubmissionInput,
    draft: RecordDraft,
    submitted_at: str,
) -> dict[str, Any]:
    actual = {
        k: record.get(k)
        for k in (
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
    }
    if scan_json("record", actual):
        raise ValueError(
            "AWS の定義や metadata に秘密情報らしき値が含まれています。管理者に連絡してください。"
        )
    if isinstance(actual["updatedAt"], datetime):
        actual["updatedAt"] = actual["updatedAt"].isoformat()
    supplement = {**management.hashed_fields(), **draft.supplement()}
    snapshot = {
        "submissionId": submission_id,
        "submittedBy": principal,
        "lastEditedBy": management.item["lastEditedBy"],
        "submittedAt": submitted_at,
        "reason": request.reason,
        "record": actual,
        "ownerTeam": management.owner_team,
        "supplement": supplement,
        "relatedReferences": request.references,
        **{
            key: supplement.get(key, "") for key in ("contact", "termsOfUse", "connectionReference")
        },
        "contentHash": content_hash(record, management),
    }
    return {**snapshot, "snapshotHash": digest(snapshot)}

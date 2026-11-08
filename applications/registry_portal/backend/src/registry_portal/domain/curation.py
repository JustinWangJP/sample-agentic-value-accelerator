"""決裁入力、決裁の規則、公開版スナップショットの書き出し。"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from registry_portal.domain.drafts import (
    AWS_FAILED_STATUSES,
    IN_FLIGHT_STATUSES,
    PENDING_APPROVAL_STATUS,
    REJECTED_STATUS,
    PrincipalRecord,
)
from registry_portal.domain.secrets import scan_text

# 決裁理由の上限。AWS の UpdateRegistryRecordStatus の statusReason が 0〜255 文字のため、
# ポータルも同じ上限にして、記録した理由をそのまま AWS に送れるようにする
REASON_MAX_LENGTH = 255
# 公開版スナップショット（PUBLISHED#版）に写す項目。カタログ詳細が読み取る契約（ADR-0007）
PUBLISHED_FIELDS = ("contact", "termsOfUse", "connectionReference")
HASH_PATTERN = re.compile(r"[a-f0-9]{64}")


@dataclass(frozen=True)
class DecisionInput:
    """Curator が送る決裁の入力。AWS の status は本文から受け取らない。"""

    snapshot_hash: str
    reason: str

    @classmethod
    def parse(cls, body: Mapping[str, Any], *, reason_required: bool) -> DecisionInput:
        if set(body) != {"snapshotHash", "reason"}:
            raise ValueError(
                "snapshotHash と reason を指定してください。"
                "ステータスは API ごとに決まるため指定できません。"
            )
        snapshot_hash, reason = body["snapshotHash"], body["reason"]
        if not isinstance(snapshot_hash, str) or not HASH_PATTERN.fullmatch(snapshot_hash):
            raise ValueError("審査したスナップショットのハッシュを指定してください。")
        if not isinstance(reason, str):
            raise ValueError(cls._reason_message(reason_required))
        stripped = reason.strip()
        if reason_required and not stripped:
            raise ValueError("却下理由は必須です。Publisher が修正できるように記載してください。")
        if len(stripped) > REASON_MAX_LENGTH:
            raise ValueError(
                f"決裁理由は{REASON_MAX_LENGTH}文字以内で指定してください。"
                "細かい内容は申請理由や別の記録に残してください。"
            )
        if scan_text("reason", stripped):
            raise ValueError(
                "決裁理由に秘密情報らしき値が含まれています。保管先への参照を記載してください。"
            )
        return cls(snapshot_hash, stripped)

    @staticmethod
    def _reason_message(reason_required: bool) -> str:
        return "却下理由は必須です。" if reason_required else "決裁理由を指定してください。"


class SelfApprovalError(Exception):
    """申請者または最終編集者自身が決裁しようとした。"""


def check_decidable(status: str) -> None:
    """決裁できる状態か検査する。AWS の承認待ちだけが決裁対象（ADR-0002）。"""
    if status == PENDING_APPROVAL_STATUS:
        return
    if status in IN_FLIGHT_STATUSES or status in AWS_FAILED_STATUSES:
        raise ValueError("AWS が処理中のため決裁できません。状態が確定してから確認してください。")
    if status == REJECTED_STATUS:
        raise ValueError(
            "却下済みのレコードは決裁できません。Publisher が編集して再申請してください。"
        )
    raise ValueError("審査待ちの申請ではないため決裁できません。一覧から確認してください。")


def check_not_self_approval(
    decider: PrincipalRecord, submitted_by: Any, last_edited_by: Any
) -> None:
    """申請者と最終編集者は決裁できない。Admin であっても同じ（設計書「ロールと所有」）。"""
    if decider in (submitted_by, last_edited_by):
        raise SelfApprovalError()


def published_item(submission: Mapping[str, Any]) -> dict[str, Any]:
    """申請のスナップショットから、公開版スナップショット（PUBLISHED#版）に写す項目。

    Curator が確認した内容と一致させるため、写しの値はすべてスナップショットから取る。
    関連参照は Agent 以外では空のリストになる（申請時点で検査済み）。
    """
    return {
        **{field: submission[field] for field in PUBLISHED_FIELDS},
        "relatedReferences": [dict(reference) for reference in submission["relatedReferences"]],
    }

"""決裁入力、決裁の規則、公開版スナップショットの書き出しと読み取り。"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Literal

from registry_portal.domain.drafts import (
    APPROVE_KIND,
    AWS_FAILED_STATUSES,
    IN_FLIGHT_STATUSES,
    PENDING_APPROVAL_STATUS,
    PUBLISHED_STATUS,
    REJECT_KIND,
    REJECTED_STATUS,
    OperationKind,
    PrincipalRecord,
    RecordStatus,
)
from registry_portal.domain.secrets import scan_text

# 決裁理由の上限。AWS の UpdateRegistryRecordStatus の statusReason が 0〜255 文字のため、
# ポータルも同じ上限にして、記録した理由をそのまま AWS に送れるようにする
REASON_MAX_LENGTH = 255
# 決裁の種別ごとの AWS の遷移先。クライアントから status は受け取らず、API の種別だけで決める
DECISION_TARGETS: Mapping[OperationKind, RecordStatus] = {
    APPROVE_KIND: PUBLISHED_STATUS,
    REJECT_KIND: REJECTED_STATUS,
}
# 公開版スナップショット（PUBLISHED#版）に写す項目。カタログ詳細が読み取る契約（ADR-0007）
PUBLISHED_FIELDS = ("contact", "termsOfUse", "connectionReference")
HASH_PATTERN = re.compile(r"[a-f0-9]{64}")

# 審査対象の申請スナップショット・公開版スナップショットの取得状態。
# NONE はそもそも存在しない（ポータル外の申請、初回公開）、MISSING は存在するはずなのに読めない（保存内容の異常）。
# SPA の `ATTACHMENT_STATES`（frontend/src/reviews/api.ts）と揃える
AttachmentState = Literal["NONE", "AVAILABLE", "MISSING"]
ATTACHMENT_NONE: AttachmentState = "NONE"
ATTACHMENT_AVAILABLE: AttachmentState = "AVAILABLE"
ATTACHMENT_MISSING: AttachmentState = "MISSING"


@dataclass(frozen=True)
class DecisionInput:
    """Curator が送る決裁の入力。AWS の status は本文から受け取らない。"""

    snapshot_hash: str
    reason: str

    @classmethod
    def parse(cls, body: Mapping[str, Any], kind: OperationKind) -> DecisionInput:
        """決裁の本文を検査する。理由は公開承認・却下の両方で必須（設計書「API」）。"""
        if set(body) != {"snapshotHash", "reason"}:
            raise ValueError(
                "snapshotHash と reason を指定してください。"
                "ステータスは API ごとに決まるため指定できません。"
            )
        snapshot_hash, reason = body["snapshotHash"], body["reason"]
        if not isinstance(snapshot_hash, str) or not HASH_PATTERN.fullmatch(snapshot_hash):
            raise ValueError("審査したスナップショットのハッシュを指定してください。")
        stripped = reason.strip() if isinstance(reason, str) else ""
        if not stripped:
            raise ValueError(_REASON_REQUIRED[kind])
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


_REASON_REQUIRED: Mapping[OperationKind, str] = {
    APPROVE_KIND: "公開承認の理由は必須です。判断の根拠を記載してください。",
    REJECT_KIND: "却下理由は必須です。Publisher が修正できるように記載してください。",
}


class SelfApprovalError(Exception):
    """申請者または最終編集者自身が決裁しようとした。"""


def check_decidable(status: str) -> None:
    """決裁できる状態か検査する。AWS の承認待ちだけが決裁対象（ADR-0002）。"""
    if status == PENDING_APPROVAL_STATUS:
        return
    if status in IN_FLIGHT_STATUSES:
        raise ValueError("AWS が処理中のため決裁できません。状態が確定してから確認してください。")
    if status in AWS_FAILED_STATUSES:
        raise ValueError(
            "AWS での作成・更新が失敗した状態のため決裁できません。Publisher に修正を依頼してください。"
        )
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


def published_item(
    submission: Mapping[str, Any], approved_by: PrincipalRecord, approved_at: str
) -> dict[str, Any]:
    """申請のスナップショットから、公開版スナップショット（PUBLISHED#版）に写す項目。

    Curator が確認した内容と一致させるため、写しの値はすべてスナップショットから取る。
    関連参照は Agent 以外では空のリストになる（申請時点で検査済み）。
    定義と metadata（record）と公開承認した Curator は、次回審査の差分表示に使う
    （設計書「補助テーブルに保存するもの」の公開内容のスナップショット）。
    """
    return {
        **{field: submission[field] for field in PUBLISHED_FIELDS},
        "relatedReferences": [dict(reference) for reference in submission["relatedReferences"]],
        "record": dict(submission["record"]),
        "approvedBy": dict(approved_by),
        "approvedAt": approved_at,
    }


def published_base(stored: Mapping[str, Any], record_version: str) -> dict[str, Any]:
    """保存した公開版スナップショットを、審査の差分の比較対象にする。

    定義と承認者を記録する前に作られたスナップショットでは、record と approvedBy は None
    （画面では「未取得」「不明」と表示する）。
    """
    return {
        "recordVersion": record_version,
        **{field: stored.get(field) for field in PUBLISHED_FIELDS},
        "relatedReferences": stored.get("relatedReferences", []),
        "record": stored.get("record"),
        "approvedBy": stored.get("approvedBy"),
    }

"""決裁入力、決裁の規則、公開版スナップショットの書き出しと読み取り。"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Literal

from registry_portal.domain.drafts import (
    APPROVE_KIND,
    AWS_FAILED_STATUSES,
    DEPRECATE_KIND,
    DEPRECATED_STATUS,
    IN_FLIGHT_STATUSES,
    PENDING_APPROVAL_STATUS,
    PUBLISHED_STATUS,
    REJECT_KIND,
    REJECTED_STATUS,
    SUSPEND_KIND,
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
    # 公開停止は「却下による非公開化」。再公開には Publisher の編集と再申請が必要（設計書「業務上の状態」）
    SUSPEND_KIND: REJECTED_STATUS,
    # 廃止は元に戻せない DEPRECATED
    DEPRECATE_KIND: DEPRECATED_STATUS,
}
# 公開停止・廃止の操作。申請のスナップショットではなく、公開中の現物を対象にする
LIFECYCLE_KINDS: frozenset[OperationKind] = frozenset({SUSPEND_KIND, DEPRECATE_KIND})
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
    SUSPEND_KIND: "公開停止の理由は必須です。問題の内容を記載してください。",
    DEPRECATE_KIND: "廃止理由は必須です。判断の根拠を記載してください。",
}


@dataclass(frozen=True)
class LifecycleInput:
    """Curator が送る公開停止・廃止の入力。AWS の status は本文から受け取らない。

    廃止は元に戻せないため、確認（confirm: true）を必須にする（設計書「業務上の状態」）。
    """

    reason: str

    @classmethod
    def parse(cls, body: Mapping[str, Any], kind: OperationKind) -> LifecycleInput:
        keys = {"reason"} if kind == SUSPEND_KIND else {"reason", "confirm"}
        if set(body) != keys:
            if kind == DEPRECATE_KIND:
                raise ValueError(
                    "reason と confirm を指定してください。"
                    "ステータスは API ごとに決まるため指定できません。"
                )
            raise ValueError(
                "reason を指定してください。ステータスは API ごとに決まるため指定できません。"
            )
        reason = body["reason"]
        stripped = reason.strip() if isinstance(reason, str) else ""
        if not stripped:
            raise ValueError(_REASON_REQUIRED[kind])
        if len(stripped) > REASON_MAX_LENGTH:
            raise ValueError(
                f"理由は{REASON_MAX_LENGTH}文字以内で指定してください。"
                "細かい内容は別の記録に残してください。"
            )
        if scan_text("reason", stripped):
            raise ValueError(
                "理由に秘密情報らしき値が含まれています。保管先への参照を記載してください。"
            )
        if kind == DEPRECATE_KIND and body["confirm"] is not True:
            raise ValueError(
                "廃止は元に戻せません。確認した場合だけ confirm に true を指定してください。"
            )
        return cls(stripped)


def check_suspendable(status: str) -> None:
    """公開停止できる状態か検査する。公開中（APPROVED）だけが対象（設計書「業務上の状態」）。"""
    if status == PUBLISHED_STATUS:
        return
    if status in IN_FLIGHT_STATUSES:
        raise ValueError(
            "AWS が処理中のため公開停止できません。状態が確定してから確認してください。"
        )
    if status == PENDING_APPROVAL_STATUS:
        raise ValueError(
            "審査待ちの申請は却下してください。公開停止は公開中のレコードに対して行います。"
        )
    if status == REJECTED_STATUS:
        raise ValueError("このレコードはすでに非公開です。Publisher が編集して再申請できます。")
    if status == DEPRECATED_STATUS:
        raise ValueError("廃止済みのレコードは公開停止できません。")
    raise ValueError("公開中のレコードではないため公開停止できません。一覧から確認してください。")


def check_deprecatable(status: str) -> None:
    """廃止できる状態か検査する。公開中と、公開停止済み（再公開前）が対象。"""
    if status in (PUBLISHED_STATUS, REJECTED_STATUS):
        return
    if status in IN_FLIGHT_STATUSES:
        raise ValueError("AWS が処理中のため廃止できません。状態が確定してから確認してください。")
    if status == DEPRECATED_STATUS:
        raise ValueError("このレコードはすでに廃止されています。")
    raise ValueError(
        "公開中または公開停止済みのレコードではないため廃止できません。一覧から確認してください。"
    )


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

"""決裁入力、決裁の規則、公開版スナップショットの書き出しと読み取り。"""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any, Literal, get_args

from registry_portal.domain.drafts import (
    APPROVE_KIND,
    AWS_FAILED_STATUSES,
    DEPRECATE_KIND,
    DEPRECATED_STATUS,
    DRAFT_STATUS,
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
    # 公開停止は却下で発見できなくする。再公開には Publisher の編集と再申請が必要（設計書「業務上の状態」）
    SUSPEND_KIND: REJECTED_STATUS,
    # 廃止は元に戻せない DEPRECATED
    DEPRECATE_KIND: DEPRECATED_STATUS,
}
# 公開停止・廃止の操作。申請のスナップショットではなく、公開中の現物を対象にする。
# キーは API の path（/api/records/{recordId}/suspend・/deprecate）と公開管理の照会の actions のキー。
# SPA の `LIFECYCLE_ACTIONS`（frontend/src/lifecycle/api.ts）と揃える
LIFECYCLE_ACTIONS: Mapping[str, OperationKind] = {
    "suspend": SUSPEND_KIND,
    "deprecate": DEPRECATE_KIND,
}
LIFECYCLE_KINDS: frozenset[OperationKind] = frozenset(LIFECYCLE_ACTIONS.values())

# 審査待ちの一覧の項目の種別。申請（AWS の承認待ち）と廃止依頼（ポータル内で発生）を並べる。
# SPA の `REVIEW_KINDS`（frontend/src/reviews/api.ts）と揃える
ReviewKind = Literal["SUBMISSION", "DEPRECATION_REQUEST"]
REVIEW_KINDS: tuple[ReviewKind, ...] = get_args(ReviewKind)
REVIEW_SUBMISSION: ReviewKind = "SUBMISSION"
REVIEW_DEPRECATION_REQUEST: ReviewKind = "DEPRECATION_REQUEST"
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
        snapshot_hash = body["snapshotHash"]
        if not isinstance(snapshot_hash, str) or not HASH_PATTERN.fullmatch(snapshot_hash):
            raise ValueError("審査したスナップショットのハッシュを指定してください。")
        return cls(snapshot_hash, parse_reason(body["reason"], required=_REASON_REQUIRED[kind]))


def parse_reason(
    value: object,
    *,
    required: str,
    label: str = "理由",
    max_length: int = REASON_MAX_LENGTH,
) -> str:
    """Curator・Publisher が入力する理由を検査し、前後の空白を除いて返す。

    決裁・公開停止・廃止・廃止依頼・依頼の見送りで共通。空、上限超え、秘密情報らしき値は ValueError。
    """
    stripped = value.strip() if isinstance(value, str) else ""
    if not stripped:
        raise ValueError(required)
    if len(stripped) > max_length:
        raise ValueError(
            f"{label}は{max_length}文字以内で指定してください。細かい内容は別の記録に残してください。"
        )
    if scan_text("reason", stripped):
        raise ValueError(
            f"{label}に秘密情報らしき値が含まれています。保管先への参照を記載してください。"
        )
    return stripped


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
        reason = parse_reason(body["reason"], required=_REASON_REQUIRED[kind])
        if kind == DEPRECATE_KIND and body["confirm"] is not True:
            raise ValueError(
                "廃止は元に戻せません。確認した場合だけ confirm に true を指定してください。"
            )
        return cls(reason)


@dataclass(frozen=True)
class RecordState:
    """決裁の前提を検査するときの、Registry レコードの現在の状態。

    status は AWS の状態（管理 API が返す最新版）、published_version は発見できる版（なければ None）。
    """

    status: str
    published_version: str | None


# 公開中の版を残したまま新しい版が下書き・審査待ち・却下済みになっている状態（二重リビジョン）。
# この状態で却下したときに旧版が発見されなくなるかは AWS の文書になく、実環境でも未確認のため、
# 公開停止を受け付けない（README「公開停止・廃止・廃止依頼」）
DUAL_REVISION_STATUSES: frozenset[str] = frozenset(
    {DRAFT_STATUS, PENDING_APPROVAL_STATUS, REJECTED_STATUS}
)


def check_suspendable(state: RecordState) -> None:
    """公開停止できる状態か検査する。最新版が公開中（APPROVED）のときだけが対象（設計書「業務上の状態」）。"""
    status = state.status
    if status == PUBLISHED_STATUS:
        return
    if status in IN_FLIGHT_STATUSES:
        raise ValueError(
            "AWS が処理中のため公開停止できません。状態が確定してから確認してください。"
        )
    if status == DEPRECATED_STATUS:
        raise ValueError("廃止済みのレコードは公開停止できません。")
    if status in DUAL_REVISION_STATUSES and state.published_version is not None:
        raise ValueError(
            f"公開中の版 {state.published_version} に加えて新しい版があるため、公開停止できません。"
            "この状態で公開を止められるかは確認できていません。審査待ちなら先に申請を却下し、"
            "カタログに表示されなくなったかを確認してください。表示が続く場合は管理者に連絡してください。"
        )
    if status == PENDING_APPROVAL_STATUS:
        raise ValueError(
            "公開中の版がない審査待ちの申請です。公開停止ではなく、審査で却下してください。"
        )
    if status == REJECTED_STATUS:
        raise ValueError(
            "公開中の版がありません（公開停止中、または却下済み）。公開停止は不要です。"
        )
    raise ValueError("公開中の版がないため公開停止できません。状態を確認してください。")


def check_deprecatable(state: RecordState) -> None:
    """廃止できる状態か検査する。AWS ではどの状態からも廃止できるため、処理中と廃止済みだけを拒否する。"""
    if state.status in IN_FLIGHT_STATUSES:
        raise ValueError("AWS が処理中のため廃止できません。状態が確定してから確認してください。")
    if state.status == DEPRECATED_STATUS:
        raise ValueError("このレコードはすでに廃止されています。")


class SelfApprovalError(Exception):
    """申請者または最終編集者自身が決裁しようとした。"""


def check_decidable(state: RecordState) -> None:
    """公開承認・却下できる状態か検査する。AWS の承認待ちだけが決裁対象（ADR-0002）。"""
    status = state.status
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


# 決裁の種別ごとの前提の検査。満たさなければ ValueError（利用者に見せる理由）
DECISION_PRECONDITIONS: Mapping[OperationKind, Callable[[RecordState], None]] = {
    APPROVE_KIND: check_decidable,
    REJECT_KIND: check_decidable,
    SUSPEND_KIND: check_suspendable,
    DEPRECATE_KIND: check_deprecatable,
}


def precondition_failure(kind: OperationKind, state: RecordState) -> str | None:
    """決裁の前提を満たさない理由。満たしていれば None。公開管理の画面に実行できない理由を示すために使う。"""
    try:
        DECISION_PRECONDITIONS[kind](state)
    except ValueError as error:
        return str(error)
    return None


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

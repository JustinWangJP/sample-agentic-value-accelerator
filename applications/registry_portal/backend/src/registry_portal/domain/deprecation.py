"""廃止依頼の値と規則（README「公開停止・廃止・廃止依頼」）。

廃止依頼は AWS の状態を変えず、補助テーブルにレコードごとに1件だけ保存する。
依頼の終わり方は2つだけで、廃止（Curator の廃止で解決）か見送り（Curator が理由を付けて閉じる）。
公開停止は依頼を閉じない。廃止は元に戻せないため、まず公開停止で止め、影響を確かめてから廃止する運用を想定する。
"""

from __future__ import annotations

import dataclasses
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Literal, cast, get_args

from registry_portal.domain.curation import parse_reason
from registry_portal.domain.drafts import PrincipalRecord, is_principal

# 依頼理由・見送り理由の上限。AWS には送らずポータルの記録にだけ残す。
# SPA の `DEPRECATION_REASON_MAX_LENGTH`（frontend/src/drafts/api.ts）と揃える
DEPRECATION_REASON_MAX_LENGTH = 255

# 廃止依頼の状態。SPA の `DEPRECATION_REQUEST_STATES`（frontend/src/drafts/api.ts）と揃える
DeprecationRequestState = Literal["OPEN", "RESOLVED"]
DEPRECATION_REQUEST_STATES: tuple[DeprecationRequestState, ...] = get_args(DeprecationRequestState)
DEPRECATION_OPEN: DeprecationRequestState = "OPEN"
DEPRECATION_RESOLVED: DeprecationRequestState = "RESOLVED"

# 解決済みの依頼の終わり方。SPA の `DEPRECATION_RESOLUTIONS`（frontend/src/drafts/api.ts）と揃える
DeprecationResolution = Literal["DEPRECATED", "DECLINED"]
DEPRECATION_RESOLUTIONS: tuple[DeprecationResolution, ...] = get_args(DeprecationResolution)
RESOLUTION_DEPRECATED: DeprecationResolution = "DEPRECATED"
RESOLUTION_DECLINED: DeprecationResolution = "DECLINED"

_STORED_INVALID = "保存された廃止依頼の形式が正しくありません。"


def parse_request_reason(value: object) -> str:
    """Publisher の依頼理由を検査する。"""
    return parse_reason(
        value,
        required="廃止依頼の理由は必須です。終了の背景を記載してください。",
        label="廃止依頼の理由",
        max_length=DEPRECATION_REASON_MAX_LENGTH,
    )


def parse_decline_reason(value: object) -> str:
    """Curator の見送り理由を検査する。Publisher が依頼を見直せるように必須にする。"""
    return parse_reason(
        value,
        required="見送りの理由は必須です。Publisher が判断を理解できるように記載してください。",
        label="見送りの理由",
        max_length=DEPRECATION_REASON_MAX_LENGTH,
    )


@dataclass(frozen=True)
class DeprecationRequest:
    """補助テーブルに保存する廃止依頼1件。依頼の正本（主体・理由・日時）は解決後も書き換えない。"""

    request_id: str
    record_id: str
    reason: str
    requested_by: PrincipalRecord
    requested_at: str
    state: DeprecationRequestState = DEPRECATION_OPEN
    resolution: DeprecationResolution | None = None
    # 見送りでは見送りの理由、廃止では廃止の理由（Curator が入力したもの）
    resolution_reason: str | None = None
    resolved_by: PrincipalRecord | None = None
    resolved_at: str | None = None

    @property
    def is_open(self) -> bool:
        return self.state == DEPRECATION_OPEN

    @classmethod
    def parse(cls, item: Mapping[str, Any]) -> DeprecationRequest:
        """保存された廃止依頼を検査する。型が契約と異なれば ValueError。"""
        state = item.get("state")
        resolution = item.get("resolution")
        resolved_by = item.get("resolvedBy")
        if (
            any(
                not isinstance(item.get(key), str)
                for key in ("requestId", "recordId", "reason", "requestedAt")
            )
            or not is_principal(item.get("requestedBy"))
            or state not in DEPRECATION_REQUEST_STATES
            or (resolution is not None and resolution not in DEPRECATION_RESOLUTIONS)
            or (resolved_by is not None and not is_principal(resolved_by))
            or (state == DEPRECATION_RESOLVED and resolution is None)
            or any(
                item.get(key) is not None and not isinstance(item[key], str)
                for key in ("resolutionReason", "resolvedAt")
            )
        ):
            raise ValueError(_STORED_INVALID)
        return cls(
            request_id=str(item["requestId"]),
            record_id=str(item["recordId"]),
            reason=str(item["reason"]),
            requested_by=cast(PrincipalRecord, dict(item["requestedBy"])),
            requested_at=str(item["requestedAt"]),
            state=cast(DeprecationRequestState, state),
            resolution=cast("DeprecationResolution | None", resolution),
            resolution_reason=item.get("resolutionReason"),
            resolved_by=cast("PrincipalRecord | None", dict(resolved_by) if resolved_by else None),
            resolved_at=item.get("resolvedAt"),
        )

    def resolve(
        self,
        resolution: DeprecationResolution,
        reason: str,
        by: PrincipalRecord,
        at: str,
    ) -> DeprecationRequest:
        """解決済みにした依頼。依頼の正本はそのまま残し、解決の記録だけを加える。"""
        return dataclasses.replace(
            self,
            state=DEPRECATION_RESOLVED,
            resolution=resolution,
            resolution_reason=reason,
            resolved_by=by,
            resolved_at=at,
        )

    def to_item(self) -> dict[str, Any]:
        """補助テーブルに保存する項目（キーを除く）。未解決の依頼は解決の項目を持たない。"""
        item: dict[str, Any] = {
            "requestId": self.request_id,
            "recordId": self.record_id,
            "reason": self.reason,
            "requestedBy": dict(self.requested_by),
            "requestedAt": self.requested_at,
            "state": self.state,
        }
        if not self.is_open:
            item.update(
                {
                    "resolution": self.resolution,
                    "resolutionReason": self.resolution_reason,
                    "resolvedBy": dict(self.resolved_by) if self.resolved_by else None,
                    "resolvedAt": self.resolved_at,
                }
            )
        return item

    def view(self) -> dict[str, Any]:
        """API が返す依頼。SPA の isDeprecationRequest（frontend/src/drafts/api.ts）と揃える。"""
        return {
            "requestId": self.request_id,
            "recordId": self.record_id,
            "reason": self.reason,
            "requestedBy": dict(self.requested_by),
            "requestedAt": self.requested_at,
            "state": self.state,
            "resolution": self.resolution,
            "resolutionReason": self.resolution_reason,
            "resolvedBy": dict(self.resolved_by) if self.resolved_by else None,
            "resolvedAt": self.resolved_at,
        }

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


# 未解決の依頼が API で返す解決の項目。保存する項目には含めない
_UNRESOLVED_FIELDS: Mapping[str, None] = {
    "resolution": None,
    "resolutionReason": None,
    "resolvedBy": None,
    "resolvedAt": None,
}


@dataclass(frozen=True)
class RequestResolution:
    """廃止依頼の解決の記録。終わり方・理由・主体・日時は常にそろって記録する。"""

    resolution: DeprecationResolution
    # 見送りでは見送りの理由、廃止では廃止の理由（Curator が入力したもの）
    reason: str
    resolved_by: PrincipalRecord
    resolved_at: str

    @classmethod
    def parse(cls, item: Mapping[str, Any]) -> RequestResolution:
        """保存された解決の項目を検査する。そろっていなければ ValueError。"""
        resolution = item.get("resolution")
        resolved_by = item.get("resolvedBy")
        if (
            resolution not in DEPRECATION_RESOLUTIONS
            or not is_principal(resolved_by)
            or any(not isinstance(item.get(key), str) for key in ("resolutionReason", "resolvedAt"))
        ):
            raise ValueError(_STORED_INVALID)
        return cls(
            resolution=cast(DeprecationResolution, resolution),
            reason=str(item["resolutionReason"]),
            resolved_by=cast(PrincipalRecord, dict(resolved_by)),
            resolved_at=str(item["resolvedAt"]),
        )

    def fields(self) -> dict[str, Any]:
        """保存と API で共通の解決の項目。"""
        return {
            "resolution": self.resolution,
            "resolutionReason": self.reason,
            "resolvedBy": dict(self.resolved_by),
            "resolvedAt": self.resolved_at,
        }


@dataclass(frozen=True)
class DeprecationRequest:
    """補助テーブルに保存する廃止依頼1件。依頼の正本（主体・理由・日時）は解決後も書き換えない。"""

    request_id: str
    record_id: str
    reason: str
    requested_by: PrincipalRecord
    requested_at: str
    # 解決の記録。未解決なら None
    resolved: RequestResolution | None = None

    @property
    def is_open(self) -> bool:
        return self.resolved is None

    @property
    def state(self) -> DeprecationRequestState:
        return DEPRECATION_OPEN if self.is_open else DEPRECATION_RESOLVED

    @classmethod
    def parse(cls, item: Mapping[str, Any]) -> DeprecationRequest:
        """保存された廃止依頼を検査する。型が契約と異なれば ValueError。"""
        state = item.get("state")
        if (
            any(
                not isinstance(item.get(key), str)
                for key in ("requestId", "recordId", "reason", "requestedAt")
            )
            or not is_principal(item.get("requestedBy"))
            or state not in DEPRECATION_REQUEST_STATES
        ):
            raise ValueError(_STORED_INVALID)
        return cls(
            request_id=str(item["requestId"]),
            record_id=str(item["recordId"]),
            reason=str(item["reason"]),
            requested_by=cast(PrincipalRecord, dict(item["requestedBy"])),
            requested_at=str(item["requestedAt"]),
            resolved=RequestResolution.parse(item) if state == DEPRECATION_RESOLVED else None,
        )

    def resolve(
        self,
        resolution: DeprecationResolution,
        reason: str,
        by: PrincipalRecord,
        at: str,
    ) -> DeprecationRequest:
        """解決済みにした依頼。依頼の正本はそのまま残し、解決の記録だけを加える。"""
        return dataclasses.replace(self, resolved=RequestResolution(resolution, reason, by, at))

    def _request_fields(self) -> dict[str, Any]:
        return {
            "requestId": self.request_id,
            "recordId": self.record_id,
            "reason": self.reason,
            "requestedBy": dict(self.requested_by),
            "requestedAt": self.requested_at,
            "state": self.state,
        }

    def to_item(self) -> dict[str, Any]:
        """補助テーブルに保存する項目（キーを除く）。未解決の依頼は解決の項目を持たない。"""
        return {**self._request_fields(), **(self.resolved.fields() if self.resolved else {})}

    def view(self) -> dict[str, Any]:
        """API が返す依頼。SPA の isDeprecationRequest（frontend/src/drafts/api.ts）と揃える。"""
        return {
            **self._request_fields(),
            **(self.resolved.fields() if self.resolved else _UNRESOLVED_FIELDS),
        }

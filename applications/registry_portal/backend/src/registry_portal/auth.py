"""認可: 検証済みの JWT claims から主体・業務ロール・所属チームを導出する。"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from registry_portal.config import AuthSettings
from registry_portal.errors import AuthenticationError, ForbiddenError


@dataclass(frozen=True)
class Principal:
    """人間の主体。`iss` と `sub` の組で一意に識別する。"""

    issuer: str
    subject: str


@dataclass(frozen=True)
class Caller:
    """認可済みの呼び出し元。"""

    principal: Principal
    roles: tuple[str, ...]
    teams: tuple[str, ...]


# 業務ロールと Cognito グループの対応。Viewer はログインした全員で、グループを持たない。
VIEWER = "Viewer"
_ROLE_GROUPS: tuple[tuple[str, str], ...] = (
    ("Publisher", "registry-publisher"),
    ("Curator", "registry-curator"),
    ("Admin", "registry-admin"),
)
TEAM_GROUP_PREFIX = "team-"


def _parse_groups(value: Any) -> set[str]:
    """`cognito:groups` を集合にする。

    HTTP API の JWT authorizer は配列の claim を "[a b]" 形式の文字列で渡す。
    直接配列で渡される場合も受け付ける。
    """
    if value is None:
        return set()
    if isinstance(value, (list, tuple)):
        return {str(item) for item in value}
    text = str(value).strip()
    if text.startswith("[") and text.endswith("]"):
        text = text[1:-1]
    return {item for item in text.replace(",", " ").split() if item}


def authorize(claims: Mapping[str, Any] | None, settings: AuthSettings) -> Caller:
    """検証済みの claims を検査し、呼び出し元を返す。

    HTTP API の JWT authorizer が署名・有効期限・audience を検証した後でも、
    Lambda 側で次を再確認する（多層防御）。

    - アクセストークンであること（`token_use=access`。ID トークンは拒否する）
    - 想定した issuer であること
    - 人間用 SPA client に発行されたトークンであること（発見用 M2M などは拒否する）
    - 主体（`sub`）があること

    メールアドレスやリクエストヘッダー（`x-user-email` など）は判定に使わない。

    人間の主体として認証できないトークンは 401（AuthenticationError）、
    正規だが人間用 SPA client 以外に発行されたトークンは 403（ForbiddenError）で拒否する。

    業務ロールはグループだけから導出する（表示用）。Publisher の書き込み操作で
    所属チーム（`team-*`）を必須とする検査は、各操作の認可で行う（設計書「ロールと所有」）。
    """
    if not claims:
        raise AuthenticationError()
    if claims.get("token_use") != "access":
        raise AuthenticationError("アクセストークンを指定してください。")
    if claims.get("iss") != settings.issuer:
        raise AuthenticationError("このトークンの発行元は受け付けません。")
    if claims.get("client_id") != settings.human_client_id:
        # 正規に発行されたトークンなので、ログインし直しても解決しない。401 ではなく 403 とする
        raise ForbiddenError("このクライアントのトークンはポータル API では受け付けません。")
    subject = claims.get("sub")
    if not isinstance(subject, str) or not subject:
        raise AuthenticationError("トークンに主体（sub）が含まれていません。")

    groups = _parse_groups(claims.get("cognito:groups"))
    roles = (VIEWER,) + tuple(role for role, group in _ROLE_GROUPS if group in groups)
    teams = tuple(
        sorted(
            g for g in groups if g.startswith(TEAM_GROUP_PREFIX) and len(g) > len(TEAM_GROUP_PREFIX)
        )
    )
    return Caller(
        principal=Principal(issuer=settings.issuer, subject=subject),
        roles=roles,
        teams=teams,
    )

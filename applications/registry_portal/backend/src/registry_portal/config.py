"""Registry ポータルの実行時設定。"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass


class ConfigurationError(RuntimeError):
    """必須の設定が不足している。Lambda の初期化時に送出し、起動を止める。"""


@dataclass(frozen=True)
class AuthSettings:
    """認証・認可に使う設定。"""

    # Cognito User Pool の issuer（例: https://cognito-idp.us-east-1.amazonaws.com/<pool-id>）
    issuer: str
    # 人間用の公開 SPA client（Authorization Code + PKCE）の client_id
    human_client_id: str


_REQUIRED_AUTH_KEYS = ("COGNITO_ISSUER", "HUMAN_CLIENT_ID")


def load_auth_settings(environ: Mapping[str, str]) -> AuthSettings:
    """環境変数から認証設定を読み込む。

    未設定・空文字の項目があれば ConfigurationError とする。
    開発用に認証を通す既定値は持たない。
    """
    missing = [key for key in _REQUIRED_AUTH_KEYS if not environ.get(key, "").strip()]
    if missing:
        raise ConfigurationError(f"認証設定が不足しています: {', '.join(missing)}")
    return AuthSettings(
        issuer=environ["COGNITO_ISSUER"].strip(),
        human_client_id=environ["HUMAN_CLIENT_ID"].strip(),
    )

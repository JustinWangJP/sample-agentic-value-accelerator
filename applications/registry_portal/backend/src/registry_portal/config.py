"""Registry ポータルの実行時設定。"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from urllib.parse import quote


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


@dataclass(frozen=True)
class DiscoverySettings:
    """JWT 認可の Registry に、発見用 M2M トークンで問い合わせるための設定。

    Registry の ARN と接続先は設定だけから決める。利用者のリクエストからは受け取らない。
    """

    # 既存 Registry の ARN（arn:aws:agent-registry:<region>:<account>:registry/<id>）
    registry_arn: str
    # Registry のリージョン。ARN から導出する
    registry_region: str
    # Cognito のトークンエンドポイント（https://<domain>/oauth2/token）
    token_url: str
    # 発見用 M2M client の client_id
    client_id: str
    # 発見用 M2M client の secret を保管した Secrets Manager の ARN
    client_secret_arn: str
    # secret のリージョン。ARN から導出する
    client_secret_region: str
    # 要求する scope（スペース区切り。空なら client の既定）
    scope: str

    @property
    def discoverable_records_list_url(self) -> str:
        """ListDiscoverableRegistryRecords の宛先。ARN は1つの path 要素として符号化する（Botocore と同じ）。"""
        return (
            f"https://agent-registry.{self.registry_region}.api.aws/registries/"
            f"{quote(self.registry_arn, safe='')}/discoverable-records-list"
        )


_REQUIRED_DISCOVERY_KEYS = (
    "REGISTRY_ARN",
    "DISCOVERY_TOKEN_URL",
    "DISCOVERY_CLIENT_ID",
    "DISCOVERY_CLIENT_SECRET_ARN",
)
_REGISTRY_ARN = re.compile(
    r"arn:aws(?:-[a-z]+)*:agent-registry:(?P<region>[a-z0-9-]+):\d{12}:registry/[A-Za-z0-9]{12,16}"
)

_SECRET_ARN = re.compile(
    r"arn:aws(?:-[a-z]+)*:secretsmanager:(?P<region>[a-z0-9-]+):\d{12}:secret:.+"
)


def load_discovery_settings(environ: Mapping[str, str]) -> DiscoverySettings:
    """環境変数から発見の設定を読み込む。不足・形式の誤りは ConfigurationError とする。"""
    missing = [key for key in _REQUIRED_DISCOVERY_KEYS if not environ.get(key, "").strip()]
    if missing:
        raise ConfigurationError(f"発見設定が不足しています: {', '.join(missing)}")
    registry_arn = environ["REGISTRY_ARN"].strip()
    match = _REGISTRY_ARN.fullmatch(registry_arn)
    if match is None:
        raise ConfigurationError("発見設定が正しくありません: REGISTRY_ARN の形式が不正です")
    token_url = environ["DISCOVERY_TOKEN_URL"].strip()
    if not token_url.startswith("https://"):
        raise ConfigurationError(
            "発見設定が正しくありません: DISCOVERY_TOKEN_URL は https で指定してください"
        )
    client_secret_arn = environ["DISCOVERY_CLIENT_SECRET_ARN"].strip()
    secret_match = _SECRET_ARN.fullmatch(client_secret_arn)
    if secret_match is None:
        raise ConfigurationError(
            "発見設定が正しくありません: DISCOVERY_CLIENT_SECRET_ARN の形式が不正です"
        )
    return DiscoverySettings(
        registry_arn=registry_arn,
        registry_region=match.group("region"),
        token_url=token_url,
        client_id=environ["DISCOVERY_CLIENT_ID"].strip(),
        client_secret_arn=client_secret_arn,
        client_secret_region=secret_match.group("region"),
        scope=environ.get("DISCOVERY_SCOPE", "").strip(),
    )

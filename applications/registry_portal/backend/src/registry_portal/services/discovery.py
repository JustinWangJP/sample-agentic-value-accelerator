"""発見アダプター: JWT 認可の Registry に、発見用 M2M トークンで問い合わせる。

- 発見用 M2M client の secret は、AWS Parameters and Secrets Lambda Extension を通じて
  Secrets Manager から取得し、メモリー上だけに保持する
- M2M トークン（client credentials）は有効期限を考慮して再利用する
- Registry が 401 を返したら、トークンを取り直して再試行する。再試行は1回に限る
- トークン、secret、応答の本文はログに出さない。出すのは HTTP ステータスと処理の種類だけ

Registry の発見 API は Boto3 では JWT 認可に対応しないため、HTTP で直接呼ぶ（設計書
「管理用の IAM と発見用の JWT を分ける」）。
"""

from __future__ import annotations

import base64
import json
import logging
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote, urlencode

from registry_portal.config import DiscoverySettings
from registry_portal.errors import DiscoveryUnavailableError, InvalidPageTokenError
from registry_portal.services.http import Transport, TransportError, TransportResponse

logger = logging.getLogger(__name__)

Clock = Callable[[], float]

# 有効期限の直前に期限切れになるのを避けるため、この秒数だけ早めに取り直す
TOKEN_EXPIRY_MARGIN_SECONDS = 60
# Registry が 401 を返したときに、トークンを取り直して再試行する回数の上限
MAX_AUTH_RETRIES = 1
# 1ページの件数。AWS の上限は100
PAGE_SIZE = 50
HTTP_TIMEOUT_SECONDS = 5.0
SECRETS_EXTENSION_URL = "http://localhost:2773/secretsmanager/get"
# アクセストークン（JWT）として受け付ける文字。改行などを含む値をヘッダーに載せない
_ACCESS_TOKEN = re.compile(r"[A-Za-z0-9\-_.~+/=]+")


def _parse_json_object(response: TransportResponse) -> Mapping[str, Any] | None:
    try:
        value = json.loads(response.body)
    except ValueError:
        return None
    return value if isinstance(value, Mapping) else None


class ExtensionSecretSource:
    """Secrets Manager の secret を、Lambda 拡張（localhost）経由で取得する。

    SecretString が JSON オブジェクトで `client_secret` を持てばその値を、そうでなければ
    SecretString 全体を client secret として使う。
    """

    def __init__(self, secret_arn: str, transport: Transport, environ: Mapping[str, str]) -> None:
        self._secret_arn = secret_arn
        self._transport = transport
        # Lambda の実行環境では、拡張の認証に AWS_SESSION_TOKEN を使う。呼び出し時点の値を読む
        self._environ = environ
        self._cached: str | None = None

    def get(self) -> str:
        if self._cached is not None:
            return self._cached
        url = f"{SECRETS_EXTENSION_URL}?{urlencode({'secretId': self._secret_arn})}"
        headers = {"x-aws-parameters-secrets-token": self._environ.get("AWS_SESSION_TOKEN", "")}
        try:
            response = self._transport("GET", url, headers, None, HTTP_TIMEOUT_SECONDS)
        except TransportError as error:
            logger.warning("発見用 secret の取得に失敗しました: transport=%s", error)
            raise DiscoveryUnavailableError() from None
        body = _parse_json_object(response) if response.status == 200 else None
        secret_string = body.get("SecretString") if body else None
        if not isinstance(secret_string, str) or not secret_string:
            logger.warning("発見用 secret の取得に失敗しました: status=%s", response.status)
            raise DiscoveryUnavailableError()
        self._cached = _client_secret_of(secret_string)
        return self._cached

    def invalidate(self) -> None:
        """secret のローテーションに備え、次回は取り直す。"""
        self._cached = None


def _client_secret_of(secret_string: str) -> str:
    try:
        value = json.loads(secret_string)
    except ValueError:
        return secret_string
    if isinstance(value, Mapping) and isinstance(value.get("client_secret"), str):
        return str(value["client_secret"])
    return secret_string


@dataclass
class _CachedToken:
    value: str
    refresh_at: float


class M2MTokenProvider:
    """発見用 M2M client のアクセストークンを、client credentials で取得してキャッシュする。

    Lambda の1実行環境は同時に1リクエストしか処理しないため、ロックは持たない。
    """

    def __init__(
        self,
        settings: DiscoverySettings,
        secrets: ExtensionSecretSource,
        transport: Transport,
        clock: Clock,
    ) -> None:
        self._settings = settings
        self._secrets = secrets
        self._transport = transport
        self._clock = clock
        self._cached: _CachedToken | None = None

    def get(self) -> str:
        cached = self._cached
        if cached is not None and self._clock() < cached.refresh_at:
            return cached.value
        self._cached = self._fetch()
        return self._cached.value

    def invalidate(self) -> None:
        self._cached = None

    def _fetch(self) -> _CachedToken:
        credentials = f"{self._settings.client_id}:{self._secrets.get()}".encode()
        form: dict[str, str] = {"grant_type": "client_credentials"}
        if self._settings.scope:
            form["scope"] = self._settings.scope
        headers = {
            "authorization": f"Basic {base64.b64encode(credentials).decode()}",
            "content-type": "application/x-www-form-urlencoded",
            "accept": "application/json",
        }
        try:
            response = self._transport(
                "POST",
                self._settings.token_url,
                headers,
                urlencode(form).encode(),
                HTTP_TIMEOUT_SECONDS,
            )
        except TransportError as error:
            logger.warning("M2M トークンの取得に失敗しました: transport=%s", error)
            raise DiscoveryUnavailableError() from None
        if response.status in (400, 401):
            # secret がローテーションされた可能性がある。次回は secret から取り直す
            self._secrets.invalidate()
        body = _parse_json_object(response) if response.status == 200 else None
        token = body.get("access_token") if body else None
        expires_in = body.get("expires_in") if body else None
        if (
            not isinstance(token, str)
            or _ACCESS_TOKEN.fullmatch(token) is None
            or not isinstance(expires_in, int)
            or isinstance(expires_in, bool)
            or expires_in <= 0
        ):
            logger.warning("M2M トークンの取得に失敗しました: status=%s", response.status)
            raise DiscoveryUnavailableError()
        refresh_at = self._clock() + max(expires_in - TOKEN_EXPIRY_MARGIN_SECONDS, 0)
        return _CachedToken(value=token, refresh_at=refresh_at)


@dataclass(frozen=True)
class CatalogItem:
    """カタログ項目の要約（公開承認済みの版）。"""

    record_id: str
    name: str
    display_name: str
    description: str
    record_type: str
    descriptor_types: tuple[str, ...]
    record_version: str
    updated_at: str

    @property
    def is_a2a(self) -> bool:
        return "a2aAgentCard" in self.descriptor_types


@dataclass(frozen=True)
class CatalogPage:
    items: tuple[CatalogItem, ...]
    # 次のページがなければ None。AWS の nextToken をそのまま中継する
    next_token: str | None


def _str_or_empty(value: Any) -> str:
    return value if isinstance(value, str) else ""


def _parse_item(value: Any) -> CatalogItem | None:
    if not isinstance(value, Mapping):
        return None
    record_id, name, record_type = value.get("recordId"), value.get("name"), value.get("recordType")
    if not all(isinstance(v, str) and v for v in (record_id, name, record_type)):
        return None
    descriptor_types = value.get("descriptorTypes")
    return CatalogItem(
        record_id=str(record_id),
        name=str(name),
        display_name=_str_or_empty(value.get("displayName")),
        description=_str_or_empty(value.get("description")),
        record_type=str(record_type),
        descriptor_types=(
            tuple(d for d in descriptor_types if isinstance(d, str))
            if isinstance(descriptor_types, list)
            else ()
        ),
        record_version=_str_or_empty(value.get("recordVersion")),
        updated_at=_str_or_empty(value.get("updatedAt")),
    )


def _parse_page(response: TransportResponse) -> CatalogPage | None:
    body = _parse_json_object(response)
    if body is None:
        return None
    records = body.get("registryRecords", [])
    next_token = body.get("nextToken")
    if not isinstance(records, list) or not (next_token is None or isinstance(next_token, str)):
        return None
    items = [_parse_item(record) for record in records]
    if any(item is None for item in items):
        return None
    return CatalogPage(
        items=tuple(item for item in items if item is not None),
        next_token=next_token or None,
    )


class DiscoveryClient:
    """Registry の発見 API（ListDiscoverableRegistryRecords）を呼ぶ。"""

    def __init__(
        self, settings: DiscoverySettings, tokens: M2MTokenProvider, transport: Transport
    ) -> None:
        self._tokens = tokens
        self._transport = transport
        # 宛先は設定の Registry ARN だけから組み立てる。ARN は1つの path 要素として符号化する
        self._list_url = (
            f"https://agent-registry.{settings.region}.api.aws/registries/"
            f"{quote(settings.registry_arn, safe='')}/discoverable-records-list"
        )

    def list_records(self, record_type: str, next_token: str | None) -> CatalogPage:
        """公開承認済みの Registry レコードを、種別で絞り込んで1ページ分返す。

        AWS はページが疎であることを保証しない（0件でも nextToken が返ることがある）。
        """
        payload: dict[str, Any] = {
            "filters": [{"name": "recordType", "values": [record_type]}],
            "maxResults": PAGE_SIZE,
        }
        if next_token is not None:
            payload["nextToken"] = next_token
        body = json.dumps(payload).encode()

        for attempt in range(MAX_AUTH_RETRIES + 1):
            headers = {
                "authorization": f"Bearer {self._tokens.get()}",
                "content-type": "application/json",
                "accept": "application/json",
            }
            try:
                response = self._transport(
                    "POST", self._list_url, headers, body, HTTP_TIMEOUT_SECONDS
                )
            except TransportError as error:
                logger.warning("発見 API の呼び出しに失敗しました: transport=%s", error)
                raise DiscoveryUnavailableError() from None
            if response.status != 401:
                break
            # トークンが失効・取り消された可能性がある。取り直して再試行する（回数は制限する）
            logger.info("発見 API が 401 を返しました: attempt=%s", attempt + 1)
            self._tokens.invalidate()
        else:
            logger.warning("発見 API の認証に失敗しました: 再取得の上限に達しました")
            raise DiscoveryUnavailableError()

        if response.status == 400 and next_token is not None:
            logger.info("発見 API が nextToken を拒否しました: status=400")
            raise InvalidPageTokenError()
        page = _parse_page(response) if response.status == 200 else None
        if page is None:
            logger.warning("発見 API が想定外の応答を返しました: status=%s", response.status)
            raise DiscoveryUnavailableError()
        return page


def create_discovery_client(
    settings: DiscoverySettings,
    transport: Transport,
    clock: Clock,
    environ: Mapping[str, str],
) -> DiscoveryClient:
    secrets = ExtensionSecretSource(settings.client_secret_arn, transport, environ)
    tokens = M2MTokenProvider(settings, secrets, transport, clock)
    return DiscoveryClient(settings, tokens, transport)

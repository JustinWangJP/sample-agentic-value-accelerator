"""発見アダプター: JWT 認可の Registry に、発見用 M2M トークンで問い合わせる。

- 発見用 M2M client の secret は、Boto3 で Secrets Manager から取得し、メモリー上だけに保持する
- M2M トークン（client credentials）は有効期限を考慮して再利用する
- Registry が 401 を返したら、トークンを取り直して1回だけ再試行する
- トークン、secret、応答の本文はログに出さない。出すのは HTTP ステータスと処理の種類だけ

Registry の発見 API は Boto3 では JWT 認可に対応しないため、HTTP で直接呼ぶ（設計書
「管理用の IAM と発見用の JWT を分ける」）。Secrets Manager は IAM 認可なので Boto3 を使う。

失敗はこのモジュールの例外（DiscoveryUnavailable・PageTokenRejected）で表す。
API の応答（HTTP ステータスと日本語メッセージ）への変換は呼び出し側で行う。
"""

from __future__ import annotations

import base64
import json
import logging
import re
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any
from urllib.parse import urlencode

import boto3
from botocore.exceptions import BotoCoreError, ClientError

from registry_portal.config import DiscoverySettings
from registry_portal.domain.catalog import (
    CatalogItem,
    CatalogPage,
    CatalogRecordType,
    is_catalog_record_type,
)
from registry_portal.services.http import (
    Transport,
    TransportError,
    TransportResponse,
    urllib_transport,
)

if TYPE_CHECKING:
    from mypy_boto3_secretsmanager import SecretsManagerClient

logger = logging.getLogger(__name__)

Clock = Callable[[], float]

# 有効期限の直前に期限切れになるのを避けるため、この秒数だけ早めに取り直す
TOKEN_EXPIRY_MARGIN_SECONDS = 60
# 1ページの件数。AWS の上限は100
PAGE_SIZE = 50
HTTP_TIMEOUT_SECONDS = 5.0
# アクセストークン（JWT）として受け付ける文字。改行などを含む値をヘッダーに載せない
_ACCESS_TOKEN_PATTERN = re.compile(r"[A-Za-z0-9\-_.~+/=]+")


class DiscoveryUnavailable(Exception):
    """Registry の発見 API から結果を得られなかった（認証・接続・応答の異常）。0件とは区別する。"""


class PageTokenRejected(Exception):
    """Registry が nextToken を拒否した（期限切れ・改ざんなど）。"""


@dataclass(frozen=True)
class DiscoveryDependencies:
    """発見アダプターが外部とやり取りする部品。テストではフェイクに差し替える。"""

    transport: Transport = urllib_transport
    clock: Clock = time.monotonic
    # None なら Lambda の実行ロールの認証情報で、secret のリージョンのクライアントを作る
    secrets_client: SecretsManagerClient | None = None


def _json_object(raw: bytes | str) -> Mapping[str, Any] | None:
    try:
        value = json.loads(raw)
    except ValueError:
        return None
    return value if isinstance(value, Mapping) else None


def _ok_json(response: TransportResponse) -> Mapping[str, Any] | None:
    return _json_object(response.body) if response.status == 200 else None


def _unavailable(message: str, *args: object) -> DiscoveryUnavailable:
    """失敗をログに残し、送出する例外を返す。ログの引数に秘密の値を渡さないこと。"""
    logger.warning(message, *args)
    return DiscoveryUnavailable()


class SecretsManagerSecretSource:
    """発見用 M2M client の secret を、Boto3 で Secrets Manager から取得する。

    SecretString が JSON オブジェクトで `client_secret` を持てばその値を、そうでなければ
    SecretString 全体を client secret として使う。
    """

    def __init__(self, client: SecretsManagerClient, secret_arn: str) -> None:
        self._client = client
        self._secret_arn = secret_arn
        self._cached: str | None = None

    def get(self) -> str:
        if self._cached is not None:
            return self._cached
        try:
            response = self._client.get_secret_value(SecretId=self._secret_arn)
        except (ClientError, BotoCoreError) as error:
            # AWS のエラーメッセージは ARN などを含みうるので、エラーコードだけを残す
            code = (
                error.response.get("Error", {}).get("Code", "unknown")
                if isinstance(error, ClientError)
                else type(error).__name__
            )
            raise _unavailable("発見用 secret の取得に失敗しました: code=%s", code) from None
        secret_string = response.get("SecretString")
        if not secret_string:
            raise _unavailable("発見用 secret の取得に失敗しました: SecretString がありません")
        value = _json_object(secret_string)
        client_secret = value.get("client_secret") if value is not None else None
        self._cached = client_secret if isinstance(client_secret, str) else secret_string
        return self._cached

    def invalidate(self) -> None:
        """secret のローテーションに備え、次回は取り直す。"""
        self._cached = None


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
        secrets: SecretsManagerSecretSource,
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
            raise _unavailable("M2M トークンの取得に失敗しました: transport=%s", error) from None
        if response.status in (400, 401):
            # secret がローテーションされた可能性がある。次回は secret から取り直す
            self._secrets.invalidate()
        body = _ok_json(response)
        token = body.get("access_token") if body else None
        expires_in = body.get("expires_in") if body else None
        if (
            not isinstance(token, str)
            or _ACCESS_TOKEN_PATTERN.fullmatch(token) is None
            or not isinstance(expires_in, int)
            or isinstance(expires_in, bool)
            or expires_in <= 0
        ):
            raise _unavailable("M2M トークンの取得に失敗しました: status=%s", response.status)
        refresh_at = self._clock() + max(expires_in - TOKEN_EXPIRY_MARGIN_SECONDS, 0)
        return _CachedToken(value=token, refresh_at=refresh_at)


def _str_or_empty(value: Any) -> str:
    return value if isinstance(value, str) else ""


def _parse_item(value: Any) -> CatalogItem | None:
    if not isinstance(value, Mapping):
        return None
    record_id, name, record_type = value.get("recordId"), value.get("name"), value.get("recordType")
    if not (isinstance(record_id, str) and record_id and isinstance(name, str) and name):
        return None
    if not is_catalog_record_type(record_type):
        return None
    descriptor_types = value.get("descriptorTypes")
    return CatalogItem(
        record_id=record_id,
        name=name,
        display_name=_str_or_empty(value.get("displayName")),
        description=_str_or_empty(value.get("description")),
        record_type=record_type,
        descriptor_types=(
            tuple(d for d in descriptor_types if isinstance(d, str))
            if isinstance(descriptor_types, list)
            else ()
        ),
        record_version=_str_or_empty(value.get("recordVersion")),
        updated_at=_str_or_empty(value.get("updatedAt")),
    )


def _parse_page(response: TransportResponse) -> CatalogPage | None:
    body = _ok_json(response)
    if body is None:
        return None
    records = body.get("registryRecords", [])
    next_token = body.get("nextToken")
    if not isinstance(records, list) or not (next_token is None or isinstance(next_token, str)):
        return None
    items = [_parse_item(record) for record in records]
    parsed = [item for item in items if item is not None]
    if len(parsed) != len(items):
        return None
    return CatalogPage(items=tuple(parsed), next_token=next_token or None)


def _rejects_next_token(response: TransportResponse) -> bool:
    """ValidationException（rest-json）の fieldList に nextToken があるか。"""
    if response.status != 400:
        return False
    fields = (_json_object(response.body) or {}).get("fieldList")
    return isinstance(fields, list) and any(
        isinstance(field, Mapping) and field.get("name") == "nextToken" for field in fields
    )


class DiscoveryClient:
    """Registry の発見 API（ListDiscoverableRegistryRecords）を呼ぶ。"""

    def __init__(
        self, settings: DiscoverySettings, tokens: M2MTokenProvider, transport: Transport
    ) -> None:
        self._tokens = tokens
        self._transport = transport
        # 宛先は設定の Registry ARN だけから決まる。利用者の入力は使わない
        self._list_url = settings.discoverable_records_list_url

    def list_records(self, record_type: CatalogRecordType, next_token: str | None) -> CatalogPage:
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

        response = self._post(body)
        if response.status == 401:
            # トークンが失効・取り消された可能性がある。取り直して1回だけ再試行する
            logger.info("発見 API が 401 を返しました。トークンを取り直して再試行します")
            self._tokens.invalidate()
            response = self._post(body)
            if response.status == 401:
                raise _unavailable("発見 API の認証に失敗しました: 再取得後も 401 でした")

        if next_token is not None and _rejects_next_token(response):
            logger.info("発見 API が nextToken を拒否しました: status=400")
            raise PageTokenRejected()
        page = _parse_page(response)
        if page is None:
            raise _unavailable("発見 API が想定外の応答を返しました: status=%s", response.status)
        return page

    def _post(self, body: bytes) -> TransportResponse:
        headers = {
            "authorization": f"Bearer {self._tokens.get()}",
            "content-type": "application/json",
            "accept": "application/json",
        }
        try:
            return self._transport("POST", self._list_url, headers, body, HTTP_TIMEOUT_SECONDS)
        except TransportError as error:
            raise _unavailable("発見 API の呼び出しに失敗しました: transport=%s", error) from None


def create_discovery_client(
    settings: DiscoverySettings, dependencies: DiscoveryDependencies
) -> DiscoveryClient:
    """発見アダプターを組み立てる。通信は最初の呼び出しまで行わない。"""
    secrets_client = dependencies.secrets_client or boto3.client(
        "secretsmanager", region_name=settings.client_secret_region
    )
    secrets = SecretsManagerSecretSource(secrets_client, settings.client_secret_arn)
    tokens = M2MTokenProvider(settings, secrets, dependencies.transport, dependencies.clock)
    return DiscoveryClient(settings, tokens, dependencies.transport)

"""標準ライブラリだけで実装した、小さな HTTP トランスポート。

発見アダプターはこの関数型（Transport）だけに依存する。テストではフェイクに差し替える。
"""

from __future__ import annotations

import urllib.error
import urllib.request
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class TransportResponse:
    """HTTP 応答。4xx・5xx も例外にせず、そのまま返す。"""

    status: int
    body: bytes


class TransportError(Exception):
    """接続できない、タイムアウトしたなど、HTTP 応答を得られなかった。"""


class Transport(Protocol):
    def __call__(
        self,
        method: str,
        url: str,
        headers: Mapping[str, str],
        body: bytes | None,
        timeout: float,
    ) -> TransportResponse: ...


def urllib_transport(
    method: str,
    url: str,
    headers: Mapping[str, str],
    body: bytes | None,
    timeout: float,
) -> TransportResponse:
    """urllib による実装。宛先 URL は設定から組み立てたものだけを渡す（利用者の入力は渡さない）。"""
    request = urllib.request.Request(url, data=body, method=method, headers=dict(headers))
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return TransportResponse(status=response.status, body=response.read())
    except urllib.error.HTTPError as error:
        with error:
            return TransportResponse(status=error.code, body=error.read())
    except (urllib.error.URLError, TimeoutError, OSError, ValueError) as error:
        # 例外の詳細（URL やヘッダーの値）を外へ出さない。種類だけを残す。
        # ValueError は不正なヘッダー値などで送信前に起きる。メッセージにトークンを含みうる
        raise TransportError(type(error).__name__) from None

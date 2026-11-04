"""API のエラーと、利用者に返す共通エラー DTO。

AWS や内部の例外メッセージはそのまま返さず、ここで定義した日本語メッセージに置き換える。
"""

from __future__ import annotations

from typing import Any


class ApiError(Exception):
    """HTTP ステータスとエラーコードを持つ API エラー。"""

    status: int = 500
    code: str = "INTERNAL_ERROR"
    default_message: str = "内部エラーが発生しました。時間をおいて再度お試しください。"

    def __init__(self, message: str | None = None) -> None:
        self.message = message or self.default_message
        super().__init__(self.message)

    def to_dto(self, request_id: str | None) -> dict[str, Any]:
        """共通エラー DTO に変換する。"""
        return {"error": {"code": self.code, "message": self.message, "requestId": request_id}}


class AuthenticationError(ApiError):
    """トークンがない、または人間の主体として認証できないトークン。

    ログインし直せば解決しうる場合に使う（トークンなし、ID トークン、別 issuer、sub なし）。
    """

    status = 401
    code = "UNAUTHENTICATED"
    default_message = "認証が必要です。ログインし直してください。"


class ForbiddenError(ApiError):
    """署名検証を通った正規のトークンだが、この API では受け付けない呼び出し元。

    ログインし直しても解決しない場合に使う（別 client、発見用 M2M client など）。
    """

    status = 403
    code = "FORBIDDEN"
    default_message = "この操作を行う権限がありません。"


class RouteNotFoundError(ApiError):
    """存在しない API。"""

    status = 404
    code = "NOT_FOUND"
    default_message = "指定された API は存在しません。"


class RequestValidationError(ApiError):
    """リクエストの値が不正。"""

    status = 400
    code = "VALIDATION_ERROR"
    default_message = "リクエストの指定が正しくありません。"


class InvalidPageTokenError(ApiError):
    """ページの指定（nextToken）が不正、または期限切れ。"""

    status = 400
    code = "INVALID_PAGE_TOKEN"
    default_message = "ページの指定が正しくありません。最初のページから表示し直してください。"


class DiscoveryUnavailableError(ApiError):
    """Registry の発見 API から結果を得られなかった（認証・接続・応答の異常）。

    結果が0件の場合とは区別する。AWS のエラー内容は利用者に返さない。
    """

    status = 502
    code = "DISCOVERY_UNAVAILABLE"
    default_message = "カタログを取得できませんでした。時間をおいて再度お試しください。"

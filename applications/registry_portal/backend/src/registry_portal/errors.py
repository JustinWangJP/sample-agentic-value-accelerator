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
    """トークンがない、またはポータル API で受け付けないトークン。"""

    status = 401
    code = "UNAUTHENTICATED"
    default_message = "認証が必要です。ログインし直してください。"


class RouteNotFoundError(ApiError):
    """存在しない API。"""

    status = 404
    code = "NOT_FOUND"
    default_message = "指定された API は存在しません。"

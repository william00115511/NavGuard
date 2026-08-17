"""System-level error types.

Per ForAI.md 4.5.4：業務邏輯失敗（聽不懂地點、資訊不足等）一律回 HTTP 200，
body 用 status: "error" 表示；只有系統層級錯誤才用這裡的 HTTP 錯誤碼。
"""


class ApiError(Exception):
    """Base class for system-level errors mapped to HTTP error codes."""

    status_code = 500
    error_code = "INTERNAL_ERROR"

    def __init__(self, message: str, error_code: str | None = None):
        super().__init__(message)
        self.message = message
        if error_code:
            self.error_code = error_code


class BadRequestError(ApiError):
    status_code = 400
    error_code = "BAD_REQUEST"


class SessionNotFoundError(ApiError):
    status_code = 404
    error_code = "SESSION_NOT_FOUND"


class UpstreamError(ApiError):
    """Gemini API timeout, route engine exception, etc."""

    status_code = 500
    error_code = "INTERNAL_ERROR"


class RouteNotFoundError(ApiError):
    """起訖點在路網圖上不連通，找不到可行路徑。"""

    status_code = 500
    error_code = "ROUTE_NOT_FOUND"

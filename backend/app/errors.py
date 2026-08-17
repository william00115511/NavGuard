"""系統層級錯誤型別（AGENTS.md §6.5）。

§6.5 的分界：
- **業務邏輯失敗**（聽不懂地點、資訊不足、超出覆蓋範圍、找不到路徑）一律回
  HTTP 200，body 用 `status: "error"` + `error_code`——請求本身是有效的，
  只是這次的結果失敗。這類失敗以 inner_interface 的領域例外表達，不在這裡。
- **系統層級錯誤**才用 HTTP 錯誤碼，就是下面這些。
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


class UpstreamTimeoutError(ApiError):
    """Gemini 或 geocoding 逾時。"""

    status_code = 504
    error_code = "UPSTREAM_TIMEOUT"


class InternalError(ApiError):
    status_code = 500
    error_code = "INTERNAL_ERROR"


# §6.2 常見的業務層 error_code，回在 HTTP 200 的 body 裡。
GEOCODING_FAILED = "GEOCODING_FAILED"
OUT_OF_COVERAGE = "OUT_OF_COVERAGE"
NO_ROUTE_FOUND = "NO_ROUTE_FOUND"

import logging

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.errors import ApiError
from app.routers import chat, route, session

logger = logging.getLogger(__name__)

app = FastAPI(title="Safeway Backend", version="0.1.0")

app.include_router(session.router)
app.include_router(chat.router)
app.include_router(route.router)


@app.exception_handler(ApiError)
async def api_error_handler(request: Request, exc: ApiError) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status_code,
        content={"status": "error", "error_code": exc.error_code, "message": exc.message},
    )


@app.exception_handler(RequestValidationError)
async def validation_error_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    # 4.5.4節：request 格式錯（如缺 message）一律 400 BAD_REQUEST
    # 只取 loc/msg，不把 str(exc) 內含的檔案路徑等內部資訊回傳給 client
    details = "; ".join(f"{'.'.join(str(p) for p in e['loc'])}: {e['msg']}" for e in exc.errors())
    return JSONResponse(
        status_code=400,
        content={"status": "error", "error_code": "BAD_REQUEST", "message": details},
    )


@app.exception_handler(Exception)
async def unhandled_error_handler(request: Request, exc: Exception) -> JSONResponse:
    # 4.5.4節：其餘未預期例外一律當系統層級 500
    logger.exception("unhandled error while processing %s", request.url)
    return JSONResponse(
        status_code=500,
        content={"status": "error", "error_code": "INTERNAL_ERROR", "message": str(exc)},
    )


@app.get("/healthz")
async def healthz() -> dict:
    return {"ok": True}

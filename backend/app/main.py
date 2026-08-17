import asyncio
import logging
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.chat.dependencies import get_chat_service
from app.config import Settings
from app.data.store import get_data_store
from app.engine.graph import get_road_graph
from app.errors import ApiError
from app.routers import chat, route, session

# uvicorn 預設的 LOGGING_CONFIG 只設定 uvicorn.* 這幾個 logger，root logger 沒有
# handler，導致 app 內的 logger.info(...) 全部被吃掉不會輸出，這裡補上設定讓其生效。
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

logger = logging.getLogger(__name__)


async def _reap_sessions_periodically(interval_seconds: float) -> None:
    """§6.6：背景排程定時回收過期 session，與存取觸發式的過期檢查互補。"""
    chat_service = get_chat_service()
    while True:
        await asyncio.sleep(interval_seconds)
        try:
            reaped = await chat_service.reap_expired_sessions()
            if reaped:
                logger.info("reaped %d expired session(s)", reaped)
        except Exception:
            logger.exception("session reap failed")


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = Settings()
    reap_task = asyncio.create_task(
        _reap_sessions_periodically(settings.session_reap_interval_seconds)
    )
    try:
        yield
    finally:
        reap_task.cancel()


app = FastAPI(title="Safeway Backend", version="0.1.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

_LOG_BODY_MAX_CHARS = 2000


async def _read_body_for_log(request: Request) -> str:
    """讀取 request body 供 log 使用；Starlette 會快取內容，不影響後續 handler 讀取。"""
    body = await request.body()
    if not body:
        return ""
    text = body.decode("utf-8", errors="replace")
    if len(text) > _LOG_BODY_MAX_CHARS:
        text = text[:_LOG_BODY_MAX_CHARS] + f"...(truncated, {len(body)} bytes total)"
    return text


@app.middleware("http")
async def log_api_requests(request: Request, call_next):
    """記錄每次前端與後端的 API 交互（method、path、request body、status code、耗時）。"""
    body_text = await _read_body_for_log(request)
    start = time.perf_counter()
    response = await call_next(request)
    duration_ms = (time.perf_counter() - start) * 1000
    logger.info(
        "%s %s body=%s -> %d (%.1fms)",
        request.method,
        request.url.path,
        body_text,
        response.status_code,
        duration_ms,
    )
    return response


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
    # §6.5：request 格式錯（如缺 message）一律 400 BAD_REQUEST。
    # 只取 loc/msg，不把 str(exc) 內含的檔案路徑等內部資訊回傳給 client。
    details = "; ".join(f"{'.'.join(str(p) for p in e['loc'])}: {e['msg']}" for e in exc.errors())
    return JSONResponse(
        status_code=400,
        content={"status": "error", "error_code": "BAD_REQUEST", "message": details},
    )


@app.exception_handler(Exception)
async def unhandled_error_handler(request: Request, exc: Exception) -> JSONResponse:
    # §6.5：其餘未預期例外一律當系統層級 500。細節只寫進 log，不回傳給 client
    # （例外訊息可能含檔案路徑、SQL、上游 API 回應等內部資訊）。
    logger.exception("unhandled error while processing %s", request.url)
    return JSONResponse(
        status_code=500,
        content={"status": "error", "error_code": "INTERNAL_ERROR", "message": "後端內部錯誤"},
    )


@app.get("/healthz")
async def healthz() -> dict:
    """§6.4：除了存活之外，也回報資料實際載入的狀況。"""
    graph = get_road_graph()
    store = get_data_store()
    return {
        "ok": True,
        "graph_loaded": bool(graph.nodes),
        "points_loaded": len(store.static_points),
    }

import asyncio
import json
import logging
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


app = FastAPI(title="NavGuard Backend", version="0.1.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

_LOGGED_STATUSES = {"error", "route_ready"}


async def _replay(body: bytes):
    yield body


@app.middleware("http")
async def log_api_responses(request: Request, call_next):
    """只記錄 status 為 error 或 route_ready 的回應內容，其餘 request/response 一律不記。"""
    response = await call_next(request)

    body = b"".join([chunk async for chunk in response.body_iterator])
    response.body_iterator = _replay(body)

    try:
        data = json.loads(body)
    except ValueError:
        data = None

    if isinstance(data, dict) and data.get("status") in _LOGGED_STATUSES:
        logger.info("%s %s -> %s", request.method, request.url.path, data)

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

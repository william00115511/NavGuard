import logging

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.data.store import get_data_store
from app.engine.graph import get_road_graph
from app.errors import ApiError
from app.routers import chat, route, session

logger = logging.getLogger(__name__)

app = FastAPI(title="Safeway Backend", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

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

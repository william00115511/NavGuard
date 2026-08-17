from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.errors import ApiError

app = FastAPI(title="Safeway Backend", version="0.1.0")


@app.exception_handler(ApiError)
async def api_error_handler(request: Request, exc: ApiError) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status_code,
        content={"status": "error", "error_code": exc.error_code, "message": exc.message},
    )


@app.get("/healthz")
async def healthz() -> dict:
    return {"ok": True}

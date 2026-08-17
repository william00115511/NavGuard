"""Central paths and scoring constants for the backend.

Paths are all local files so the app never calls out to a database at
request time (AGENTS.md §3.1). Scoring constants live here rather than
inline so they can be tuned once against the real data distribution
without touching the engine (§4.3).
"""

from pathlib import Path

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parent.parent
PROJECT_ROOT = BACKEND_DIR.parent
DATA_DIR = BACKEND_DIR / "data"
POINTS_DIR = DATA_DIR / "points"
CATEGORIES_PATH = DATA_DIR / "categories.json"
ROAD_NETWORK_PATH = DATA_DIR / "road_network.json"

# §4.2 沿 edge 每隔固定距離取樣一次再取平均，避免長邊只用中點代表。
EDGE_SAMPLE_INTERVAL_M = 25.0

# §4.3 固定參數的 squashing 函式：safety = 1 / (1 + exp(-k × raw_score))。
# 固定 k（而非 min-max）才能讓分數跨請求可比較、可測試。
SAFETY_SIGMOID_K = 0.6

# §4.4 綜合成本函數的預設安全權重。
DEFAULT_ALPHA = 0.6

# §4.6 duration_min_est 用的步行速度。
WALK_SPEED_MPS = 1.3

# §4.7 / §9.1 起訖點吸附到路網節點的容忍距離；超過視為超出覆蓋範圍，
# 不做外插，直接回 OUT_OF_COVERAGE。
MAX_SNAP_DISTANCE_M = 500.0

# §5.4 規則 2：Gemini 回報了 categories.json 中不存在的類別時的 fallback。
FALLBACK_DYNAMIC_CATEGORY = "dynamic_unknown"

# §1 原則 1：每次提供路線都必須附上的免責聲明。
DISCLAIMER = "此建議依公開資料與即時資訊產生，無法保證安全；緊急狀況請立即撥打 110 或 119。"


class Settings(BaseSettings):
    """Runtime-only settings. Secrets are loaded from the ignored root .env."""

    model_config = SettingsConfigDict(
        env_file=PROJECT_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    chat_service_backend: str = "fake"
    gemini_api_key: SecretStr = SecretStr("")
    gemini_model: str = "gemini-3.5-flash-lite"
    gemini_fallback_models: list[str] = [
        "gemini-3.1-flash-lite",
        "gemini-3.7-flash",
        "gemini-flash-latest",
        "gemini-3.5-flash",
    ]
    # Vertex AI 用 service account JSON 金鑰做 OAuth2 驗證，不是字串型 API key；
    # 檔名對齊 Google Cloud 用戶端函式庫慣用的 GOOGLE_APPLICATION_CREDENTIALS 環境變數。
    google_application_credentials: Path = PROJECT_ROOT / "google-credentials.json"
    vertex_location: str = "global"
    # 同時支援 maps_api_key / geocoding_api_key / routes_api_key
    maps_api_key: SecretStr = SecretStr("")
    geocoding_api_key: SecretStr = SecretStr("")
    routes_api_key: SecretStr = SecretStr("")
    session_ttl_seconds: float = Field(default=30 * 60, gt=0)
    # §6.6：背景排程多久掃一次過期 session 並回收，與 session_ttl_seconds 互補——
    # 就算沒有請求觸發存取式過期檢查，閒置 session 也會被定期清掉。
    session_reap_interval_seconds: float = Field(default=5 * 60, gt=0)
    max_history_messages: int = Field(default=20, ge=4)

    @field_validator("google_application_credentials")
    @classmethod
    def _resolve_credentials_path(cls, value: Path) -> Path:
        # 相對路徑一律以 PROJECT_ROOT 為基準，不受執行時 cwd 影響
        # （例如從 backend/ 目錄啟動 uvicorn 時）。
        return value if value.is_absolute() else PROJECT_ROOT / value

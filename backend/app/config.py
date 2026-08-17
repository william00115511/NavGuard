"""Central paths and scoring constants for the backend.

Paths are all local files so the app never calls out to a database at
request time (AGENTS.md §3.1). Scoring constants live here rather than
inline so they can be tuned once against the real data distribution
without touching the engine (§4.3).
"""

from pathlib import Path

from pydantic import Field, SecretStr
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

# §4.6 幾個 metric 的固定半徑（與類別自身的影響半徑無關，這是報告口徑）。
LIT_COVERAGE_RADIUS_M = 30.0
HELP_POINT_RADIUS_M = 50.0
POLICE_RADIUS_M = 150.0

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
    gemini_model: str = "gemini-2.5-flash"
    # 同時給 Places Geocoding API（正式 API 用）與 Routes API
    # （backend/evaluation/ 離線驗證子專案用）；該 GCP 專案需分別啟用這兩個 API。
    maps_api_key: SecretStr = SecretStr("")
    session_ttl_seconds: float = Field(default=30 * 60, gt=0)
    max_history_messages: int = Field(default=20, ge=4)
    # §6.6：改成 client_id 自動建立 session 後，用固定大小的 LRU pool 取代
    # 「顯式建立/清除」的兩段式流程；超過這個數量時逐出最久未使用的 session。
    max_active_sessions: int = Field(default=50, ge=1)

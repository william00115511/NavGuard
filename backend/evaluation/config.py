"""評估子專案的常數。跟 app/config.py 分開，避免評估用的參數跟正式服務的
scoring 常數混在一起——這裡的東西只影響「怎麼取樣、報告存哪」，不影響任何
使用者看得到的安全分數。
"""

from pathlib import Path

EVALUATION_DIR = Path(__file__).resolve().parent
OUTPUT_DIR = EVALUATION_DIR / "output"
GOOGLE_CACHE_DIR = OUTPUT_DIR / "google_cache"
RESULTS_CSV_PATH = OUTPUT_DIR / "results.csv"
FAILURES_CSV_PATH = OUTPUT_DIR / "failures.csv"
REPORT_MD_PATH = OUTPUT_DIR / "report.md"
REPORT_HTML_PATH = OUTPUT_DIR / "report.html"

# 起訖點取樣的直線距離範圍：太短沒有繞路空間比不出差異，太長不像真實的
# 夜間步行單趟距離。
MIN_SCENARIO_DISTANCE_M = 400.0
MAX_SCENARIO_DISTANCE_M = 2500.0

# 每次隨機取樣起訖節點時，最多嘗試幾次才放棄（避免不連通/距離不符的節點對
# 讓迴圈卡住）。
MAX_SAMPLE_ATTEMPTS_PER_SCENARIO = 50

DEFAULT_N_SCENARIOS = 30
DEFAULT_SEED = 42

GOOGLE_ROUTES_URL = "https://routes.googleapis.com/directions/v2:computeRoutes"
GOOGLE_ROUTES_TIMEOUT_S = 10.0

# bootstrap 信賴區間用的重抽樣次數；純 stdlib，不含 scipy。
BOOTSTRAP_ITERATIONS = 2000
BOOTSTRAP_CONFIDENCE = 0.95

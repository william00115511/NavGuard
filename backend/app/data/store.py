"""啟動時載入一次、之後供路徑引擎查詢的資料容器。"""

from dataclasses import dataclass

from app.config import CATEGORIES_PATH, POINTS_DIR
from app.data.loader import load_categories, load_points
from app.data.schema import CategoryConfig, PointRecord


@dataclass
class DataStore:
    categories: dict[str, CategoryConfig]
    static_points: list[PointRecord]

    @classmethod
    def load(cls) -> "DataStore":
        return cls(
            categories=load_categories(CATEGORIES_PATH),
            static_points=load_points(POINTS_DIR),
        )


_store: DataStore | None = None


def get_data_store() -> DataStore:
    """Process 內單例；FastAPI 啟動時（或第一次用到時）載入一次。"""
    global _store
    if _store is None:
        _store = DataStore.load()
    return _store

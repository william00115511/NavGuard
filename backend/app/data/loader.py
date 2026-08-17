"""載入 categories.json 與 /data/points/*.json（ForAI.md 2.2 / 2.3）。

新增點位類型只需新增資料檔＋在 categories.json 登記一筆設定，這個模組完全
不用改：啟動時掃描整個目錄，自動載入符合 schema 的檔案。
"""

import json
import logging
from pathlib import Path
from typing import Optional

from app.data.schema import CategoryConfig, PointRecord

logger = logging.getLogger(__name__)

_REQUIRED_POINT_FIELDS = {"id", "category", "lat", "lng", "source", "source_type"}


def load_categories(path: Path) -> dict[str, CategoryConfig]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    categories: dict[str, CategoryConfig] = {}
    for name, cfg in raw.items():
        categories[name] = CategoryConfig(
            name=name,
            effect=cfg["effect"],
            weight=cfg["weight"],
            radius_m=cfg["radius_m"],
            kind=cfg["kind"],
            default_ttl_hours=cfg.get("default_ttl_hours"),
            label=cfg.get("label"),
        )
    return categories


def _parse_point(raw: dict, source_file: str) -> Optional[PointRecord]:
    if not _REQUIRED_POINT_FIELDS.issubset(raw.keys()):
        missing = _REQUIRED_POINT_FIELDS - raw.keys()
        logger.warning("跳過 %s 中不符合 schema 的點位（缺少欄位 %s）: %s", source_file, missing, raw)
        return None
    return PointRecord(
        id=raw["id"],
        category=raw["category"],
        lat=raw["lat"],
        lng=raw["lng"],
        source=raw["source"],
        source_type=raw["source_type"],
        expires_at=raw.get("expires_at"),
        confidence=raw.get("confidence", 1.0),
        place_id=raw.get("place_id"),
        meta=raw.get("meta", {}),
    )


def load_points(points_dir: Path) -> list[PointRecord]:
    points: list[PointRecord] = []
    if not points_dir.exists():
        return points

    for file_path in sorted(points_dir.glob("*.json")):
        try:
            raw_list = json.loads(file_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            logger.warning("跳過無法解析的點位檔案: %s", file_path)
            continue

        for raw in raw_list:
            point = _parse_point(raw, file_path.name)
            if point is not None:
                points.append(point)

    return points

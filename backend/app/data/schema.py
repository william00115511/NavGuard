"""Unified point schema and category config (ForAI.md 2.2 / 2.3)."""

from dataclasses import dataclass, field
from typing import Optional


@dataclass(frozen=True)
class PointRecord:
    """一筆點位資料，靜態(street_light.json...)與動態(即時新聞回報)共用同一格式。"""

    id: str
    category: str
    lat: float
    lng: float
    source: str
    source_type: str  # "static_local" | "dynamic_realtime"
    expires_at: Optional[str] = None
    confidence: float = 1.0
    meta: dict = field(default_factory=dict)


@dataclass(frozen=True)
class CategoryConfig:
    """categories.json 一筆設定：該類別對安全分數的影響方向、半徑、權重。"""

    name: str
    effect: str  # "positive" | "negative"
    weight: float
    radius_m: float
    kind: str  # "static" | "dynamic"
    default_ttl_hours: Optional[float] = None

    @property
    def sign(self) -> float:
        return 1.0 if self.effect == "positive" else -1.0

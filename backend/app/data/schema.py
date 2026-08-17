"""統一點位格式與類別設定（AGENTS.md §3.2 / §3.3）。"""

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
    """categories.json 一筆設定：該類別對安全分數的影響方向、半徑、權重。

    計分公式只認識「正面／負面、影響半徑、權重」，不認識任何具體類別名稱；
    新增類別只要加一行設定 + 對應資料檔即可（§3.3）。
    """

    name: str
    effect: str  # "positive" | "negative"
    weight: float
    radius_m: float
    kind: str  # "static" | "dynamic"
    default_ttl_hours: Optional[float] = None
    # 給使用者看的中文名稱（warnings 文案用），未設定時退回類別代號。
    label: Optional[str] = None

    @property
    def sign(self) -> float:
        """§4.2：正負面點位進入公式的唯一入口。"""
        return 1.0 if self.effect == "positive" else -1.0

    @property
    def display_name(self) -> str:
        return self.label or self.name

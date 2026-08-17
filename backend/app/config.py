"""Central path/config constants for the backend.

Kept intentionally tiny: everything is a local file path so the app never
calls out to a database. See ForAI.md section 2 for the data layer design.
"""

from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BACKEND_DIR / "data"
POINTS_DIR = DATA_DIR / "points"
CATEGORIES_PATH = DATA_DIR / "categories.json"
ROAD_NETWORK_PATH = DATA_DIR / "road_network.json"

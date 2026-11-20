from __future__ import annotations
import os
from pathlib import Path

PROJECT_NAME = "抄表簿"
ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = ROOT / "data"
# 可用 METERSEAL_DB 覆盖（核对脚本用独立临时库，不污染开发库）
DB_PATH = Path(os.environ.get("METERSEAL_DB", str(DATA_DIR / "meterseal.db")))

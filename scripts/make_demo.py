#!/usr/bin/env python3
"""Сгенерировать СИНТЕТИЧЕСКИЙ демо-набор в data/demo (не данные Островка)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app import synthetic  # noqa: E402
from app.config import BASE_DIR  # noqa: E402

print(synthetic.build(BASE_DIR / "data" / "demo"))

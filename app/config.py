"""Настройки из переменных окружения (см. .env.example)."""
from __future__ import annotations

import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default)


class Settings:
    port: int = int(_env("APP_PORT", "8080"))
    data_dir: Path = Path(_env("DATA_DIR", str(BASE_DIR / "data")))
    runtime_dir: Path = Path(_env("RUNTIME_DIR", str(BASE_DIR / "runtime")))
    web_dir: Path = Path(_env("WEB_DIR", str(BASE_DIR / "web")))

    llm_base_url: str = _env("LLM_BASE_URL", "https://api.openai.com/v1").rstrip("/")
    llm_api_key: str = _env("LLM_API_KEY", "")
    llm_model: str = _env("LLM_MODEL", "gpt-4o-mini")
    llm_project: str = _env("LLM_PROJECT", "")       # заголовок OpenAI-Project (нужен Yandex AI Studio: идентификатор каталога)

    region_slug: str = _env("REGION_SLUG", "russia/baykal")
    collect_limit: int = int(_env("COLLECT_LIMIT", "60"))
    collect_catalog_pages: int = int(_env("COLLECT_CATALOG_PAGES", "100"))
    enrich_on_demand: bool = _env("ENRICH_ON_DEMAND", "true").lower() in ("1", "true", "yes")
    enrich_batch: int = int(_env("ENRICH_BATCH", "8"))
    enrich_max_per_hour: int = int(_env("ENRICH_MAX_PER_HOUR", "120"))
    collect_delay: float = float(_env("COLLECT_DELAY", "1.5"))

    hotelier_login: str = _env("HOTELIER_LOGIN", "hotelier")
    hotelier_password: str = _env("HOTELIER_PASSWORD", "hotelier123")

    # рельеф (плитки Terrarium) и внешние источники «скрытых» характеристик
    dem_enabled: bool = _env("DEM_ENABLED", "true").lower() in ("1", "true", "yes")
    dem_fetch: bool = _env("DEM_FETCH", "true").lower() in ("1", "true", "yes")          # докачивать недостающие плитки, если есть интернет
    probes_online: bool = _env("PROBES_ONLINE", "true").lower() in ("1", "true", "yes")  # при проверке отеля по запросу ходить во внешние источники
    probe_timeout: float = float(_env("PROBE_TIMEOUT", "8"))
    opencellid_key: str = _env("OPENCELLID_KEY", "")                                      # необязательно: точнее оценка сотовой связи
    forest_height_m: float = float(_env("FOREST_HEIGHT_M", "14"))                         # типовая высота леса для расчёта видимости

    refresh_enabled: bool = _env("REFRESH_ENABLED", "false").lower() in ("1", "true", "yes")
    refresh_interval_min: int = int(_env("REFRESH_INTERVAL_MIN", "60"))
    refresh_online: bool = _env("REFRESH_ONLINE", "false").lower() in ("1", "true", "yes")

    @property
    def db_path(self) -> Path:
        return self.runtime_dir / "lupa.db"

    @property
    def llm_enabled(self) -> bool:
        return bool(self.llm_api_key)


settings = Settings()

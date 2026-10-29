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

    # GigaChat (Сбер): свой протокол авторизации и картинок, поэтому отдельный провайдер.
    # LLM_PROVIDER=gigachat включает его; без явного указания — включается сам, если задан GIGACHAT_AUTH_KEY.
    gigachat_auth_key: str = _env("GIGACHAT_AUTH_KEY", "")            # «Ключ авторизации» из личного кабинета (base64 от client_id:secret)
    gigachat_scope: str = _env("GIGACHAT_SCOPE", "GIGACHAT_API_PERS")  # PERS — физлица; B2B/CORP — организации
    gigachat_model: str = _env("GIGACHAT_MODEL", "GigaChat-2")
    gigachat_vision_model: str = _env("GIGACHAT_VISION_MODEL", "GigaChat-2-Max")   # для фото — модель, которая видит картинки
    gigachat_base_url: str = _env("GIGACHAT_BASE_URL", "https://api.giga.chat/v1").rstrip("/")
    gigachat_auth_url: str = _env("GIGACHAT_AUTH_URL", "https://ngw.devices.sberbank.ru:9443/api/v2/oauth")
    gigachat_ca_bundle: str = _env("GIGACHAT_CA_BUNDLE", str(BASE_DIR / "certs" / "russian_trusted_root_ca.pem"))  # корневой сертификат Минцифры
    gigachat_max_parallel: int = int(_env("GIGACHAT_MAX_PARALLEL", "1"))  # у бесплатного тарифа — один запрос за раз
    llm_provider: str = (_env("LLM_PROVIDER", "") or ("gigachat" if _env("GIGACHAT_AUTH_KEY", "") else "openai")).lower()

    @property
    def model_name(self) -> str:
        return self.gigachat_model if self.llm_provider == "gigachat" else self.llm_model

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
        return self.runtime_dir / "kva.db"

    @property
    def llm_enabled(self) -> bool:
        if self.llm_provider == "gigachat":
            return bool(self.gigachat_auth_key)
        return bool(self.llm_api_key)


settings = Settings()

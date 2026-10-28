import pytest

from app import db, pipeline, registry, synthetic
from app.config import settings


@pytest.fixture(scope="session")
def world(tmp_path_factory):
    """Синтетический мир (НЕ данные Островка) + вся обработка. Один раз на сессию тестов."""
    root = tmp_path_factory.mktemp("lupa")
    saved = (settings.data_dir, settings.runtime_dir)
    settings.data_dir = root / "data"
    settings.runtime_dir = root / "runtime"
    synthetic.build(settings.data_dir)
    db.reset_connection(str(root / "t.db"))
    registry.invalidate()
    pipeline.bootstrap(force=True)
    yield root
    settings.data_dir, settings.runtime_dir = saved

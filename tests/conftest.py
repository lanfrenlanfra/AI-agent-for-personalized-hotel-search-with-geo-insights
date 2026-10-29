import pytest

from app import db, pipeline, registry, synthetic
from app.config import settings


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    """Тесты не ходят в сеть: ни за рельефом, ни за доп. данными OSM, ни за внешними источниками."""
    from app import dem, net
    monkeypatch.setattr(settings, "dem_fetch", False)
    monkeypatch.setattr(settings, "probes_online", False)
    monkeypatch.setattr(net, "ensure_extras", lambda *a, **k: {"tiles": 0, "around": 0, "dem": None, "errors": []})
    monkeypatch.setattr(dem, "fetch_tile", lambda *a, **k: (_ for _ in ()).throw(dem.DemUnavailable("тест: сеть отключена")))
    dem.clear_cache()


@pytest.fixture(scope="session")
def world(tmp_path_factory):
    """Синтетический мир (НЕ данные Островка) + вся обработка. Один раз на сессию тестов."""
    root = tmp_path_factory.mktemp("kva")
    saved = (settings.data_dir, settings.runtime_dir)
    settings.data_dir = root / "data"
    settings.runtime_dir = root / "runtime"
    synthetic.build(settings.data_dir)
    db.reset_connection(str(root / "t.db"))
    registry.invalidate()
    pipeline.bootstrap(force=True)
    yield root
    settings.data_dir, settings.runtime_dir = saved

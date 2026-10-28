"""Записанные ответы модели работают без ключа."""
import json

from app import llm
from app.config import settings


def test_replay_without_key(tmp_path, monkeypatch):
    msgs = [{"role": "system", "content": "s"}, {"role": "user", "content": "тихий номер"}]
    (tmp_path / "llm_cache.json").write_text(json.dumps({llm._ckey(msgs): '{"intents": []}'}), encoding="utf-8")
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    monkeypatch.setattr(settings, "runtime_dir", tmp_path / "rt")
    monkeypatch.setattr(settings, "llm_api_key", "")
    monkeypatch.setattr(llm, "_loaded", False)
    monkeypatch.setattr(llm, "_replay", {})
    monkeypatch.setattr(llm, "_cache", {})
    assert not llm.available() and llm.usable()
    assert llm.chat(msgs) == '{"intents": []}'
    assert llm.chat([{"role": "user", "content": "новый"}]) is None     # нет ключа и нет записи → правила


def test_live_answer_is_recorded(tmp_path, monkeypatch):
    class R:
        def raise_for_status(self): pass
        def json(self): return {"choices": [{"message": {"content": "<think>x</think>ответ"}}]}
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    monkeypatch.setattr(settings, "runtime_dir", tmp_path / "rt")
    monkeypatch.setattr(settings, "llm_api_key", "k")
    monkeypatch.setattr(llm, "_loaded", False)
    monkeypatch.setattr(llm, "_replay", {})
    monkeypatch.setattr(llm.requests, "post", lambda *a, **k: R())
    m = [{"role": "user", "content": "q"}]
    assert llm.chat(m) == "ответ"
    assert json.loads((tmp_path / "rt" / "llm_cache.json").read_text(encoding="utf-8"))[llm._ckey(m)] == "ответ"

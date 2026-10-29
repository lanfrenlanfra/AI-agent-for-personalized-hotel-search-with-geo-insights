"""GigaChat как провайдер модели: сеть подменена поддельным сервером, повторяющим протокол Сбера."""
import base64
import json
import time

import pytest

from app import gigachat, llm
from app.config import settings


class FakeResp:
    def __init__(self, status=200, data=None):
        self.status_code, self._data = status, data or {}
        self.text = json.dumps(self._data, ensure_ascii=False)

    def json(self):
        return self._data

    def raise_for_status(self):
        if self.status_code >= 400:
            import requests
            raise requests.HTTPError(f"{self.status_code}", response=self)


class FakeSber:
    """Ведёт себя как API GigaChat: oauth → токен, /files → id, /chat/completions, /models."""

    def __init__(self, new_base_404=False, first_429=False, expire_after_first=False):
        self.calls, self.uploads, self.chats = [], [], []
        self.tokens = 0
        self.new_base_404, self.first_429, self.expire_after_first = new_base_404, first_429, expire_after_first
        self.valid = set()

    def post(self, url, **kw):                                      # только oauth идёт через requests.post
        self.calls.append(("POST", url))
        assert kw["headers"]["Authorization"] == "Basic S0VZ" and kw["headers"]["RqUID"] and kw["data"] == {"scope": "GIGACHAT_API_PERS"}
        self.tokens += 1
        tok = f"tok{self.tokens}"
        self.valid = {tok}
        return FakeResp(200, {"access_token": tok, "expires_at": int((time.time() + 1800) * 1000)})   # миллисекунды, как на практике

    def request(self, method, url, **kw):
        self.calls.append((method, url))
        tok = kw["headers"]["Authorization"].split()[-1]
        if tok not in self.valid:
            return FakeResp(401, {"message": "Token has expired"})
        if self.new_base_404 and url.startswith("https://api.giga.chat") and url.endswith(("/chat/completions", "/models")):
            return FakeResp(404, {"message": "not found"})
        if url.endswith("/files"):
            name, raw, mime = kw["files"]["file"]
            assert kw["data"] == {"purpose": "general"} and mime == "image/jpeg" and raw.startswith(b"\xff\xd8")
            self.uploads.append(name)
            return FakeResp(200, {"id": "file-123", "object": "file"})
        if url.endswith("/models"):
            return FakeResp(200, {"data": [{"id": "GigaChat-2"}, {"id": "GigaChat-2-Max"}]})
        if url.endswith("/chat/completions"):
            if self.first_429 and not self.chats:
                self.chats.append("429")
                return FakeResp(429, {"message": "Too many requests"})
            body = kw["json"]
            self.chats.append(body)
            if self.expire_after_first:
                self.valid = set()                                   # токен «протух» сразу после ответа
            has_img = any(m.get("attachments") for m in body["messages"])
            return FakeResp(200, {"choices": [{"message": {"content": "На фото озеро" if has_img else "готово"}}]})
        return FakeResp(404)


@pytest.fixture
def sber(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "llm_provider", "gigachat")
    monkeypatch.setattr(settings, "gigachat_auth_key", "S0VZ")
    monkeypatch.setattr(settings, "gigachat_base_url", "https://api.giga.chat/v1")
    monkeypatch.setattr(settings, "runtime_dir", tmp_path)
    monkeypatch.setattr(gigachat, "_token", ("", 0.0))
    monkeypatch.setattr(gigachat, "_base", None)
    monkeypatch.setattr(gigachat.time, "sleep", lambda s: None)
    monkeypatch.setattr(llm, "_replay", {})
    monkeypatch.setattr(llm, "_loaded", True)
    fake = FakeSber()
    monkeypatch.setattr(gigachat.requests, "post", fake.post)
    monkeypatch.setattr(gigachat.requests, "request", fake.request)
    return fake


def _img():
    return "data:image/jpeg;base64," + base64.b64encode(b"\xff\xd8\xff\xe0fakejpeg").decode()


def test_text_chat_and_token_reuse(sber):
    assert settings.llm_enabled and llm.available()
    assert llm.chat([{"role": "user", "content": "привет"}]) == "готово"
    assert llm.chat([{"role": "user", "content": "ещё раз"}]) == "готово"
    assert sber.tokens == 1, "токен живёт 30 минут — второй раз не запрашиваем"
    body = sber.chats[0]
    assert body["model"] == "GigaChat-2" and body["temperature"] > 0 and body["messages"][0] == {"role": "user", "content": "привет"}


def test_image_goes_through_files_and_attachments(sber):
    msg = [{"role": "user", "content": [{"type": "text", "text": "Что на фото?"}, {"type": "image_url", "image_url": {"url": _img()}}]}]
    assert llm.chat(msg) == "На фото озеро"
    body = sber.chats[-1]
    assert sber.uploads and body["model"] == settings.gigachat_vision_model
    assert body["messages"][0] == {"role": "user", "content": "Что на фото?", "attachments": ["file-123"]}


def test_retries_on_429_and_refreshes_expired_token(sber):
    sber.first_429 = True
    assert llm.chat([{"role": "user", "content": "a"}]) == "готово"          # первый ответ 429 — подождали и повторили
    sber.expire_after_first = True
    assert llm.chat([{"role": "user", "content": "b"}]) == "готово"
    assert llm.chat([{"role": "user", "content": "c"}]) == "готово"          # 401 → новый токен → повтор
    assert sber.tokens == 2


def test_falls_back_to_old_address(sber):
    sber.new_base_404 = True
    assert llm.chat([{"role": "user", "content": "привет"}]) == "готово"
    assert gigachat._base == gigachat.OLD_BASE
    assert gigachat.probe() == "ready"


def test_failures_do_not_break_the_service(sber, monkeypatch):
    def down(*a, **k):
        import requests
        raise requests.ConnectionError("нет сети")
    monkeypatch.setattr(gigachat.requests, "post", down)
    assert llm.chat([{"role": "user", "content": "привет"}]) is None          # правила подхватят
    assert gigachat.probe() == "loading"


def test_frog_reads_photo_via_gigachat(sber):
    from app import frog
    sber_reply = '{"relevant": true, "what": "озеро и горы", "wishes": "Вид на озеро и горы, лето."}'
    orig = sber.request

    def answer_json(method, url, **kw):
        r = orig(method, url, **kw)
        if url.endswith("/chat/completions") and r.status_code == 200:
            return FakeResp(200, {"choices": [{"message": {"content": sber_reply}}]})
        return r
    gigachat.requests.request = answer_json
    d = frog.describe_image(_img())
    assert d == {"relevant": True, "what": "озеро и горы", "wishes": "Вид на озеро и горы, лето."}
    assert sber.uploads, "фото гостя ушло в GigaChat как файл"

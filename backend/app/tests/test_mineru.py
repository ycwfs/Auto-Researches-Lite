"""MinerU extraction client — v4 async task flow."""
from __future__ import annotations

import io
import zipfile


def test_mineru_v4_async_flow(monkeypatch) -> None:
    from app.integrations import mineru

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("full.md", "# Extracted\n\nbody from mineru")
    zip_bytes = buf.getvalue()

    class _R:
        def __init__(self, sc: int, payload=None, content: bytes = b"") -> None:
            self.status_code = sc
            self._p = payload
            self.content = content

        def json(self):
            return self._p

    def fake_req(method, url, **_kw):
        if method == "POST":
            return _R(200, {"code": 0, "data": {"task_id": "t1"}})  # submit → task_id
        if method == "GET" and url.endswith("/t1"):
            return _R(200, {"code": 0, "data": {"state": "done", "full_zip_url": "http://cdn/x.zip"}})
        if method == "GET" and "x.zip" in url:
            return _R(200, content=zip_bytes)  # download the result zip
        return _R(404, {})

    monkeypatch.setattr(mineru, "request_with_retry", fake_req)
    out = mineru._try_mineru(
        "http://example/p.pdf", api_key="k", api_url="https://mineru.net/api/v4/extract/task"
    )
    assert "body from mineru" in out  # markdown pulled from full.md in the zip


def test_mineru_v4_failed_task_returns_empty(monkeypatch) -> None:
    from app.integrations import mineru

    class _R:
        def __init__(self, sc, payload=None):
            self.status_code = sc
            self._p = payload

        def json(self):
            return self._p

    def fake_req(method, url, **_kw):
        if method == "POST":
            return _R(200, {"data": {"task_id": "t9"}})
        return _R(200, {"data": {"state": "failed", "err_msg": "bad pdf"}})

    monkeypatch.setattr(mineru, "request_with_retry", fake_req)
    assert mineru._try_mineru("http://x/p.pdf", api_key="k", api_url="https://m/api/v4/extract/task") == ""

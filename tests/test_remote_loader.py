import pytest

from grais.remote_loader import _get_cached


def test_cache_round_trip(monkeypatch):
    called = {"count": 0}

    class DummyResp:
        def __init__(self):
            self.headers = {"Last-Modified": "Mon, 01 Jan 2024 00:00:00 GMT"}

        def raise_for_status(self):
            pass

        def json(self):
            return {"regions": []}

    def fake_get(url, timeout):
        called["count"] += 1
        return DummyResp()

    monkeypatch.setattr("requests.get", fake_get)
    data1, meta1 = _get_cached("http://example.com", 5)
    data2, meta2 = _get_cached("http://example.com", 5)
    assert called["count"] == 1
    assert data1 == data2
    assert "last_updated" in meta1

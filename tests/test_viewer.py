from types import SimpleNamespace

from fastapi.testclient import TestClient

from civ_mcp.broadcast import Broadcast
from civ_mcp.web_api import create_app


def _client(b):
    return TestClient(create_app(SimpleNamespace(), b))


def test_broadcast_endpoint_pages(tmp_path):
    b = Broadcast(tmp_path)
    b.bind_game("x", "rome")
    [b.append("plan", f"p{i}", turn=1) for i in range(3)]
    r = _client(b).get("/api/broadcast?after=1").json()
    assert [x["text"] for x in r["rows"]] == ["p1", "p2"]
    assert r["next"] == 3 and r["game"] == "01-rome"


def test_broadcast_limit_and_empty(tmp_path):
    b = Broadcast(tmp_path)
    c = _client(b)
    r = c.get("/api/broadcast?after=0").json()
    assert r["rows"] == [] and r["next"] == 0 and r["game"] is None
    b.bind_game("x", "rome")
    [b.append("plan", f"p{i}", turn=1) for i in range(5)]
    r = c.get("/api/broadcast?after=0&limit=2").json()
    assert len(r["rows"]) == 2 and r["next"] == 2
    r = c.get("/api/broadcast?after=5").json()
    assert r["rows"] == [] and r["next"] == 5


def test_root_serves_viewer(tmp_path):
    r = _client(Broadcast(tmp_path)).get("/")
    assert r.status_code == 200 and "<title>" in r.text
    assert "/api/broadcast" in r.text and "/api/board" in r.text
    assert "/api/map" not in r.text


def test_board_endpoint(tmp_path):
    b = Broadcast(tmp_path)
    c = _client(b)
    assert c.get("/api/board").json() == {"text": None}
    b.set_board("TURN 5")
    assert c.get("/api/board").json() == {"text": "TURN 5"}

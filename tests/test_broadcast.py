"""Tests for the broadcast commentary log and its server wiring."""

import asyncio
import types

import pytest

from civ_mcp import server
from civ_mcp.broadcast import ACTION_TOOLS, KINDS, Broadcast


def test_bind_game_creates_numbered_dir(tmp_path):
    b = Broadcast(tmp_path)
    d = b.bind_game("abc123", "sumeria", "Gilgamesh")
    assert d.name == "01-sumeria" and (d / "meta.json").exists()
    assert Broadcast(tmp_path).bind_game("abc123", "sumeria") == d
    assert Broadcast(tmp_path).bind_game("def456", "nubia").name == "02-nubia"


def test_append_writes_md_and_jsonl_with_turn_headers(tmp_path):
    b = Broadcast(tmp_path)
    b.bind_game("x", "rome")
    b.append("plan", "Settle in place.", turn=1)
    b.append("reaction", "Barbarians east.", turn=2)
    md = (b.game_dir / "log.md").read_text()
    assert "## Turn 1" in md and "## Turn 2" in md and "[plan] Settle in place." in md
    assert len((b.game_dir / "log.jsonl").read_text().splitlines()) == 2
    assert "—" not in md


def test_unbound_append_goes_to_lobby(tmp_path):
    b = Broadcast(tmp_path)
    b.append("read", "At the menu.")
    assert "[read] At the menu." in (tmp_path / "lobby.md").read_text()
    assert (tmp_path / "lobby.jsonl").exists()


def test_tail_and_board(tmp_path):
    b = Broadcast(tmp_path)
    b.bind_game("x", "rome")
    for i in range(5):
        b.append("plan", f"p{i}", turn=1)
    rows = b.tail(after=3)
    assert [r["text"] for r in rows] == ["p3", "p4"] and rows[-1]["i"] == 5
    b.set_board("EMPIRE ...")
    assert b.last_board() == "EMPIRE ..."


def test_invalid_kind_rejected(tmp_path):
    with pytest.raises(ValueError):
        Broadcast(tmp_path).append("vibes", "no")


def test_action_text_format():
    t = Broadcast.action_text(
        "unit_action",
        {
            "unit_id": 3,
            "action": "move",
            "target_x": 12,
            "target_y": 8,
            "why": "scout river",
        },
        "OK: moved\nmore",
    )
    assert t == "unit_action move #3 -> (12,8): scout river => OK: moved"


def test_kinds_and_action_tools_contract():
    assert "action" in KINDS and len(KINDS) == 12
    assert "unit_action" in ACTION_TOOLS and "end_turn" not in ACTION_TOOLS
    assert "run_lua" not in ACTION_TOOLS and len(ACTION_TOOLS) == 30


def test_end_turn_requires_summary(monkeypatch, tmp_path):
    called = []
    monkeypatch.setattr(
        server, "execute_end_turn", lambda gs: called.append(1), raising=False
    )
    gs = types.SimpleNamespace(_high_water_turn=1)
    life = types.SimpleNamespace(game=gs, broadcast=Broadcast(tmp_path))
    ctx = types.SimpleNamespace(
        request_context=types.SimpleNamespace(lifespan_context=life)
    )
    fn = server.end_turn if callable(server.end_turn) else server.end_turn.fn
    out = asyncio.run(fn(ctx, summary="   "))
    assert out.startswith("Error: summary required") and called == []
    assert not (tmp_path / "lobby.md").exists()


def test_every_action_tool_has_why_param():
    import inspect

    for name in ACTION_TOOLS:
        fn = getattr(server, name)
        fn = fn if callable(fn) else fn.fn
        assert "why" in inspect.signature(fn).parameters, name


def test_event_text_block_and_fallback():
    report = "Turn 5 -> 6\nGold 10\n\n== Events ==\n  !!! Barbarians\n  -- Wonder built\n\n== Other ==\nx"
    assert Broadcast.event_text(report) == "!!! Barbarians | -- Wonder built"
    assert Broadcast.event_text("a\nb") == "a | b"


def test_narrate_logs_and_rejects_bad_kind(tmp_path):
    gs = types.SimpleNamespace(_high_water_turn=7)
    life = types.SimpleNamespace(game=gs, broadcast=Broadcast(tmp_path))
    ctx = types.SimpleNamespace(
        request_context=types.SimpleNamespace(lifespan_context=life)
    )
    fn = server.narrate if callable(server.narrate) else server.narrate.fn
    assert asyncio.run(fn(ctx, "vibes", "x")).startswith("Error")
    assert asyncio.run(fn(ctx, "plan", "Go east.")) == "logged"
    assert "[plan] Go east." in (tmp_path / "lobby.md").read_text()

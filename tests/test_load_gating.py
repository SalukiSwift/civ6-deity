"""Every save load needs a reason and is recorded and announced."""

import asyncio
import importlib

from civ_mcp import game_lifecycle
from civ_mcp.game_state import GameState


def _gs() -> GameState:
    gs = GameState.__new__(GameState)
    gs._save_load_history = []
    gs._high_water_turn = 12
    gs.on_load = None
    return gs


def test_record_load_appends_history_and_calls_on_load():
    gs = _gs()
    seen: list[tuple[str, str]] = []
    gs.on_load = lambda name, reason: seen.append((name, reason))
    gs.record_load("AutoSave_0012", "game crashed")
    assert [h[2] for h in gs._save_load_history] == ["AutoSave_0012"]
    assert gs._save_load_history[0][1] == 12
    assert seen == [("AutoSave_0012", "game crashed")]


def test_record_load_without_callback_still_records():
    gs = _gs()
    gs.record_load("x", "why")
    assert len(gs._save_load_history) == 1


def test_gamestate_load_game_save_records_with_reason(monkeypatch):
    gs = _gs()
    gs.conn = object()

    async def fake(conn, name):
        return "OK loaded"

    monkeypatch.setattr(game_lifecycle, "load_game_save", fake)
    seen = []
    gs.on_load = lambda n, r: seen.append((n, r))
    asyncio.run(gs.load_game_save("quick", "retry after crash"))
    assert seen == [("quick", "retry after crash")]


def _server():
    from civ_mcp import server

    return importlib.reload(server)


def test_load_game_save_tool_refuses_empty_reason(monkeypatch):
    server = _server()
    called = []

    async def fake(conn, name):
        called.append(name)
        return "OK"

    monkeypatch.setattr(game_lifecycle, "load_game_save", fake)
    for reason in ("", "   "):
        out = asyncio.run(server.load_game_save(None, "x", reason=reason))
        assert out.startswith("Error:")
    assert called == []


def test_other_load_tools_refuse_empty_reason():
    server = _server()
    assert asyncio.run(server.load_save(None, 1, "")).startswith("Error:")
    assert asyncio.run(server.load_save_from_menu(None, "")).startswith("Error:")
    assert asyncio.run(server.restart_and_load(None, "")).startswith("Error:")


def test_load_tools_require_reason_in_schema():
    server = _server()
    tools = {t.name: t for t in server.mcp._tool_manager.list_tools()}
    for name in ("load_save", "load_game_save", "load_save_from_menu", "restart_and_load"):
        assert "reason" in tools[name].parameters["required"], name


def test_launcher_load_ok_heuristic():
    server = _server()
    assert server._launcher_load_ok("Kill: ok | Load: done")
    assert not server._launcher_load_ok("Kill | ABORTED: Game failed to launch.")
    assert not server._launcher_load_ok("Save 'x' not found. Available: none")


def test_launcher_load_ok_recognises_failed_forms():
    server = _server()
    assert not server._launcher_load_ok("FAILED: Could not find 'Load Game' button.")
    assert not server._launcher_load_ok(
        "Kill: ok | Launch: ok | Load: FAILED: Save 'x' not found. Steps completed: a"
    )
    assert not server._launcher_load_ok("Error: nope")


def test_on_load_broadcasts_via_record_load():
    gs = _gs()
    entries = []
    gs.on_load = lambda n, r: entries.append(f"loaded {n}: {r}")
    gs.record_load("s", "r")
    assert entries == ["loaded s: r"]


def test_lifespan_sets_on_load_before_auto_boot():
    import inspect

    server = _server()
    src = inspect.getsource(server.lifespan)
    assert src.index("gs.on_load = _announce_load") < src.index("await _auto_boot(")


def test_restart_and_load_checks_identity_instead_of_retrying():
    """A wrong game is a loud error now (Task 13 fix 4c), not a blind retry
    of the same save."""
    import inspect

    server = _server()
    src = inspect.getsource(server.restart_and_load)
    assert "(retry: wrong game loaded)" not in src
    assert "_finish_load(" in src and "_recovery_save(" in src

import asyncio
import json
import types
from pathlib import Path

from civ_mcp import server
from civ_mcp.broadcast import ACTION_TOOLS, Broadcast
from civ_mcp.newgame import (
    GAME_SETTINGS,
    build_frontend_setup_lua,
    settings_card,
    valid_leader,
)

ROOT = Path(__file__).parents[1]


def test_settings_card_lists_all_five_and_leader():
    c = settings_card("LEADER_GILGAMESH")
    assert "Deity" in c and "Fractal" in c and "Standard" in c and "Gilgamesh" in c
    assert "Gathering Storm" in c


def test_setup_lua_contains_settings():
    lua = build_frontend_setup_lua("LEADER_GILGAMESH")
    assert "RULESET_EXPANSION_2" in lua and "Fractal.lua" in lua
    assert "LEADER_GILGAMESH" in lua and "Network.HostGame" in lua
    assert 'DB.MakeHash("DIFFICULTY_DEITY")' in lua
    assert '"GAME_HANDICAP"' in lua and '"GAME_SPEED_TYPE"' in lua
    assert 'MapConfiguration.SetValue("MAP_SIZE"' in lua
    assert 'MapConfiguration.SetScript("Fractal.lua")' in lua
    assert "SetLeaderTypeName" in lua


def test_setup_lua_sets_before_building_headless_setup():
    lua = build_frontend_setup_lua("LEADER_GILGAMESH")
    build = lua.index("BuildHeadlessGameSetup()")
    for s in ("SetRuleSet", "GAME_HANDICAP", "SetScript", "SetLeaderTypeName"):
        assert lua.index(s) < build
    assert lua.index("SetToDefaults") < lua.index("SetRuleSet")
    assert build < lua.index("RebuildPlayerParameters(true)")
    assert lua.index("GameSetup_RefreshParameters()") < lua.index("ReleasePlayerParameters()")
    assert lua.index("ReleasePlayerParameters()") < lua.index("Network.HostGame")


def test_setup_lua_rejects_injection():
    import pytest

    with pytest.raises(ValueError):
        build_frontend_setup_lua('LEADER_X"); os.exit() --')


def test_settings_values():
    assert GAME_SETTINGS["difficulty"] == "DIFFICULTY_DEITY"
    assert len(GAME_SETTINGS) == 5


def test_valid_leader():
    assert valid_leader("LEADER_GILGAMESH") and not valid_leader("LEADER_BOGUS")
    assert not valid_leader("") and not valid_leader("gilgamesh")


def test_leaders_file_excludes_non_playable():
    names = (ROOT / "docs" / "leaders.txt").read_text().split()
    assert names == sorted(set(names)) and len(names) > 60
    for bad in ("LEADER_DEFAULT", "LEADER_BARBARIAN", "LEADER_FREE_CITIES"):
        assert bad not in names
    assert not any(n.startswith("LEADER_MINOR_CIV_") or "SCENARIO" in n for n in names)
    assert (ROOT / "src/civ_mcp/data/leaders.txt").read_text() == (
        ROOT / "docs" / "leaders.txt"
    ).read_text()


def test_pending_leader_consumed_by_bind_game(tmp_path):
    b = Broadcast(tmp_path)
    b.pending_leader = "LEADER_GILGAMESH"
    d = b.bind_game("abc", "sumeria")
    assert json.loads((d / "meta.json").read_text())["leader"] == "LEADER_GILGAMESH"
    assert b.pending_leader is None
    b2 = Broadcast(tmp_path / "x")
    b2.pending_leader = "LEADER_CYRUS"
    d2 = b2.bind_game("def", "persia", "Explicit")
    assert json.loads((d2 / "meta.json").read_text())["leader"] == "Explicit"


def _ctx(bc):
    life = types.SimpleNamespace(broadcast=bc)
    return types.SimpleNamespace(request_context=types.SimpleNamespace(lifespan_context=life))


def test_new_game_tool(tmp_path):
    bc = Broadcast(tmp_path)
    out = asyncio.run(server.new_game(_ctx(bc), "LEADER_GILGAMESH", "fixed opening"))
    assert "Gilgamesh" in out and "get_game_overview" in out
    assert bc.pending_leader == "LEADER_GILGAMESH"
    row = json.loads((tmp_path / "lobby.jsonl").read_text().splitlines()[-1])
    assert row["kind"] == "game" and "LEADER_GILGAMESH" in row["text"]
    assert "RULESET_EXPANSION_2" in json.dumps(row["meta"])


def test_new_game_rejects_unknown_leader(tmp_path):
    bc = Broadcast(tmp_path)
    out = asyncio.run(server.new_game(_ctx(bc), "LEADER_BOGUS", "x"))
    assert out.startswith("Error: unknown leader") and "docs/leaders.txt" in out
    assert bc.pending_leader is None


def test_new_game_not_an_action_tool():
    assert "new_game" not in ACTION_TOOLS


def test_fairness_row():
    assert "`new_game`" in (ROOT / "docs" / "FAIRNESS.md").read_text()

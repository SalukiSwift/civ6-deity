"""Mutations must use the player's own commands in the InGame state."""

import asyncio
import importlib
import inspect

from civ_mcp import end_turn
from civ_mcp.game_state import GameState
from civ_mcp.lua import governance, tech
from civ_mcp.lua.cities import build_cities_query
from civ_mcp.lua.diplomacy import build_propose_trade
from civ_mcp.lua.governance import build_promote_unit
from civ_mcp.lua.map import build_map_area_query
from civ_mcp.lua.units import (
    build_skip_remaining_units,
    build_skip_unit,
    build_units_query,
)


class _Stub:
    def __init__(self, write_lines=(), read_lines=()):
        self._write = list(write_lines)
        self._read = list(read_lines)
        self.read_calls: list[str] = []
        self.write_calls: list[str] = []

    async def execute_write(self, lua):
        self.write_calls.append(lua)
        return self._write.pop(0) if self._write else ["OK:DONE"]

    async def execute_read(self, lua):
        self.read_calls.append(lua)
        return self._read.pop(0) if self._read else ["OK:DONE"]


def _gs(stub) -> GameState:
    gs = GameState.__new__(GameState)
    gs.conn = stub
    return gs


def test_skip_uses_stock_operation_not_finishmoves():
    for lua in (build_skip_unit(5), build_skip_remaining_units()):
        assert "FinishMoves" not in lua
        assert "UNITOPERATION_SKIP_TURN" in lua
        assert "UnitManager.RequestOperation" in lua


def test_promote_uses_stock_command_and_never_heals():
    lua = build_promote_unit(5, "PROMOTION_BATTLECRY")
    assert "UnitCommandTypes.PROMOTE" in lua
    assert "UnitManager.RequestCommand" in lua
    assert "PARAM_PROMOTION_TYPE" in lua
    for banned in ("SetDamage", "SetPromotion", "ChangeStoredPromotions"):
        assert banned not in lua
    assert "Players[me]:GetUnits():FindID" not in lua  # InGame lookup


def test_set_research_has_no_gamecore_fallback():
    assert not hasattr(tech, "_build_set_gamecore")
    assert not hasattr(tech, "build_set_research_gamecore")
    assert not hasattr(tech, "build_set_civic_gamecore")


def test_skip_and_promote_go_through_execute_write():
    stub = _Stub(write_lines=[["OK:SKIPPED"], ["OK:PROMOTED|X"]])
    gs = _gs(stub)
    asyncio.run(gs.skip_unit(1))
    asyncio.run(gs.promote_unit(1, "PROMOTION_X"))
    asyncio.run(gs.skip_remaining_units())
    assert stub.read_calls == []
    assert len(stub.write_calls) == 4  # skip, promote, fortify sweep, skip sweep


def test_set_research_mismatch_is_an_error_not_a_gamecore_write():
    stub = _Stub(write_lines=[["OK:RESEARCHING|TECH_MINING"]], read_lines=[["MISMATCH:1~=2"]])
    result = asyncio.run(_gs(stub).set_research("TECH_MINING"))
    assert result.startswith("Error")
    # only the verification read happened, and it contains no setter
    assert len(stub.read_calls) == 1
    assert "SetResearchingTech" not in stub.read_calls[0]


def test_set_civic_mismatch_is_an_error():
    stub = _Stub(write_lines=[["OK:PROGRESSING|CIVIC_X"]], read_lines=[["MISMATCH:1~=2"]])
    result = asyncio.run(_gs(stub).set_civic("CIVIC_X"))
    assert result.startswith("Error")
    assert len(stub.read_calls) == 1


def test_run_lua_absent_by_default(monkeypatch):
    from civ_mcp import server

    monkeypatch.delenv("CIV_MCP_DEV_LUA", raising=False)
    server = importlib.reload(server)
    assert "run_lua" not in [t.name for t in server.mcp._tool_manager.list_tools()]


def test_run_lua_present_with_dev_flag(monkeypatch):
    from civ_mcp import server

    monkeypatch.setenv("CIV_MCP_DEV_LUA", "1")
    server = importlib.reload(server)
    try:
        assert "run_lua" in [t.name for t in server.mcp._tool_manager.list_tools()]
    finally:
        monkeypatch.delenv("CIV_MCP_DEV_LUA", raising=False)
        importlib.reload(server)


def test_read_queries_do_not_mutate():
    for lua in (build_cities_query(), build_units_query(), build_map_area_query(1, 1, 1)):
        assert "RemoveAt" not in lua and "Request" not in lua


def test_propose_trade_send_does_not_auto_accept():
    lua = build_propose_trade(
        3, [{"type": "GOLD", "amount": 10}], [{"type": "GOLD", "amount": 20}]
    )
    assert "DealProposalAction.ACCEPTED" not in lua
    assert "OK:COUNTER:" in lua


def test_propose_trade_counter_leaves_session_open():
    stub = _Stub(write_lines=[["OK:COUNTER: Rome countered. They give: 5 gold."]])
    result = asyncio.run(
        _gs(stub).propose_trade(3, [{"type": "GOLD", "amount": 10}], [])
    )
    assert result.startswith("COUNTER:")
    assert len(stub.write_calls) == 1  # no UI-dismiss follow-up


def test_blocker_handlers_do_not_auto_keep_cities():
    src = inspect.getsource(end_turn)
    assert 'resolve_city_capture("keep")' not in src
    assert "Auto-kept" not in src


def test_end_turn_has_no_gamecore_mutations():
    src = inspect.getsource(end_turn)
    assert "UnitManager.FinishMoves" not in src
    assert "exp:ChangeStoredPromotions" not in src


def test_city_attack_unseen_target_is_not_reported_killed():
    stub = _Stub(write_lines=[["OK:CITY_RANGE_ATTACK|pre_hp:30"], ["UNSEEN"]])
    result = asyncio.run(_gs(stub).city_attack(1, 5, 5))
    assert "outcome unknown" in result
    assert "killed" not in result and "eliminated" not in result.lower()


def test_governance_module_has_no_setpromotion():
    assert "SetPromotion" not in inspect.getsource(governance.build_promote_unit)


def test_fairness_doc_lists_every_registered_tool():
    import re
    from pathlib import Path

    from civ_mcp import server

    doc = (Path(__file__).parents[1] / "docs" / "FAIRNESS.md").read_text(
        encoding="utf-8"
    )
    documented = set(re.findall(r"^\| `(\w+)` \|", doc, flags=re.M))
    registered = {t.name for t in server.mcp._tool_manager.list_tools()}
    assert registered - documented == set()
    assert "—" not in doc

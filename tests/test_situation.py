"""Tests for the situation board."""

import asyncio
import types

import pytest

from civ_mcp import lua as lq
from civ_mcp.lua.models import (
    CityInfo,
    GameOverview,
    RivalRow,
    ThreatInfo,
    TurnEvent,
    UnitInfo,
)
from civ_mcp.situation import Situation, format_situation, gather_situation


def _ov(**kw):
    base = dict(
        turn=42, player_id=0, civ_name="Sumeria", leader_name="Gilgamesh",
        gold=120.0, gold_per_turn=7.0, science_yield=12.0, culture_yield=5.0,
        faith=3.0, current_research="TECH_WRITING", current_civic="CIVIC_CRAFTSMANSHIP",
        num_cities=2, num_units=4, score=88, era_name="Classical",
    )
    base.update(kw)
    return GameOverview(**base)


def _unit(uid, moves=2.0, busy=False, promo=False):
    return UnitInfo(
        unit_id=uid, unit_index=uid, name="u", unit_type="UNIT_WARRIOR", x=1, y=1,
        moves_remaining=moves, max_moves=2.0, health=100, max_health=100,
        needs_promotion=promo, is_busy=busy,
    )


def _city(name, building="BUILDING_MONUMENT"):
    return CityInfo(
        city_id=1, name=name, x=0, y=0, population=3, food=2, production=3, gold=1,
        science=1, culture=1, faith=0, housing=3, amenities=1, turns_to_grow=5,
        currently_building=building,
    )


def _rival(name, score, met=True):
    return RivalRow(player_id=1, name=name, met=met, score=score, techs=10, military_strength=50)


def _situation(**kw):
    base = dict(
        overview=_ov(),
        units=[_unit(3), _unit(7), _unit(9, busy=True), _unit(11, moves=0.0)],
        cities=[_city("Uruk"), _city("Ur", building="NONE")],
        threats=[ThreatInfo("UNIT_HORSEMAN", 4, 5, 90, 100, 36, 0, 3, owner_name="Barbarian")],
        rivals=[_rival("Egypt", 50), _rival("Rome", 120), _rival("Hidden", 999, met=False)],
        blockers=["Choose a pantheon"],
        victory_warnings=[TurnEvent(1, "victory", "!! DIPLOMATIC VICTORY THREAT: Rome has 15/20 DVP!")],
        trade_capacity=(1, 2),
        envoys_available=2,
        governor_titles=1,
    )
    base.update(kw)
    return Situation(**base)


def _empty():
    return _situation(
        units=[], cities=[_city("Uruk")], threats=[], rivals=[], blockers=[],
        victory_warnings=[], trade_capacity=(0, 0), envoys_available=0, governor_titles=0,
    )


def test_format_sections_and_limit():
    out = format_situation(_situation())
    lines = out.splitlines()
    assert lines[0].startswith("EMPIRE")
    for h in ("ATTENTION", "THREATS", "RIVALS"):
        assert h in lines
    assert len(lines) <= 40
    assert "—" not in out


def test_idle_units_and_empty_production_listed():
    out = format_situation(_situation())
    assert "idle units: 3, 7" in out
    assert "no production: Ur" in out
    assert "Uruk" not in out.split("no production:")[1].splitlines()[0]
    assert "envoys to place: 2" in out
    assert "governor titles: 1" in out
    assert "trade routes: 1/2" in out
    assert "pending choice: Choose a pantheon" in out


def test_research_not_set_and_promotions():
    out = format_situation(_situation(
        overview=_ov(current_research="NONE"),
        units=[_unit(5, promo=True, busy=True)],
    ))
    assert "research/civic not set" in out
    assert "promotions available: 5" in out
    ok = format_situation(_situation())
    assert "research/civic not set" not in ok


def test_empty_sections_say_none():
    out = format_situation(_empty())
    assert "THREATS\n  none" in out
    assert "RIVALS\n  none" in out
    assert "ATTENTION\n  none" in out


def test_trade_routes_hidden_when_full_or_no_capacity():
    assert "trade routes" not in format_situation(_situation(trade_capacity=(2, 2)))


def test_rivals_sorted_by_score_and_warning_verbatim():
    out = format_situation(_situation())
    assert out.index("Rome") < out.index("Egypt")
    assert "Hidden" not in out
    assert "!! DIPLOMATIC VICTORY THREAT: Rome has 15/20 DVP!" in out


def test_limit_truncates_least_important_with_more_line():
    rivals = [_rival(f"Civ{i}", i) for i in range(60)]
    out = format_situation(_situation(rivals=rivals))
    lines = out.splitlines()
    assert len(lines) <= 40
    assert any(l.strip().startswith("+") and "more" in l for l in lines)
    assert "Civ59" in out  # highest scores kept
    assert "!! DIPLOMATIC VICTORY THREAT" in out


def test_parse_units_is_busy_field():
    base = "5|5|Warrior|UNIT_WARRIOR|3,4|2/2|100/100|20|0|0||0|0||0||"
    assert lq.parse_units_response([base + "|1"])[0].is_busy is True
    assert lq.parse_units_response([base + "|0"])[0].is_busy is False
    assert lq.parse_units_response([base])[0].is_busy is False


def test_units_query_emits_busy_flag():
    assert "GetActivityType" in lq.build_units_query()


def test_gather_situation_uses_existing_queries():
    gs = types.SimpleNamespace()

    async def ov(): return _ov()
    async def units(): return [_unit(3)]
    async def cities(): return ([_city("Ur", "NONE")], [])
    async def threats(): return []
    async def rivals(): return [_rival("Rome", 5)]
    async def trade(): return types.SimpleNamespace(capacity=3, active_count=1)
    async def cs(): return types.SimpleNamespace(tokens_available=4)
    async def gov(): return types.SimpleNamespace(points_available=2)

    async def exec_write(lua):
        return ["BLOCKING|ENDTURN_BLOCKING_UNITS|Units", "BLOCKING|ENDTURN_BLOCKING_PANTHEON|Choose Pantheon"]

    gs.get_game_overview = ov
    gs.get_units = units
    gs.get_cities = cities
    gs.get_threat_scan = threats
    gs.get_rival_snapshot = rivals
    gs.get_trade_routes = trade
    gs.get_city_states = cs
    gs.get_governors = gov
    gs.conn = types.SimpleNamespace(execute_write=exec_write)

    async def victory(g): return []
    import civ_mcp.situation as sit
    orig = sit._victory_warnings
    sit._victory_warnings = victory
    try:
        s = asyncio.run(gather_situation(gs))
    finally:
        sit._victory_warnings = orig
    assert s.trade_capacity == (1, 3) and s.envoys_available == 4 and s.governor_titles == 2
    assert s.blockers == ["Choose Pantheon"]


def test_gather_survives_failing_query():
    gs = types.SimpleNamespace()
    async def boom(*a, **k): raise RuntimeError("x")
    async def ov(): return _ov()
    for n in ("get_units", "get_cities", "get_threat_scan", "get_rival_snapshot",
              "get_trade_routes", "get_city_states", "get_governors"):
        setattr(gs, n, boom)
    gs.get_game_overview = ov
    gs.conn = types.SimpleNamespace(execute_write=boom)
    s = asyncio.run(gather_situation(gs))
    assert "EMPIRE" in format_situation(s)


def test_get_situation_tool_registered_readonly():
    from civ_mcp import server
    tools = {t.name: t for t in server.mcp._tool_manager.list_tools()}
    assert tools["get_situation"].annotations.readOnlyHint is True

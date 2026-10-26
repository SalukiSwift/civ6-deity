"""Tests for the broadcast commentary log and its server wiring."""

import json
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
    assert t == "unit #3 move -> (12,8): scout river => moved"
    labelled = Broadcast.action_text(
        "unit_action",
        {"unit_id": 131073, "action": "move", "target_x": 62, "target_y": 36, "why": "scout"},
        "MOVING_TO|62,36|from:59,36|now_at:60,36|(moved dx:+1 dy:+0)|STOPPED_MID_PATH (moves exhausted)",
        unit_label="Warrior #131073",
    )
    assert labelled == (
        "Warrior #131073 move -> (62,36): scout => moved to (60,36), still 2 tiles from (62,36)"
    )


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
    assert Broadcast.event_text("a\nb") == "quiet turn"


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


def _bound_ctx(tmp_path, identity_box, turn=7):
    async def get_game_identity():
        return identity_box[0]

    gs = types.SimpleNamespace(
        _high_water_turn=turn, get_game_identity=get_game_identity
    )
    life = types.SimpleNamespace(game=gs, broadcast=Broadcast(tmp_path))
    return types.SimpleNamespace(
        request_context=types.SimpleNamespace(lifespan_context=life)
    )


def test_logged_action_binds_hashed_identity_and_logs_turn(tmp_path):
    ctx = _bound_ctx(tmp_path, [("civilization_rome", 12345)])
    ctx.request_context.lifespan_context.logger = types.SimpleNamespace(
        _turn=7, log_tool_call=_noop, log_error=_noop
    )
    ctx.request_context.lifespan_context.spatial = types.SimpleNamespace(record=_noop)

    async def fn():
        return "OK: moved\nmore"

    out = asyncio.run(
        server._logged(
            ctx,
            "unit_action",
            {"unit_id": 3, "action": "fortify", "why": "hold ridge"},
            fn,
        )
    )
    assert out.startswith("OK")
    d = tmp_path / "games" / "01-rome"
    md = (d / "log.md").read_text()
    assert "## Turn 7" in md
    assert "[action] unit #3 fortify: hold ridge => moved" in md
    row = json.loads((d / "log.jsonl").read_text().splitlines()[-1])
    assert row["meta"]["result"] == "OK: moved\nmore"  # raw result kept
    for p in tmp_path.rglob("*"):
        if p.is_file():
            assert "12345" not in p.read_text(), p


def test_rebind_on_identity_change(tmp_path):
    box = [("rome", 1)]
    ctx = _bound_ctx(tmp_path, box)
    asyncio.run(server._bind_broadcast(ctx))
    box[0] = ("nubia", 2)
    bc = asyncio.run(server._bind_broadcast(ctx))
    assert bc.game_dir.name == "02-nubia"
    assert "[game] new game detected" in (bc.game_dir / "log.md").read_text()


async def _noop(*a, **k):
    return None



# --- humanize_result (rehearsal round 2, D11b) -------------------------------

from civ_mcp.broadcast import game_name, hex_distance, humanize_result  # noqa: E402


def _h(result, tool="unit_action", **params):
    return humanize_result(tool, params, result)


def test_humanize_move_mid_path_and_arrival():
    raw = "OK MOVING_TO|62,36|from:59,36|now_at:60,36|(moved dx:+1 dy:+0)|STOPPED_MID_PATH (moves exhausted)"
    assert _h(raw) == "moved to (60,36), still 2 tiles from (62,36)"
    assert _h("MOVING_TO|61,36|from:59,36|now_at:61,36|(moved dx:+2 dy:+0)") == "moved to (61,36)"


def test_humanize_found_city_uses_city_name():
    assert _h("OK FOUNDED|59,35|Uruk") == "founded Uruk at (59,35)"
    assert _h("FOUNDED|59,35") == "founded a city at (59,35)"


def test_humanize_fortify_production_research_civic():
    assert _h("OK FORTIFIED") == "fortified"
    assert _h("PRODUCING|BUILDING_MONUMENT|5 turns", "set_city_production") == (
        "now building Monument (5 turns)"
    )
    assert _h("RESEARCHING|TECH_POTTERY", "set_research") == "researching Pottery"
    assert _h("PROGRESSING|CIVIC_CODE_OF_LAWS", "set_research") == "civic: Code of Laws"
    assert _h("PURCHASED|UNIT_SCOUT|cost=120g (had 200g)", "purchase_item") == (
        "bought Scout for 120 gold"
    )


def test_humanize_attacks():
    melee = (
        "Combat Estimate (Melee):\n  WARRIOR vs BARBARIAN\n"
        "MELEE_ATTACK|target:UNIT_WARRIOR at (12,8)|enemy HP:100 -> 40/100|your HP:100 -> 80 CS:20"
    )
    assert _h(melee) == "attacked (12,8): enemy at 40 HP"
    killed = "MELEE_ATTACK|target:UNIT_SCOUT at (12,8)|enemy HP:30 -> KILLED|your HP:100 -> 95 CS:20"
    assert _h(killed) == "attacked (12,8): enemy destroyed"
    ranged = (
        "RANGE_ATTACK|target:UNIT_WARRIOR at (5,6)|pre_hp:100/100|your HP:100|range:2 dist:2"
        "|est damage dealt:~30\n  Post-combat: ~70/100 (estimate)"
    )
    assert _h(ranged) == "attacked (5,6): enemy at about 70 HP"


def test_humanize_blocked_and_refused():
    blocked = (
        "FAIL BLOCKED: water tile - land units need Shipbuilding tech to embark "
        "(unit still at (62,36), target (63,38))"
    )
    assert _h(blocked) == "blocked: water tile"
    assert _h("FAIL Error: CANNOT_SLEEP|Unit cannot sleep") == "refused: Unit cannot sleep"


def test_humanize_fallback_is_first_line():
    assert _h("PROMOTED|PROMOTION_BATTLECRY\nmore") == "PROMOTED|PROMOTION_BATTLECRY"
    assert _h("OK something new\nsecond") == "something new"
    assert _h("FAIL no path") == "failed: no path"
    assert _h("") == ""


def test_game_name_and_hex_distance():
    assert game_name("CIVIC_CODE_OF_LAWS") == "Code of Laws"
    assert game_name("UNIT_SUMERIAN_WAR_CART") == "Sumerian War Cart"
    assert game_name("BUILDING_MONUMENT") == "Monument"
    # Engine neighbours of (28,29): NE (29,30), NW (28,30), SW (28,28).
    assert hex_distance(28, 29, 29, 30) == 1
    assert hex_distance(28, 29, 28, 30) == 1
    assert hex_distance(28, 29, 28, 28) == 1
    assert hex_distance(28, 29, 27, 30) == 2
    assert hex_distance(60, 36, 62, 36) == 2


def test_found_city_line_names_city_and_settler():
    t = Broadcast.action_text(
        "unit_action",
        {"unit_id": 0, "action": "found_city", "why": "capital in place"},
        "OK FOUNDED|59,35|Uruk",
        unit_label="Settler #65536",
    )
    assert t == "Settler #65536 found city: capital in place => founded Uruk at (59,35)"


def test_research_and_production_lines():
    assert Broadcast.action_text(
        "set_research", {"tech_or_civic": "TECH_POTTERY", "category": "tech", "why": "granary"},
        "RESEARCHING|TECH_POTTERY",
    ) == "research: granary => researching Pottery"
    assert Broadcast.action_text(
        "set_city_production", {"city_id": 65536, "item_name": "UNIT_SCOUT", "why": "explore"},
        "PRODUCING|UNIT_SCOUT|4 turns",
    ) == "city #65536 production: explore => now building Scout (4 turns)"

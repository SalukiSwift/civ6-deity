"""Tests for batch_orders parsing, execution and formatting."""

import json
import asyncio
from types import SimpleNamespace

from civ_mcp.connection import LuaError
from civ_mcp.orders import (
    format_results,
    item_type_of,
    parse_orders,
    run_orders,
)


async def _noop(order, res):
    return None


def _gs(**over):
    calls = []

    def rec(name, fail=None):
        async def f(*a):
            calls.append((name, a))
            if fail:
                raise fail
            return f"{name} done"

        return f

    gs = SimpleNamespace(
        move_unit=rec("move"),
        fortify_unit=rec("fortify"),
        set_city_production=rec("prod"),
        purchase_item=rec("buy"),
        set_research=rec("research"),
        set_civic=rec("civic"),
        calls=calls,
    )
    for k, v in over.items():
        setattr(gs, k, v)
    return gs


def test_parse_valid_and_invalid_mixed():
    orders, errs = parse_orders(
        [
            {"unit_id": 3, "action": "move", "x": 1, "y": 2},
            {"action": "move"},
            {"research": "TECH_MINING"},
        ]
    )
    assert [o.kind for o in orders] == ["unit", "research"]
    assert [o.index for o in orders] == [1, 3]
    assert errs == ["#2: unit order needs unit_id"]


def test_parse_every_kind():
    orders, errs = parse_orders(
        [
            {"unit_id": 1, "action": "improve", "improvement": "IMPROVEMENT_FARM"},
            {"city_id": 2, "production": "DISTRICT_CAMPUS", "x": 4, "y": 5},
            {"city_id": 2, "purchase": "BUILDING_MONUMENT"},
            {"civic": "CIVIC_CRAFTSMANSHIP", "why": "culture"},
        ]
    )
    assert errs == []
    assert [o.kind for o in orders] == ["unit", "production", "purchase", "civic"]
    assert orders[3].why == "culture"


def test_parse_rejects_bad_orders():
    _, errs = parse_orders(
        [
            {"unit_id": 1, "action": "move"},
            {"unit_id": 1, "action": "dance"},
            {"unit_id": 1, "action": "improve"},
            {"city_id": 1},
            {"city_id": 1, "production": "FOO"},
            {},
        ]
    )
    assert len(errs) == 6 and errs[0].startswith("#1:") and errs[5].startswith("#6:")


def test_item_type_of():
    assert item_type_of("WONDER_PYRAMIDS") == "BUILDING"
    assert item_type_of("unit_scout") == "UNIT"
    assert item_type_of("PROJECT_X") == "PROJECT"


def test_run_continues_past_failures():
    gs = _gs(move_unit=_gs().move_unit)

    async def boom(*a):
        raise LuaError("no path")

    gs.move_unit = boom
    orders, _ = parse_orders(
        [
            {"unit_id": 3, "action": "move", "x": 1, "y": 2},
            {"unit_id": 4, "action": "fortify"},
        ]
    )
    seen = []

    async def on_each(o, r):
        seen.append(o.index)

    results = asyncio.run(run_orders(gs, orders, on_each))
    assert results[0][1].startswith("FAIL") and results[1][1].startswith("OK")
    assert seen == [1, 2]


def test_run_dispatches_to_gamestate_methods():
    gs = _gs()
    orders, _ = parse_orders(
        [
            {"unit_id": 65536 + 7, "action": "fortify"},
            {"city_id": 2, "production": "DISTRICT_CAMPUS", "x": 4, "y": 5},
            {"city_id": 2, "purchase": "UNIT_SCOUT"},
            {"research": "TECH_MINING"},
            {"civic": "CIVIC_CODE_OF_LAWS"},
        ]
    )
    asyncio.run(run_orders(gs, orders, _noop))
    assert gs.calls == [
        ("fortify", (7,)),
        ("prod", (2, "DISTRICT", "DISTRICT_CAMPUS", 4, 5)),
        ("buy", (2, "UNIT", "UNIT_SCOUT", "YIELD_GOLD")),
        ("research", ("TECH_MINING",)),
        ("civic", ("CIVIC_CODE_OF_LAWS",)),
    ]


def test_game_error_string_counts_as_fail():
    async def err(*a):
        return "Error: nope"

    gs = _gs(fortify_unit=err)
    orders, _ = parse_orders([{"unit_id": 1, "action": "fortify"}])
    results = asyncio.run(run_orders(gs, orders, _noop))
    assert results[0][1] == "FAIL Error: nope"


def test_tool_result_lists_each_index():
    async def boom(*a):
        raise LuaError("blocked")

    gs = _gs(move_unit=boom)
    raw = [
        {"unit_id": 3, "action": "move", "x": 1, "y": 2},
        {"unit_id": 4, "action": "fortify"},
        {"action": "move"},
    ]
    orders, errs = parse_orders(raw)
    results = asyncio.run(run_orders(gs, orders, _noop))
    lines = format_results(len(raw), results, errs).split("\n")
    assert lines[0].startswith("#1 FAIL")
    assert lines[1].startswith("#2 OK")
    assert lines[2] == "#3 skipped: unit order needs unit_id"


def test_unexpected_exception_does_not_abort_batch():
    async def slow(*a):
        raise TimeoutError("socket stalled")

    gs = _gs(move_unit=slow)
    orders, _ = parse_orders(
        [
            {"unit_id": 1, "action": "fortify"},
            {"unit_id": 3, "action": "move", "x": 1, "y": 2},
            {"unit_id": 2, "action": "fortify"},
        ]
    )
    results = asyncio.run(run_orders(gs, orders, _noop))
    assert [r[:2] for _, r in results] == ["OK", "FA", "OK"]
    assert results[1][1] == "FAIL TimeoutError: socket stalled"
    assert results[2][1].startswith("OK")


def test_city_id_must_be_present_and_int():
    orders, errs = parse_orders(
        [
            {"city_id": None, "production": "UNIT_SCOUT"},
            {"production": "UNIT_SCOUT", "city_id": "4"},
            {"city_id": 4, "production": "UNIT_SCOUT"},
        ]
    )
    assert len(orders) == 1 and orders[0].id == 4
    assert errs[0] == "#1: city order needs city_id"
    assert errs[1].startswith("#2:")


def test_tool_wires_camera_and_broadcast(tmp_path):
    import types

    from civ_mcp import server
    from civ_mcp.broadcast import Broadcast

    pushes = []
    gs = _gs(_high_water_turn=5)

    async def ident():
        raise ConnectionError("no game")

    gs.get_game_identity = ident

    async def bad_attack(*a):
        raise LuaError("out of range")

    gs.attack_unit = bad_attack
    life = types.SimpleNamespace(
        game=gs,
        broadcast=Broadcast(tmp_path),
        camera=types.SimpleNamespace(push=lambda x, y, label: pushes.append((x, y))),
        logger=types.SimpleNamespace(_turn=5, log_tool_call=_noop2, log_error=_noop2),
        spatial=types.SimpleNamespace(record=_noop2),
    )
    ctx = types.SimpleNamespace(
        request_context=types.SimpleNamespace(lifespan_context=life)
    )
    fn = server.batch_orders if callable(server.batch_orders) else server.batch_orders.fn
    out = asyncio.run(
        fn(
            ctx,
            [
                {"unit_id": 3, "action": "move", "x": 12, "y": 8, "why": "to ridge"},
                {"unit_id": 4, "action": "attack", "x": 9, "y": 9},
                {"city_id": 4, "production": "BUILDING_MONUMENT"},
            ],
            why="turn plan",
        )
    )
    assert out.split("\n")[0].startswith("#1 OK")
    assert out.split("\n")[1].startswith("#2 FAIL")
    assert pushes == [(12, 8)]  # failed attack did not move the camera
    log = (tmp_path / "lobby.md").read_text().splitlines()
    actions = [l for l in log if "[action]" in l]
    assert len(actions) == 3
    assert "unit #3 move -> (12,8): to ridge => " in actions[0]
    assert "city #4 production: turn plan => " in actions[2]
    assert "unit #4 attack -> (9,9): turn plan => failed: out of range" in actions[1]
    rows = [json.loads(l) for l in (tmp_path / "lobby.jsonl").read_text().splitlines()]
    acts = [r for r in rows if r["kind"] == "action"]
    assert acts[1]["meta"]["result"] == "FAIL out of range"  # raw result kept


async def _noop2(*a, **k):
    return None


# --- D11a: a move the unit did not make is a failure -------------------------

BLOCKED = (
    "BLOCKED: water tile - land units need Shipbuilding tech to embark "
    "(unit still at (62,36), target (63,38))"
)


def test_blocked_move_counts_as_fail():
    async def blocked(*a):
        return BLOCKED

    gs = _gs(move_unit=blocked)
    orders, _ = parse_orders([{"unit_id": 1, "action": "move", "x": 63, "y": 38}])
    results = asyncio.run(run_orders(gs, orders, _noop))
    assert results[0][1] == "FAIL " + BLOCKED
    assert format_results(1, results, []).startswith("#1 FAIL BLOCKED: water tile")


def test_gamestate_move_unit_reports_blocked_as_failure():
    from civ_mcp.game_state import GameState

    gs = GameState.__new__(GameState)

    async def no_popup():
        return ""

    async def write(lua, timeout=5.0):
        return ["OK:MOVING_TO|63,38|from:62,36"]

    async def read(lua, timeout=5.0):
        return [
            "POS|62|36",
            "DIAG|WATER|water tile - land units need Shipbuilding tech to embark",
        ]

    gs.dismiss_popup = no_popup
    gs.conn = SimpleNamespace(execute_write=write, execute_read=read)
    out = asyncio.run(gs.move_unit(1, 63, 38))
    assert out.startswith("BLOCKED: water tile - land units need Shipbuilding")
    assert "(unit still at (62,36), target (63,38))" in out


def _life(tmp_path, gs, pushes):
    import types

    from civ_mcp.broadcast import Broadcast

    async def ident():
        raise ConnectionError("no game")

    gs.get_game_identity = ident
    life = types.SimpleNamespace(
        game=gs,
        broadcast=Broadcast(tmp_path),
        camera=types.SimpleNamespace(push=lambda x, y, label: pushes.append((x, y))),
        logger=types.SimpleNamespace(_turn=5, log_tool_call=_noop2, log_error=_noop2),
        spatial=types.SimpleNamespace(record=_noop2),
    )
    return types.SimpleNamespace(
        request_context=types.SimpleNamespace(lifespan_context=life)
    )


def test_blocked_move_does_not_hop_camera_and_logs_blocked(tmp_path):
    from civ_mcp import server

    async def blocked(*a):
        return BLOCKED

    pushes = []
    gs = _gs(_high_water_turn=5, move_unit=blocked)
    ctx = _life(tmp_path, gs, pushes)
    batch = server.batch_orders if callable(server.batch_orders) else server.batch_orders.fn
    out = asyncio.run(
        batch(ctx, [{"unit_id": 1, "action": "move", "x": 63, "y": 38}], why="test")
    )
    single = server.unit_action if callable(server.unit_action) else server.unit_action.fn
    res = asyncio.run(single(ctx, 1, "move", 63, 38, why="again"))
    assert out.startswith("#1 FAIL BLOCKED")
    assert res.startswith("BLOCKED")
    assert pushes == []
    log = (tmp_path / "lobby.md").read_text()
    assert log.count("=> blocked: water tile") == 2

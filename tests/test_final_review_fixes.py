"""Fixes from the final whole-branch review (Task 13).

One section per finding: unit-level visibility (Critical 1), refused attacks
(Important 2), recovery at the menu (3), recovery saves and identity (4),
queued moves (5), the situation board (6), and the minors.
"""

import asyncio
import json
import re
import types
from datetime import datetime
from pathlib import Path

import pytest

from civ_mcp import lua as lq
from civ_mcp import server
from civ_mcp.broadcast import Broadcast, humanize_result
from civ_mcp.diary import identity_hash
from civ_mcp.lua.models import (
    CityInfo,
    GameNotification,
    GameOverview,
    ThreatInfo,
    UnitInfo,
)
from civ_mcp import orders as orders_mod
from civ_mcp.orders import format_results, is_failure, parse_orders, run_orders
from civ_mcp.situation import Situation, format_situation, gather_situation

ROOT = Path(__file__).resolve().parents[1]


async def _noop(*a, **k):
    return None


def _guarded(lua: str, header: str, var: str, nth: int = 0, window: int = 3) -> bool:
    """True when the nth loop `header` checks IsUnitVisible(var) within `window` lines."""
    lines = lua.splitlines()
    hits = [i for i, ln in enumerate(lines) if header in ln]
    assert len(hits) > nth, f"loop {header!r} not found"
    i = hits[nth]
    return any(f"IsUnitVisible({var})" in ln for ln in lines[i : i + window + 1])


# --- Critical 1: unit-level visibility ---------------------------------------


@pytest.mark.parametrize(
    "name,lua,header,var",
    [
        ("units query targets", lq.build_units_query(), "for other in plotUnits:Units() do", "other"),
        ("attack", lq.build_attack_unit(3, 10, 11), "for other in tgtUnits:Units() do", "other"),
        (
            "attack follow-up",
            lq.build_attack_followup_query(10, 11),
            "for _, u in Players[i]:GetUnits():Members() do",
            "u",
        ),
        ("combat estimate", lq.build_combat_estimate_query(3, 10, 11), "for other in tgtUnits:Units() do", "other"),
        ("threat scan", lq.build_threat_scan_query(), "for _, bu in Players[pid]:GetUnits():Members() do", "bu"),
        (
            "post-move visibility",
            lq.build_post_move_visibility_query(10, 11),
            "for _, u in Players[pid]:GetUnits():Members() do",
            "u",
        ),
        ("map area", lq.build_map_area_query(10, 11, 2), "for _, u in units:Members() do", "u"),
        ("cities query", lq.build_cities_query(), "if pu then for other in pu:Units() do", "other"),
        ("city attack", lq.build_city_attack(65536, 10, 11), "if pu then for other in pu:Units() do", "other"),
    ],
)
def test_every_foreign_unit_loop_checks_unit_visibility(name, lua, header, var):
    assert _guarded(lua, header, var), name


def test_combat_estimate_support_counts_only_visible_supporters():
    q = lq.build_combat_estimate_query(3, 10, 11)
    assert "adjU ~= enemy and PlayersVisibility[me]:IsUnitVisible(adjU)" in q
    assert "PlayersVisibility[me]:IsVisible(sPlot:GetIndex())" in q


def test_attack_follow_up_keeps_own_units():
    q = lq.build_attack_followup_query(10, 11)
    assert "(i == _me or _pv:IsUnitVisible(u))" in q


def test_move_counts_only_a_visible_hostile_for_capture():
    q = lq.build_move_unit(3, 10, 11)
    assert "_mvVis:IsVisible(_mvPlot:GetIndex())" in q
    assert "_mvVis:IsUnitVisible(other) then hasHostile = true" in q
    assert "if other:GetOwner() ~= me then hasHostile = true end" not in q


def test_blocked_move_diagnostic_needs_revealed_terrain_and_visible_owner():
    q = lq.build_unit_position_query(3, 10, 11)
    assert q.index("dVis:IsRevealed(plot:GetIndex())") < q.index("plot:IsWater()")
    assert "DIAG|UNREVEALED|unit did not move (tile not revealed)" in q
    assert "if dVis:IsVisible(plot:GetIndex()) then owner = plot:GetOwner() end" in q
    assert "local owner = plot:GetOwner()" not in q
    lines = ["POS|5|5", "DIAG|UNREVEALED|unit did not move (tile not revealed)"]
    assert lq.parse_blocked_diagnostic(lines) == "unit did not move (tile not revealed)"


def test_threat_scan_reports_war_state():
    q = lq.build_threat_scan_query()
    assert '"|war:" .. atWar' in q
    line = "THREAT|1|Rome|UNIT_LEGION|5,6|100/100|CS:40|RS:0|dist:3|cs:0|uid:9|war:1"
    old = "THREAT|2|Egypt|UNIT_ARCHER|5,6|100/100|CS:15|RS:25|dist:3|cs:0|uid:9"
    t1, t2 = lq.parse_threat_scan_response([line, old])
    assert t1.at_war is True and t2.at_war is False


# --- Important 2: a refused attack is a failure --------------------------------

ESTIMATE = "ESTIMATE|UNIT_ARCHER|UNIT_WARRIOR|25|20|1|hills +3|100|100"


def _attack_gs(attack_line: str, followup: list[str] | None = None):
    from civ_mcp.game_state import GameState

    gs = GameState.__new__(GameState)
    gs._local_player_id = 0

    async def no_popup():
        return ""

    async def write(lua, timeout=5.0):
        if "ESTIMATE|" in lua:
            return [ESTIMATE, "---END---"]
        if "UNSEEN" in lua:  # the follow-up read
            return followup or ["UNIT|UNIT_WARRIOR|100/100|owner:1"]
        return [attack_line]

    gs.dismiss_popup = no_popup
    gs.conn = types.SimpleNamespace(execute_write=write)
    return gs


@pytest.mark.parametrize(
    "line",
    [
        "ERR:NO_MOVES|Unit has no movement points for ranged attack.",
        "ERR:OUT_OF_RANGE|Target at distance 3 but range is 2. Move closer first.",
        "ERR:NO_LOS|Cannot ranged-attack target at (10,11) from (8,8).",
        "ERR:ZOC|Unit entered Zone of Control this turn",
        "ERR:STOPPED_SHORT|Unit moved to (9,9) but could not reach target",
    ],
)
def test_refused_attack_returns_the_error_without_estimate(line):
    out = asyncio.run(_attack_gs(line).attack_unit(3, 10, 11))
    assert out.startswith("Error: ")
    assert "Combat Estimate" not in out
    assert is_failure(out)


def test_successful_attack_puts_the_outcome_first():
    line = "OK:RANGE_ATTACK|target:UNIT_WARRIOR at (10,11)|pre_hp:100/100|your HP:100|range:2 dist:2"
    out = asyncio.run(_attack_gs(line).attack_unit(3, 10, 11))
    assert out.startswith("RANGE_ATTACK|target:UNIT_WARRIOR")
    assert "\nCombat Estimate (Ranged):" in out
    assert not is_failure(out)


def test_is_failure_reads_every_line():
    old_shape = "Combat Estimate (Melee):\n  A vs B\n  Modifiers: none\nError: ZOC|entered ZOC"
    assert is_failure(old_shape)
    assert is_failure("text\nERR:SOMETHING")
    assert is_failure("text\nFAIL nope")
    assert not is_failure("MELEE_ATTACK|target:UNIT_WARRIOR at (1,1)\nCombat Estimate (Melee):\n  x")


def test_batch_shows_the_attack_outcome_not_the_estimate():
    ok_new = "RANGE_ATTACK|target:UNIT_WARRIOR at (5,6)|pre_hp:100/100\n  Post-combat: x\nCombat Estimate (Ranged):\n  A vs B"
    ok_old = "Combat Estimate (Melee):\n  A vs B\nMELEE_ATTACK|target:UNIT_WARRIOR at (5,6)|enemy HP:100 -> 40/100"
    refused_old = "Combat Estimate (Melee):\n  A vs B\nError: NO_MOVES|no moves"

    results = []
    for raw in (ok_new, ok_old, refused_old):
        async def attack(*a, raw=raw):
            return raw

        gs = types.SimpleNamespace(attack_unit=attack)
        orders, _ = parse_orders([{"unit_id": 1, "action": "attack", "x": 5, "y": 6}])
        results.append(asyncio.run(run_orders(gs, orders, _noop))[0])
    lines = [format_results(1, [r], []) for r in results]
    assert lines[0] == "#1 OK RANGE_ATTACK|target:UNIT_WARRIOR at (5,6)|pre_hp:100/100"
    assert lines[1] == "#1 OK MELEE_ATTACK|target:UNIT_WARRIOR at (5,6)|enemy HP:100 -> 40/100"
    assert lines[2] == "#1 FAIL Error: NO_MOVES|no moves"
    assert orders_mod.outcome_line("OK FOUNDED|1,2|Uruk") == "OK FOUNDED|1,2|Uruk"


def test_refused_attack_in_a_batch_does_not_hop_the_camera(tmp_path):
    """Through the real GameState.attack_unit: the estimate ran, the attack
    was refused; the batch says FAIL, the camera stays, the log says refused."""
    pushes = []

    async def ident():
        raise ConnectionError("no game")

    gs = _attack_gs("ERR:NO_MOVES|Unit has no movement points for ranged attack.")
    gs._high_water_turn = 5
    gs.get_game_identity = ident
    life = types.SimpleNamespace(
        game=gs,
        broadcast=Broadcast(tmp_path),
        camera=types.SimpleNamespace(push=lambda x, y, label: pushes.append((x, y))),
        logger=types.SimpleNamespace(_turn=5, log_tool_call=_noop, log_error=_noop),
        spatial=types.SimpleNamespace(record=_noop),
    )
    ctx = types.SimpleNamespace(request_context=types.SimpleNamespace(lifespan_context=life))
    out = asyncio.run(server.batch_orders(ctx, [{"unit_id": 1, "action": "attack", "x": 10, "y": 11}]))
    assert out.startswith("#1 FAIL Error: NO_MOVES")
    assert pushes == []
    md = (tmp_path / "lobby.md").read_text()
    assert "=> refused: Unit has no movement points for ranged attack." in md
    assert "Combat Estimate" not in md


# --- Important 3: no auto-recovery at the main menu or during new_game -------


class _MenuConn:
    def __init__(self, states):
        self.states = states

    async def query_states(self, timeout=5.0):
        if self.states is None:
            raise ConnectionError("no game")
        return self.states


def _logged_ctx(tmp_path, conn, **life_kw):
    gs = types.SimpleNamespace(conn=conn, _high_water_turn=0)
    life = types.SimpleNamespace(
        game=gs,
        broadcast=Broadcast(tmp_path),
        logger=types.SimpleNamespace(_turn=12, log_tool_call=_noop, log_error=_noop),
        spatial=types.SimpleNamespace(record=_noop),
        **life_kw,
    )
    return types.SimpleNamespace(request_context=types.SimpleNamespace(lifespan_context=life))


def _down():
    async def fn():
        raise ConnectionError("GameCore_Tuner/InGame states not found.")

    return fn


def test_main_menu_never_triggers_recovery(tmp_path, monkeypatch):
    calls = []

    async def restart(save, conn):
        calls.append(save)
        return "Kill | Launch | Load: done"

    monkeypatch.setattr(server.game_launcher, "restart_and_load", restart)
    monkeypatch.setattr(server._logged, "_conn_errors", 0, raising=False)
    ctx = _logged_ctx(tmp_path, _MenuConn({0: "Main State", 3: "FrontEnd", 5: "MainMenu"}))
    for _ in range(7):
        out = asyncio.run(server._logged(ctx, "get_units", {}, _down()))
        assert out == "Error: game is at the main menu; call new_game or load_game_save"
    assert calls == []
    assert server._logged._conn_errors == 0


def test_new_game_in_progress_never_triggers_recovery(tmp_path, monkeypatch):
    calls = []

    async def restart(save, conn):
        calls.append(save)
        return "ok"

    monkeypatch.setattr(server.game_launcher, "restart_and_load", restart)
    monkeypatch.setattr(server._logged, "_conn_errors", 0, raising=False)
    ctx = _logged_ctx(tmp_path, _MenuConn(None), new_game_active=True)
    for _ in range(6):
        out = asyncio.run(server._logged(ctx, "get_units", {}, _down()))
        assert out.startswith("Error: new_game is in progress")
    assert calls == []


def test_crash_recovery_without_a_recorded_save_is_not_attempted(tmp_path, monkeypatch):
    calls = []

    async def restart(save, conn):
        calls.append(save)
        return "ok"

    monkeypatch.setattr(server.game_launcher, "restart_and_load", restart)
    monkeypatch.setattr(server._logged, "_conn_errors", 0, raising=False)
    ctx = _logged_ctx(tmp_path, _MenuConn(None))
    outs = [asyncio.run(server._logged(ctx, "get_units", {}, _down())) for _ in range(5)]
    assert calls == []
    assert "Connection recovery not attempted" in outs[-1]
    assert "no campaign game found" in outs[-1]


def test_new_game_rebinds_trackers_and_clears_history(tmp_path, monkeypatch):
    from civ_mcp.newgame import StartResult, started_line

    async def create(conn, leader, **kw):
        assert life.new_game_active is True
        return StartResult(True, started_line("LEADER_AMANITORE"), found={"turn": "1"}, click="ocr")

    monkeypatch.setattr(server, "create_game", create)
    bound = []
    logger = types.SimpleNamespace(_turn=40, bind_game=lambda c, s: bound.append(("logger", c, s)))
    spatial = types.SimpleNamespace(_turn=40, bind_game=lambda c, s: bound.append(("spatial", c, s)))

    class Game:
        conn = None
        fog = None
        _high_water_turn = 40
        _pending_end_turn = False
        _pending_end_turn_from = None
        _end_turn_blocked = False
        _save_load_history = [(0.0, 30, "0_MCP_0030")]
        spatial = None

        async def get_game_identity(self):
            return ("nubia", 7)

        async def get_game_overview(self):
            return types.SimpleNamespace(difficulty="Deity")

    game = Game()
    life = types.SimpleNamespace(broadcast=Broadcast(tmp_path), game=game, logger=logger, spatial=spatial)
    ctx = types.SimpleNamespace(request_context=types.SimpleNamespace(lifespan_context=life))
    out = asyncio.run(server.new_game(ctx, "LEADER_AMANITORE", "fresh start"))
    assert out == started_line("LEADER_AMANITORE")
    assert logger._turn == 1 and spatial._turn == 1
    assert ("logger", "nubia", 7) in bound and ("spatial", "nubia", 7) in bound
    assert game._save_load_history == []
    assert life.new_game_active is False


def test_new_game_why_message_is_its_own():
    out = asyncio.run(server.new_game(None, "LEADER_GILGAMESH", "  "))
    assert "required for new_game" in out
    assert "every load" not in out


# --- Important 4: recovery loads the game's own save, identity checked -------


def _campaign(tmp_path, identity="aaa111", last_save="0_MCP_0042", turn=42, bind=True):
    bc = Broadcast(tmp_path)
    d = bc.bind_game(identity, "sumeria", "LEADER_GILGAMESH")
    fields = {"turn": turn}
    if last_save:
        fields["last_save"] = last_save
    bc.update_meta(**fields)
    return (bc if bind else Broadcast(tmp_path)), d


def test_update_meta_merges_into_bound_game(tmp_path):
    bc, d = _campaign(tmp_path)
    bc.update_meta(turn=43, last_save="0_MCP_0043")
    meta = json.loads((d / "meta.json").read_text())
    assert meta["identity"] == "aaa111" and meta["leader"] == "LEADER_GILGAMESH"
    assert meta["turn"] == 43 and meta["last_save"] == "0_MCP_0043"
    assert Broadcast(tmp_path / "unbound").update_meta(turn=1) == {}


def test_end_turn_writes_last_save_and_turn(tmp_path):
    bc, d = _campaign(tmp_path, last_save=None, turn=None)
    gs = types.SimpleNamespace(_last_autosave="0_MCP_0012")
    ctx = types.SimpleNamespace(
        request_context=types.SimpleNamespace(lifespan_context=types.SimpleNamespace(broadcast=bc))
    )
    server._record_turn_meta(ctx, gs, 12)
    meta = json.loads((d / "meta.json").read_text())
    assert meta["last_save"] == "0_MCP_0012" and meta["turn"] == 12
    # A failed save keeps the previous one but still moves the turn on.
    gs._last_autosave = None
    server._record_turn_meta(ctx, gs, 13)
    meta = json.loads((d / "meta.json").read_text())
    assert meta["last_save"] == "0_MCP_0012" and meta["turn"] == 13


def test_end_turn_wiring_records_the_save_it_wrote():
    import inspect

    from civ_mcp import end_turn

    src = inspect.getsource(end_turn.execute_end_turn)
    assert "gs._last_autosave = save_name" in src
    assert "_record_turn_meta(ctx, gs," in inspect.getsource(server.end_turn)


def test_recovery_save_is_the_campaign_games_own(tmp_path):
    bc, _ = _campaign(tmp_path)
    assert server._recovery_save(bc) == ("0_MCP_0042", "aaa111", None)
    # A fresh session (nothing bound yet) uses the latest campaign game.
    fresh = Broadcast(tmp_path)
    assert server._recovery_save(fresh)[0] == "0_MCP_0042"
    _, _, err = server._recovery_save(Broadcast(tmp_path / "empty"))
    assert err.startswith("Error: no campaign game found")


def test_recovery_save_refuses_a_game_without_one(tmp_path):
    bc, d = _campaign(tmp_path, last_save=None)
    save, _, err = server._recovery_save(bc)
    assert save is None and d.name in err and "Give save_name" in err


def test_latest_game_dir_is_the_highest_number(tmp_path):
    b = Broadcast(tmp_path)
    b.bind_game("one", "sumeria")
    b.bind_game("two", "nubia")
    assert Broadcast(tmp_path).latest_game_dir().name == "02-nubia"


class _LoadGame:
    """GameState stand-in: identity after the load, records loads."""

    def __init__(self, live_seed):
        self.live_seed = live_seed
        self.loads = []
        self._high_water_turn = 0
        self.conn = types.SimpleNamespace(gamecore_index=1, ingame_index=2, reconnect=_noop)
        self.fog = None

    async def get_game_identity(self):
        return ("sumeria", self.live_seed)

    def record_load(self, name, reason):
        self.loads.append((name, reason))


def _load_ctx(bc, game):
    life = types.SimpleNamespace(broadcast=bc, game=game)
    return types.SimpleNamespace(request_context=types.SimpleNamespace(lifespan_context=life))


def _fast_loads(monkeypatch):
    monkeypatch.setattr(server, "_LOAD_SETTLE_S", 0)
    monkeypatch.setattr(server, "_IDENTITY_RETRY_S", 0)


def test_restart_and_load_defaults_to_the_games_last_save(tmp_path, monkeypatch):
    _fast_loads(monkeypatch)
    seed = 4242
    bc, d = _campaign(tmp_path, identity=identity_hash(seed))
    asked = []

    async def restart(save, conn):
        asked.append(save)
        return "Kill: ok | Launch: ok | Load: Save loading (49s). Steps: Selected save 0_MCP_0042"

    monkeypatch.setattr(server.game_launcher, "restart_and_load", restart)
    game = _LoadGame(seed)
    out = asyncio.run(server.restart_and_load(_load_ctx(bc, game), "game crashed"))
    assert asked == ["0_MCP_0042"]
    assert "WRONG GAME" not in out
    assert game.loads == [("0_MCP_0042", "game crashed")]


def test_restart_and_load_refuses_without_a_recorded_save(tmp_path, monkeypatch):
    called = []

    async def restart(save, conn):
        called.append(save)
        return "ok"

    monkeypatch.setattr(server.game_launcher, "restart_and_load", restart)
    bc, _ = _campaign(tmp_path, last_save=None)
    out = asyncio.run(server.restart_and_load(_load_ctx(bc, _LoadGame(1)), "crash"))
    assert out.startswith("Error:") and called == []


def test_wrong_game_after_load_is_a_loud_error_and_not_recorded(tmp_path, monkeypatch):
    _fast_loads(monkeypatch)
    bc, d = _campaign(tmp_path, identity=identity_hash(1111))

    async def restart(save, conn):
        return "Kill: ok | Launch: ok | Load: Save loading (49s)."

    monkeypatch.setattr(server.game_launcher, "restart_and_load", restart)
    game = _LoadGame(9999)  # some other game came up
    out = asyncio.run(server.restart_and_load(_load_ctx(bc, game), "crash", "0_MCP_0042"))
    assert out.startswith("ERROR: WRONG GAME LOADED")
    assert d.name in out
    assert game.loads == []
    log_md = d / "log.md"
    assert not log_md.exists() or "[load]" not in log_md.read_text()


def test_fresh_session_load_is_checked_against_the_latest_game(tmp_path, monkeypatch):
    """Nothing bound yet (a new session after a crash): the latest game dir
    supplies the identity the load must match."""
    _fast_loads(monkeypatch)
    _campaign(tmp_path, identity=identity_hash(1111))
    fresh = Broadcast(tmp_path)

    async def from_menu(save, conn):
        return "Save loading (51s). Steps: Selected save 0_MCP_0042"

    monkeypatch.setattr(server.game_launcher, "load_save_from_menu", from_menu)
    game = _LoadGame(2222)
    out = asyncio.run(server.load_save_from_menu(_load_ctx(fresh, game), "crash"))
    assert out.startswith("ERROR: WRONG GAME LOADED") and game.loads == []


def test_load_game_save_defaults_and_checks_identity(tmp_path, monkeypatch):
    _fast_loads(monkeypatch)
    seed = 5150
    bc, d = _campaign(tmp_path, identity=identity_hash(seed))
    asked = []

    class G(_LoadGame):
        async def load_game_save(self, name, reason="", *, record=True):
            asked.append((name, record))
            return f"Loading save: {name}. Game will reload."

    game = G(seed)
    life = types.SimpleNamespace(
        broadcast=bc,
        game=game,
        logger=types.SimpleNamespace(_turn=42, log_tool_call=_noop, log_error=_noop),
        spatial=types.SimpleNamespace(record=_noop),
    )
    ctx = types.SimpleNamespace(request_context=types.SimpleNamespace(lifespan_context=life))
    out = asyncio.run(server.load_game_save(ctx, reason="game froze"))
    assert asked == [("0_MCP_0042", False)]
    assert out.startswith("Loading save: 0_MCP_0042")
    assert game.loads == [("0_MCP_0042", "game froze")]


def test_binding_an_existing_game_seeds_the_high_water_turn(tmp_path):
    seed = 777
    _campaign(tmp_path, identity=identity_hash(seed), turn=40)

    async def ident():
        return ("civilization_sumeria", seed)

    gs = types.SimpleNamespace(_high_water_turn=0, get_game_identity=ident, fog=None)
    life = types.SimpleNamespace(game=gs, broadcast=Broadcast(tmp_path))
    ctx = types.SimpleNamespace(request_context=types.SimpleNamespace(lifespan_context=life))
    asyncio.run(server._bind_broadcast(ctx))
    assert gs._high_water_turn == 40


def test_playbook_has_no_unnamed_recovery_form():
    text = (ROOT / "PLAYBOOK.md").read_text(encoding="utf-8")
    assert "restart_and_load(reason)` loads the latest autosave" not in text
    assert "(or `restart_and_load(reason)`" not in text
    assert "last_save" in text


# --- Important 5: a queued move is not a blocked move -------------------------


def _move_gs(queued: str):
    from civ_mcp.game_state import GameState

    gs = GameState.__new__(GameState)

    async def no_popup():
        return ""

    async def write(lua, timeout=5.0):
        if "_queuedDest(unit)" in lua:
            return [queued, "---END---"]
        return ["OK:MOVING_TO|63,38|from:62,36"]

    async def read(lua, timeout=5.0):
        return ["POS|62|36", "DIAG|UNKNOWN|tile appears passable - path may be blocked"]

    gs.dismiss_popup = no_popup
    gs.conn = types.SimpleNamespace(execute_write=write, execute_read=read)
    return gs


def test_queued_move_is_reported_as_queued_success():
    out = asyncio.run(_move_gs("QUEUED|63,38").move_unit(1, 63, 38))
    assert out.startswith("QUEUED: path to (63,38) continues next turn")
    assert not is_failure(out)
    assert humanize_result("unit_action", {}, "OK " + out) == "path to (63,38) queued, continues next turn"


def test_move_without_queued_path_is_still_blocked():
    out = asyncio.run(_move_gs("QUEUED|NONE").move_unit(1, 63, 38))
    assert out.startswith("BLOCKED:")
    assert is_failure(out)


def test_queued_destination_query_and_parser():
    q = lq.build_queued_destination_query(4)
    assert "local function _queuedDest(u)" in q and "Map.IsPlot(d)" in q
    assert lq.parse_queued_destination(["QUEUED|29,31"]) == (29, 31)
    assert lq.parse_queued_destination(["QUEUED|NONE"]) is None
    assert lq.parse_queued_destination([]) is None


def test_one_queued_path_form_everywhere():
    import inspect

    from civ_mcp import end_turn

    for q in (
        lq.build_units_query(),
        lq.build_skip_remaining_units(),
        lq.build_fortify_remaining_units(),
        lq.build_queued_destination_query(1),
    ):
        assert "local function _queuedDest(u)" in q
        assert "GetQueuedDestination(u) == nil" not in q
        assert "GetQueuedDestination(unit) == nil" not in q
    assert "GetQueuedDestination(u) == nil" not in inspect.getsource(end_turn)


# --- Important 6: the board's spec items --------------------------------------


def _ov(**kw):
    base = dict(
        turn=42, player_id=0, civ_name="Sumeria", leader_name="Gilgamesh",
        gold=120.0, gold_per_turn=7.0, science_yield=12.0, culture_yield=5.0,
        faith=3.0, current_research="TECH_WRITING", current_civic="CIVIC_CRAFTSMANSHIP",
        num_cities=2, num_units=4, score=88, era_name="Classical",
    )
    base.update(kw)
    return GameOverview(**base)


def _city(name, **kw):
    base = dict(
        city_id=1, name=name, x=0, y=0, population=3, food=2, production=3, gold=1,
        science=1, culture=1, faith=0, housing=8, amenities=1, turns_to_grow=5,
        currently_building="BUILDING_MONUMENT",
    )
    base.update(kw)
    return CityInfo(**base)


def _unit(uid, **kw):
    base = dict(
        unit_id=uid, unit_index=uid, name="u", unit_type="UNIT_SLINGER", x=1, y=1,
        moves_remaining=0.0, max_moves=2.0, health=100, max_health=100,
    )
    base.update(kw)
    return UnitInfo(**base)


def _board(**kw):
    return format_situation(Situation(overview=kw.pop("overview", _ov()), **kw))


def _section(out, name):
    order = ["EMPIRE", "ATTENTION", "THREATS", "RIVALS"]
    start = out.index(name + "\n")
    nxt = order[order.index(name) + 1] if name != "RIVALS" else None
    return out[start: out.index(nxt + "\n") if nxt else len(out)]


def test_empire_shows_era_score_against_thresholds():
    out = _board(overview=_ov(era_score=12, era_dark_threshold=15, era_golden_threshold=27))
    assert "  era score 12 (dark < 15, golden >= 27)" in _section(out, "EMPIRE")
    assert "era score" not in _board()  # thresholds unknown: no line


def test_attention_flags_amenities_and_housing():
    cities = [
        _city("Uruk", amenities_net=-2),
        _city("Ur", population=5, housing=6.0, amenities_net=0),
        _city("Lagash", population=2, housing=8.0, amenities_net=1),
    ]
    att = _section(_board(cities=cities), "ATTENTION")
    assert "amenities short: Uruk -2" in att
    assert "housing low: Ur (pop 5, housing 6)" in att
    assert "Lagash" not in att


def test_attention_lists_available_upgrades():
    units = [_unit(65537, can_upgrade=True, upgrade_target="UNIT_ARCHER"), _unit(65538)]
    att = _section(_board(units=units), "ATTENTION")
    assert "upgrades available: Slinger #65537 (1,1) -> Archer" in att
    assert "#65538" not in att


def test_threats_list_war_declarations_and_denouncements():
    notes = [
        GameNotification("NOTIFICATION_DECLARE_WAR", "Declaration of War", 42, -1, -1,
                         summary="Gilgamesh has declared war on Rome."),
        GameNotification("NOTIFICATION_DIPLOMACY_SESSION", "You have been Denounced", 42, -1, -1,
                         summary="Rome has denounced your civilization."),
        GameNotification("NOTIFICATION_DIPLOMACY_SESSION", "You have been Denounced", 42, -1, -1,
                         summary="Rome has denounced your civilization."),
        GameNotification("NOTIFICATION_CHOOSE_TECH", "Choose Research", 42, -1, -1),
    ]
    thr = _section(_board(notifications=notes), "THREATS")
    assert "  war declared: Gilgamesh has declared war on Rome\n" in thr
    assert thr.count("denounced: Rome has denounced your civilization") == 1
    assert "Choose Research" not in thr


def test_threats_only_from_war_enemies_and_barbarians():
    threats = [
        ThreatInfo("UNIT_HORSEMAN", 4, 5, 90, 100, 36, 0, 3),  # barbarian (owner 63)
        ThreatInfo("UNIT_LEGION", 6, 5, 100, 100, 40, 0, 4, owner_id=1, owner_name="Rome", at_war=True),
        ThreatInfo("UNIT_ARCHER", 7, 5, 100, 100, 15, 25, 2, owner_id=2, owner_name="Egypt"),
    ]
    thr = _section(_board(threats=threats), "THREATS")
    assert "Barbarian HORSEMAN" in thr and "Rome LEGION" in thr
    assert "Egypt" not in thr


def test_threats_list_cities_under_siege():
    cities = [_city("Uruk", under_siege=True), _city("Ur")]
    thr = _section(_board(cities=cities), "THREATS")
    assert "  cities under siege: Uruk" in thr


def test_cities_query_reads_net_amenities_and_siege():
    q = lq.build_cities_query()
    assert "g:GetAmenities() - g:GetAmenitiesNeeded()" in q
    assert "d:IsUnderSiege()" in q
    base = "65536|Uruk|3,4|3|2|3|1|1|1|0|6.0|4|5|nothing|0|10|100/100|0/0||||100.0|100.0|0.0|0|2.0|5.0|20||UNIT_WARRIOR"
    new, old = lq.parse_cities_response([base + "|-1|1", base.replace("65536", "65537")])[0]
    assert new.amenities_net == -1 and new.under_siege is True
    assert old.amenities_net is None and old.under_siege is False


def test_notifications_carry_the_summary():
    q = lq.build_notifications_query()
    assert "entry:GetSummary()" in q
    n_new, n_old = lq.parse_notifications_response(
        [
            "NOTIF|NOTIFICATION_DECLARE_WAR|Declaration of War|42|-1,-1|Rome has declared war on you.",
            "NOTIF|NOTIFICATION_CHOOSE_TECH|Choose Research|42|-1,-1",
        ]
    )
    assert n_new.summary == "Rome has declared war on you." and n_old.summary == ""


def test_gather_situation_reads_notifications():
    gs = types.SimpleNamespace()

    async def ov():
        return _ov()

    async def empty():
        return []

    async def cities():
        return ([], [])

    async def notes():
        return [GameNotification("NOTIFICATION_DECLARE_WAR", "Declaration of War", 1, -1, -1)]

    async def none():
        return None

    async def write(lua):
        return ["NONE"]

    gs.get_game_overview = ov
    gs.get_units = empty
    gs.get_cities = cities
    gs.get_threat_scan = empty
    gs.get_rival_snapshot = empty
    gs.get_trade_routes = none
    gs.get_city_states = none
    gs.get_governors = none
    gs.get_notifications = notes
    gs.conn = types.SimpleNamespace(execute_write=write)
    import civ_mcp.situation as sit

    async def no_victory(g):
        return []

    orig = sit._victory_warnings
    sit._victory_warnings = no_victory
    try:
        s = asyncio.run(gather_situation(gs))
    finally:
        sit._victory_warnings = orig
    assert len(s.notifications) == 1
    assert "war declared: Declaration of War" in format_situation(s)


def test_board_stays_within_40_lines_with_everything():
    threats = [ThreatInfo("UNIT_HORSEMAN", i, i, 90, 100, 36, 0, i) for i in range(30)]
    notes = [
        GameNotification("NOTIFICATION_DECLARE_WAR", "Declaration of War", 1, -1, -1, summary=f"Civ{i} declared war.")
        for i in range(10)
    ]
    cities = [_city(f"C{i}", amenities_net=-1, under_siege=True, housing=3.0) for i in range(20)]
    units = [_unit(i, moves_remaining=2.0, can_upgrade=True, needs_promotion=True) for i in range(30)]
    out = _board(
        overview=_ov(era_score=1, era_dark_threshold=15, era_golden_threshold=27),
        threats=threats, notifications=notes, cities=cities, units=units,
        blockers=[f"choice {i}" for i in range(20)],
    )
    lines = out.splitlines()
    assert len(lines) <= 40
    for h in ("EMPIRE", "ATTENTION", "THREATS", "RIVALS"):
        assert h in lines


# --- Minors ----------------------------------------------------------------------


def test_log_text_has_no_em_dashes(tmp_path):
    b = Broadcast(tmp_path)
    b.append("action", "Cannot attack Warrior — you are at peace with Rome")
    md = (tmp_path / "lobby.md").read_text(encoding="utf-8")
    assert "—" not in md and "Warrior, you are at peace" in md
    row = json.loads((tmp_path / "lobby.jsonl").read_text().splitlines()[-1])
    assert "—" not in row["text"]


def test_log_times_are_local_with_offset(tmp_path, monkeypatch):
    import time

    if not hasattr(time, "tzset"):
        pytest.skip("needs time.tzset")
    monkeypatch.setenv("TZ", "EST5")  # UTC-5, no zoneinfo needed
    time.tzset()
    try:
        b = Broadcast(tmp_path)
        e = b.append("plan", "x")
        ts = datetime.fromisoformat(e.ts)
        assert ts.utcoffset().total_seconds() == -5 * 3600
        assert e.ts.endswith("-05:00")
        md = (tmp_path / "lobby.md").read_text()
        assert f"- {ts.strftime('%H:%M')} [plan] x" in md
    finally:
        monkeypatch.undo()
        time.tzset()


@pytest.mark.parametrize(
    "raw,plain",
    [
        ("OK:ALREADY_FORTIFIED|Fortify turns: 2", "already fortified"),
        ("PROMOTED|PROMOTION_BATTLECRY|hp:100/100|moves:2", "promoted: Battlecry"),
        (
            "COUNTER: Rome countered. We give: 10 gold. They give: 1x Silk. The session is open: call respond_to_trade to accept or reject.",
            "counter-offer: Rome countered. We give: 10 gold. They give: 1x Silk.",
        ),
        ("ACCEPTED|Trade accepted with Rome", "Trade accepted with Rome"),
        ("ACCEPTED|Peace established with Rome", "Peace established with Rome"),
        ("UPGRADED|UNIT_SLINGER -> UNIT_ARCHER", "upgraded to Archer"),
        ("ENVOY_SENT|Mitla", "envoy sent to Mitla"),
    ],
)
def test_leftover_codes_are_humanized(raw, plain):
    assert humanize_result("x", {}, raw) == plain


def test_city_actions_are_named_by_city():
    line = Broadcast.action_text(
        "set_city_production",
        {"city_id": 65536, "item_name": "BUILDING_MONUMENT", "why": "culture"},
        "PRODUCING|BUILDING_MONUMENT|5 turns",
        city_label="Uruk",
    )
    assert line == "Uruk production: culture => now building Monument (5 turns)"
    from civ_mcp.game_state import GameState

    gs = GameState.__new__(GameState)
    gs._city_names = {65536: "Uruk"}
    # Tools take the id or the per-player index (id % 65536).
    assert gs.city_label(65536) == "Uruk" and gs.city_label(0) == "Uruk"
    assert gs.city_label(5) is None and gs.city_label("x") is None
    assert server._city_label(gs, {"city_id": 65536}) == "Uruk"


@pytest.mark.parametrize("kind", ["load", "action", "turn", "event", "game"])
def test_narrate_refuses_system_kinds(tmp_path, kind):
    gs = types.SimpleNamespace(_high_water_turn=3)
    life = types.SimpleNamespace(game=gs, broadcast=Broadcast(tmp_path))
    ctx = types.SimpleNamespace(request_context=types.SimpleNamespace(lifespan_context=life))
    assert asyncio.run(server.narrate(ctx, kind, "x")).startswith("Error: unknown kind")
    assert not (tmp_path / "lobby.md").exists()


def test_snapshot_reads_units_in_ingame():
    from civ_mcp.game_state import GameState

    gs = GameState.__new__(GameState)
    seen = []

    async def write(lua, timeout=5.0):
        if "GetActivityType" in lua:
            seen.append("write")
        return ["---END---"]

    async def read(lua, timeout=5.0):
        if "GetActivityType" in lua:
            seen.append("read")
        return ["---END---"]

    gs.conn = types.SimpleNamespace(execute_write=write, execute_read=read)
    asyncio.run(gs._take_snapshot(_ov()))
    assert seen == ["write"]


def test_docs_say_what_the_code_does():
    fair = (ROOT / "docs" / "FAIRNESS.md").read_text(encoding="utf-8")
    assert "Later tasks append rows" not in fair
    assert "The hard stop makes `end_turn` refuse" in fair
    assert "IsUnitVisible" in fair
    path_row = next(l for l in fair.splitlines() if l.startswith("| `get_pathing_estimate`"))
    assert "no extra IsRevealed filter" in path_row
    for tool in ("get_trade_options", "propose_trade"):
        row = next(l for l in fair.splitlines() if l.startswith(f"| `{tool}`"))
        assert "read directly" in row and "verified live" in row
    runtime = (ROOT / "docs" / "RUNTIME.md").read_text(encoding="utf-8")
    assert "0.0.0.0:8000" not in runtime and "127.0.0.1:8000" in runtime
    assert "Always use this option" in runtime and "April has ticked" in runtime
    assert "GameCore FinishMoves" not in (server.skip_remaining_units.__doc__ or "")


def test_no_em_dashes_in_new_board_and_load_text():
    for text in (server.MENU_ERROR, server.NEW_GAME_BUSY_ERROR):
        assert "—" not in text
    assert "—" not in lq.parse_blocked_diagnostic([])
    assert not re.search("—", (ROOT / "docs" / "FAIRNESS.md").read_text(encoding="utf-8"))

"""Fixes for the live-rehearsal defects (docs/REHEARSAL-2026-10-25.md)."""

import logging
import re

import pytest

from civ_mcp import lua as lq
from civ_mcp import narrate as nr
from civ_mcp.broadcast import Broadcast
from civ_mcp.lua import units as lq_units
from civ_mcp.lua.map import STRATEGIC_DIRECTIONS, build_strategic_map_query
from civ_mcp.lua.models import CityInfo, FogBoundary, GameOverview, StrategicMapData
from civ_mcp.situation import Situation, format_situation

BASE_UNIT = "5|5|Warrior|UNIT_WARRIOR|3,4|2/2|100/100|20|0|0||0|0||0||"


# --- D2: busy units --------------------------------------------------------


def test_units_query_reads_activity_through_unit_manager():
    q = lq.build_units_query()
    assert "UnitManager.GetActivityType(u)" in q
    assert "u:GetActivityType()" not in q
    assert "GetFortifyTurns()" in q
    assert "UnitManager.GetQueuedDestination(u)" in q
    assert 'print("ACTERR|"' in q


def test_parse_units_activity_field():
    u = lq.parse_units_response([BASE_UNIT + "|1|fortified"])[0]
    assert u.is_busy is True
    assert u.activity == "fortified"
    awake = lq.parse_units_response([BASE_UNIT + "|0|awake"])[0]
    assert awake.is_busy is False
    assert awake.activity == "awake"
    old = lq.parse_units_response([BASE_UNIT + "|1"])[0]
    assert old.activity == ""


def test_activity_error_line_is_skipped_and_logged_once(caplog, monkeypatch):
    monkeypatch.setattr(lq_units, "_activity_error_logged", False)
    lines = ["ACTERR|attempt to call a nil value", BASE_UNIT + "|0|?"]
    with caplog.at_level(logging.WARNING, logger="civ_mcp.lua.units"):
        units = lq.parse_units_response(lines)
        lq.parse_units_response(lines)
    assert len(units) == 1
    warnings = [r for r in caplog.records if "activity read failed" in r.getMessage()]
    assert len(warnings) == 1


@pytest.mark.parametrize(
    "activity,marker",
    [
        ("fortified", "[fortified]"),
        ("sleeping", "[sleeping]"),
        ("alert", "[alert]"),
        ("healing", "[healing]"),
        ("skipping", "[skipping turn]"),
        ("moving:62,36", "[moving to (62,36)]"),
    ],
)
def test_get_units_narration_marks_activity(activity, marker):
    u = lq.parse_units_response([BASE_UNIT + f"|1|{activity}"])[0]
    out = nr.narrate_units([u])
    assert marker in out


def test_awake_unit_has_no_marker():
    u = lq.parse_units_response([BASE_UNIT + "|0|awake"])[0]
    out = nr.narrate_units([u])
    assert "[fortified]" not in out and "[sleeping]" not in out


# --- D3: a city with an empty queue -----------------------------------------


def _ov():
    return GameOverview(
        turn=6, player_id=0, civ_name="Sumeria", leader_name="Gilgamesh",
        gold=25.0, gold_per_turn=3.0, science_yield=3.0, culture_yield=2.0,
        faith=0.0, current_research="TECH_POTTERY", current_civic="CIVIC_CODE_OF_LAWS",
        num_cities=1, num_units=2, score=8,
    )


def _city(name, building):
    return CityInfo(
        city_id=1, name=name, x=59, y=35, population=1, food=2, production=2,
        gold=1, science=1, culture=1, faith=0, housing=3, amenities=1,
        turns_to_grow=5, currently_building=building,
    )


def test_board_flags_city_with_nothing_in_production():
    out = format_situation(Situation(overview=_ov(), cities=[_city("Uruk", "nothing")]))
    assert "no production: Uruk" in out


def test_board_does_not_flag_busy_city():
    out = format_situation(
        Situation(overview=_ov(), cities=[_city("Uruk", "BUILDING_MONUMENT")])
    )
    assert "no production" not in out


# --- D4: compass directions --------------------------------------------------


def test_strategic_directions_put_north_at_higher_y():
    dirs = {label: (dx, dy) for label, dx, dy in STRATEGIC_DIRECTIONS}
    assert [label for label, _, _ in STRATEGIC_DIRECTIONS] == [
        "N", "NE", "SE", "S", "SW", "NW",
    ]
    for label, (dx, dy) in dirs.items():
        if label.startswith("N"):
            assert dy > 0, label
        if label.startswith("S"):
            assert dy < 0, label
        if label.endswith("E"):
            assert dx > 0, label
        if label.endswith("W"):
            assert dx < 0, label


def test_strategic_map_lua_uses_the_same_vectors():
    q = build_strategic_map_query()
    m = re.search(r"local dirVecs = (\{.*\})", q)
    assert m, q
    vecs = [tuple(int(v) for v in pair.split(",")) for pair in re.findall(r"\{(-?\d+,-?\d+)\}", m.group(1))]
    assert vecs == [(dx, dy) for _, dx, dy in STRATEGIC_DIRECTIONS]
    assert "{DIR_VECS}" not in q


def test_strategic_map_labels_follow_the_lua_order():
    data = StrategicMapData(
        fog_boundaries=[FogBoundary("Uruk", 59, 35, [7, 4, 5, 4, 3, 3])],
        unclaimed_resources=[],
    )
    out = nr.narrate_strategic_map(data)
    assert "Uruk (59,35): N:7 NE:4 SE:5 S:4 SW:3 NW:3" in out


# --- D7: trade options with an unmet civ -------------------------------------


def test_deal_options_error_is_returned_not_a_default_screen():
    with pytest.raises(ValueError, match="NOT_MET\\|Have not met player 1"):
        lq.parse_deal_options_response(["ERR:NOT_MET|Have not met player 1"])


def test_deal_options_normal_response_still_parses():
    opts = lq.parse_deal_options_response(
        ["CIV|1|Egypt", "ECON|25|3|0|100|5|10", "OB|0", "ALLIANCE|0|"]
    )
    assert opts.other_civ_name == "Egypt"
    assert opts.our_gold == 25


# --- D8: quiet-turn event entries --------------------------------------------


BOARD = "\n== Situation ==\nEMPIRE\n  T2 Ancient Era | Sumeria (Gilgamesh) | score 8\nATTENTION\n  none"


def test_event_text_quiet_turn_is_one_line():
    assert Broadcast.event_text("Turn 1 -> 2 | Score: 8" + BOARD) == "quiet turn"


def test_event_text_never_copies_the_board():
    report = "Turn 5 -> 6 | Score: 9\n\n== Events ==\n  !!! Barbarians" + BOARD
    out = Broadcast.event_text(report)
    assert out == "!!! Barbarians"
    assert "EMPIRE" not in out and "Situation" not in out


def test_event_text_includes_action_required():
    report = (
        "Turn 6 -> 7 | Score: 9\n\n== Events ==\n  >> Uruk finished building UNIT_SCOUT. Now: nothing.\n"
        "\n== Action Required ==\n  * Choose Production  -> Use: set_city_production\n"
        "\n== Notifications ==\n  - something old" + BOARD
    )
    out = Broadcast.event_text(report)
    assert out == (
        ">> Uruk finished building UNIT_SCOUT. Now: nothing. | "
        "* Choose Production  -> Use: set_city_production"
    )


def test_event_text_action_required_only():
    report = "Turn 6 -> 7\n\n== Action Required ==\n  * Choose Research" + BOARD
    assert Broadcast.event_text(report) == "* Choose Research"


# --- D11: no map land total in the overview ----------------------------------


def test_overview_explored_line_has_no_land_total():
    ov = _ov()
    ov.explored_land = 22
    ov.total_land = 1627
    out = nr.narrate_overview(ov)
    line = next(ln for ln in out.splitlines() if ln.startswith("Explored:"))
    assert "1627" not in line
    assert line == "Explored: 1% of land (22 land tiles revealed)"


# --- D1: restart_and_load waits for the main menu through the tuner -----------

import asyncio  # noqa: E402

from civ_mcp import game_launcher  # noqa: E402

INTRO = {0: "Main State", 1: "DebugHotloadCache"}
MENU = {0: "Main State", 1: "DebugHotloadCache", 24: "MainMenu", 30: "FrontEnd"}
GAME = {0: "Main State", 2: "GameCore_Tuner", 94: "InGame"}


class FakeConn:
    """Serves a scripted series of state lists, one per reconnect."""

    def __init__(self, series, fail_first=0):
        self.series = list(series)
        self.fail_first = fail_first
        self.lua_states = {}
        self.reconnects = 0
        self.disconnected = False

    async def reconnect(self):
        self.reconnects += 1
        if self.fail_first > 0:
            self.fail_first -= 1
            raise asyncio.IncompleteReadError(b"", 8)
        self.lua_states = self.series.pop(0) if len(self.series) > 1 else self.series[0]

    async def disconnect(self):
        self.disconnected = True


async def _no_sleep(_):
    await asyncio.sleep(0)


def test_wait_for_main_menu_polls_until_frontend_and_mainmenu():
    conn = FakeConn([INTRO, INTRO, {**INTRO, 30: "FrontEnd"}, MENU], fail_first=1)
    status, _ = asyncio.run(
        game_launcher.wait_for_main_menu(conn, timeout=60, interval=5, sleep=_no_sleep)
    )
    assert status == "menu"
    assert conn.reconnects == 5  # one failed handshake, three not-yet lists, then the menu
    assert conn.disconnected


def test_wait_for_main_menu_reports_a_loaded_game():
    conn = FakeConn([GAME])
    status, _ = asyncio.run(
        game_launcher.wait_for_main_menu(conn, timeout=60, sleep=_no_sleep)
    )
    assert status == "ingame"


def test_wait_for_main_menu_times_out():
    conn = FakeConn([INTRO])
    status, _ = asyncio.run(
        game_launcher.wait_for_main_menu(conn, timeout=0, sleep=_no_sleep)
    )
    assert status == "timeout"


def test_menu_wait_budget_and_ocr_backstop():
    assert game_launcher._MENU_WAIT_TIMEOUT == 360
    assert game_launcher._MENU_WAIT_INTERVAL == 5
    assert game_launcher._SINGLE_PLAYER_OCR_TIMEOUT == 120


def test_load_save_from_menu_waits_for_menu_before_ocr(tmp_path, monkeypatch):
    (tmp_path / "AutoSave_0007.Civ6Save").write_text("x")
    monkeypatch.setattr(game_launcher, "SAVE_DIR", str(tmp_path))
    monkeypatch.setattr(game_launcher, "is_game_running", lambda: True)
    order = []

    async def fake_wait(conn):
        order.append(("wait", conn))
        return "menu", 212.0

    def fake_nav(save_name, tab, menu_note=None):
        order.append(("nav", save_name, tab, menu_note))
        return "Save loading (50s). Steps: ..."

    monkeypatch.setattr(game_launcher, "wait_for_main_menu", fake_wait)
    monkeypatch.setattr(game_launcher, "_navigate_to_save_sync", fake_nav)
    sentinel = object()
    out = asyncio.run(game_launcher.load_save_from_menu("AutoSave_0007", sentinel))
    assert out.startswith("Save loading")
    assert order == [
        ("wait", sentinel),
        ("nav", "AutoSave_0007", "Autosaves", "Main menu up after 212s (tuner)"),
    ]


def test_single_player_ocr_wait_is_120s(monkeypatch):
    seen = {}

    def fake_click(text, timeout=30, **kw):
        seen[text] = timeout
        return False

    monkeypatch.setattr(game_launcher, "_require_gui_deps", lambda: None)
    monkeypatch.setattr(game_launcher, "is_game_running", lambda: True)
    monkeypatch.setattr(game_launcher, "_dismiss_crash_dialog", lambda: False)
    monkeypatch.setattr(game_launcher, "_click_aspyr_launcher_sync", lambda: None)
    monkeypatch.setattr(game_launcher, "_click_text", fake_click)
    out = game_launcher._navigate_to_save_sync("AutoSave_0007", "Autosaves", "Main menu up after 5s (tuner)")
    assert seen["Single Player"] == 120
    assert out.startswith("FAILED")
    assert "Main menu up after 5s (tuner)" in out


def test_restart_and_load_passes_the_connection_on(monkeypatch):
    calls = []

    async def fake_dismiss():
        return []

    async def fake_kill():
        return "killed"

    async def fake_launch():
        return "Game launched. Process started after 2s, FireTuner port is open."

    async def fake_load(save_name=None, conn=None):
        calls.append((save_name, conn))
        return "Save loading"

    monkeypatch.setattr(game_launcher, "dismiss_crash_dialogs", fake_dismiss)
    monkeypatch.setattr(game_launcher, "kill_game", fake_kill)
    monkeypatch.setattr(game_launcher, "launch_game", fake_launch)
    monkeypatch.setattr(game_launcher, "load_save_from_menu", fake_load)
    sentinel = object()
    out = asyncio.run(game_launcher.restart_and_load(None, sentinel))
    assert calls == [(None, sentinel)]
    assert "Load: Save loading" in out


# --- D5: queued multi-turn moves are not cancelled by our skip paths ---------


def test_skip_and_fortify_remaining_leave_queued_moves_alone():
    for q in (lq.build_skip_remaining_units(), lq.build_fortify_remaining_units()):
        assert "_queuedDest(unit) == nil" in q
        assert "local function _queuedDest(u)" in q


def test_end_turn_units_blocker_never_skips_a_moving_unit():
    import inspect

    from civ_mcp import end_turn

    src = inspect.getsource(end_turn.execute_end_turn)
    assert "_queuedDest(u) == nil and UnitManager.CanStartOperation(u, skipOp.Hash" in src
    assert "{_LUA_QUEUED_DEST}" in src


# --- Round 2 -------------------------------------------------------------------


def test_load_entry_names_the_save_the_menu_loaded():
    from civ_mcp import server

    res = (
        "Kill: ok | Launch: ok | Load: Save loading (49s). Steps: Main menu up after "
        "188s (tuner), Clicked Single Player, Clicked Load Game, Clicked Autosaves "
        "filter, Selected save AutoSave_0007, Clicked Load Game button, Clicked CONTINUE"
    )
    assert server._loaded_save_name(None, res) == "AutoSave_0007"
    assert server._loaded_save_name("0_MCP_0012", res) == "0_MCP_0012"
    assert server._loaded_save_name(None, "Save loading. Steps: x") == "latest autosave"


def test_city_states_query_skips_free_cities():
    from civ_mcp.lua.governance import build_city_states_query

    q = build_city_states_query()
    assert "IsFreeCities" in q and "CIVILIZATION_FREE_CITIES" in q
    assert "not isFreeCities(i)" in q


def test_activity_unknown_is_flagged_in_units_and_board():
    u = lq.parse_units_response([BASE_UNIT + "|0|?"])[0]
    assert "[activity unknown]" in nr.narrate_units([u])
    out = format_situation(Situation(overview=_ov(), units=[u]))
    assert "(unit activity unavailable)" in out
    ok = lq.parse_units_response([BASE_UNIT + "|0|awake"])[0]
    assert "(unit activity unavailable)" not in format_situation(
        Situation(overview=_ov(), units=[ok])
    )


class _Svc:
    def __init__(self):
        self.starts = 0
        self.stops = 0

    def start(self):
        self.starts += 1

    async def stop(self):
        self.stops += 1


def test_background_pause_nests_and_restarts_once():
    import types

    from civ_mcp import server

    popup, cam, dog = _Svc(), _Svc(), _Svc()
    cam.clear = lambda: None
    life = types.SimpleNamespace(popup_watcher=popup, camera=cam, watchdog=dog)
    ctx = types.SimpleNamespace(request_context=types.SimpleNamespace(lifespan_context=life))

    async def go():
        async with server._background_paused(ctx):
            async with server._background_paused(ctx):
                pass
            assert (popup.starts, cam.starts, dog.starts) == (0, 0, 0)

    asyncio.run(go())
    for svc in (popup, cam, dog):
        assert svc.stops == 1 and svc.starts == 1
    assert life.pause_depth == 0


def test_service_start_twice_keeps_one_task():
    from civ_mcp.game_over_watchdog import GameOverWatchdog
    from civ_mcp.spectator import CameraController, PopupWatcher

    async def go():
        made = []
        for svc in (
            PopupWatcher(conn=None),
            CameraController(conn=None),
            GameOverWatchdog(gs=None, logger=None),
        ):
            svc.start()
            first = svc._task
            svc.start()
            made.append(svc._task is first)
            await svc.stop()
            assert svc._task is None and first.cancelled()
            svc.start()  # a stopped service starts again
            assert svc._task is not None and svc._task is not first
            await svc.stop()
        return made

    assert asyncio.run(go()) == [True, True, True]

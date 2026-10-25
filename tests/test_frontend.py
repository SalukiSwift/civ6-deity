"""FrontEnd game creation without the game: state resolution, verification,
the BEGIN GAME matcher and create_game's control flow against a fake tuner."""

import asyncio

import pytest

from civ_mcp import connection as cn
from civ_mcp import newgame as ng
from civ_mcp import tuner_client as tc

MENU = {0: "Main State", 1: "DebugHotloadCache", 24: "MainMenu", 30: "FrontEnd"}
GAME = {0: "Main State", 3: "GameCore_Tuner", 119: "InGame"}
LOADING = {0: "Main State", 1: "DebugHotloadCache", 2: "LoadScreen"}


def _run(coro):
    return asyncio.run(coro)


async def _nosleep(_s):
    return None


# -- state list parsing and resolution ---------------------------------------


def test_parse_state_list_pairs_and_junk():
    items = ["0", "Main State", "1", "DebugHotloadCache", "24", "MainMenu", "30", "FrontEnd"]
    assert cn.parse_state_list(items) == {
        0: "Main State",
        1: "DebugHotloadCache",
        24: "MainMenu",
        30: "FrontEnd",
    }
    # The APP: reply is also tag 4 but has no index/name pairs.
    assert cn.parse_state_list(["Civ6", "Sid Meier's Civilization 6", "C:\\x"]) == {}


def test_find_state_by_name_lowest_index():
    states = {9: "LoadGameMenu", 5: "LoadGameMenu", 30: "FrontEnd"}
    assert cn.find_state(states, "LoadGameMenu") == 5
    assert cn.find_state(states, "FrontEnd") == 30
    assert cn.find_state(states, "InGame") is None


def test_query_states_skips_output_and_parses_lsq(monkeypatch):
    conn = cn.GameConnection()
    conn._reader, conn._writer = object(), object()

    async def connected():
        return None

    sent = []

    async def send(_w, tag, payload):
        sent.append((tag, payload))

    async def drain(_r, timeout=0.5):
        return []

    replies = [
        tc.Message(tag=-1, payload="O\x00MainMenu: hello"),
        tc.Message(tag=4, payload="0\x00Main State\x0024\x00MainMenu\x0030\x00FrontEnd"),
    ]

    async def recv(_r, timeout=2.0):
        return replies.pop(0) if replies else None

    monkeypatch.setattr(conn, "ensure_connected", connected)
    monkeypatch.setattr(tc, "send_message", send)
    monkeypatch.setattr(tc, "drain_messages", drain)
    monkeypatch.setattr(tc, "recv_message_timeout", recv)
    states = _run(conn.query_states())
    assert sent == [(tc.TAG_HANDSHAKE, "LSQ:")]
    assert states[30] == "FrontEnd" and conn.lua_states == states
    assert conn.gamecore_index is None and conn.ingame_index is None


def _frontend_conn(monkeypatch, states):
    conn = cn.GameConnection()
    ran = []

    async def query(timeout=5.0):
        return dict(states)

    async def locked(idx, lua, timeout):
        ran.append((idx, lua))
        return ["ok"]

    monkeypatch.setattr(conn, "query_states", query)
    monkeypatch.setattr(conn, "_locked_execute", locked)
    return conn, ran


def test_execute_frontend_runs_in_mainmenu_resolved_by_name(monkeypatch):
    renumbered = {0: "Main State", 7: "MainMenu", 12: "FrontEnd"}
    conn, ran = _frontend_conn(monkeypatch, renumbered)
    assert _run(conn.execute_frontend("print(1)")) == ["ok"]
    assert ran == [(7, "print(1)")]


def test_execute_frontend_refuses_without_frontend(monkeypatch):
    conn, ran = _frontend_conn(monkeypatch, GAME)
    with pytest.raises(cn.StateNotFound, match="main menu"):
        _run(conn.execute_frontend("print(1)"))
    assert ran == []


# -- setup result and verification -------------------------------------------


def test_run_lua_wraps_setup_in_pcall_with_sentinel():
    lua = ng.build_frontend_run_lua("LEADER_GILGAMESH")
    assert lua.startswith("local ok, err = pcall(function()")
    assert "NEWGAME|" in lua and lua.rstrip().endswith('print("---END---")')
    assert ng.build_frontend_setup_lua("LEADER_GILGAMESH") in lua


def test_parse_setup_result():
    assert ng.parse_setup_result(["Building Headless Game Setup", "NEWGAME|true|nil"]) is None
    assert ng.parse_setup_result(["NEWGAME|false|attempt to call a nil value"]) == (
        "attempt to call a nil value"
    )
    assert "no result" in ng.parse_setup_result([])


GOOD = [
    "NG|ruleset|RULESET_EXPANSION_2",
    "NG|favor|true",
    "NG|difficulty|DIFFICULTY_DEITY",
    "NG|player_difficulty|DIFFICULTY_DEITY",
    "NG|speed|GAMESPEED_STANDARD",
    "NG|map_script|Fractal.lua",
    "NG|map_size|MAPSIZE_STANDARD",
    "NG|grid|84x54",
    "NG|leader|LEADER_GILGAMESH",
    "NG|turn|1",
]


def test_verify_all_settings_match():
    found = ng.parse_verify(GOOD)
    assert found["turn"] == "1"
    assert ng.check_started_game(found, "LEADER_GILGAMESH") == []


def test_verify_names_each_mismatch():
    lines = [
        ln.replace("RULESET_EXPANSION_2", "RULESET_STANDARD")
        .replace("NG|favor|true", "NG|favor|false")
        .replace("MAPSIZE_STANDARD", "MAPSIZE_SMALL")
        for ln in GOOD
        if not ln.startswith("NG|grid")
    ]
    out = ng.check_started_game(ng.parse_verify(lines), "LEADER_AMANITORE")
    joined = "; ".join(out)
    assert "ruleset: expected RULESET_EXPANSION_2, got RULESET_STANDARD" in joined
    assert "favor: expected true, got false" in joined
    assert "map_size: expected MAPSIZE_STANDARD, got MAPSIZE_SMALL" in joined
    assert "grid: expected 84x54, got <missing>" in joined
    assert "leader: expected LEADER_AMANITORE, got LEADER_GILGAMESH" in joined
    assert len(out) == 5


def test_verify_lua_reads_from_ingame_apis():
    lua = ng.VERIFY_LUA
    for needle in (
        "GameConfiguration.GetRuleSet()",
        "GetFavor",
        '"GAME_HANDICAP"',
        "GetHandicapTypeID()",
        "MapConfiguration.GetScript()",
        "Map.GetGridSize()",
        "GetLeaderTypeName()",
    ):
        assert needle in lua
    assert lua.rstrip().endswith('print("---END---")')


def test_started_line():
    assert ng.started_line("LEADER_GILGAMESH") == (
        "Game started: LEADER_GILGAMESH, Deity, Fractal, Standard, Gathering Storm"
    )


# -- BEGIN GAME matcher (OCR lines captured live at 2560x1440) ---------------

LIVE_OCR = [
    ("SUMERIAN EMPIRE", 1060, 357, 205, 20),
    ("Age... to man taking his first upright steps, you have come far. Now beginsyour", 1048, 439, 517, 12),
    ("for it is time to begin your epic tale.", 907, 589, 232, 12),
    ("FEATURES & ABILITIES", 1060, 620, 160, 12),
    ("BEGIN (GAIME", 1059, 1078, 111, 14),
]


def test_find_begin_button_reads_noisy_label():
    assert ng.find_begin_button(LIVE_OCR, 0, 1440) == (1059, 1078)


def test_find_begin_button_ignores_intro_text_and_top_half():
    assert ng.find_begin_button(LIVE_OCR[:4], 0, 1440) is None
    assert ng.find_begin_button([("Begin Game", 100, 300, 50, 10)], 0, 1440) is None
    assert ng.leader_screen_up(LIVE_OCR)
    assert not ng.leader_screen_up([("LOADING, PLEASE WAIT...", 1, 1, 1, 1)])


# -- create_game control flow against a fake tuner ---------------------------


class FakeConn:
    """Scripted tuner: each reconnect() takes the next state list."""

    def __init__(self, phases, verify=GOOD, setup=("NEWGAME|true|nil",)):
        self.phases = list(phases)
        self.lua_states: dict[int, str] = {}
        self.verify = list(verify)
        self.setup = list(setup)
        self.calls: list[str] = []

    async def disconnect(self):
        self.calls.append("disconnect")

    async def reconnect(self):
        self.calls.append("reconnect")
        nxt = self.phases.pop(0) if self.phases else self.lua_states
        if isinstance(nxt, Exception):
            raise nxt
        self.lua_states = dict(nxt)

    async def execute_once(self, name, lua, timeout=10.0):
        self.calls.append(f"once:{name}")
        assert lua == ng.EXIT_TO_MENU_LUA
        self.lua_states = {}
        return []

    async def execute_frontend(self, lua, timeout=10.0):
        self.calls.append("frontend")
        if cn.find_state(self.lua_states, cn.FRONTEND_STATE) is None:
            raise cn.StateNotFound("Not at the main menu (no FrontEnd state).")
        return list(self.setup)

    async def execute_write(self, lua, timeout=5.0):
        self.calls.append("write")
        assert lua == ng.VERIFY_LUA
        return list(self.verify)


def _create(conn, leader="LEADER_GILGAMESH", click="ocr", **kw):
    clicks = []

    def fake_click(timeout):
        clicks.append(timeout)
        return click

    res = _run(
        ng.create_game(
            conn,
            leader,
            click=fake_click,
            sleep=_nosleep,
            menu_timeout=kw.get("menu_timeout", 5.0),
            click_timeout=7.0,
            ingame_timeout=kw.get("ingame_timeout", 5.0),
        )
    )
    return res, clicks


def test_create_game_from_main_menu():
    conn = FakeConn([MENU, GAME])
    res, clicks = _create(conn)
    assert res.ok and res.message == ng.started_line("LEADER_GILGAMESH")
    assert res.click == "ocr" and not res.left_game and clicks == [7.0]
    assert conn.calls.index("frontend") < conn.calls.index("write")
    assert "once:InGame" not in conn.calls


def test_create_game_leaves_running_game_first():
    conn = FakeConn([GAME, LOADING, MENU, GAME])
    res, _ = _create(conn)
    assert res.ok and res.left_game
    assert conn.calls.index("once:InGame") < conn.calls.index("frontend")
    assert "menu" in res.timings and "ingame" in res.timings


def test_create_game_not_at_main_menu():
    conn = FakeConn([LOADING])
    res, clicks = _create(conn)
    assert not res.ok and "not at the main menu" in res.message
    assert clicks == [] and "frontend" not in conn.calls


def test_create_game_cannot_connect():
    conn = FakeConn([ConnectionError("refused")] * 5)
    res, clicks = _create(conn)
    assert not res.ok and "cannot reach Civ 6" in res.message and clicks == []


def test_create_game_retries_a_dropped_first_connect():
    conn = FakeConn([OSError("WinError 64"), MENU, GAME])
    res, _ = _create(conn)
    assert res.ok


def test_create_game_setup_lua_error_is_reported():
    conn = FakeConn([MENU], setup=["NEWGAME|false|boom"])
    res, clicks = _create(conn)
    assert not res.ok and "setup Lua failed: boom" in res.message and clicks == []


def test_create_game_no_click_when_not_foreground():
    conn = FakeConn([MENU])
    res, _ = _create(conn, click="not-foreground")
    assert not res.ok and res.click == "not-foreground"
    assert "not the foreground window" in res.message and "write" not in conn.calls


def test_create_game_times_out_waiting_for_ingame():
    conn = FakeConn([MENU, LOADING])
    res, _ = _create(conn, ingame_timeout=0.0)
    assert not res.ok and "did not come up" in res.message


def test_create_game_reports_mismatch_and_leaves_game():
    bad = [ln.replace("Fractal.lua", "Continents.lua") for ln in GOOD]
    conn = FakeConn([MENU, GAME], verify=bad)
    res, _ = _create(conn)
    assert not res.ok
    assert "map_script: expected Fractal.lua, got Continents.lua" in res.message
    assert "left running" in res.message

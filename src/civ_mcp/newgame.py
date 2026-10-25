"""Fixed-settings new game: leader list, settings card, setup Lua and the
live path that creates the game from the main menu (create_game).

Configuration keys (from Base/Assets/Configuration/Data/SetupParameters.xml):
  Ruleset        Game / RULESET           (SetRuleSet)
  GameDifficulty Game / GAME_HANDICAP     (Hash=1: written as DB.MakeHash)
  GameSpeeds     Game / GAME_SPEED_TYPE   (Hash=1)
  MapSize        Map  / MAP_SIZE          (Hash=1)
  Map            Map  / MAP_SCRIPT        (plain string, set with SetScript)
"""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import TYPE_CHECKING, Awaitable, Callable

from civ_mcp.connection import (
    FRONTEND_STATE,
    MAIN_MENU_STATE,
    StateNotFound,
    find_state,
)
from civ_mcp.lua._helpers import SENTINEL

if TYPE_CHECKING:
    from civ_mcp.connection import GameConnection

GAME_SETTINGS = {
    "ruleset": "RULESET_EXPANSION_2",
    "difficulty": "DIFFICULTY_DEITY",
    "map_script": "Fractal.lua",
    "map_size": "MAPSIZE_STANDARD",
    "speed": "GAMESPEED_STANDARD",
}

_LEADERS_FILE = Path(__file__).parent / "data" / "leaders.txt"
_NAME_RE = re.compile(r"^LEADER_[A-Z0-9_]+$")


@lru_cache(maxsize=1)
def _leaders() -> frozenset[str]:
    return frozenset(_LEADERS_FILE.read_text(encoding="utf-8").split())


def valid_leader(leader: str) -> bool:
    return bool(leader) and leader in _leaders()


def leader_display(leader: str) -> str:
    return leader.removeprefix("LEADER_").replace("_", " ").title()


def settings_card(leader: str) -> str:
    """Human-readable list April can click through if automation fails."""
    return "\n".join(
        [
            "New game settings (Advanced Setup, single player):",
            "  Game rules:  Gathering Storm (Expansion 2)",
            "  Difficulty:  Deity",
            "  Map type:    Fractal",
            "  Map size:    Standard",
            "  Game speed:  Standard",
            f"  Leader:      {leader_display(leader)} ({leader})",
        ]
    )


def build_frontend_setup_lua(leader: str) -> str:
    """Lua that creates the game, mirroring Play Now. Runs in the MainMenu state
    (where GameSetupLogic is loaded), only while FrontEnd is present.

    Order matters. MainMenu.lua's Play Now path calls BuildHeadlessGameSetup,
    which builds the parameter set from whatever GameConfiguration holds and
    then rewrites every parameter whose value is missing or invalid for the
    current ruleset back to its default (Prince, Continents, Small, ...). So
    the ruleset (which selects the valid map and leader domains) and every
    other setting must be written first; setting them afterwards would be
    overwritten by the refresh, or ignored once the game is hosted.
    """
    if not _NAME_RE.match(leader):
        raise ValueError(f"bad leader name {leader!r}")
    s = GAME_SETTINGS
    return f"""\
GameConfiguration.SetToDefaults();
GameConfiguration.SetRuleSet("{s["ruleset"]}");
GameConfiguration.SetValue("GAME_HANDICAP", DB.MakeHash("{s["difficulty"]}"));
GameConfiguration.SetValue("GAME_SPEED_TYPE", DB.MakeHash("{s["speed"]}"));
MapConfiguration.SetScript("{s["map_script"]}");
MapConfiguration.SetValue("MAP_SIZE", DB.MakeHash("{s["map_size"]}"));
PlayerConfigurations[0]:SetLeaderTypeName("{leader}");
BuildHeadlessGameSetup();
RebuildPlayerParameters(true);
GameSetup_RefreshParameters();
ReleasePlayerParameters();
HideGameSetup();
Network.HostGame(ServerType.SERVER_TYPE_NONE);
"""


def build_frontend_run_lua(leader: str) -> str:
    """The setup Lua wrapped for the tuner: pcall, a result line, sentinel."""
    return (
        "local ok, err = pcall(function()\n"
        + build_frontend_setup_lua(leader)
        + "end)\n"
        + 'print("NEWGAME|" .. tostring(ok) .. "|" .. tostring(err))\n'
        + f'print("{SENTINEL}")\n'
    )


def parse_setup_result(lines: list[str]) -> str | None:
    """None when the setup Lua ran to the end, else the error text."""
    for line in lines:
        if line.startswith("NEWGAME|"):
            _, ok, err = line.split("|", 2)
            return None if ok == "true" else err
    return "no result line from the setup Lua"


# Leaves the current match for the main menu, without saving: the call the
# in-game menu's Exit to Main Menu button makes once its "are you sure"
# dialog is answered Yes (InGameTopOptionsMenu.lua OnYes).
EXIT_TO_MENU_LUA = f'Events.ExitToMainMenu(); print("{SENTINEL}")'

# Grid of MAPSIZE_STANDARD (Base Maps table: GridWidth 84, GridHeight 54).
STANDARD_GRID = "84x54"

# Run in InGame (GetFavor is nil in GameCore). Each setting is printed as the
# database type whose hash matches the configuration value.
VERIFY_LUA = (
    """\
local me = Game.GetLocalPlayer()
local pc = PlayerConfigurations[me]
local function typeOf(rows, key, hash)
  for row in rows() do
    if DB.MakeHash(row[key]) == hash then return row[key] end
  end
  return "hash:" .. tostring(hash)
end
local w, h = Map.GetGridSize()
print("NG|ruleset|" .. tostring(GameConfiguration.GetRuleSet()))
print("NG|favor|" .. tostring(Players[me] ~= nil and Players[me].GetFavor ~= nil))
print("NG|difficulty|" .. typeOf(GameInfo.Difficulties, "DifficultyType", GameConfiguration.GetValue("GAME_HANDICAP")))
print("NG|player_difficulty|" .. typeOf(GameInfo.Difficulties, "DifficultyType", pc:GetHandicapTypeID()))
print("NG|speed|" .. typeOf(GameInfo.GameSpeeds, "GameSpeedType", GameConfiguration.GetValue("GAME_SPEED_TYPE")))
print("NG|map_script|" .. tostring(MapConfiguration.GetScript()))
print("NG|map_size|" .. typeOf(GameInfo.Maps, "MapSizeType", MapConfiguration.GetValue("MAP_SIZE")))
print("NG|grid|" .. tostring(w) .. "x" .. tostring(h))
print("NG|leader|" .. tostring(pc:GetLeaderTypeName()))
print("NG|turn|" .. tostring(Game.GetCurrentGameTurn()))
"""
    + f'print("{SENTINEL}")\n'
)


def expected_settings(leader: str) -> dict[str, str]:
    s = GAME_SETTINGS
    return {
        "ruleset": s["ruleset"],
        "favor": "true",  # Gathering Storm's Diplomatic Favor exists
        "difficulty": s["difficulty"],
        "player_difficulty": s["difficulty"],
        "speed": s["speed"],
        "map_script": s["map_script"],
        "map_size": s["map_size"],
        "grid": STANDARD_GRID,
        "leader": leader,
        "turn": "1",  # a fresh game; new_game seeds the turn high-water mark from it
    }


def parse_verify(lines: list[str]) -> dict[str, str]:
    found: dict[str, str] = {}
    for line in lines:
        if line.startswith("NG|"):
            parts = line.split("|", 2)
            if len(parts) == 3:
                found[parts[1]] = parts[2]
    return found


def check_started_game(found: dict[str, str], leader: str) -> list[str]:
    """One line per setting that does not match; empty when all match."""
    return [
        f"{key}: expected {want}, got {found.get(key, '<missing>')}"
        for key, want in expected_settings(leader).items()
        if found.get(key) != want
    ]


def started_line(leader: str) -> str:
    return f"Game started: {leader}, Deity, Fractal, Standard, Gathering Storm"


@dataclass
class StartResult:
    ok: bool
    message: str
    found: dict[str, str] = field(default_factory=dict)
    left_game: bool = False
    click: str = ""
    timings: dict[str, float] = field(default_factory=dict)


_CLICK_ERRORS = {
    "not-foreground": (
        "BEGIN GAME is ready but Civ 6 was not the foreground window, so no "
        "click was sent. Click BEGIN GAME in Civ 6, then call get_game_overview."
    ),
    "not-found": (
        "The load screen never showed BEGIN GAME. The game is still running; "
        "check the screen, click BEGIN GAME if it is there, then call "
        "get_game_overview."
    ),
    "no-window": "The Civ 6 window was not found during loading.",
}


async def wait_for_states(
    conn: "GameConnection",
    names: tuple[str, ...],
    timeout: float,
    interval: float = 3.0,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> dict[int, str] | None:
    """Reconnect until every state in `names` is loaded, or None on timeout.

    During loading the tuner port accepts TCP but answers nothing, so only
    a full handshake (state list) counts as ready.
    """
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while True:
        try:
            await conn.reconnect()
            if all(find_state(conn.lua_states, n) is not None for n in names):
                return conn.lua_states
        except (ConnectionError, OSError, asyncio.TimeoutError):
            pass
        if loop.time() >= deadline:
            await conn.disconnect()
            return None
        await sleep(interval)


async def create_game(
    conn: "GameConnection",
    leader: str,
    *,
    click: Callable[[float], str] | None = None,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    menu_timeout: float = 120.0,
    click_timeout: float = 300.0,
    ingame_timeout: float = 120.0,
) -> StartResult:
    """Create the fixed game from the main menu and verify it.

    In a match: leaves it for the main menu first (no save). At the main
    menu: runs the Play Now setup with the fixed settings in the MainMenu
    state, clicks BEGIN GAME on the load screen, waits for InGame and
    GameCore_Tuner, and checks every setting and the leader in InGame.
    Every wait is bounded.
    """
    click = click or click_begin_game_sync
    loop = asyncio.get_running_loop()
    t0 = loop.time()
    timings: dict[str, float] = {}

    def mark(name: str) -> None:
        timings[name] = round(loop.time() - t0, 1)

    # A background poller may have been cancelled mid-handshake; the game
    # serves one tuner client at a time and drops a new one until it notices
    # the old socket closed. So start clean and allow a few attempts.
    await conn.disconnect()
    for attempt in range(5):
        await sleep(1.0 if attempt == 0 else 2.0)
        try:
            await conn.reconnect()
            if conn.lua_states:
                break
        except (ConnectionError, OSError, asyncio.TimeoutError):
            pass
    else:
        await conn.disconnect()
        return StartResult(
            False,
            "Error: cannot reach Civ 6 on the tuner port. Launch the game to "
            "the main menu first (launch_game).",
        )
    states = conn.lua_states
    left = False
    if find_state(states, "InGame") is not None:
        try:
            await conn.execute_once("InGame", EXIT_TO_MENU_LUA, timeout=5.0)
        except (ConnectionError, OSError, asyncio.IncompleteReadError):
            pass  # the game may drop the socket while it leaves the match
        left = True
        await conn.disconnect()
        await sleep(3.0)
        if await wait_for_states(
            conn, (FRONTEND_STATE, MAIN_MENU_STATE), menu_timeout, sleep=sleep
        ) is None:
            return StartResult(
                False,
                f"Error: left the game, but the main menu did not come back "
                f"within {menu_timeout:.0f}s.",
                left_game=True,
            )
        mark("menu")
        await sleep(3.0)  # let the main menu finish showing (OnShow resets config)
    elif find_state(states, FRONTEND_STATE) is None:
        return StartResult(
            False,
            "Error: not at the main menu (no FrontEnd state) and no game in "
            "progress. Wait until the main menu shows, then call new_game again.",
        )

    try:
        lines = await conn.execute_frontend(build_frontend_run_lua(leader), timeout=15.0)
    except StateNotFound as e:
        return StartResult(False, f"Error: {e}", left_game=left)
    err = parse_setup_result(lines)
    if err is not None:
        return StartResult(
            False, f"Error: the game setup Lua failed: {err}", left_game=left
        )
    mark("hosted")
    await conn.disconnect()

    method = await asyncio.to_thread(click, click_timeout)
    mark("click")
    if method not in ("ocr", "positional"):
        return StartResult(
            False,
            "Error: " + _CLICK_ERRORS.get(method, f"BEGIN GAME click failed ({method})."),
            left_game=left,
            click=method,
            timings=timings,
        )
    if await wait_for_states(
        conn, ("InGame", "GameCore_Tuner"), ingame_timeout, sleep=sleep
    ) is None:
        return StartResult(
            False,
            f"Error: BEGIN GAME was clicked ({method}) but the game did not come "
            f"up within {ingame_timeout:.0f}s.",
            left_game=left,
            click=method,
            timings=timings,
        )
    mark("ingame")
    found = parse_verify(await conn.execute_write(VERIFY_LUA))
    mismatches = check_started_game(found, leader)
    if mismatches:
        return StartResult(
            False,
            "Error: the new game does not match the fixed settings: "
            + "; ".join(mismatches)
            + ". The game is left running for inspection.",
            found=found,
            left_game=left,
            click=method,
            timings=timings,
        )
    return StartResult(
        True, started_line(leader), found=found, left_game=left, click=method,
        timings=timings,
    )


# ---------------------------------------------------------------------------
# BEGIN GAME click (Windows). The tuner stops serving Lua during the load
# screen, so the leader screen's BEGIN GAME button has to be clicked.
# ---------------------------------------------------------------------------

# Centre of the BEGIN GAME ribbon on the leader load screen, as fractions of
# the game's client area (measured at 2560x1440: pixel 1059,1078).
BEGIN_GAME_POS = (0.414, 0.749)
# Leader-screen text that shows the load screen is past "LOADING, PLEASE WAIT".
_LEADER_SCREEN_TEXT = ("joins the world stage", "features & abilities")

_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000


def find_begin_button(
    ocr: list[tuple[str, int, int, int, int]], win_y: int, win_h: int
) -> tuple[int, int] | None:
    """Screen point of the BEGIN GAME button in OCR lines, or None.

    Windows OCR reads the small-caps label loosely ("BEGIN (GAIME"), so match
    a short line starting with "begin" in the lower half of the window. The
    leader text above it contains "begins your" and "to begin your" inside
    longer lines, which this skips.
    """
    for text, x, y, _w, _h in ocr:
        norm = re.sub(r"[^a-z ]", "", text.lower()).strip()
        if norm.startswith("begin") and len(norm) <= 14 and y > win_y + win_h * 0.5:
            return x, y
    return None


def leader_screen_up(ocr: list[tuple[str, int, int, int, int]]) -> bool:
    text = " ".join(t for t, *_ in ocr).lower()
    return any(s in text for s in _LEADER_SCREEN_TEXT)


def foreground_civ_window() -> tuple[int, int] | None:
    """(hwnd, pid) of the foreground window if a Civ 6 process owns it, else None."""
    import ctypes
    import ctypes.wintypes
    import os

    from civ_mcp.game_launcher import _PROCESS_NAMES

    user32 = ctypes.windll.user32
    kernel32 = ctypes.windll.kernel32
    hwnd = user32.GetForegroundWindow()
    if not hwnd:
        return None
    pid = ctypes.wintypes.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    handle = kernel32.OpenProcess(_PROCESS_QUERY_LIMITED_INFORMATION, False, pid.value)
    if not handle:
        return None
    try:
        buf = ctypes.create_unicode_buffer(1024)
        size = ctypes.wintypes.DWORD(len(buf))
        if not kernel32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size)):
            return None
        exe = os.path.basename(buf.value).lower()
    finally:
        kernel32.CloseHandle(handle)
    if exe not in {n.lower() for n in _PROCESS_NAMES}:
        return None
    return int(hwnd), int(pid.value)


def click_target_ok(
    win: object, point: tuple[int, int], fg: tuple[int, int] | None
) -> bool:
    """May we click `point` in `win`? Only if the foreground window is a Civ 6
    window and is `win` itself (same handle or same process), and the point
    lies inside `win`'s client rect. `win` is a game_launcher.WindowInfo."""
    if fg is None:
        return False
    fg_hwnd, fg_pid = fg
    if fg_hwnd != win.window_id and fg_pid != win.pid:
        return False
    x, y = point
    return win.x <= x < win.x + win.w and win.y <= y < win.y + win.h


def click_begin_game_sync(
    timeout: float = 300.0,
    fallback_after: float = 120.0,
    interval: float = 2.0,
    *,
    clock: Callable[[], float] | None = None,
    sleep: Callable[[float], None] | None = None,
) -> str:
    """Wait for the BEGIN GAME button on the load screen and click it once.

    OCR first; if the leader screen has been up for `fallback_after` seconds
    and OCR still has not read the button, click its known position. No
    input is sent unless the window being read is the foreground window and
    belongs to Civ 6, and the point is inside it (click_target_ok); at most
    one click is sent. Blocking; every wait is bounded by `timeout`.

    Returns "ocr" or "positional" (a click was sent), "not-foreground" (the
    button was ready but Civ 6 was not in front, so nothing was sent),
    "not-found" (the button never showed) or "no-window".
    """
    import logging
    import time

    from civ_mcp import game_launcher as gl

    log = logging.getLogger(__name__)
    clock = clock or time.monotonic
    sleep = sleep or time.sleep
    start = clock()
    leader_since: float | None = None
    ready = False
    saw_window = False
    while clock() - start < timeout:
        win = gl._find_game_window_win32()
        if win is not None and win.w > 0 and win.h > 0:
            saw_window = True
            try:
                img = gl._capture_window_win32(win.window_id)
                ocr = gl._ocr_winrt(img, win.x, win.y, win.w, win.h)
            except Exception:
                log.debug("BEGIN GAME: capture/OCR failed", exc_info=True)
                ocr = []
            point = find_begin_button(ocr, win.y, win.h)
            method = "ocr"
            if point is None and leader_screen_up(ocr):
                now = clock()
                leader_since = leader_since if leader_since is not None else now
                if now - leader_since >= fallback_after:
                    point = (
                        win.x + int(win.w * BEGIN_GAME_POS[0]),
                        win.y + int(win.h * BEGIN_GAME_POS[1]),
                    )
                    method = "positional"
            if point is not None:
                ready = True
                if click_target_ok(win, point, foreground_civ_window()):
                    log.info("BEGIN GAME: clicking %s via %s", point, method)
                    gl._click_win32(*point)
                    return method
                log.info("BEGIN GAME: button ready but this Civ 6 window is not in front")
        sleep(interval)
    if ready:
        return "not-foreground"
    return "not-found" if saw_window else "no-window"

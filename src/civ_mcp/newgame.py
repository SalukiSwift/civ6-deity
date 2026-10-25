"""Fixed-settings new game: leader list, settings card and FrontEnd setup Lua.

Configuration keys (from Base/Assets/Configuration/Data/SetupParameters.xml):
  Ruleset        Game / RULESET           (SetRuleSet)
  GameDifficulty Game / GAME_HANDICAP     (Hash=1: written as DB.MakeHash)
  GameSpeeds     Game / GAME_SPEED_TYPE   (Hash=1)
  MapSize        Map  / MAP_SIZE          (Hash=1)
  Map            Map  / MAP_SCRIPT        (plain string, set with SetScript)
"""

from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path

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
    """Lua for the FrontEnd state that creates the game, mirroring Play Now.

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

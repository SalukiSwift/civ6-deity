"""Fog seal B: rival data limited to what the game's own screens show."""

import hashlib
import inspect
from types import SimpleNamespace

from civ_mcp.diary import diary_path, identity_hash
from civ_mcp.lua.diplomacy import (
    build_deal_options_query,
    build_diplomacy_query,
    parse_diplomacy_response,
)
from civ_mcp.lua.economy import build_trade_destinations_query
from civ_mcp.lua.governance import build_city_states_query
from civ_mcp.lua.great_people import build_great_people_query
from civ_mcp.lua.models import RivalRow
from civ_mcp.lua.notifications import build_notifications_query
from civ_mcp.lua.overview import (
    build_diary_full_query,
    build_rival_snapshot_query,
    parse_rival_snapshot_response,
)
from civ_mcp.lua.religion import build_religion_status_query
from civ_mcp.lua.units import build_threat_scan_query
from civ_mcp.lua.victory import (
    build_victory_progress_query,
    build_victory_proximity_query,
    parse_victory_progress_response,
)


def test_rival_snapshot_has_no_yields_or_stockpiles():
    lua = build_rival_snapshot_query()
    for tok in (
        "GetYield(",
        "GetScienceYield",
        "GetCultureYield",
        "GetGoldYield",
        "GetStockpile",
        "GetResourceAmount",
        "GetCivicsResearched",
        "GetNumCivicsCompleted",
        "GetFaithBalance",
        "GetGoldBalance",
        "GetPopulation",
    ):
        assert tok not in lua
    assert "HasMet(" in lua and "GetMilitaryStrength" in lua
    assert "IsRevealed(" in lua


def test_rival_snapshot_round_trip():
    row = parse_rival_snapshot_response(
        ["RIVAL|3|Rome|120|4|40|210|5|2|88|12|1|3", "---END---"]
    )[0]
    assert isinstance(row, RivalRow)
    assert (row.player_id, row.name, row.met, row.score, row.era) == (3, "Rome", True, 120, 4)
    assert (row.techs, row.military_strength, row.cities_seen) == (40, 210, 5)
    assert (row.science_vp, row.tourism, row.tourists) == (2, 88, 12)
    assert (row.religion_converts, row.diplo_vp) == (1, 3)
    fields = {f for f in RivalRow.__dataclass_fields__}
    assert fields == {
        "player_id", "name", "met", "score", "techs", "military_strength",
        "cities_seen", "science_vp", "tourism", "tourists",
        "religion_converts", "diplo_vp", "era",
    }


def test_victory_progress_has_no_demographics_or_rival_yields():
    lua = build_victory_progress_query()
    assert "DEMO" not in lua and "GetYield(" not in lua
    for tok in ("GetScienceYield", "GetCultureYield", "GetGoldYield", "GetNumCivicsCompleted"):
        assert tok not in lua
    assert "IsRevealed(" in lua
    vp = parse_victory_progress_response(
        ["PLAYER|1|Rome|10|0|50|1|2|3|4|0|0|0|false|5", "DEMO|Population|1|1|1|1|1"]
    )
    assert vp.players[0].num_cities == 5 and not vp.demographics


def test_victory_proximity_names_only_met_founders():
    lua = build_victory_proximity_query()
    assert "HasMet(" in lua and "an unmet civilization" in lua


def test_diplomacy_city_stats_require_visibility_and_no_fog_count():
    lua = build_diplomacy_query()
    assert "IsVisible(" in lua and "CIVCITIES" not in lua
    assert lua.index("IsVisible(") < lua.index("ECITY|")
    assert "GetDiplomaticModifiers" in lua and "GetMilitaryStrength" in lua
    civs = parse_diplomacy_response(["CIV|2|Rome|Trajan|1|0|NEUTRAL|0|1|0|0|0|0", "CITIESSEEN|2|3"])
    assert civs[0].num_cities == 3


def test_deal_options_is_deal_screen_data():
    lua = build_deal_options_query(2)
    assert "HasMet(" in lua
    from civ_mcp import narrate

    src = inspect.getsource(narrate)
    assert "(deal screen" in src


def test_trade_destinations_have_no_omniscient_fallback():
    lua = build_trade_destinations_query(1)
    assert "found == 0" in lua  # capacity warnings only
    # no foreign city listing inside the found == 0 branch
    tail = lua[lua.index("found == 0") :]
    assert "enrichDest" not in tail
    assert "IsAtWarWith" not in tail


def test_religion_summary_met_only():
    lua = build_religion_status_query()
    tail = lua[lua.index("local relTotals") :]
    assert "HasMet(" in tail


def test_owner_names_gated_by_hasmet():
    for lua in (
        build_threat_scan_query(),
        build_city_states_query(),
        build_great_people_query(),
        build_notifications_query(),
    ):
        assert "HasMet(" in lua
    assert "an unmet civilization" in build_threat_scan_query()
    assert "an unmet civilization" in build_great_people_query()
    assert "an unmet civilization" in build_notifications_query()
    assert "an unmet civilization" in build_city_states_query()


def test_diary_full_query_is_local_player_only():
    lua = build_diary_full_query()
    assert "for _, pid in ipairs(PlayerManager.GetAliveMajorIDs())" not in lua
    assert "if i == me and Players[i]" in lua
    assert "IsMajor() and Players[i]:IsAlive() then \"" not in lua.replace("\n", "")


def test_seed_never_in_agent_text():
    import civ_mcp.server as s

    assert "seed {seed}" not in inspect.getsource(s.get_diary)
    assert "random seed" not in (inspect.getdoc(s.get_diary) or "")


def test_identity_hash_and_filenames_hide_seed(tmp_path):
    seed = 123456789
    h = identity_hash(seed)
    assert h == hashlib.sha1(str(seed).encode()).hexdigest()[:10]
    assert str(seed) not in diary_path("rome", seed, "run1").name
    assert h in diary_path("rome", seed, "run1").name


def test_end_turn_warnings_use_techs_and_military_strength():
    import civ_mcp.end_turn as et

    src = inspect.getsource(et._check_empire_warnings)
    assert "r.techs" in src and "r.military_strength" in src
    assert "r.sci" not in src and "r.mil " not in src


def test_rivals_narration_title():
    from civ_mcp import narrate

    src = inspect.getsource(narrate)
    assert "RIVALS (World Rankings)" in src
    assert "DEMOGRAPHICS" not in src and "RIVAL INTELLIGENCE" not in src

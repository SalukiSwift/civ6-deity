"""Fog seal A: generated Lua only reads live state on VISIBLE plots."""

from civ_mcp.fog import FogMemory
from civ_mcp.lua.cities import build_cities_query
from civ_mcp.lua.map import (
    _SETTLE_PREAMBLE,
    _SETTLE_SCORE_BODY,
    build_found_city,
    build_map_area_query,
    build_strategic_map_query,
    build_wonder_advisor_query,
    parse_map_response,
)
from civ_mcp.lua.models import TileInfo
from civ_mcp.lua.units import (
    build_attack_followup_query,
    build_builder_tasks_query,
    build_combat_estimate_query,
    build_post_move_visibility_query,
    build_units_query,
)
from civ_mcp.narrate import narrate_map


def test_map_area_emits_visibility_flag_and_guards_live_fields():
    lua = build_map_area_query(10, 10, 2)
    assert "IsRevealed(" in lua and "IsVisible(" in lua
    assert lua.index("IsVisible(") < lua.index("GetOwner()")
    # owner, improvement, district and route reads sit inside visible branches
    vis_branch = lua.index("if visible then")
    assert lua.index("GetOwner()") > vis_branch
    assert lua.index("GetImprovementType()") > vis_branch
    assert lua.index("GetDistrictType()") > vis_branch
    assert lua.index("GetRouteType()") > vis_branch


def test_settle_preamble_only_counts_revealed_cities():
    assert "IsRevealed(" in _SETTLE_PREAMBLE or "IsRevealed(" in _SETTLE_SCORE_BODY


def test_unit_targets_require_visibility():
    assert "IsVisible(" in build_units_query()


def test_city_targets_and_city_tiles_are_gated():
    lua = build_cities_query()
    assert "IsVisible(" in lua and "PrereqTech" in lua and "RemoveAt" not in lua


def test_builder_tasks_and_wonder_resources_tech_gated():
    assert "resVisible(resInfo)" in build_builder_tasks_query()
    assert "PrereqTech" in build_wonder_advisor_query(1, "BUILDING_PYRAMIDS")
    assert "resVisible(rInfo)" in build_wonder_advisor_query(1, "BUILDING_PYRAMIDS")


def test_found_city_error_names_only_revealed_cities():
    assert "IsRevealed(" in build_found_city(1)


def test_strategic_map_owner_only_when_visible():
    assert "IsVisible(" in build_strategic_map_query()


def test_enemy_detail_queries_require_visibility():
    assert "IsVisible(" in build_attack_followup_query(3, 4)
    assert "IsVisible(" in build_combat_estimate_query(1, 3, 4)
    pm = build_post_move_visibility_query(3, 4)
    assert pm.index("IsVisible(") < pm.index("GetImprovementType()")


def test_no_omniscient_dumps_registered():
    import civ_mcp.lua as lq
    import civ_mcp.server as s

    assert "map_capture" not in s.AppContext.__dataclass_fields__
    assert not hasattr(lq, "build_static_map_dump")
    assert not hasattr(lq, "build_ownership_delta")
    assert s.lq.build_revealed_tiles_seed_query  # seeding import resolves


def _tile(**kw):
    base = dict(
        x=3,
        y=4,
        terrain="TERRAIN_GRASS",
        feature=None,
        resource=None,
        is_hills=False,
        is_river=False,
        is_coastal=False,
        improvement=None,
        owner_id=-1,
    )
    base.update(kw)
    return TileInfo(**base)


def test_fogged_tile_narrates_without_live_owner_or_improvement():
    t = _tile(visible=False, owner_id=2, owner_name="Rome", improvement="IMPROVEMENT_MINE")
    out = narrate_map([t], fog=FogMemory())
    assert "(fog)" in out
    assert "Rome" not in out and "MINE" not in out and "Mine" not in out


def test_fogged_tile_uses_remembered_state():
    fog = FogMemory()
    fog.remember(7, [_tile(visible=True, owner_id=2, owner_name="Rome", improvement="IMPROVEMENT_MINE")])
    out = narrate_map([_tile(visible=False)], fog=fog)
    assert "last seen T7" in out and "Rome" in out and "MINE" in out


def test_parser_blanks_live_fields_for_revealed_only_tiles():
    line = (
        "3,4|TERRAIN_GRASS|none|none|0|0|0|IMPROVEMENT_MINE|2|ROME WARRIOR|revealed|1|"
        "2,1,0,0,0,0|DISTRICT_CAMPUS|Rome|none|1|1"
    )
    (t,) = parse_map_response([line])
    assert t.visible is False
    assert t.improvement is None and t.owner_id == -1 and t.owner_name is None
    assert t.district is None and t.units is None and t.yields is None
    assert t.route_type == -1


def test_parser_keeps_live_fields_for_visible_tiles():
    line = (
        "3,4|TERRAIN_GRASS|none|none|0|0|0|IMPROVEMENT_MINE|2|none|visible|1|"
        "2,1,0,0,0,0|DISTRICT_CAMPUS|Rome|none|1|1"
    )
    (t,) = parse_map_response([line])
    assert t.visible and t.improvement == "IMPROVEMENT_MINE" and t.owner_name == "Rome"

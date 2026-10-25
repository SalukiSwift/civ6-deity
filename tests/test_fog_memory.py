"""FogMemory: last-seen memory fed only by currently visible tiles."""

from civ_mcp.fog import FogMemory
from civ_mcp.lua.models import TileInfo


def _tile(x, y, visible=True, owner="Rome", imp="IMPROVEMENT_MINE", pillaged=False):
    return TileInfo(
        x=x,
        y=y,
        terrain="TERRAIN_GRASS",
        feature=None,
        resource=None,
        is_hills=False,
        is_river=False,
        is_coastal=False,
        improvement=imp,
        owner_id=1,
        visible=visible,
        owner_name=owner,
        is_pillaged=pillaged,
    )


def test_remember_only_visible_tiles(tmp_path):
    fog = FogMemory(path=tmp_path / "fog.json")
    fog.remember(5, [_tile(1, 1), _tile(2, 2), _tile(3, 3, visible=False)])
    assert fog.recall(1, 1).owner == "Rome"
    assert fog.recall(2, 2).improvement == "IMPROVEMENT_MINE"
    assert fog.recall(3, 3) is None


def test_save_load_round_trip(tmp_path):
    path = tmp_path / "fog.json"
    fog = FogMemory(path=path)
    fog.remember(5, [_tile(1, 1, pillaged=True), _tile(2, 2)])
    fog.save()
    loaded = FogMemory.load(path)
    assert loaded.recall(1, 1) == fog.recall(1, 1)
    assert loaded.recall(1, 1).pillaged is True
    assert loaded.recall(2, 2).turn == 5
    assert loaded.recall(9, 9) is None


def test_newer_turn_overwrites(tmp_path):
    fog = FogMemory(path=tmp_path / "fog.json")
    fog.remember(5, [_tile(1, 1, owner="Rome")])
    fog.remember(9, [_tile(1, 1, owner="Egypt", imp=None)])
    seen = fog.recall(1, 1)
    assert seen.turn == 9 and seen.owner == "Egypt" and seen.improvement is None


def test_save_without_path_is_noop():
    fog = FogMemory()
    fog.remember(1, [_tile(1, 1)])
    fog.save()  # must not raise


def test_load_missing_or_corrupt_file_is_empty(tmp_path):
    p = tmp_path / "fog.json"
    p.write_text("not json")
    assert FogMemory.load(p).recall(1, 1) is None

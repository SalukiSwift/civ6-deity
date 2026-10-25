"""A new game identity clears the old game's turn tracking, so end_turn in
the new game is not reported as a turn regression."""

import asyncio

from civ_mcp.end_turn import turn_regression_message
from civ_mcp.game_state import GameState


class _Conn:
    def __init__(self, civ: str, seed: int):
        self.set(civ, seed)

    def set(self, civ: str, seed: int) -> None:
        self.reply = [f"GAMESEED|CIVILIZATION_{civ}|{seed}"]

    async def execute_write(self, code, timeout=5.0):
        return list(self.reply)


def _game_at_turn_40() -> tuple[GameState, _Conn]:
    conn = _Conn("SUMERIA", 111)
    gs = GameState(conn)
    asyncio.run(gs.get_game_identity())
    gs._high_water_turn = 40
    gs._pending_end_turn = True
    gs._pending_end_turn_from = 40
    gs._end_turn_blocked = True
    return gs, conn


def test_stale_high_water_mark_reads_as_regression():
    gs, _ = _game_at_turn_40()
    msg = turn_regression_message(gs, 2)
    assert msg is not None and msg.startswith("CRITICAL: Turn regressed from 40 to 2")


def test_identity_change_resets_turn_tracking_and_no_regression():
    gs, conn = _game_at_turn_40()
    conn.set("NUBIA", 222)
    assert asyncio.run(gs.get_game_identity()) == ("nubia", 222)
    assert gs._high_water_turn == 0
    assert gs._pending_end_turn is False and gs._pending_end_turn_from is None
    assert gs._end_turn_blocked is False
    assert turn_regression_message(gs, 2) is None


def test_same_identity_keeps_turn_tracking():
    gs, _ = _game_at_turn_40()
    asyncio.run(gs.get_game_identity())
    assert gs._high_water_turn == 40 and gs._pending_end_turn is True


def test_regression_message_rules():
    gs, _ = _game_at_turn_40()
    assert turn_regression_message(gs, None) is None
    assert turn_regression_message(gs, 39) is None  # within one turn
    assert turn_regression_message(gs, 41) is None
    gs._high_water_turn = 0
    assert turn_regression_message(gs, 1) is None

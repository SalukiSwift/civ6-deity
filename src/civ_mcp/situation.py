"""Situation board: one compact, player-visible summary of what needs attention.

format_situation is pure formatting; gather_situation reuses existing
GameState queries (no new Lua) and tolerates individual query failures.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from civ_mcp import lua as lq
from civ_mcp.lua.models import (
    CityInfo,
    GameOverview,
    RivalRow,
    ThreatInfo,
    TurnEvent,
    UnitInfo,
)

if TYPE_CHECKING:
    from civ_mcp.game_state import GameState

log = logging.getLogger(__name__)

MAX_LINES = 40
MAX_IDS = 12

# Blockers already shown as their own ATTENTION lines.
_COVERED_BLOCKERS = {
    "ENDTURN_BLOCKING_UNITS",
    "ENDTURN_BLOCKING_PRODUCTION",
    "ENDTURN_BLOCKING_RESEARCH",
    "ENDTURN_BLOCKING_CIVIC",
}

ThreatRow = ThreatInfo


@dataclass
class Situation:
    overview: GameOverview
    units: list[UnitInfo] = field(default_factory=list)
    cities: list[CityInfo] = field(default_factory=list)
    threats: list[ThreatRow] = field(default_factory=list)
    rivals: list[RivalRow] = field(default_factory=list)
    blockers: list[str] = field(default_factory=list)
    victory_warnings: list[TurnEvent] = field(default_factory=list)
    trade_capacity: tuple[int, int] = (0, 0)  # (used, capacity)
    envoys_available: int = 0
    governor_titles: int = 0
    unavailable: list[str] = field(default_factory=list)  # failed sub-queries


def _unset(name: str) -> bool:
    return not name or name.upper() == "NONE"


def _ids(ids: list[int]) -> str:
    shown = ", ".join(str(i) for i in ids[:MAX_IDS])
    if len(ids) > MAX_IDS:
        shown += f" (+{len(ids) - MAX_IDS} more)"
    return shown


def _empire(s: Situation) -> list[str]:
    ov = s.overview
    civ = f"{ov.civ_name} ({ov.leader_name})" if ov.leader_name else ov.civ_name
    era = f" {ov.era_name}" if ov.era_name else ""
    return [
        f"  T{ov.turn}{era} | {civ} | score {ov.score} | {ov.num_cities} cities, {ov.num_units} units",
        f"  gold {ov.gold:.0f} ({ov.gold_per_turn:+.0f}/t) | science {ov.science_yield:.0f}"
        f" | culture {ov.culture_yield:.0f} | faith {ov.faith:.0f}",
    ]


def _attention(s: Situation) -> list[str]:
    out: list[str] = []
    idle = [u.unit_id for u in s.units if u.moves_remaining > 0 and not u.is_busy]
    if idle:
        out.append(f"idle units: {_ids(idle)}")
    idle_cities = [c.name for c in s.cities if _unset(c.currently_building)]
    if idle_cities:
        out.append(f"no production: {', '.join(idle_cities)}")
    if _unset(s.overview.current_research) or _unset(s.overview.current_civic):
        out.append("research/civic not set")
    if s.envoys_available > 0:
        out.append(f"envoys to place: {s.envoys_available}")
    if s.governor_titles > 0:
        out.append(f"governor titles: {s.governor_titles}")
    used, cap = s.trade_capacity
    if cap > used:
        out.append(f"trade routes: {used}/{cap}")
    for b in s.blockers:
        out.append(f"pending choice: {b}")
    promo = [u.unit_id for u in s.units if u.needs_promotion]
    if promo:
        out.append(f"promotions available: {_ids(promo)}")
    for name in s.unavailable:
        if name not in ("threats", "rivals"):
            out.insert(0, f"(unavailable: {name})")
    return [f"  {line}" for line in out]


def _threats(s: Situation) -> list[str]:
    if "threats" in s.unavailable:
        return ["  (unavailable: threats)"]
    rows = sorted(s.threats, key=lambda t: t.distance)
    return [
        f"  {t.owner_name} {t.unit_type.replace('UNIT_', '')} at ({t.x},{t.y})"
        f" hp {t.hp}/{t.max_hp} dist {t.distance}"
        for t in rows
    ]


def _rival_rows(s: Situation) -> list[str]:
    if "rivals" in s.unavailable:
        return ["  (unavailable: rivals)"]
    met = sorted((r for r in s.rivals if r.met), key=lambda r: r.score, reverse=True)
    return [
        f"  {r.name} score {r.score} techs {r.techs} mil {r.military_strength}"
        for r in met
    ]


def _fit(body: list[str], room: int) -> list[str]:
    """Trim body to room lines, ending with a '+N more' line when cut."""
    if len(body) <= room:
        return body
    room = max(room, 1)
    keep = body[: room - 1]
    return keep + [f"  +{len(body) - len(keep)} more"]


def format_situation(s: Situation) -> str:
    empire = _empire(s)
    attention = _attention(s)
    threats = _threats(s)
    rivals = _rival_rows(s)
    warnings = [f"  {e.message}" for e in s.victory_warnings]

    headers = 4
    fixed = len(empire) + len(warnings)
    # Budget for the three trimmable bodies, least important trimmed first.
    budget = MAX_LINES - headers - fixed
    bodies = {"attention": attention, "threats": threats, "rivals": rivals}
    sizes = {k: max(len(v), 1) for k, v in bodies.items()}
    # Trim rivals, then threats, then attention; sections never grow. First
    # pass keeps a row plus the "+N more" line, second pass keeps one line.
    for floor in (2, 1):
        for key in ("rivals", "threats", "attention"):
            over = sum(sizes.values()) - budget
            if over <= 0:
                break
            cut = min(over, max(sizes[key] - floor, 0))
            sizes[key] -= cut

    def body(key: str, extra: list[str] | None = None) -> list[str]:
        rows = bodies[key]
        shown = _fit(rows, sizes[key]) if rows else ["  none"]
        return shown + (extra or [])

    lines = ["EMPIRE", *empire]
    lines += ["ATTENTION", *body("attention")]
    lines += ["THREATS", *body("threats")]
    lines += ["RIVALS", *body("rivals", warnings)]
    return "\n".join(lines[:MAX_LINES])


async def _victory_warnings(gs: GameState) -> list[TurnEvent]:
    from civ_mcp.end_turn import _check_victory_proximity

    return await _check_victory_proximity(gs)


async def _safe(coro, default, what: str, failed: list[str]):
    try:
        return await coro
    except Exception:
        log.warning("situation: %s query failed", what, exc_info=True)
        failed.append(what)
        return default


async def gather_situation(gs: GameState) -> Situation:
    overview = await gs.get_game_overview()
    failed: list[str] = []
    units = await _safe(gs.get_units(), [], "units", failed)
    cities_res = await _safe(gs.get_cities(), ([], []), "cities", failed)
    cities = cities_res[0]
    threats = await _safe(gs.get_threat_scan(), [], "threats", failed)
    rivals = await _safe(gs.get_rival_snapshot(), [], "rivals", failed)
    warnings = await _safe(_victory_warnings(gs), [], "victory", failed)

    async def _blockers() -> list[str]:
        lines = await gs.conn.execute_write(lq.build_end_turn_blocking_query())
        return [
            msg or btype
            for btype, msg in lq.parse_end_turn_blocking(lines)
            if btype not in _COVERED_BLOCKERS
        ]

    blockers = await _safe(_blockers(), [], "blockers", failed)
    trade = await _safe(gs.get_trade_routes(), None, "trade", failed)
    envoys = await _safe(gs.get_city_states(), None, "envoys", failed)
    govs = await _safe(gs.get_governors(), None, "governors", failed)
    return Situation(
        overview=overview,
        units=units,
        cities=cities,
        threats=threats,
        rivals=rivals,
        blockers=blockers,
        victory_warnings=warnings,
        trade_capacity=(trade.active_count, trade.capacity) if trade else (0, 0),
        envoys_available=envoys.tokens_available if envoys else 0,
        governor_titles=govs.points_available if govs else 0,
        unavailable=failed,
    )

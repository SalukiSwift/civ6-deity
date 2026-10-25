"""Batch orders: parse a list of order dicts and run them through GameState.

The per-action dispatch lives here (``dispatch_unit_action``,
``item_type_of``) and is shared by the single-order tools in server.py, so a
batched order does exactly what the matching single tool would do.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Awaitable, Callable

from civ_mcp.connection import LuaError

BATCH_UNIT_ACTIONS = frozenset(
    {"move", "attack", "fortify", "skip", "found_city", "improve", "heal", "alert", "sleep"}
)
_NEEDS_XY = frozenset({"move", "attack"})
_ITEM_PREFIXES = (
    ("UNIT_", "UNIT"),
    ("BUILDING_", "BUILDING"),
    ("WONDER_", "BUILDING"),
    ("DISTRICT_", "DISTRICT"),
    ("PROJECT_", "PROJECT"),
)

UNIT_ACTIONS_HELP = (
    "move, attack, fortify, skip, found_city, improve, repair, remove_improvement, "
    "remove_feature, build_route, automate, heal, alert, sleep, delete, trade_route, "
    "activate, sacrifice_charges, teleport, spread_religion"
)


async def dispatch_unit_action(
    gs: Any,
    action: str,
    unit_index: int,
    target_x: int | None = None,
    target_y: int | None = None,
    improvement: str | None = None,
) -> str:
    """Run one unit action against GameState. Shared by unit_action and batch_orders."""
    match action.lower():
        case "move":
            if target_x is None or target_y is None:
                return "Error: move requires target_x and target_y"
            return await gs.move_unit(unit_index, target_x, target_y)
        case "attack":
            if target_x is None or target_y is None:
                return "Error: attack requires target_x and target_y"
            return await gs.attack_unit(unit_index, target_x, target_y)
        case "fortify":
            return await gs.fortify_unit(unit_index)
        case "skip":
            return await gs.skip_unit(unit_index)
        case "found_city":
            return await gs.found_city(unit_index)
        case "improve":
            if not improvement:
                return "Error: improve requires improvement name (e.g. IMPROVEMENT_FARM). To repair a pillaged improvement, use action='repair' instead."
            return await gs.improve_tile(unit_index, improvement)
        case "repair":
            return await gs.repair_improvement(unit_index)
        case "remove_improvement":
            return await gs.remove_improvement(unit_index)
        case "remove_feature":
            return await gs.remove_feature(unit_index)
        case "build_route":
            return await gs.build_route(unit_index)
        case "automate":
            return await gs.automate_explore(unit_index)
        case "heal":
            return await gs.heal_unit(unit_index)
        case "alert":
            return await gs.alert_unit(unit_index)
        case "sleep":
            return await gs.sleep_unit(unit_index)
        case "delete":
            return await gs.delete_unit(unit_index)
        case "trade_route":
            if target_x is None or target_y is None:
                return "Error: trade_route requires target_x and target_y of destination city"
            return await gs.make_trade_route(unit_index, target_x, target_y)
        case "activate":
            return await gs.activate_great_person(unit_index)
        case "sacrifice_charges":
            return await gs.sacrifice_builder_charges(unit_index)
        case "spread_religion":
            return await gs.spread_religion(unit_index)
        case "teleport":
            if target_x is None or target_y is None:
                return "Error: teleport requires target_x and target_y of the destination city"
            return await gs.teleport_to_city(unit_index, target_x, target_y)
        case _:
            return f"Error: Unknown action '{action}'. Valid: {UNIT_ACTIONS_HELP}"


def item_type_of(item_name: str) -> str:
    """UNIT/BUILDING/DISTRICT/PROJECT from an item name's prefix (wonders are buildings)."""
    upper = item_name.upper()
    for prefix, kind in _ITEM_PREFIXES:
        if upper.startswith(prefix):
            return kind
    raise ValueError(
        f"cannot tell the item type of '{item_name}' (expected a UNIT_, BUILDING_, "
        "WONDER_, DISTRICT_ or PROJECT_ name)"
    )


async def dispatch_research(gs: Any, tech_or_civic: str, category: str = "tech") -> str:
    """Shared by set_research and batch_orders."""
    if category.lower() == "civic":
        return await gs.set_civic(tech_or_civic)
    return await gs.set_research(tech_or_civic)


@dataclass
class Order:
    kind: str  # unit | production | purchase | research | civic
    id: int | None
    action: str
    x: int | None
    y: int | None
    item: str | None
    why: str
    index: int  # 1-based position in the submitted list


def _opt_int(d: dict, key: str) -> int | None:
    v = d.get(key)
    if v is None:
        return None
    if isinstance(v, bool) or not isinstance(v, int):
        raise ValueError(f"{key} must be an integer")
    return v


def _parse_one(d: dict, index: int) -> Order:
    if not isinstance(d, dict):
        raise ValueError("order must be an object")
    why = str(d.get("why") or "")
    x, y = _opt_int(d, "x"), _opt_int(d, "y")
    if "unit_id" in d or "action" in d:
        uid = _opt_int(d, "unit_id")
        if uid is None:
            raise ValueError("unit order needs unit_id")
        action = str(d.get("action") or "").lower()
        if action not in BATCH_UNIT_ACTIONS:
            raise ValueError(
                f"unit order needs action, one of {', '.join(sorted(BATCH_UNIT_ACTIONS))}"
            )
        if action in _NEEDS_XY and (x is None or y is None):
            raise ValueError(f"{action} needs x and y")
        item = d.get("improvement")
        if action == "improve" and not item:
            raise ValueError("improve needs improvement")
        return Order("unit", uid, action, x, y, item or None, why, index)
    if "city_id" in d:
        cid = _opt_int(d, "city_id")
        for key in ("production", "purchase"):
            if d.get(key):
                item = str(d[key])
                item_type_of(item)  # validates
                return Order(key, cid, key, x, y, item, why, index)
        raise ValueError("city order needs production or purchase")
    for key in ("research", "civic"):
        if d.get(key):
            return Order(key, None, key, None, None, str(d[key]), why, index)
    raise ValueError(
        "order needs unit_id+action, city_id+production/purchase, research or civic"
    )


def parse_orders(raw: list[dict]) -> tuple[list[Order], list[str]]:
    """Parse raw dicts. Bad entries are reported as '#N: reason' and skipped."""
    orders: list[Order] = []
    errors: list[str] = []
    for i, d in enumerate(raw, start=1):
        try:
            orders.append(_parse_one(d, i))
        except ValueError as e:
            errors.append(f"#{i}: {e}")
    return orders, errors


async def _execute(gs: Any, o: Order) -> str:
    if o.kind == "unit":
        return await dispatch_unit_action(gs, o.action, o.id % 65536, o.x, o.y, o.item)
    if o.kind == "production":
        return await gs.set_city_production(o.id, item_type_of(o.item), o.item, o.x, o.y)
    if o.kind == "purchase":
        return await gs.purchase_item(o.id, item_type_of(o.item), o.item, "YIELD_GOLD")
    return await dispatch_research(gs, o.item, o.kind if o.kind == "civic" else "tech")


async def run_orders(
    gs: Any,
    orders: list[Order],
    on_each: Callable[[Order, str], Awaitable[None]],
) -> list[tuple[Order, str]]:
    """Run orders in sequence, continuing past failures.

    Each result is 'OK <game result>' or 'FAIL <reason>'. A game result that
    starts with 'Error' counts as a failure.
    """
    results: list[tuple[Order, str]] = []
    for o in orders:
        try:
            raw = await _execute(gs, o)
            text = str(raw).strip()
            res = f"FAIL {text}" if text.startswith("Error") else f"OK {text}"
        except (LuaError, ValueError) as e:
            res = f"FAIL {e}"
        results.append((o, res))
        try:
            await on_each(o, res)
        except Exception:
            pass
    return results


def format_results(
    total: int, results: list[tuple[Order, str]], errors: list[str]
) -> str:
    """One line per submitted index, in order."""
    lines: dict[int, str] = {}
    for o, res in results:
        first = res.split("\n", 1)[0]
        lines[o.index] = f"#{o.index} {first}"
    for e in errors:
        num, _, msg = e.partition(": ")
        lines[int(num[1:])] = f"{num} skipped: {msg}"
    return "\n".join(lines[i] for i in sorted(lines) if i <= total)

"""Broadcast log: the running commentary the player writes while playing.

Entries land in ``campaign/games/NN-civ/log.md`` (human readable) and
``log.jsonl`` (machine readable). Before a game is bound (menu, lobby) they
go to ``campaign/lobby.md`` and ``lobby.jsonl`` instead.
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

KINDS = (
    "plan",
    "read",
    "decision",
    "reaction",
    "lesson",
    "pivot",
    "postmortem",
    "turn",
    "event",
    "action",
    "load",
    "game",
)

# The commentary kinds Claude writes with narrate; the rest of KINDS
# (turn, event, action, load, game) are written by the server itself.
NARRATE_KINDS = (
    "plan",
    "read",
    "decision",
    "reaction",
    "lesson",
    "pivot",
    "postmortem",
)

# Every mutating tool that takes a `why` argument and is logged as an action.
ACTION_TOOLS: frozenset[str] = frozenset(
    {
        "unit_action",
        "promote_unit",
        "upgrade_unit",
        "skip_remaining_units",
        "set_city_production",
        "purchase_item",
        "city_action",
        "set_city_focus",
        "purchase_tile",
        "set_research",
        "set_policies",
        "change_government",
        "appoint_governor",
        "assign_governor",
        "promote_governor",
        "choose_dedication",
        "respond_to_diplomacy",
        "send_diplomatic_action",
        "form_alliance",
        "propose_peace",
        "respond_to_trade",
        "propose_trade",
        "send_envoy",
        "choose_pantheon",
        "found_religion",
        "recruit_great_person",
        "patronize_great_person",
        "reject_great_person",
        "spy_action",
        "queue_wc_votes",
    }
)


_EM_DASH = re.compile(r"\s*\u2014\s*")


def plain_text(text: str) -> str:
    """Log text without em dashes (upstream messages use them; the log is
    read by people and should not look machine-written)."""
    return _EM_DASH.sub(", ", text)


@dataclass
class Entry:
    ts: str
    turn: int | None
    kind: str
    text: str
    meta: dict = field(default_factory=dict)


class Broadcast:
    def __init__(self, campaign_dir: Path) -> None:
        self.campaign_dir = Path(campaign_dir)
        self._game_dir: Path | None = None
        self.identity: str | None = None
        self._count = 0
        self._last_turn: int | None = None
        # Leader chosen by new_game; recorded by the first bind_game after it.
        self.pending_leader: str | None = None

    @property
    def game_dir(self) -> Path | None:
        return self._game_dir

    # -- binding ---------------------------------------------------------

    def bind_game(self, identity: str, civ: str, leader: str = "") -> Path:
        if not leader and self.pending_leader:
            leader = self.pending_leader
        self.pending_leader = None
        games = self.campaign_dir / "games"
        games.mkdir(parents=True, exist_ok=True)
        index_path = games / "index.json"
        index: dict[str, str] = {}
        if index_path.exists():
            index = json.loads(index_path.read_text(encoding="utf-8"))
        name = index.get(identity)
        if name is None:
            name = f"{len(index) + 1:02d}-{civ.lower()}"
            index[identity] = name
            index_path.write_text(json.dumps(index, indent=2), encoding="utf-8")
        d = games / name
        d.mkdir(exist_ok=True)
        meta_path = d / "meta.json"
        if not meta_path.exists():
            meta = {
                "identity": identity,
                "civ": civ,
                "leader": leader,
                "created": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            }
            meta_path.write_text(json.dumps(meta, indent=2), encoding="utf-8")
        self._game_dir = d
        self.identity = identity
        rows = self._read_rows(d / "log.jsonl")
        self._count = len(rows)
        self._last_turn = next(
            (r["turn"] for r in reversed(rows) if r.get("turn") is not None), None
        )
        return d

    # -- per-game metadata -------------------------------------------------

    @staticmethod
    def read_meta(game_dir: Path | None) -> dict:
        """A game directory's meta.json, or {} when missing or unreadable."""
        if game_dir is None:
            return {}
        path = Path(game_dir) / "meta.json"
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        return data if isinstance(data, dict) else {}

    def meta(self) -> dict:
        """The bound game's meta.json ({} when unbound)."""
        return self.read_meta(self._game_dir)

    def update_meta(self, **fields) -> dict:
        """Merge fields into the bound game's meta.json; no-op when unbound."""
        if self._game_dir is None:
            return {}
        data = self.meta()
        data.update(fields)
        (self._game_dir / "meta.json").write_text(
            json.dumps(data, indent=2), encoding="utf-8"
        )
        return data

    def latest_game_dir(self) -> Path | None:
        """The newest campaign game (highest NN in games/index.json) with a
        meta.json: the current game of a fresh session, before any bind."""
        games = self.campaign_dir / "games"
        try:
            index = json.loads((games / "index.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        best: tuple[int, Path] | None = None
        for name in index.values() if isinstance(index, dict) else ():
            d = games / str(name)
            if not (d / "meta.json").exists():
                continue
            try:
                num = int(str(name).split("-", 1)[0])
            except ValueError:
                continue
            if best is None or num > best[0]:
                best = (num, d)
        return best[1] if best else None

    def campaign_game(self) -> tuple[Path, dict] | None:
        """(dir, meta) of the game a recovery load must return to: the bound
        game, else the latest one. None when the campaign has no game yet."""
        d = self._game_dir or self.latest_game_dir()
        if d is None:
            return None
        return d, self.read_meta(d)

    # -- writing ---------------------------------------------------------

    def append(
        self,
        kind: str,
        text: str,
        *,
        turn: int | None = None,
        meta: dict | None = None,
    ) -> Entry:
        if kind not in KINDS:
            raise ValueError(f"Unknown kind {kind!r}; use one of: {', '.join(KINDS)}")
        entry = Entry(
            # Local time with its offset: log.md shows April's clock, and
            # log.jsonl keeps a full ISO timestamp.
            ts=datetime.now().astimezone().isoformat(timespec="seconds"),
            turn=turn,
            kind=kind,
            text=plain_text(text),
            meta=dict(meta or {}),
        )
        base = self._game_dir if self._game_dir is not None else self.campaign_dir
        stem = "log" if self._game_dir is not None else "lobby"
        base.mkdir(parents=True, exist_ok=True)
        md = base / f"{stem}.md"
        line = f"- {entry.ts[11:16]} [{kind}] {entry.text}\n"
        with open(md, "a", encoding="utf-8") as f:
            if (
                self._game_dir is not None
                and turn is not None
                and turn != self._last_turn
            ):
                f.write(f"\n## Turn {turn}\n\n")
            f.write(line)
        with open(base / f"{stem}.jsonl", "a", encoding="utf-8") as f:
            f.write(json.dumps(asdict(entry)) + "\n")
        if self._game_dir is not None:
            self._count += 1
            if turn is not None:
                self._last_turn = turn
        return entry

    # -- reading ---------------------------------------------------------

    @staticmethod
    def _read_rows(path: Path) -> list[dict]:
        if not path.exists():
            return []
        rows = []
        for line in path.read_text(encoding="utf-8").splitlines():
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        return rows

    def tail(self, after: int = 0, limit: int = 200) -> list[dict]:
        """Rows with 1-based index > ``after``, oldest first, at most ``limit``."""
        if self._game_dir is not None:
            path = self._game_dir / "log.jsonl"
        else:
            path = self.campaign_dir / "lobby.jsonl"
        rows = self._read_rows(path)
        out = []
        for i, row in enumerate(rows, start=1):
            if i > after:
                out.append({**row, "i": i})
            if len(out) >= limit:
                break
        return out

    def count(self) -> int:
        """Total rows in the current log."""
        path = (self._game_dir or self.campaign_dir) / (
            "log.jsonl" if self._game_dir is not None else "lobby.jsonl"
        )
        return len(self._read_rows(path))

    def _board_path(self) -> Path:
        base = self._game_dir if self._game_dir is not None else self.campaign_dir
        return base / "board.txt"

    def set_board(self, text: str) -> None:
        p = self._board_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")

    def last_board(self) -> str | None:
        p = self._board_path()
        return p.read_text(encoding="utf-8") if p.exists() else None

    # -- formatting ------------------------------------------------------

    @staticmethod
    def action_text(
        tool: str,
        params: dict,
        result: str,
        unit_label: str | None = None,
        city_label: str | None = None,
    ) -> str:
        """One readable log line: who did what, why, and the outcome in plain
        Civ terms (see humanize_result). The raw result belongs in meta.
        unit_label / city_label name the unit or city from the last lists."""
        tx = params.get("target_x", params.get("x"))
        ty = params.get("target_y", params.get("y"))
        target = f" -> ({tx},{ty})" if tx is not None and ty is not None else ""
        if params.get("unit_id") is not None:
            subject = unit_label or f"unit #{params['unit_id']}"
            words = [str(params.get("action") or tool.replace("_", " ")).replace("_", " ")]
            if params.get("improvement"):
                words.append(game_name(str(params["improvement"])))
            detail = " ".join(words) + target
        elif tool in _CITY_TOOLS and params.get("city_id") is not None:
            subject = city_label or f"city #{params['city_id']}"
            detail = _CITY_TOOLS[tool]
            if tool == "city_action" and params.get("action"):
                detail = str(params["action"]).replace("_", " ")
            detail += target
        elif tool == "set_research":
            subject = "civic" if str(params.get("category", "")).lower() == "civic" else "research"
            detail = ""
        else:
            subject = tool.replace("_", " ")
            detail = ""
            for key in ("item", "item_name", "name"):
                if params.get(key):
                    detail = game_name(str(params[key]))
                    break
            detail += target
        text = f"{subject} {detail}".strip()
        if params.get("why"):
            text += f": {params['why']}"
        outcome = humanize_result(tool, params, result)
        if outcome:
            text += f" => {outcome}"
        return text

    @staticmethod
    def event_text(report: str) -> str:
        """The `== Events ==` and `== Action Required ==` blocks of a turn report.

        Never the appended `== Situation ==` board (that is in board.txt and
        /api/board). A report with neither block logs "quiet turn".
        """
        lines = [ln.rstrip() for ln in report.splitlines()]
        if "== Situation ==" in lines:
            lines = lines[: lines.index("== Situation ==")]
        block: list[str] = []
        for header in ("== Events ==", "== Action Required =="):
            if header not in lines:
                continue
            for line in lines[lines.index(header) + 1 :]:
                if not line.strip() or line.startswith("=="):
                    break
                block.append(line.strip())
        return " | ".join(block) if block else "quiet turn"


# -- plain-language action results ------------------------------------------

_CITY_TOOLS = {
    "set_city_production": "production",
    "purchase_item": "purchase",
    "city_action": "action",
    "set_city_focus": "focus",
    "purchase_tile": "tile purchase",
}
_TYPE_PREFIXES = (
    "UNIT_",
    "BUILDING_",
    "TECH_",
    "CIVIC_",
    "DISTRICT_",
    "IMPROVEMENT_",
    "PROJECT_",
    "WONDER_",
    "RESOURCE_",
    "PROMOTION_",
    "POLICY_",
    "GOVERNMENT_",
)
_SMALL_WORDS = frozenset({"of", "the", "and", "a", "an", "in", "on", "to", "for"})


def game_name(type_name: str) -> str:
    """'CIVIC_CODE_OF_LAWS' -> 'Code of Laws', 'BUILDING_MONUMENT' -> 'Monument'."""
    s = type_name.strip()
    for prefix in _TYPE_PREFIXES:
        if s.startswith(prefix):
            s = s[len(prefix):]
            break
    if not s.isupper() and " " in s:
        return s  # already a display name
    words = s.replace("_", " ").lower().split()
    return " ".join(
        w if i and w in _SMALL_WORDS else w.capitalize() for i, w in enumerate(words)
    )


def hex_distance(x1: int, y1: int, x2: int, y2: int) -> int:
    """Tiles between two plots (Civ 6 offset rows: odd rows sit half a tile east)."""

    def axial(x: int, y: int) -> tuple[int, int]:
        return x - (y - (y & 1)) // 2, y

    q1, r1 = axial(x1, y1)
    q2, r2 = axial(x2, y2)
    dq, dr = q1 - q2, r1 - r2
    return (abs(dq) + abs(dr) + abs(dq + dr)) // 2


def _xy(text: str) -> tuple[int, int] | None:
    m = re.search(r"(-?\d+),(-?\d+)", text)
    return (int(m.group(1)), int(m.group(2))) if m else None


def _tiles(n: int) -> str:
    return f"{n} tile" if n == 1 else f"{n} tiles"


def _humanize_move(code: str, fields: list[str]) -> str:
    target = _xy(fields[0]) if fields else None
    now = next((_xy(f) for f in fields if f.startswith("now_at:")), None)
    verb = "moved to capture at" if code == "CAPTURE_MOVE" else "moved to"
    if target is None:
        return "moved"
    tx, ty = target
    if now is None:
        return f"ordered to move to ({tx},{ty})"
    if now == target:
        return f"{verb} ({tx},{ty})"
    nx, ny = now
    if any(f.startswith("STOPPED_MID_PATH") for f in fields):
        left = hex_distance(nx, ny, tx, ty)
        return f"moved to ({nx},{ny}), still {_tiles(left)} from ({tx},{ty})"
    return f"moved to ({nx},{ny}) on the way to ({tx},{ty})"


def _humanize_attack(lines: list[str]) -> str | None:
    line = next(
        (ln for ln in lines if re.match(r"(OK )?(OK:)?(MELEE|RANGE|CITY_RANGE|AIR)_ATTACK\|", ln)),
        None,
    )
    if line is None:
        return None
    tgt = re.search(r"at \((-?\d+),(-?\d+)\)", line) or re.search(r"@(-?\d+),(-?\d+)", line)
    where = f"({tgt.group(1)},{tgt.group(2)})" if tgt else "the target"
    text = "\n".join(lines)
    if "KILLED" in line or "(killed)" in line or "Target eliminated" in text:
        return f"attacked {where}: enemy destroyed"
    m = re.search(r"enemy HP:\d+ -> (\d+)/\d+", line)
    if m:
        return f"attacked {where}: enemy at {m.group(1)} HP"
    pre = re.search(r"pre_hp:(\d+)/\d+", line)
    est = re.search(r"est damage dealt:~(\d+)", line)
    done = re.search(r"\|damage dealt:(\d+)", line)
    if pre and done:
        return f"attacked {where}: enemy at {int(pre.group(1)) - int(done.group(1))} HP"
    if pre and est:
        return f"attacked {where}: enemy at about {max(int(pre.group(1)) - int(est.group(1)), 0)} HP"
    return f"attacked {where}"


def humanize_result(tool: str, params: dict, result: str) -> str:
    """A tool result in plain Civ terms for the log; else its first line."""
    raw = (result or "").strip()
    lines = [ln.strip() for ln in raw.splitlines() if ln.strip()]
    if not lines:
        return ""
    attack = _humanize_attack(lines)
    if attack:
        return attack
    first = lines[0]
    failed = first.startswith("FAIL ")  # batch_orders' own prefixes
    for prefix in ("OK ", "FAIL "):
        if first.startswith(prefix):
            first = first[len(prefix):]
            break
    if first.startswith("OK:"):
        first = first[3:].strip()
    if first.startswith("BLOCKED"):
        reason = first.split(":", 1)[1] if ":" in first else first
        reason = re.split(r" - | \(unit still at", reason, maxsplit=1)[0].strip()
        return f"blocked: {reason}" if reason else "blocked"
    if first.startswith("QUEUED"):
        xy = _xy(first)
        where = f" to ({xy[0]},{xy[1]})" if xy else ""
        return f"path{where} queued, continues next turn"
    if first.startswith("COUNTER"):
        # "COUNTER: Rome countered. We give: ... They give: ... The session is open: ..."
        terms = first.split(":", 1)[1].split(" The session is open", 1)[0].strip()
        return f"counter-offer: {terms}" if terms else "counter-offer"
    if first.startswith("Error"):
        msg = first[len("Error"):].lstrip(": ").strip()
        code, _, detail = msg.partition("|")
        return f"refused: {detail.strip() or code.replace('_', ' ').lower()}"
    code, *fields = first.split("|")
    if code in ("MOVING_TO", "CAPTURE_MOVE"):
        return _humanize_move(code, fields)
    if code == "FOUNDED":
        xy = _xy(fields[0]) if fields else None
        where = f" at ({xy[0]},{xy[1]})" if xy else ""
        name = fields[1].strip() if len(fields) > 1 and fields[1].strip() else "a city"
        return f"founded {name}{where}"
    if code == "FORTIFIED":
        return fields[0] if fields else "fortified"
    if code == "ALREADY_FORTIFIED":
        return "already fortified"
    if code == "PROMOTED" and fields:
        return f"promoted: {game_name(fields[0])}"
    if code in ("ACCEPTED", "REJECTED", "SENT", "APPOINTED") and fields:
        return fields[0].strip() or code.lower()
    if code == "ENVOY_SENT" and fields:
        return f"envoy sent to {fields[0].strip()}"
    if code == "UPGRADED" and fields:
        target = fields[0].split("->")[-1].strip()
        return f"upgraded to {game_name(target)}"
    if code == "POLICIES_SET":
        return "policies set"
    if code == "SLEEPING":
        return "sleeping"
    if code == "ALERT":
        return "on alert"
    if code == "HEALING":
        hp = fields[0].replace("HP:", "") if fields else ""
        return f"healing (HP {hp})" if hp else "healing"
    if code == "SKIPPED":
        return f"skipped {fields[0]}" if fields else "skipped this turn"
    if code == "PRODUCING" and fields:
        turns = f" ({fields[1]})" if len(fields) > 1 and fields[1] else ""
        return f"now building {game_name(fields[0])}{turns}"
    if code == "PURCHASED" and fields:
        cost = re.search(r"cost=(\d+)([gf])", first)
        paid = ""
        if cost:
            paid = f" for {cost.group(1)} {'faith' if cost.group(2) == 'f' else 'gold'}"
        return f"bought {game_name(fields[0])}{paid}"
    if code == "RESEARCHING" and fields:
        return f"researching {game_name(fields[0])}"
    if code == "PROGRESSING" and fields:
        return f"civic: {game_name(fields[0])}"
    if code == "IMPROVING" and fields:
        xy = _xy(fields[1]) if len(fields) > 1 else None
        where = f" at ({xy[0]},{xy[1]})" if xy else ""
        return f"building {game_name(fields[0])}{where}"
    return f"failed: {first}" if failed else first


NOTEBOOK_SECTIONS = ("Standing lessons", "Game log", "Current hypothesis")


def append_notebook_file(path: Path, section: str, text: str) -> None:
    """Append a dated bullet under ``## <section>`` in the campaign notebook.

    Creates the file and the heading if missing. Em dashes are replaced and
    line breaks collapsed so each note stays a single bullet.
    """
    if section not in NOTEBOOK_SECTIONS:
        raise ValueError(
            f"Unknown section {section!r}; use one of: {', '.join(NOTEBOOK_SECTIONS)}"
        )
    clean = " ".join(text.replace("—", ", ").split())
    if not clean:
        raise ValueError("text required")
    bullet = f"- {datetime.now(timezone.utc).strftime('%Y-%m-%d')}: {clean}"
    path = Path(path)
    lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    heading = f"## {section}"
    if heading not in lines:
        while lines and not lines[-1].strip():
            lines.pop()
        if lines:
            lines.append("")
        lines += [heading, ""]
    start = lines.index(heading) + 1
    end = start
    while end < len(lines) and not lines[end].startswith("## "):
        end += 1
    # insert after the last non-blank line of the section
    ins = end
    while ins > start and not lines[ins - 1].strip():
        ins -= 1
    if ins == start:
        lines[start:end] = ["", bullet, ""]
    else:
        lines.insert(ins, bullet)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines).rstrip("\n") + "\n", encoding="utf-8")

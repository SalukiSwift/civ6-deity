"""Broadcast log: the running commentary the player writes while playing.

Entries land in ``campaign/games/NN-civ/log.md`` (human readable) and
``log.jsonl`` (machine readable). Before a game is bound (menu, lobby) they
go to ``campaign/lobby.md`` and ``lobby.jsonl`` instead.
"""

from __future__ import annotations

import json
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
        self._count = 0
        self._last_turn: int | None = None

    @property
    def game_dir(self) -> Path | None:
        return self._game_dir

    # -- binding ---------------------------------------------------------

    def bind_game(self, identity: str, civ: str, leader: str = "") -> Path:
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
        rows = self._read_rows(d / "log.jsonl")
        self._count = len(rows)
        self._last_turn = next(
            (r["turn"] for r in reversed(rows) if r.get("turn") is not None), None
        )
        return d

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
            ts=datetime.now(timezone.utc).isoformat(timespec="seconds"),
            turn=turn,
            kind=kind,
            text=text,
            meta=dict(meta or {}),
        )
        base = self._game_dir if self._game_dir is not None else self.campaign_dir
        stem = "log" if self._game_dir is not None else "lobby"
        base.mkdir(parents=True, exist_ok=True)
        md = base / f"{stem}.md"
        line = f"- {entry.ts[11:16]} [{kind}] {text}\n"
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
    def action_text(tool: str, params: dict, result: str) -> str:
        parts = [tool]
        if params.get("action"):
            parts.append(str(params["action"]))
        if params.get("unit_id") is not None:
            try:
                parts.append(f"#{int(params['unit_id']) % 65536}")
            except (TypeError, ValueError):
                parts.append(f"#{params['unit_id']}")
        for key in ("item", "item_name", "improvement", "name"):
            if params.get(key):
                parts.append(str(params[key]))
                break
        tx = params.get("target_x", params.get("x"))
        ty = params.get("target_y", params.get("y"))
        if tx is not None and ty is not None:
            parts.append(f"-> ({tx},{ty})")
        text = " ".join(parts)
        if params.get("why"):
            text += f": {params['why']}"
        first = (result or "").strip().split("\n", 1)[0].strip()
        if first:
            text += f" => {first}"
        return text

    @staticmethod
    def event_text(report: str) -> str:
        """The `== Events ==` block of a turn report, else its first 15 lines."""
        lines = report.splitlines()
        if "== Events ==" in lines:
            block = []
            for line in lines[lines.index("== Events ==") + 1 :]:
                if not line.strip() or line.startswith("=="):
                    break
                block.append(line.strip())
            if block:
                return " | ".join(block)
        return " | ".join(ln.strip() for ln in lines[:15] if ln.strip())

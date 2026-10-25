"""Last-seen memory for fogged tiles.

A human player remembers what a tile looked like the last time it was in
view. ``FogMemory`` records only what was actually VISIBLE at the time, so
fogged tiles can be narrated from memory instead of from live game state.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable

from civ_mcp.lua.models import TileInfo

log = logging.getLogger(__name__)


@dataclass
class SeenTile:
    turn: int
    owner: str | None
    city: str | None
    district: str | None
    improvement: str | None
    pillaged: bool


class FogMemory:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path
        self._tiles: dict[tuple[int, int], SeenTile] = {}

    def remember(self, turn: int, tiles: Iterable[TileInfo]) -> None:
        """Record tiles that are currently visible. Fogged tiles are ignored."""
        for t in tiles:
            if not getattr(t, "visible", False):
                continue
            prev = self._tiles.get((t.x, t.y))
            if prev is not None and prev.turn > turn:
                continue
            self._tiles[(t.x, t.y)] = SeenTile(
                turn=turn,
                owner=t.owner_name,
                city=None,
                district=t.district,
                improvement=t.improvement,
                pillaged=bool(t.is_pillaged),
            )

    def recall(self, x: int, y: int) -> SeenTile | None:
        return self._tiles.get((x, y))

    def save(self) -> None:
        if self.path is None:
            return
        data = {f"{x},{y}": asdict(s) for (x, y), s in self._tiles.items()}
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(self.path.suffix + ".tmp")
            tmp.write_text(json.dumps(data), encoding="utf-8")
            os.replace(tmp, self.path)
        except OSError:
            log.warning("FogMemory save failed: %s", self.path, exc_info=True)

    @classmethod
    def load(cls, path: Path) -> "FogMemory":
        mem = cls(path=path)
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            for key, d in raw.items():
                xs, ys = key.split(",")
                mem._tiles[(int(xs), int(ys))] = SeenTile(**d)
        except (OSError, ValueError, TypeError):
            log.warning("FogMemory load failed: %s", path, exc_info=True)
        return mem

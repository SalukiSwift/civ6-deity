# Civ 6 Deity Cybernetics Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn the civ6-mcp fork into a fair, spectator-friendly, call-efficient set of tools with which Claude plays Civilization VI on Deity while April watches a live commentary log.

**Architecture:** The existing MCP server (Python, FireTuner Lua socket) stays. Runtime moves to Windows Python launched from WSL over the interop. Four additions bolt on: a fog seal over every Lua query and action (player-visible information only, stock player commands only), a `Broadcast` log every action and narration flows through, a per-turn situation board and `batch_orders` to cut tool calls, and a campaign layer (notebook, playbook, FrontEnd-Lua game creation). A tiny viewer page is served from the server's existing FastAPI app.

**Tech Stack:** Python 3.12, uv, pytest, FastAPI (already a dependency), Lua snippets run in the game's `GameCore_Tuner` (reads) and `InGame` (commands) states, Windows `uv.exe`.

**Spec:** `docs/superpowers/specs/2026-10-25-civ6-deity-design.md`

## Global Constraints

- Repo: `C:\Users\April Tomlinson\civ6-deity` = `/mnt/c/Users/April Tomlinson/civ6-deity`. Work on branch `deity-v1` from `main`; merge to `main` only after Task 13.
- Python `>=3.12`. Runtime venv is the Windows `.venv` created by `C:\Users\April Tomlinson\.local\bin\uv.exe`. WSL tests must NOT touch that venv: always run `scripts/test.sh` (created in Task 2), which sets `UV_PROJECT_ENVIRONMENT=$HOME/.venvs/civ6-deity`.
- Fairness: every tile fact needs `IsRevealed`, every unit/city live fact needs `IsVisible`, every rival fact needs `HasMet` plus a named in-game screen; resources need the revealing tech. Mutations only in the `InGame` state through the APIs the game's own UI uses (`UnitManager.RequestOperation/RequestCommand`, `CityManager.*`, `UI.RequestPlayerOperation`, `DiplomacyManager`, `DealManager`). Nothing in tool code reads the game's `Logs/` directory or `GAME_SYNC_RANDOM_SEED` into agent-visible text.
- Writing style for docs, log text and viewer copy: no em dashes, no decorative emoji (April's rule).
- Commit messages: conventional prefix, last line `Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>`.
- Live tasks (1, 11, 12) are the only tasks that may launch or touch the game, and only after April has been told. All other tasks run without the game, using the stub patterns in `tests/`.
- Do not modify `web/`, `evals/`, `scripts/orchestrator*`, `notebooks/`, `fixtures/`. Leave `~/.civ6-mcp` telemetry local (never set `CIV_MCP_TELEMETRY_BUCKET` or `CIV_MCP_ALERT_WEBHOOK`).
- The game's own UI Lua is the reference for player-legal calls: `C:\Program Files (x86)\Steam\steamapps\common\Sid Meier's Civilization VI\Base\Assets\UI\` (read-only).

## Review Focus

1. A revealed-but-fogged tile with no FogMemory record must narrate as `(fog)` with static terrain only, never live owner or improvement. Pinned in Task 3.
2. `narrate`/action tools called before any game is bound (main menu, or first call of a session) must land in `campaign/lobby.md`, not crash or vanish. Pinned in Task 2.
3. `end_turn` with an empty `summary` must refuse without advancing the turn. Pinned in Task 2.
4. `batch_orders` with one malformed order among valid ones must run the valid ones and report the bad one by index. Pinned in Task 8.
5. Any load path (`load_save`, `load_game_save`, `load_save_from_menu`, `restart_and_load`, the `_logged` auto-restart) must record into `gs._save_load_history` and the broadcast, and tool-initiated loads without a `reason` must be refused. Pinned in Task 5.

---

### Task 1: Windows runtime spike (LIVE, Opus tester)

**Files:**
- Modify: `pyproject.toml` (launcher-windows extra: add `pywin32`, `Pillow`)
- Modify: `.mcp.json`
- Create: `scripts/mcp_smoke.py`, `docs/RUNTIME.md`
- Backup then modify: `C:\Users\April Tomlinson\AppData\Local\Firaxis Games\Sid Meier's Civilization VI\AppOptions.txt` (`EnableTuner 0` -> `1`)

**Interfaces:**
- Produces: `docs/RUNTIME.md` with the tuner's Lua state list at the main menu and in game (exact names and indexes), the working `.mcp.json`, and the Windows commands. Task 11 consumes the FrontEnd state name.

- [ ] **Step 1: Branch and extra deps**

```bash
cd "/mnt/c/Users/April Tomlinson/civ6-deity" && git checkout -b deity-v1
```
Add `pywin32>=306` and `Pillow>=10` to the `launcher-windows` extra in `pyproject.toml` (the OCR fallback imports both; upstream forgot them).

- [ ] **Step 2: Enable the tuner (backup first)**

Copy `AppOptions.txt` to `AppOptions.txt.bak-2026-10-25` beside it, then change the line `EnableTuner 0` to `EnableTuner 1`. Confirm `AutoEndTurn 0` is still set in `UserOptions.txt` (it is today).

- [ ] **Step 3: Windows sync**

Run from WSL (note the Windows path in `--directory`):
```bash
"/mnt/c/Users/April Tomlinson/.local/bin/uv.exe" sync --directory 'C:\Users\April Tomlinson\civ6-deity' --extra launcher-windows
```
Expected: `.venv` created under the repo with cpython 3.12.x, no errors.

- [ ] **Step 4: Launch the game and check the port**

`cmd.exe /c start steam://run/289070`. Poll `netstat.exe -ano | grep -i 4318` every 10 s for up to 4 min. Expected: a `LISTENING` line once the main menu is up. If the main menu is up for 60 s and nothing listens: STOP, write the finding to `docs/RUNTIME.md` ("SDK needed: Steam > Library > Tools > Sid Meier's Civilization VI Development Tools") and report back; do not continue.

- [ ] **Step 5: Tuner handshake and state list**

```bash
"/mnt/c/Users/April Tomlinson/.local/bin/uv.exe" run --directory 'C:\Users\April Tomlinson\civ6-deity' python scripts/test_connection.py
```
Record every `index -> state name` pair printed at the MAIN MENU. Then (April will see this) start any quick game from the menu, wait for the map, re-run, and record the in-game list. Which state holds the main-menu UI (`FrontEnd`, `MainMenu` or similar) goes in `docs/RUNTIME.md` verbatim. Quit to desktop afterwards (`taskkill.exe /IM CivilizationVI_DX12.exe /F` is acceptable).

- [ ] **Step 6: MCP over the interop**

Write `.mcp.json`:
```json
{"mcpServers": {"civ6": {"command": "/mnt/c/Users/April Tomlinson/.local/bin/uv.exe",
  "args": ["run", "--directory", "C:\\Users\\April Tomlinson\\civ6-deity", "civ-mcp"]}}}
```
Write `scripts/mcp_smoke.py` (stdlib only, runs under WSL `python3`): spawn that command with pipes, send JSON-RPC `initialize`, then `notifications/initialized`, then `tools/list` (Content-Length framing is NOT used by MCP stdio; one JSON object per line), print `serverInfo.name` and the tool count, exit 0. Run it with the game closed: the server must still answer `initialize` and `tools/list` (lifespan connects lazily or logs a connection error without dying; if it dies, fix `lifespan` so a missing game does not kill the server, and note it).

Run: `python3 scripts/mcp_smoke.py`
Expected: `Civilization VI` and `76` (or the current count).

- [ ] **Step 7: Document and commit**

`docs/RUNTIME.md`: the commands above, the state lists, the Windows firewall note (uvicorn binds a port at startup; a prompt may appear once), and "restart the game if the tuner refuses a connection".
```bash
git add pyproject.toml .mcp.json scripts/mcp_smoke.py docs/RUNTIME.md && git commit -m "chore: windows runtime spike, tuner on, mcp over interop"
```

---

### Task 2: Broadcast log core

**Files:**
- Create: `src/civ_mcp/broadcast.py`, `scripts/test.sh`, `campaign/.gitkeep`
- Modify: `src/civ_mcp/server.py` (AppContext, lifespan, `_logged`, `end_turn`, new `narrate`, `why` on action tools)
- Modify: `.gitignore` (add `campaign/games/`, `campaign/lobby.md`)
- Test: `tests/test_broadcast.py`

**Interfaces:**
- Produces:
  ```python
  KINDS = ("plan","read","decision","reaction","lesson","pivot","postmortem","turn","event","action","load","game")
  ACTION_TOOLS: frozenset[str]   # names of every mutating tool
  @dataclass class Entry: ts: str; turn: int | None; kind: str; text: str; meta: dict
  class Broadcast:
      def __init__(self, campaign_dir: Path) -> None
      def bind_game(self, identity: str, civ: str, leader: str = "") -> Path   # games/index.json maps identity -> "NN-civ"; creates dir + meta.json
      @property
      def game_dir(self) -> Path | None
      def append(self, kind: str, text: str, *, turn: int | None = None, meta: dict | None = None) -> Entry
      def tail(self, after: int = 0, limit: int = 200) -> list[dict]   # rows include "i" (1-based index)
      def set_board(self, text: str) -> None
      def last_board(self) -> str | None
      @staticmethod
      def action_text(tool: str, params: dict, result: str) -> str   # "unit_action move #3 -> (12,8): <why> => <first result line>"
  ```
- Unbound writes go to `campaign/lobby.md` + `lobby.jsonl`; `log.md` gets a `## Turn N` heading whenever `turn` changes; lines are `- HH:MM [kind] text`.

- [ ] **Step 1: Test runner script**

`scripts/test.sh`:
```bash
#!/usr/bin/env bash
set -e; cd "$(dirname "$0")/.."
export UV_PROJECT_ENVIRONMENT="${UV_PROJECT_ENVIRONMENT:-$HOME/.venvs/civ6-deity}"
exec uv run pytest -q "$@"
```
Run: `bash scripts/test.sh` Expected: existing 7 test files pass (count them in the output).

- [ ] **Step 2: Write failing tests**

`tests/test_broadcast.py`:
```python
def test_bind_game_creates_numbered_dir(tmp_path):
    b = Broadcast(tmp_path); d = b.bind_game("abc123", "sumeria", "Gilgamesh")
    assert d.name == "01-sumeria" and (d / "meta.json").exists()
    assert Broadcast(tmp_path).bind_game("abc123", "sumeria") == d        # same identity, same dir
    assert Broadcast(tmp_path).bind_game("def456", "nubia").name == "02-nubia"

def test_append_writes_md_and_jsonl_with_turn_headers(tmp_path):
    b = Broadcast(tmp_path); b.bind_game("x", "rome")
    b.append("plan", "Settle in place.", turn=1); b.append("reaction", "Barbarians east.", turn=2)
    md = (b.game_dir / "log.md").read_text()
    assert "## Turn 1" in md and "## Turn 2" in md and "[plan] Settle in place." in md
    assert len((b.game_dir / "log.jsonl").read_text().splitlines()) == 2
    assert "\u2014" not in md

def test_unbound_append_goes_to_lobby(tmp_path):
    b = Broadcast(tmp_path); b.append("read", "At the menu.")
    assert "[read] At the menu." in (tmp_path / "lobby.md").read_text()

def test_tail_and_board(tmp_path):
    b = Broadcast(tmp_path); b.bind_game("x", "rome")
    for i in range(5): b.append("plan", f"p{i}", turn=1)
    rows = b.tail(after=3); assert [r["text"] for r in rows] == ["p3", "p4"] and rows[-1]["i"] == 5
    b.set_board("EMPIRE ..."); assert b.last_board() == "EMPIRE ..."

def test_invalid_kind_rejected(tmp_path):
    with pytest.raises(ValueError): Broadcast(tmp_path).append("vibes", "no")

def test_action_text_format():
    t = Broadcast.action_text("unit_action", {"unit_id": 3, "action": "move", "target_x": 12, "target_y": 8, "why": "scout river"}, "OK: moved\nmore")
    assert t == "unit_action move #3 -> (12,8): scout river => OK: moved"

async def test_end_turn_requires_summary(monkeypatch):   # Review Focus 3; fake ctx per tests/test_advisor_budget.py's SimpleNamespace pattern
    called = []; monkeypatch.setattr(server, "execute_end_turn", lambda gs: called.append(1))
    out = await server.end_turn.fn(fake_ctx, summary="   ")
    assert out.startswith("Error: summary required") and called == []
```

- [ ] **Step 3: Run to verify failure**

Run: `bash scripts/test.sh tests/test_broadcast.py` Expected: ImportError on `civ_mcp.broadcast`.

- [ ] **Step 4: Implement `broadcast.py`**

Per the Interfaces block. `action_text` rules: unit tools show `#<unit_id % 65536>`; a target pair becomes `-> (x,y)`; an `item`/`improvement`/`name` param is appended after the action; `why` after a colon when non-empty; ` => ` plus the first line of `result`.

- [ ] **Step 5: Wire into the server**

- `AppContext` gains `broadcast: Broadcast`; `lifespan` builds `Broadcast(Path(__file__).parents[2] / "campaign")`.
- `_get_broadcast(ctx)` helper beside the others.
- `_logged`: after a successful call, if `tool_name in ACTION_TOOLS`, `broadcast.append("action", Broadcast.action_text(...), turn=gs._high_water_turn)`. Before that, if `broadcast.game_dir is None`, try `civ, seed = await gs.get_game_identity()` and bind with `identity=hashlib.sha1(str(seed).encode()).hexdigest()[:10]`; on `ConnectionError` stay unbound.
- Add `why: str = ""` (keyword) to every tool in `ACTION_TOOLS` and include it in that tool's `params` dict. `ACTION_TOOLS` = every tool the explorer marked M except `end_turn`, `dismiss_popup`, `run_lua`: unit_action, promote_unit, upgrade_unit, skip_remaining_units, set_city_production, purchase_item, city_action, set_city_focus, purchase_tile, set_research, set_policies, change_government, appoint_governor, assign_governor, promote_governor, choose_dedication, respond_to_diplomacy, send_diplomatic_action, form_alliance, propose_peace, respond_to_trade, propose_trade, send_envoy, choose_pantheon, found_religion, recruit_great_person, patronize_great_person, reject_great_person, spy_action, queue_wc_votes.
- New tool `narrate(ctx, kind: str, text: str) -> str`: validates kind, appends with `turn=gs._high_water_turn`, returns `"logged"`.
- `end_turn(ctx, summary: str)`: replace the five-reflection parameters. Empty or whitespace `summary` returns `"Error: summary required (what you did this turn and why)"` before anything else. Log `turn` with the summary before advancing; after a successful advance log `event` with the report's `== Events ==` block (or the first 15 lines if the block is absent). The diary row keeps `reflections={"summary": summary}`; remove the code that insists on five non-empty fields.

- [ ] **Step 6: Run all tests, then commit**

Run: `bash scripts/test.sh` Expected: all pass.
```bash
git add -A && git commit -m "feat: broadcast log, narrate tool, why on actions, end_turn summary"
```

---

### Task 3: Fog seal A: tiles, units, cities, FogMemory

**Files:**
- Create: `src/civ_mcp/fog.py`
- Modify: `src/civ_mcp/lua/map.py`, `src/civ_mcp/lua/units.py`, `src/civ_mcp/lua/cities.py`, `src/civ_mcp/lua/models.py` (TileInfo), `src/civ_mcp/narrate.py` (`narrate_map`), `src/civ_mcp/game_state.py` (feed FogMemory), `src/civ_mcp/server.py` (lifespan: no `MapCapture`; fix the `lq` import at line ~538)
- Test: `tests/test_fog_memory.py`, `tests/test_fog_seal_tiles.py`

**Interfaces:**
- Produces:
  ```python
  @dataclass class SeenTile: turn: int; owner: str | None; city: str | None; district: str | None; improvement: str | None; pillaged: bool
  class FogMemory:
      def __init__(self, path: Path | None = None) -> None
      def remember(self, turn: int, tiles: Iterable[TileInfo]) -> None   # only tiles with visible=True
      def recall(self, x: int, y: int) -> SeenTile | None
      def save(self) -> None
      @classmethod
      def load(cls, path: Path) -> "FogMemory"
  ```
- `TileInfo` gains `visible: bool`; `GameState` holds `fog: FogMemory`, saved to `<game_dir>/fog.json` after every `remember` (path set when Broadcast binds; Task 2's bind point).

- [ ] **Step 1: Failing tests**

`tests/test_fog_memory.py`: round trip (`remember` two visible tiles and one fogged, `recall` returns the visible ones only, `save`/`load` preserves them, later `remember` overwrites with the newer turn).

`tests/test_fog_seal_tiles.py` (string assertions on generated Lua; call each builder with dummy args):
```python
def test_map_area_emits_visibility_flag_and_guards_live_fields():
    lua = build_map_area_query(10, 10, 2)
    assert "IsRevealed(" in lua and "IsVisible(" in lua
    # live fields only inside the visible branch: owner/improvement/district/pillage tokens must not appear before the IsVisible check
    assert lua.index("IsVisible(") < lua.index("GetOwner()")
def test_settle_preamble_only_counts_revealed_cities():
    assert "IsRevealed(" in _SETTLE_PREAMBLE or "IsRevealed(" in _SETTLE_SCORE_BODY
def test_unit_targets_require_visibility():
    assert "IsVisible(" in build_units_query()
def test_city_targets_and_city_tiles_are_gated():
    lua = build_cities_query(); assert "IsVisible(" in lua and "PrereqTech" in lua and "RemoveAt" not in lua
def test_builder_tasks_and_wonder_resources_tech_gated():
    assert "PrereqTech" in build_builder_tasks_query() and "PrereqTech" in build_wonder_advisor_query(1)
def test_found_city_error_names_only_revealed_cities():
    assert "IsRevealed(" in build_found_city(1)
def test_no_omniscient_dumps_registered():
    import civ_mcp.server as s; assert not hasattr(s, "MapCapture") or "map_capture" not in s.AppContext.__dataclass_fields__
```
(Adjust builder argument lists to the real signatures; the assertions are the contract.)

- [ ] **Step 2: Run to verify failure**

Run: `bash scripts/test.sh tests/test_fog_memory.py tests/test_fog_seal_tiles.py` Expected: FAIL (fog module missing; guards absent).

- [ ] **Step 3: Implement**

- `fog.py` per Interfaces.
- `map.py:147 build_map_area_query`: emit a `V` flag per tile; for revealed-not-visible tiles print only terrain, feature, river, hills, continent and tech-visible resource; owner, improvement, district, route, pillage, units and yields only when visible. `_SETTLE_PREAMBLE`/`_SETTLE_SCORE_BODY`: iterate cities only when their plot `IsRevealed`. `build_found_city` error text: revealed cities only. `build_strategic_map_query`: owner only when visible. `build_wonder_advisor_query`: resource through `_LUA_RES_VISIBLE`.
- `units.py:23`: targets gated by `pVis:IsVisible(plot)` and `pUnit:IsVisible`-equivalent where available; `units.py:434/495`: enemy details only for visible units.
- `cities.py:16`: `cityTargets` gated by `IsVisible`; CITYTILES resources through `_LUA_RES_VISIBLE`; move the `pcall(bq:RemoveAt(0))` out of the read query into `set_city_production`'s Lua (where a corrupted head entry actually blocks the player) with a comment, or delete it if `set_city_production` already handles it.
- `narrate.py narrate_map`: for `visible=False` tiles append `(fog; last seen T{n}: owner X, improvement Y)` from `FogMemory.recall`, or `(fog)` when nothing is recalled. `narrate_map` takes an optional `fog: FogMemory | None` argument; `server.get_map_area` passes `gs.fog`.
- `game_state.py`: after `get_map_area`, `move_unit`'s post-move visibility query and `get_threat_scan`, call `self.fog.remember(turn, tiles)` and `self.fog.save()`.
- `server.py`: remove `MapCapture` from `AppContext`/lifespan (its two queries are omniscient); import `lua as lq` (or the right alias) so the revealed-tiles seeding at ~538 runs.
- Review Focus 1 test, added to `tests/test_fog_seal_tiles.py`: `narrate_map([TileInfo(..., visible=False, owner="Rome", improvement="Mine")], fog=FogMemory())` output contains `(fog)` and not `Rome` or `Mine` (the parser must blank live fields for fogged tiles; assert on the parsed TileInfo too if the parser is where blanking happens).

- [ ] **Step 4: Run all tests, commit**

Run: `bash scripts/test.sh` Expected: pass.
```bash
git add -A && git commit -m "feat: fog seal for tiles, units and cities; FogMemory for last-seen"
```

---

### Task 4: Fog seal B: rivals and players

**Files:**
- Modify: `src/civ_mcp/lua/diplomacy.py`, `lua/overview.py`, `lua/victory.py`, `lua/economy.py`, `lua/religion.py`, `lua/notifications.py`, `lua/great_people.py`, `lua/governance.py` (suzerain name), `lua/units.py` (threat-scan owner names), `src/civ_mcp/narrate.py` (rival/victory narration), `src/civ_mcp/end_turn.py` (rival warnings), `src/civ_mcp/game_state.py` (`get_game_identity`), `src/civ_mcp/server.py` (`get_diary` text)
- Test: `tests/test_fog_seal_rivals.py`

**Interfaces:**
- Produces: `RivalRow` (in `lua/models.py`) reduced to `{player_id, name, met, score, techs, military_strength, cities_seen, science_vp, tourism, tourists, religion_converts, diplo_vp, era}`; `end_turn` warnings use `techs` for the science rank and `military_strength` for the 2x warning. Task 7 consumes `RivalRow`.
- In-game screen mapping (write it into `docs/FAIRNESS.md` in Task 5): score and era from the ribbon; techs count, space projects, tourism/tourists, military strength, capitals, converts, diplomatic VP from World Rankings; gold/GPT/items/cities only inside a deal query (deal screen).

- [ ] **Step 1: Failing tests**

```python
def test_rival_snapshot_has_no_yields_or_stockpiles():
    lua = build_rival_snapshot_query()
    for tok in ("GetYield(", "GetStockpile", "GetCivicsResearched", "GetFaithBalance", "GetGoldBalance"): assert tok not in lua
    assert "HasMet(" in lua and "GetMilitaryStrength" in lua
def test_victory_progress_has_no_demographics_or_rival_yields():
    lua = build_victory_progress_query(); assert "DEMO" not in lua and "GetYield(" not in lua
def test_diplomacy_city_stats_require_visibility_and_no_fog_count():
    lua = build_diplomacy_query(); assert "IsVisible(" in lua and "CIVCITIES" not in lua
def test_trade_destinations_have_no_omniscient_fallback():
    assert "found == 0" not in build_trade_destinations_query() and "found==0" not in build_trade_destinations_query()
def test_owner_names_gated_by_hasmet():
    for lua in (build_threat_scan_query(), build_city_states_query(), build_great_people_query(), build_notifications_query()):
        assert "HasMet(" in lua
def test_diary_full_query_is_local_player_only():
    lua = build_diary_full_query(); assert "for _, pid in ipairs(PlayerManager.GetAliveMajorIDs())" not in lua or "if pid == me" in lua
def test_seed_never_in_agent_text(monkeypatch):
    # get_diary's "no entries" message must not include the seed
    import civ_mcp.server as s; assert "seed {seed}" not in inspect.getsource(s.get_diary)
```

- [ ] **Step 2: Run to verify failure**

Run: `bash scripts/test.sh tests/test_fog_seal_rivals.py` Expected: FAIL.

- [ ] **Step 3: Implement**

- `overview.py:626 build_rival_snapshot_query`: fields per Interfaces only; `cities_seen` = count of that player's cities whose plot `IsRevealed`.
- `victory.py:76`: drop DEMO and SCI/CUL/GOLD yields and civics; `numCities` -> revealed count. `victory.py:14`: REL majority names only for met civs, count otherwise.
- `diplomacy.py:21`: ECITY lines only when `IsVisible`; remove CIVCITIES; keep `GetMilitaryStrength` (Domination tab) and `GetDiplomaticAI()` state/modifiers (the diplomacy screen shows them). `diplomacy.py:502` keeps the deal-screen data but guards the session is open; narration says "(deal screen)".
- `economy.py:173`: delete the `found == 0` fallback; return "no valid destinations".
- `religion.py:291`: RSUMMARY met civs only.
- Owner/claimant/suzerain names: `HasMet` else `"an unmet civilization"` in `units.py:735`, `governance.py:410`, `great_people.py:9`, `notifications.py`, `map.py` owner names.
- `overview.py:722 build_diary_full_query`: local player row only (rival rows and foreign city rows gone). The diary writer path must tolerate a single row.
- `game_state.get_game_identity`: unchanged return, but every agent-visible string that printed the seed (`get_diary` "no entries" text, any narration) prints `game {sha1(seed)[:10]}` instead; diary and telemetry filenames use the same hash.
- `end_turn.py:235` warnings: science rank by `techs`, military warning by `military_strength`.
- `narrate.py`: update rival and victory narration to the reduced fields; section titles "RIVALS (World Rankings)" and no "DEMOGRAPHICS".

- [ ] **Step 4: Run all tests, commit**

```bash
bash scripts/test.sh && git add -A && git commit -m "feat: fog seal for rivals; world-rankings-only rival data; seed hidden"
```

---

### Task 5: Fog seal C: player-legal actions, loads, FAIRNESS.md

**Files:**
- Modify: `src/civ_mcp/lua/units.py` (skip), `lua/governance.py` (promote), `lua/tech.py` (remove GameCore fallback), `src/civ_mcp/end_turn.py` (blocker handlers), `src/civ_mcp/game_state.py` (skip/promote/set_research use `execute_write`; `_record_save_load`), `src/civ_mcp/server.py` (`run_lua` gating, load tools' `reason`, `propose_trade`), `src/civ_mcp/game_lifecycle.py`, `src/civ_mcp/game_launcher.py` (`restart_and_load` records), `src/civ_mcp/lua/diplomacy.py:800` (no auto-accept)
- Create: `docs/FAIRNESS.md`
- Test: `tests/test_player_legal_actions.py`, `tests/test_load_gating.py`

**Interfaces:**
- Produces: load tools gain `reason: str` (required, non-empty); `GameState.record_load(save_name: str, reason: str) -> None` wraps `_record_save_load` and appends a `load` broadcast entry via a callback set by the server (`gs.on_load: Callable[[str, str], None] | None`). `propose_trade(mode="send")` returns `COUNTER: ...` text when the AI counters and leaves the session open for `respond_to_trade`.

- [ ] **Step 1: Failing tests**

`tests/test_player_legal_actions.py`:
```python
def test_skip_runs_in_ingame_with_stock_operation():
    lua = build_skip_unit(5); assert "FinishMoves" not in lua or "UnitManager.RequestOperation" in lua
def test_promote_uses_stock_command_and_never_heals():
    lua = build_promote_unit(5, "PROMOTION_BATTLECRY")
    assert "UnitCommandTypes.PROMOTE" in lua and "SetDamage" not in lua and "SetPromotion" not in lua
def test_set_research_has_no_gamecore_fallback():
    import civ_mcp.lua.tech as t; assert not hasattr(t, "_build_set_gamecore")
def test_skip_and_promote_go_through_execute_write(stub_gs):   # GameState.__new__ + _StubConnection pattern from tests/test_set_city_production.py
    ... assert stub.read_calls == [] after gs.skip_unit(1) and gs.promote_unit(1, "X")
def test_run_lua_absent_by_default(monkeypatch):
    monkeypatch.delenv("CIV_MCP_DEV_LUA", raising=False); importlib.reload(server); assert "run_lua" not in [t.name for t in server.mcp._tool_manager.list_tools()]
def test_read_queries_do_not_mutate():
    for lua in (build_cities_query(), build_units_query(), build_map_area_query(1,1,1)): assert "RemoveAt" not in lua and "Request" not in lua
def test_propose_trade_send_does_not_auto_accept():
    assert "AddResponse(DiplomacyResponseTypes.ACCEPT" not in build_send_trade_lua(...)   # name per the real builder at diplomacy.py:800
```
`tests/test_load_gating.py` (Review Focus 5): `record_load` appends to `_save_load_history` and calls `on_load`; calling the `load_game_save` tool body with `reason=""` returns an `Error:` string and does not call the lifecycle loader (monkeypatch it).

- [ ] **Step 2: Run to verify failure**

Run: `bash scripts/test.sh tests/test_player_legal_actions.py tests/test_load_gating.py` Expected: FAIL.

- [ ] **Step 3: Implement**

- Read `Base/Assets/UI/Panels/UnitPanel.lua` (find it with `grep -rl "UnitCommandTypes.PROMOTE" .../Base/Assets/UI`) and copy its exact calls for skip and promote. Skip: the operation the panel's skip button requests (InGame). Promote: `UnitManager.RequestCommand(pUnit, UnitCommandTypes.PROMOTE, {[UnitCommandTypes.PARAM_PROMOTION_TYPE] = GameInfo.UnitPromotions[name].Hash})`, then report the unit's HP and moves as the game left them.
- `game_state.py`: `skip_unit`, `skip_remaining_units`, `promote_unit` call `execute_write`; `set_research`/`set_civic` lose the fallback and return the operation's error.
- `end_turn.py:1009-1023`: ENDTURN_BLOCKING_UNITS resolved by issuing the stock skip per unit in InGame; `925-951` UNIT_PROMOTION stale: dismiss the notification only. CONSIDER_RAZE_CITY and CONSIDER_DISLOYAL_CITY are no longer auto-answered "keep": report them as blockers with the `city_action` hint so the player decides (test: the blocker-handler table has no entry for either).
- `server.py`: register `run_lua` only when `os.environ.get("CIV_MCP_DEV_LUA") == "1"` (invert upstream's `CIV_MCP_DISABLE_LUA`). Every load tool: `reason: str` required; refuse empty; call `gs.record_load(name, reason)` on success; the `_logged` auto-restart path records `reason="connection lost 5x"`. `restart_and_load`, `load_save_from_menu`, `_auto_boot` all record.
- `diplomacy.py:800`: no `AddResponse(ACCEPT)` on the counter; return the counter items.
- `docs/FAIRNESS.md`: a table of every registered tool: name, R/M/L, what it exposes or changes, the in-game screen or button it corresponds to, guard used. Plus the standing rules (from Global Constraints) and the save-scum policy.

- [ ] **Step 4: Run all tests, commit**

```bash
bash scripts/test.sh && git add -A && git commit -m "feat: player-legal actions only, gated loads with reasons, FAIRNESS.md"
```

---

### Task 6: Situation board

**Files:**
- Create: `src/civ_mcp/situation.py`
- Modify: `src/civ_mcp/server.py` (`get_situation` tool; `end_turn` appends the board and calls `broadcast.set_board`)
- Test: `tests/test_situation.py`

**Interfaces:**
- Consumes: `GameOverview`, `UnitInfo`, `CityInfo`, `RivalRow` (Task 4), `TurnEvent`, blocking notifications list, threat scan rows (existing dataclasses in `lua/models.py`).
- Produces:
  ```python
  @dataclass class Situation: overview: GameOverview; units: list[UnitInfo]; cities: list[CityInfo]; threats: list[ThreatRow]; rivals: list[RivalRow]; blockers: list[str]; victory_warnings: list[TurnEvent]; trade_capacity: tuple[int, int]; envoys_available: int; governor_titles: int
  def format_situation(s: Situation) -> str          # <= 40 lines
  async def gather_situation(gs: GameState) -> Situation
  ```
- Board layout, exact section headers: `EMPIRE`, `ATTENTION`, `THREATS`, `RIVALS`. `ATTENTION` lines: `idle units: <ids>`, `no production: <cities>`, `research/civic not set`, `envoys to place: N`, `governor titles: N`, `trade routes: used/cap`, `pending choice: <blocker title>`, `promotions available: <unit ids>`. `RIVALS`: one line per met civ `name score techs mil` sorted by score, then any victory warning line verbatim. Empty sections print `none`.

- [ ] **Step 1: Failing tests**

```python
def test_format_sections_and_limit(sample_situation):   # build dataclasses by hand in the test
    out = format_situation(sample_situation); lines = out.splitlines()
    assert lines[0].startswith("EMPIRE") and "ATTENTION" in out and "THREATS" in out and "RIVALS" in out and len(lines) <= 40
def test_idle_units_and_empty_production_listed(sample_situation):
    assert "idle units: 3, 7" in format_situation(sample_situation) and "no production: Ur" in ...
def test_empty_sections_say_none(empty_situation): assert "THREATS\n  none" in format_situation(empty_situation)
def test_rivals_sorted_by_score(sample_situation): ...
```

- [ ] **Step 2: Run to verify failure**  Run: `bash scripts/test.sh tests/test_situation.py` Expected: ImportError.

- [ ] **Step 3: Implement**

`gather_situation` reuses existing `GameState` methods (overview, units, cities, blocking notifications, threat scan, rival snapshot, trade capacity, city-states envoy count, governors) and makes no new Lua. Idle unit = moves remaining > 0 and no fortify/sleep/automation flag (use whatever `UnitInfo` exposes; if no such flag exists, add `is_busy: bool` to the units query from the unit's activity type, InGame, own units only). `server.py`: tool `get_situation(ctx) -> str` (readOnlyHint); in `end_turn`, when the turn advanced, append `"\n== Situation ==\n" + board` to the report and `broadcast.set_board(board)`.

- [ ] **Step 4: Run all tests, commit**

```bash
bash scripts/test.sh && git add -A && git commit -m "feat: situation board after every turn and on demand"
```

---

### Task 7: batch_orders

**Files:**
- Create: `src/civ_mcp/orders.py`
- Modify: `src/civ_mcp/server.py` (`batch_orders` tool)
- Test: `tests/test_orders.py`

**Interfaces:**
- Produces:
  ```python
  @dataclass class Order: kind: str; id: int | None; action: str; x: int | None; y: int | None; item: str | None; why: str; index: int
  def parse_orders(raw: list[dict]) -> tuple[list[Order], list[str]]     # errors like "#2: unit order needs unit_id"
  async def run_orders(gs: GameState, orders: list[Order], on_each: Callable[[Order, str], Awaitable[None]]) -> list[tuple[Order, str]]
  ```
- Order dict schema: `{"unit_id": int, "action": "move|attack|fortify|skip|found_city|improve|heal|alert|sleep", "x": int, "y": int, "improvement": str, "why": str}` or `{"city_id": int, "production": str}` or `{"city_id": int, "purchase": str}` or `{"research": str}` / `{"civic": str}`. Dispatch to the same `GameState` methods `unit_action`/`set_city_production`/`purchase_item`/`set_research`/`set_civic` use.

- [ ] **Step 1: Failing tests** (Review Focus 4 included)

```python
def test_parse_valid_and_invalid_mixed():
    orders, errs = parse_orders([{"unit_id": 3, "action": "move", "x": 1, "y": 2}, {"action": "move"}, {"research": "TECH_MINING"}])
    assert [o.kind for o in orders] == ["unit", "research"] and errs == ["#2: unit order needs unit_id"]
async def test_run_continues_past_failures(fake_gs):   # fake_gs.move_unit raises LuaError once
    results = await run_orders(fake_gs, orders, on_each=noop); assert results[0][1].startswith("FAIL") and results[1][1].startswith("OK")
def test_tool_result_lists_each_index(): ...  # "#1 OK ...", "#2 FAIL ...", "#3 skipped: <parse error>"
```

- [ ] **Step 2: Run to verify failure**  Run: `bash scripts/test.sh tests/test_orders.py` Expected: ImportError.

- [ ] **Step 3: Implement**

Tool `batch_orders(ctx, orders: list[dict], why: str = "") -> str`: parse, run, per order push the camera for move/attack targets and `broadcast.append("action", ...)` using `Broadcast.action_text` with the order's own `why` (falling back to the batch `why`), return one line per input index.

- [ ] **Step 4: Run all tests, commit**

```bash
bash scripts/test.sh && git add -A && git commit -m "feat: batch_orders"
```

---

### Task 8: Live viewer page

**Files:**
- Modify: `src/civ_mcp/web_api.py` (`create_app(gs, broadcast)`, routes), `src/civ_mcp/server.py` (bind `127.0.0.1`, port from `CIV_MCP_WEB_PORT`, default 8000)
- Create: `src/civ_mcp/static/viewer.html`
- Modify: `pyproject.toml` (include `src/civ_mcp/static/*` in the wheel/sdist if hatch excludes it)
- Test: `tests/test_viewer.py`

**Interfaces:**
- Consumes: `Broadcast.tail`, `Broadcast.last_board`, `Broadcast.game_dir`.
- Produces: `GET /` -> viewer.html; `GET /api/broadcast?after=N&limit=M` -> `{"game": "<dir name or null>", "rows": [...], "next": int}`; `GET /api/board` -> `{"text": str | null}`.

- [ ] **Step 1: Failing tests**

```python
def test_broadcast_endpoint_pages(tmp_path):
    b = Broadcast(tmp_path); b.bind_game("x", "rome"); [b.append("plan", f"p{i}", turn=1) for i in range(3)]
    c = TestClient(create_app(SimpleNamespace(), b)); r = c.get("/api/broadcast?after=1").json()
    assert [x["text"] for x in r["rows"]] == ["p1", "p2"] and r["next"] == 3 and r["game"] == "01-rome"
def test_root_serves_viewer(tmp_path):
    c = TestClient(create_app(SimpleNamespace(), Broadcast(tmp_path))); r = c.get("/"); assert r.status_code == 200 and "<title>" in r.text
def test_board_endpoint(tmp_path): ...
```

- [ ] **Step 2: Run to verify failure**  Run: `bash scripts/test.sh tests/test_viewer.py` Expected: TypeError (create_app signature) / 404.

- [ ] **Step 3: Implement**

`viewer.html`: no framework, one file, dark theme, two panes (log left, board right), polls `/api/broadcast` every 2 s with `after=next`, renders `## Turn N` separators when `turn` changes, a coloured badge per kind, auto-scrolls unless the reader has scrolled up, title shows the game dir name. `server.py`: `uvicorn.Config(app, host="127.0.0.1", port=int(os.environ.get("CIV_MCP_WEB_PORT", "8000")))`.

- [ ] **Step 4: Run all tests, commit**

```bash
bash scripts/test.sh && git add -A && git commit -m "feat: local log viewer page on the existing web app"
```

---

### Task 9: Campaign layer and playbook

**Files:**
- Create: `campaign/NOTEBOOK.md`, `PLAYBOOK.md`
- Modify: `src/civ_mcp/server.py` (`read_notebook`, `append_notebook`), `README.md` (fork banner at the top), `CLAUDE.md` (replace symlink with a real file), delete `GEMINI.md` symlink
- Test: `tests/test_notebook.py`

**Interfaces:**
- Produces: `read_notebook(ctx) -> str` (whole file); `append_notebook(ctx, section: str, text: str) -> str` where `section` is one of `Standing lessons`, `Game log`, `Current hypothesis`; appends a dated bullet under that heading (creates the heading if missing).

- [ ] **Step 1: Failing tests**

```python
def test_append_under_section(tmp_path):
    p = tmp_path / "NOTEBOOK.md"; p.write_text("# Notebook\n\n## Standing lessons\n\n## Game log\n")
    append_notebook_file(p, "Game log", "Game 1 lost to Scythia T41.")
    txt = p.read_text(); assert txt.index("## Game log") < txt.index("Game 1 lost") and "\u2014" not in txt
def test_unknown_section_rejected(tmp_path): with pytest.raises(ValueError): append_notebook_file(tmp_path/"n.md", "Misc", "x")
```

- [ ] **Step 2: Run to verify failure**  Run: `bash scripts/test.sh tests/test_notebook.py` Expected: ImportError.

- [ ] **Step 3: Implement**

- `append_notebook_file(path: Path, section: str, text: str) -> None` lives in `broadcast.py`; the tools wrap it with `campaign/NOTEBOOK.md`.
- `campaign/NOTEBOOK.md` seed: the three headings, with `Standing lessons` pre-filled from the spec's Deity notes (army parity, walls, let the AI attack into you, watch religion/culture/diplomatic victories every 10 turns) as bullets.
- `PLAYBOOK.md` sections, in this order: Rules of engagement (fairness, no reload, Deity only, April-facing commitments: camera, log quality, pivots); Session start and resume (read `campaign/NOTEBOOK.md`, find the current game dir, `get_situation`; after a crash `restart_and_load` with a reason); Turn loop (`get_situation` -> `narrate plan` -> `batch_orders` -> `end_turn(summary)` -> `narrate reaction` when something surprising happened); Call budget (targets: 3 to 6 calls per turn early, 8 to 15 mid-game); Mechanics kept from upstream `AGENTS.md` (the ten rules the explorer listed, corrected: autosaves keep 8, end_turn takes one `summary`); Deity facts (extract from the game data: `grep -ril DIFFICULTY_DEITY` under `Base/Assets` and `DLC/*/Data`; list the AI bonuses found with file references; if none are found say so); Victory watch (what to check every 10 turns); Loss protocol (`narrate postmortem`, `append_notebook("Game log", ...)`, new game). No strategy essays: the strategy sections are Claude's to write in the notebook at play time.
- `CLAUDE.md`: a real file: "Read PLAYBOOK.md before touching the game" plus `@PLAYBOOK.md`. Remove `GEMINI.md`. `README.md`: a short fork banner linking the spec, `PLAYBOOK.md`, `docs/FAIRNESS.md`, `docs/RUNTIME.md`.

- [ ] **Step 4: Run all tests, commit**

```bash
bash scripts/test.sh && git add -A && git commit -m "feat: campaign notebook tools and playbook"
```

---

### Task 10: Suggested-leader helper and settings card (no game)

**Files:**
- Create: `src/civ_mcp/newgame.py`
- Modify: `src/civ_mcp/server.py` (`new_game` tool, settings-card half)
- Test: `tests/test_newgame.py`

**Interfaces:**
- Produces:
  ```python
  GAME_SETTINGS = {"ruleset": "RULESET_EXPANSION_2", "difficulty": "DIFFICULTY_DEITY", "map_script": "Fractal.lua", "map_size": "MAPSIZE_STANDARD", "speed": "GAMESPEED_STANDARD"}
  def settings_card(leader: str) -> str                       # human-readable list April can click through
  def build_frontend_setup_lua(leader: str) -> str            # Task 11 fills the body; here it returns the skeleton with the five settings and the leader
  def valid_leader(leader: str) -> bool                       # LEADER_* names from a bundled list of GS leaders (docs/leaders.txt generated from the game's XML)
  ```

- [ ] **Step 1: Failing tests**

```python
def test_settings_card_lists_all_five_and_leader(): c = settings_card("LEADER_GILGAMESH"); assert "Deity" in c and "Fractal" in c and "Standard" in c and "Gilgamesh" in c
def test_setup_lua_contains_settings(): lua = build_frontend_setup_lua("LEADER_GILGAMESH"); assert "RULESET_EXPANSION_2" in lua and "Fractal.lua" in lua and "LEADER_GILGAMESH" in lua and "Network.HostGame" in lua
def test_valid_leader(): assert valid_leader("LEADER_GILGAMESH") and not valid_leader("LEADER_BOGUS")
```

- [ ] **Step 2: Run to verify failure**  Run: `bash scripts/test.sh tests/test_newgame.py` Expected: ImportError.

- [ ] **Step 3: Implement**

Generate `docs/leaders.txt` once from `Base/Assets/Gameplay/Data/Leaders.xml` plus every `DLC/*/Data/*Leaders*.xml` (`LeaderType` values starting `LEADER_`, excluding `LEADER_DEFAULT`, `LEADER_BARBARIAN`, `LEADER_FREE_CITIES`, `LEADER_MINOR_CIV_*`). `build_frontend_setup_lua` mirrors `MainMenu.lua`'s Play Now path: `GameConfiguration.SetToDefaults()`, `GameConfiguration.SetRuleSet(...)`, the difficulty/speed/size setters keyed as `Base/Assets/Configuration/Data/*.xml` define them (read the `Parameters` rows for `GameDifficulty`, `GameSpeed`, `MapSize`, `Map`: `ConfigurationGroup` + `ConfigurationId`), `MapConfiguration.SetScript("Fractal.lua")`, `PlayerConfigurations[0]:SetLeaderTypeName(leader)`, then `BuildHeadlessGameSetup(); RebuildPlayerParameters(true); GameSetup_RefreshParameters(); ReleasePlayerParameters(); Network.HostGame(ServerType.SERVER_TYPE_NONE)`. Tool `new_game(ctx, leader: str, why: str)`: validates the leader, logs a `game` entry with the settings, and (until Task 11 wires the FrontEnd state) returns the settings card with "create this game from the main menu, then call get_game_overview".

- [ ] **Step 4: Run all tests, commit**

```bash
bash scripts/test.sh && git add -A && git commit -m "feat: new_game settings card and FrontEnd setup Lua skeleton"
```

---

### Task 11: FrontEnd game creation (LIVE, Opus)

**Files:**
- Modify: `src/civ_mcp/connection.py` (`frontend_index`, discover the state name from `docs/RUNTIME.md`), `src/civ_mcp/newgame.py`, `src/civ_mcp/server.py` (`new_game` runs the Lua when the FrontEnd state is present)
- Modify: `docs/RUNTIME.md` (what worked)

**Interfaces:**
- Consumes: Task 1's state names, Task 10's `build_frontend_setup_lua`.
- Produces: `GameConnection.execute_frontend(lua: str, timeout: float = 10.0) -> list[str]`; `new_game` returns `"Game started: <leader>, Deity, Fractal, Standard"` after `InGame` appears and `get_game_overview` confirms difficulty Deity, and binds the broadcast with the leader.

- [ ] **Step 1: Tell April, launch to the main menu** (the game will appear on her screen).

- [ ] **Step 2: Iterate the Lua live**

Use `execute_frontend` with the skeleton; read `GameSetupLogic.lua` and `AdvancedSetup.lua` in the game's UI folder when a call errors. Success = the map loads, `get_game_overview` reports Deity, and `MapConfiguration.GetScript()` (InGame) returns `Fractal.lua`. Time box: 2 hours of attempts. If it cannot be made to work, leave `new_game` returning the settings card, record why in `docs/RUNTIME.md`, and report.

- [ ] **Step 3: Verify the whole path twice** (two different leaders), quit each test game without saving, then close the game.

- [ ] **Step 4: Commit**

```bash
bash scripts/test.sh && git add -A && git commit -m "feat: new_game creates a Deity Fractal game from the main menu via FrontEnd Lua"
```

---

### Task 12: Live rehearsal (LIVE, Opus tester)

**Files:**
- Create: `docs/REHEARSAL-2026-10-25.md` (findings)

- [ ] **Step 1: Tell April; start the MCP server from WSL the way the play session will** (a Claude Code session in the repo dir, or `scripts/mcp_smoke.py` extended to drive tools; the tester may call tools through a throwaway Claude Code session with `--mcp-config .mcp.json`).

- [ ] **Step 2: Checklist, each item pass/fail with evidence:**
  1. `new_game("LEADER_GILGAMESH")` lands in a Deity Fractal game; broadcast bound; `campaign/games/NN-sumeria/` created.
  2. Viewer at `http://127.0.0.1:8000/` on April's second monitor updates within 3 s of a `narrate`.
  3. `get_situation` returns the four sections under 40 lines.
  4. `batch_orders` with three unit orders moves units; camera hops to each target.
  5. `get_map_area` on a fogged tile shows `(fog)` and no live owner.
  6. `end_turn("...")` advances, autosaves `0_MCP_0002`, appends `turn`, `event` entries and the board.
  7. Kill the game process; `restart_and_load(reason="crash test")` recovers to the autosave; the `load` entry appears in the log.
  8. `run_lua` is absent from the tool list.
  9. Five turns played end to end; count tool calls per turn (write the numbers down).

- [ ] **Step 3: File findings** in `docs/REHEARSAL-2026-10-25.md`; delete the rehearsal game dir from `campaign/games/` and its index entry; close the game. Each failing item goes back to an implementer as a fix task before Task 13.

---

### Task 13: Whole-branch review, merge, push

- [ ] **Step 1:** Opus reviewer reads the spec, `docs/FAIRNESS.md`, and the full `main..deity-v1` diff with the fairness rules as the primary lens (any live data on fogged tiles, any GameCore mutation, any rival field without a screen).
- [ ] **Step 2:** Fix findings; `bash scripts/test.sh` green.
- [ ] **Step 3:**
```bash
git checkout main && git merge --ff-only deity-v1 && git push origin main deity-v1
```
- [ ] **Step 4:** Update April's memory notes (project file for this campaign) and hand over: how to start a play session (`cd` to the repo, `claude`, the viewer URL).

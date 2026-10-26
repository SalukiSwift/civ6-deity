# Live rehearsal, 2026-10-25 (Task 12)

A throwaway Deity game (Sumeria, Gilgamesh) played through the MCP tools on April's PC to exercise the whole loop before Task 13. Branch `deity-v1` at `f4755d1`. Tester only: no source changes.

How it was driven: `scripts/mcp_drive.py` kept ONE server process (the `.mcp.json` command, Windows Python through WSL interop) alive for the whole session in `--follow` mode; each tool call was appended to `scripts/rehearsal/01-session.jsonl` with `mcp_drive.py send` and its result read back, with the latency of every step logged. The viewer endpoints were read from the Windows side with PowerShell, and the screen was checked with Windows-side screenshots (not kept).

Timeline: game launched 22:58, `new_game` 23:01, turns 1 to 7 played 23:03 to 23:12, crash test 23:12 to 23:17 (back on turn 7), turn 7 played again and turn 8 started, game closed 23:20. 65 tool calls in the live session plus 5 offline.

## Results

| # | Item | Result | Evidence | Latency |
|---|---|---|---|---|
| 1 | `new_game(leader="LEADER_GILGAMESH", why="rehearsal")` | PASS | `Game started: LEADER_GILGAMESH, Deity, Fractal, Standard, Gathering Storm`. Server log: `click=ocr timings={'hosted': 2.4, 'click': 58.7, 'ingame': 62.0}`, verified `RULESET_EXPANSION_2`, `favor true`, `DIFFICULTY_DEITY` (game and player), `GAMESPEED_STANDARD`, `Fractal.lua`, `MAPSIZE_STANDARD`, grid `84x54`, turn 1. `campaign/games/01-sumeria/` created with `meta.json` (`"leader": "LEADER_GILGAMESH"`), `log.md`, `log.jsonl`; `index.json` `{"8bda726297": "01-sumeria"}`; `/api/broadcast` then reported `"game":"01-sumeria"`. | 64.8 s (BEGIN GAME click at 58.7 s, later than the ~40 s in RUNTIME.md) |
| 2 | Viewer: `/api/board`, `/api/broadcast`, narrate visible within 3 s | PASS (endpoints) | Before the game: `{"text":null}`, `{"game":null,"rows":[],"next":0,"count":0}`, `/` 200. After `narrate(kind="plan", ...)`: a 200 ms poll loop found the row on its first poll, 0.4 s wall clock from the narrate return (`"kind":"plan","text":"Rehearsal turn 1: settle the capital in place, ...","i":2`). The page polls every 2 s when idle. The page itself on April's second monitor was not seen: Windows reported only one display (`\\.\DISPLAY1 2560x1440`). | narrate 0.29 to 0.49 s |
| 3 | `get_situation` | PASS | Turn 1: 10 lines, sections `EMPIRE`, `ATTENTION`, `THREATS`, `RIVALS`; `idle units: 65536, 131073` and `research/civic not set`; no `(unavailable: ...)` marker all session. | 4.2 to 4.6 s |
| 4 | `get_units`, `get_cities`, `get_map_area`, fog, unrevealed | PASS | Units and city sane (`Uruk (pop 1) at (59,35)`, `[id:65536]`). Radius 4 around Uruk on turn 2: revealed-not-visible tiles read `(60,32): PLAINS JUNGLE (fog; last seen T1) [mv:2]`, `(56,35): COAST [FISH] (fog; last seen T1)`, with no yields and no owner, while visible tiles carry yields and `(owned by Sumeria)`. Unrevealed areas: `get_map_area(45,25,2)` and `(40,45,1)` returned `No tiles.`; radius 3 on turn 1 returned 32 of 37 tiles (unrevealed ones left out). | 0.30 to 0.53 s |
| 5 | `batch_orders` with three orders, camera hops | PASS | `#1 OK FOUNDED\|59,35`, `#2 OK MOVING_TO\|62,36\|from:59,36\|now_at:60,36\|...`, `#3 OK RESEARCHING\|TECH_POTTERY`; each order logged as its own `action` entry with its own `why`. Camera, by screenshot: parked over unrevealed ground by `get_map_area(40,45)`, then a batch move to (60,30) brought it back centred on the target, south of Uruk. Only move and attack orders hop the camera (by design); `found_city` and research do not. | 36.7 s (see D6); later batches 0.6 to 7.1 s |
| 6 | `end_turn(summary=...)` | PASS | `Turn 1 -> 2 \| Score: 8` followed by `== Situation ==` and the board. `0_MCP_0002.Civ6Save` written at 23:06:10, in `Saves\Single` (not `Saves\Single\auto`, which holds the game's own `AutoSave_0002`); one `0_MCP_NNNN` per turn after that up to `0_MCP_0008`. The broadcast gained the `turn` entry (the summary) and an `event` entry, and `/api/board` returned the new board. | 19.2 to 22.1 s (7 calls) |
| 7a | Five turns through the loop, production and research | PASS | Turns 1 to 7 played (plus turn 7 again after the reload). Production `UNIT_SCOUT` (`OK PRODUCING\|UNIT_SCOUT\|5 turns`; built on turn 5) and `BUILDING_MONUMENT`; research `TECH_POTTERY`. Calls per turn below. | loop: narrate 0.3 s, batch about 2.5 s, end_turn about 20 s |
| 7b | Fortify, then `is_busy` keeps it off the idle list | FAIL | See D2. | |
| 7c | `promote_unit` | NOT REACHED | No unit had XP. The refusal path ran cleanly: `Error: CANNOT_PROMOTE\|The game does not offer this promotion now (...)`; `get_unit_promotions`: `No promotions available for UNIT_WARRIOR (id:1).` | 0.9 s |
| 8a | Units query `is_busy` | FAIL | See D2. | |
| 8b | Settle advisor (`Terrain_YieldChanges` / `Feature_YieldChanges`) | PASS | `get_settle_advisor(65536)`: `Top 5 settle locations:`, #1 (59,35) score 164; `get_global_settle_advisor`: `Top 10 settle locations:`. No Lua error. | 0.96 s / 0.30 s |
| 8c | `get_victory_progress`, `get_diplomacy` | PASS | Both ran without error. Victory: `RIVALS (World Rankings)` shows only our own row (`Sumeria: 1 cities \| Techs 0 \| Tourism 0 \| Mil 21`); no `DEMOGRAPHICS` section. Diplomacy: `7 civilizations:`, each `Unmet Civilization (Unknown Leader) ... not met`. | 0.59 s / 0.30 s |
| 8d | Cities query in InGame with `PlayersVisibility[...]:IsVisible` | PASS | `get_cities` ran 7 times in InGame without a Lua error, before and after the city was founded, so `local pVis = PlayersVisibility[me]` resolves there. The `pVis:IsVisible(...)` call itself sits in the ranged-target scan, which runs only for a city with walls; Uruk had none, so that line was not executed. Still to check once a city has walls. | 0.29 to 0.59 s |
| 8e | `get_trade_options`, `propose_trade` | NOT REACHED | No civ met (only the city-state Mitla). An unmet-civ probe showed the HasMet gate holds but its error is swallowed: D7. | 0.35 s |
| 9 | Crash recovery: `taskkill`, then `restart_and_load(reason="rehearsal crash test")` | FAIL | `Kill: Game is not running. \| Launch: Game launched. Process started after 2s, FireTuner port is open. \| Load: FAILED: Could not find 'Single Player' on main menu. Is the game at the main menu?` See D1. Recovered by hand with `load_save_from_menu(reason=...)` once the menu was up: `Save loading (51s). Steps: Clicked Single Player, Clicked Load Game, Clicked Autosaves filter, Selected save AutoSave_0007, Clicked Load Game button, Clicked CONTINUE, WARNING: FireTuner port not open after load.` Then the `load` entry was in the log and on `/api/broadcast` (`"kind":"load","text":"loaded latest autosave: rehearsal crash test: ..."`), and `get_game_overview` read `Turn 7 \| Sumeria (Gilgamesh) \| Score: 8 \| Deity`. Turn 7 then ended normally (`Turn 7 -> 8`), with no turn-regression warning. | restart_and_load 159.3 s (failed); load_save_from_menu 51.2 s |
| 10 | `run_lua` absent, `read_notebook`, empty load reason | PASS | `tools/list`: 81 tools, no `run_lua`. `read_notebook` returned `# Campaign notebook` with its three sections. `load_game_save(reason="")`, `load_game_save(reason="   ")` and `restart_and_load(reason="")` each returned `Error: a non-empty \`reason\` is required for every load. ...`. While the game was down, `get_game_overview` answered `Cannot connect to Civ 6 at 127.0.0.1:4318. Is the game running with EnableTuner=1?` in 2 s. | under 0.01 s |
| 11 | Fairness spot-check | PASS | `get_diplomacy` names no civ (7 unmet rows); `get_game_overview` names only Sumeria; `get_victory_progress` has no rival rows and no rival yields. Minor: the overview's `Explored: 1% of land (22/1627 tiles)` gives the total land-tile count of the map, which the game does not show a human (D11). | |

Totals over the 17 rows: 12 PASS, 3 FAIL (7b, 8a, 9; 7b and 8a are the same defect, D2), 2 NOT REACHED (7c, 8e).

## Defects

Ordered by severity. Each one goes back to an implementer before Task 13.

### D1 (high): `restart_and_load` gives up during the intro movie

The game plays its intro movies on every launch. On this PC the main menu appeared about 3 min 45 s after launch (launched 23:12:38; intro still playing at 23:15:25; menu up by 23:16:25; on the first launch of the session the intro was playing 23 s in, the title card was still up at 3 min 19 s and the menu was up at about 3 min 35 s). `restart_and_load` waits for the tuner port (open about 20 s after launch), then the OCR step waits only 90 s for "Single Player":

```
[1/7] Waiting for main menu (Single Player)...
WARNING _wait_for_text: 'Single Player' not found after 91s. Saw 1 items: '66 FPS'
```

So the load step fails every time on this machine, and the tool returns `... | Load: FAILED: Could not find 'Single Player' on main menu. Is the game at the main menu?` after 159 s with the game left at the main menu. Recovery then needs a second call (`load_save_from_menu`, which worked) that the playbook does not mention. No `load` entry is written for the failed attempt (correct by FAIRNESS.md).

Candidate fixes: before the OCR navigation, wait for the `FrontEnd` and `MainMenu` Lua states through the tuner (RUNTIME.md already uses them as the "at the main menu" signal) with a 5-minute budget, or raise the "Single Player" wait to at least 300 s; skipping the intro (a click or key once the window is in front) would also save about 3 minutes per recovery. If an AppOptions setting can turn the intro off, that is April's call (her game files are not edited by agents).

Also seen in this step: `load_save_from_menu` reported `WARNING: FireTuner port not open after load`, yet `get_game_overview` worked 5 s later. The warning is spurious or its wait is too short.

### D2 (high): fortified units are never busy, so the idle list never shrinks

After `batch_orders` fortify on turn 3 (`#1 OK FORTIFIED`), the game shows the unit fortified (the turn button reads NEXT TURN, no unit needs orders), but:

```
get_units:      Warrior (UNIT_WARRIOR) at (61,36) ... CS:20 moves 2/2 [id:131073, idx:1]
get_situation:  ATTENTION
                  idle units: 131073
```

and the same again on turn 4 after `end_turn`. `get_units` has no fortified marker either. In `lua/units.py` (units query) the busy flag is:

```lua
pcall(function()
    local act = u:GetActivityType()
    if act ~= nil and act ~= ActivityTypes.ACTIVITY_AWAKE then busy = "1" end
    if u.IsAutomated and u:IsAutomated() then busy = "1" end
end)
```

Likely cause (not verified, since `run_lua` is off in a fair run): this query runs in InGame, where the activity is read with `UnitManager.GetActivityType(pUnit)` (as the game's own unit panel and flag scripts do), not a `GetActivityType` method on the unit, so the call errors, the `pcall` swallows it and `busy` stays `"0"`. An implementer should check it with `CIV_MCP_DEV_LUA=1` against a fortified unit. Impact: every fortified or garrisoned unit shows as idle on every board, so the ATTENTION section fills with noise as the army grows and the agent spends calls on units that need nothing.

### D3 (medium): the board does not flag a city with no production

On turn 6 `end_turn` reported `>> Uruk finished building UNIT_SCOUT. Now: nothing.` and `* Choose Production`, and `get_cities` showed `Building: nothing`, but the board's ATTENTION had only `idle units: 196608, 131073`. The cities query returns `"nothing"` for an empty queue (`lua/cities.py`, `local producing = "nothing"`), while `situation._unset` only treats `""` and `"NONE"` as unset, so the `no production: <city>` line can never appear. `narrate.py` already handles both (`not in ("NONE", "nothing")`).

### D4 (medium): north and south are swapped in the playbook and in `get_strategic_map`

PLAYBOOK.md rule 1 ("Higher Y is south") and `docs/upstream-AGENTS.md` ("Y increases -> south (down)") disagree with the screen. On screen, higher Y is north:

- Map data: the coast tiles (59,37), (59,38), (58,36) and the reef at (60,38) are on the water that lies above Uruk (59,35) on screen; the jungle, sheep hills and mountain at (60,32), (59,33), (61,32) lie below it.
- Camera: `get_map_area(45,25)` (lower Y) moved the view so the island sat at the top right, that is the camera went down and left of it; `get_map_area(40,45)` (higher Y) left the island at the bottom right, so the camera went up and left; the move to (60,30) centred the view below Uruk.

`get_strategic_map` uses the same flipped convention (`lua/map.py`: `-- Directions: N(0,-1), ...`), so its `Uruk (59,35): N:7 NE:4 SE:5 S:4 SW:3 NW:3 <- EXPLORE NE/SE/S/SW/NW!` has N and S (and each diagonal's north or south half) reversed. Coordinates are unaffected and the agent's reasoning stays self-consistent, but every compass word in the log and the viewer will contradict what April sees on screen. Fix the playbook rule and the strategic map's labels together, and check any other narration that names directions.

### D5 (medium): multi-turn moves do not continue the next turn

Four times a unit stopped mid-path with `STOPPED_MID_PATH (moves exhausted)` and was found at the start of the next turn on the same tile with full moves and no path: warrior turn 1 to 2 (`now_at:60,36`, target (62,36)), turn 4 to 5 (`now_at:62,36`, target (62,37)); scout turn 6 to 7 (`now_at:60,33`, target (60,32)) and turn 7 to 8 (`now_at:60,32`, target (60,31)). In the normal game a unit given a multi-turn move keeps going at the start of the next turn. The move Lua is a plain `UnitManager.RequestOperation(unit, UnitOperationTypes.MOVE_TO, params)`; the cause was not found from the logs (INFO level shows nothing between the turn's orders and the next turn). A candidate to check: whether anything in `end_turn` (for example the stock Skip Turn it issues when the game reports `ENDTURN_BLOCKING_UNITS`) or the turn hand-off clears the queued path. Impact: one extra order per moving unit per turn, and those units sit on the idle list.

### D6 (medium): `batch_orders` with `found_city` took 36.7 s

```
23:04:43 INFO ExclusivePopup visible - running Phase 2 state scan
23:04:44 INFO ExclusivePopup visible - running Phase 2 state scan
23:05:16 INFO [T?] batch_orders(why=rehearsal turn 1 count=3) OK 36719ms
```

Founding the first city raises an exclusive popup and the upstream Phase 2 scan walks every popup Lua state (about 30 s). Other batches took 0.6 to 7.1 s. Expect the same cost at each wonder, era change or other exclusive popup. Also note the `[T?]` turn tag in that log line.

### D7 (low): `get_trade_options` on an unmet civ prints an empty deal screen

`get_trade_options(other_player_id=1)` with nobody met:

```
Trade options with  (player 0):

Economy:
  Our gold: 0 (+0/turn) | Favor: 0
  Their gold (deal screen): 0 (+0/turn) | Favor: 0

Agreements:
  Open borders: not active (available)
  Alliance: not eligible (requires declared friendship + Diplomatic Service civic)
```

We had 25 gold at the time. The Lua bails correctly with `ERR:NOT_MET|Have not met player 1` (so no rival data leaks), but `parse_deal_options_response` ignores `ERR:` lines and the narration prints a default, wrong deal screen. It should return the error.

### D8 (low): quiet-turn `event` entries copy the whole board into the log

When a turn report has no `== Events ==` block, `Broadcast.event_text` falls back to the first 15 lines, which now include the appended board:

```
- 23:06 [event] Turn 1 -> 2 | Score: 8 | == Situation == | EMPIRE | T2 Ancient Era | Sumeria (Gilgamesh) | score 8 | ... | RIVALS | none
```

This repeats every quiet turn in `log.md` and the viewer. The board is already in `board.txt` and `/api/board`; the fallback should stop at `== Situation ==`.

### D9 (low): "Needs builder" list is split on the coordinate commas

`get_cities`: `Needs builder: WHEAT, 35, WINE, 36 (unimproved)`. The Lua builds entries like `WHEAT@60,35` and joins them with `,` (`lua/cities.py` lines 122 and 164); the parser splits on `,` (line 724) and the narration keeps the part before `@`. Use another separator.

### D10 (low): the board's idle list has ids only

`idle units: 196608, 131073` gives no unit type or position, so on most turns the agent needed a `get_units` call just to know where its units were. Something like `Scout 196608 (60,33), Warrior 131073 (62,37)` would save a call per turn.

### D11 (low): smaller items

- A move into water returns `OK MOVING_TO|63,38|from:62,36|now_at:62,36|BLOCKED (water tile - land units need Shipbuilding tech to embark)`: it starts with `OK`, counts as OK in the batch and hops the camera.
- Action lines in `log.md` carry raw result codes (`=> OK MOVING_TO|62,36|from:59,36|now_at:60,36|(moved dx:+1 dy:+0)|STOPPED_MID_PATH (moves exhausted)`), which a Civ player watching the log cannot read easily (PLAYBOOK asks for plain Civ terms). The found-city line also names the unit by index (`unit_action found_city #0`).
- The `load` entry says `loaded latest autosave` rather than the save that was loaded (`AutoSave_0007`).
- `get_city_states` lists `Free Cities (Unknown) ... 0 envoys [player 62]` as a known city-state.
- `get_game_overview` prints `Explored: 1% of land (22/1627 tiles)`: the total land-tile count of the map is not something the game shows a human (small fairness leak; a percentage of the revealed tiles only, or no denominator, would avoid it).
- `0_MCP_NNNN` saves are written to `Saves\Single`, next to April's own named saves, not to `Saves\Single\auto` as the checklist assumed. Not wrong, but the docs should say where they go.
- `sleep` on a Warrior returns `FAIL Error: CANNOT_SLEEP|Unit cannot sleep`. That is the game's rule (melee units fortify instead), so not a defect, but the batch docstring lists `sleep` among the generic unit actions.

## Tool calls per turn

Counted from `scripts/rehearsal/01-session.jsonl`. "Used" includes tester-only checks; "needed" is what the turn loop itself took.

| Turn | Used | Needed | What the needed calls were |
|---|---|---|---|
| 1 | 15 | 8 | get_situation, get_units (positions), get_settle_advisor, narrate, batch_orders (found, move, research), get_cities (the new city's id), batch_orders (production), end_turn |
| 2 | 5 | 4 | get_units (where did the warrior stop), narrate, batch_orders, end_turn |
| 3 | 8 | 3 | narrate, batch_orders (fortify), end_turn |
| 4 | 7 | 3 | narrate, batch_orders, end_turn |
| 5 | 5 | 4 | get_units, narrate, batch_orders, end_turn |
| 6 | 5 | 5 | get_units (new scout id), get_cities (empty queue, see D3), narrate, batch_orders, end_turn |
| 7 (after reload) | 6 | 4 | get_game_overview (check the reload), narrate, batch_orders, end_turn |

The early-game budget of 3 to 6 calls holds from turn 2 on. Turn 1 needs about 8 because the city id only exists after `found_city`, so production cannot go in the same batch. D2, D3, D5 and D10 each add calls; fixing them would bring a typical early turn to 3.

Latency per tool over the session: `end_turn` 19.2 to 22.1 s (median about 20.5 s on turns 1 to 7), `get_situation` 4.2 to 4.6 s, `batch_orders` 0.6 to 7.1 s (36.7 s with the popup scan), `get_units` about 1.2 s, `get_game_overview` 1.0 to 1.7 s, `get_settle_advisor` 0.96 s, other reads 0.3 to 0.6 s, `narrate` 0.3 s, `launch_game` 22.8 s, `new_game` 64.8 s, `load_save_from_menu` 51.2 s, `restart_and_load` 159.3 s (failed). A quiet turn of the loop took about 25 s of tool time.

## Fix wave (2026-10-25, after the rehearsal)

Fixed on `deity-v1` after `5034b59`. Live checks ran in one throwaway Deity game (Sumeria, Gilgamesh, launched 23:37, `new_game` 52.6 s, turns 1 to 7, closed 23:56) driven with `scripts/mcp_drive.py`: a server with `CIV_MCP_DEV_LUA=1` for the diagnosis (D2, D4, D5), then a default server (81 tools, no `run_lua`) for the tool checks and D1. Unit tests: 305 passing (`tests/test_rehearsal_fixes.py` is new).

### D1: fixed, verified live

`load_save_from_menu` (and so `restart_and_load`, the connection-loss recovery, the hang recovery and `load_game_save`'s menu fallback) now waits for the main menu through the tuner before any OCR: a fresh state list with `FrontEnd` and `MainMenu`, polled every 5 s for up to 6 minutes (`game_launcher.wait_for_main_menu`). The server pauses its popup watcher, camera and watchdog around these calls, since the game serves one tuner client at a time. The "Single Player" OCR wait is now 120 s, a backstop. The port check after CONTINUE now polls for up to 60 s instead of reading once 3 s after the click.

Live: game killed on turn 6 with `taskkill`, then `restart_and_load(reason="fix verification")`:

```
Kill: Game is not running. | Launch: Game launched. Process started after 2s, FireTuner port is open. | Load: Save loading (49s). Steps: Main menu up after 188s (tuner), Clicked Single Player, Clicked Load Game, Clicked Autosaves filter, Selected save AutoSave_0006, Clicked Load Game button, Clicked CONTINUE, FireTuner port open (0s after CONTINUE). Wait ~10s then use get_game_overview to verify.
```

263.5 s in all (call 23:51:16; process 23:51:19; port 23:51:35; menu states 23:54:43, about 3 min 26 s after launch; "Single Player" found by OCR 1.0 s later; CONTINUE clicked 23:55:29; returned 23:55:40). The log gained `- 23:55 [load] loaded latest autosave: fix verification` (also on `/api/broadcast` as `"kind":"load"`, turn 6) and `get_game_overview` read `Turn 6 | Sumeria (Gilgamesh) | Score: 11 | Deity`.

### D2: fixed, verified live

Cause confirmed with `run_lua` in InGame: `u:GetActivityType()` fails (`function expected instead of nil`) while `UnitManager.GetActivityType(u)` returns `1797587246` (`ActivityTypes.ACTIVITY_AWAKE`). The units query now reads the activity the way `WorldTracker.lua` and `UnitPanel.lua` do (sleep, hold, alert, heal; fortified = not awake and `GetFortifyTurns() > 0`), treats a unit with a queued destination (`UnitManager.GetQueuedDestination`) or automation as busy, and sends the activity as a new field. If the read fails the unit's activity is `?` and the query prints one `ACTERR|...` line, which the parser logs once as a warning. `get_units` shows `[fortified]`, `[sleeping]`, `[alert]`, `[healing]`, `[skipping turn]`, `[automated]` or `[moving to (x,y)]`.

Live, turn 1, after `batch_orders` fortify (`#1 OK FORTIFIED`):

```
get_units:      Settler (UNIT_SETTLER) at (28,29) ... moves 2/2 [id:65536, idx:0]
                Warrior (UNIT_WARRIOR) at (27,29) ... CS:20 moves 2/2 [fortified] [id:131073, idx:1]
get_situation:  ATTENTION
                  idle units: 65536
```

The settler, with no orders, stayed idle. Later, a scout given fortify showed `[alert]` (recon units cannot fortify).

### D3: fixed, verified live

`situation._unset` treats `nothing` (the cities query's empty queue) as unset. Live: after turn 1 -> 2 and again after turn 5 -> 6 (`>> Uruk finished building UNIT_SCOUT. Now: nothing.`) the board had `no production: Uruk`.

### D4: fixed, verified live

Higher Y is north. Evidence in game: `Map.GetAdjacentPlot(28, 29, DirectionTypes.DIRECTION_NORTHEAST)` is (29,30) and `DIRECTION_SOUTHWEST` is (28,28), so the engine's own compass puts +Y to the north. On screen (Windows-side screenshot after a move from (27,29) to (28,30) on the way to (29,31)): the warrior stood above Uruk (28,29), and the desert hills at (28,31) and (29,31) formed the top edge of the revealed land, above the city. Changes: PLAYBOOK rule 1 now reads "Higher Y is north (up the screen), X increases east"; `get_strategic_map` takes its rays and labels from one table, `lua/map.STRATEGIC_DIRECTIONS` (N is (0,+1), NE (+1,+1), SE (+1,-1), S (0,-1), SW (-1,-1), NW (-1,+1)); the move result's `dy` comment says north. No other code turns a dy into a compass word (the lost-contact fog directions use the engine's `DirectionTypes`, which are right). `docs/upstream-AGENTS.md` and `evals/` still say south and were left alone.

### D5: not a path loss; the queued path runs when the turn ends

Live with `run_lua` (`UnitManager.GetQueuedDestination`, `GetMovesRemaining`, `GetActivityType`) around a move of the warrior from (27,29) to (29,31):

| Moment | Warrior | Moves | Activity | Queued |
|---|---|---|---|---|
| T1 after the order (`STOPPED_MID_PATH`) | (28,30) | 1 | OPERATION | (29,31) |
| T2 start, after `end_turn` (and 10 s later) | (28,30) | 2 | OPERATION | (29,31) |
| T3 start, after `end_turn` | (29,30) | 2 | OPERATION | (29,31) |
| T4 start, after `end_turn` | (29,31), arrived | 2 | AWAKE | none |

So the game keeps the path and moves the unit on along it when the turn ENDS, using that turn's moves; at the start of the next turn the unit is still where it stopped, with full moves and its path queued. The path survived two `end_turn`s, the post-move visibility query, and also a save and reload (after `restart_and_load` the warrior still showed `[moving to (26,30)]` and moved on at the end of turn 6). No blocker was raised for a moving unit (`ENDTURN_BLOCKING_*` count 0, `UI.CanEndTurn()` true, including with 0 moves left). In the rehearsal the units looked stuck because of D2 (they sat on the idle list) and the agent re-ordered them each turn.

One real risk was found and closed: the stock `UNITOPERATION_SKIP_TURN` cancels a queued path (checked: queued (26,30) became none, activity HOLD). `skip_remaining_units`, its fortify/heal step and the `end_turn` units-blocker auto-skip now leave units with a queued destination alone. Live: `skip_remaining_units` with the warrior mid-path returned `SKIPPED|0 units` and the warrior kept `[moving to (26,30)]`. The move result now reads `STOPPED_MID_PATH (moves exhausted; the rest of the path runs when the turn ends)`, and PLAYBOOK mechanics rule 3 says so.

### D6: unchanged

The 30 s Phase 2 scan is upstream's: it visits every Lua state whose name contains Popup, Wonder, Moment, Era or Disaster. Narrowing it to the states the pre-check saw visible looks possible but needs a live exclusive popup to verify, so it was left as is.

### D7: fixed, verified live

`parse_deal_options_response` raises on an `ERR:` line, so the tool returns the game's reason. Live, nobody met: `get_trade_options(other_player_id=1)` returned `Error: NOT_MET|Have not met player 1`.

### D8: fixed, verified live

`Broadcast.event_text` logs the `== Events ==` and `== Action Required ==` blocks only, never anything from `== Situation ==` on, and `quiet turn` when neither block is there. Live log lines: `- 23:46 [event] quiet turn`, and on turn 6 `- 23:50 [event] >> Uruk finished building UNIT_SCOUT. Now: nothing. | -- New unit: Scout (UNIT_SCOUT) at (28,29). | -- Uruk grew to population 2. | * Choose Production  -> Use: set_city_production(...)`.

### D11 (overview land total): fixed, verified live

`get_game_overview` prints the share and our own count only: `Explored: 1% of land (28 land tiles revealed)`. FAIRNESS.md's `get_game_overview` row says so.

### Round 2 (unit tests only, no game)

- D9: the cities query joins the "Needs builder" and pillaged-tile entries with `;` and the parser matches whole `NAME@x,y` entries (old `,` lines parse too); `get_cities` prints `Needs builder: WHEAT at (60,35), WINE at (61,36) (unimproved)`.
- D10: the board names units by type, id and tile: `idle units: Warrior #131073 (62,37), Scout #196608 (60,33)`, the same for `promotions available:`, and units on a queued move get their own line, `moving: Warrior #131073 (28,30) to (29,31)`. Still 12 per line and 40 lines in all.
- D11a: a move the game accepted but the unit did not make returns `BLOCKED: <reason> (unit still at (x,y), target (x,y))`; `batch_orders` reports it as `FAIL BLOCKED: ...`, the camera does not hop (batch or `unit_action`) and the log says `blocked: water tile`.
- D11b: `log.md` action lines are plain Civ terms (`Warrior #131073 move -> (62,36): scout => moved to (60,36), still 2 tiles from (62,36)`, `Settler #65536 found city: ... => founded Uruk at (59,35)`, `city #65536 production: ... => now building Monument (5 turns)`, `research: ... => researching Pottery`, `attacked (12,8): enemy at 40 HP`, `refused: Unit cannot sleep`); units are named from the last unit list; `found_city` now returns the city name (`FOUNDED|59,35|Uruk`, read in the same verification query; not yet checked live). `log.jsonl` keeps the raw result in `meta.result`.
- D11c: the `load` entry names the save the menu navigation opened (`loaded AutoSave_0007: <reason>`).
- D11d: `get_city_states` skips the Free Cities player (`IsFreeCities`, or civ type `CIVILIZATION_FREE_CITIES`).
- D11e: RUNTIME.md and PLAYBOOK.md say `0_MCP_NNNN` saves go to `Saves\Single`; the `batch_orders` docstring says `sleep` is refused for melee units.
- Re-review: the popup watcher, camera and watchdog `start()` is a no-op while its task runs, `stop()` clears it, and `_background_paused` counts nesting so only the outermost block restarts them (a `load_game_save` whose `_logged` call hits the connection-loss recovery nests two blocks). When the units query cannot read activities, `get_units` marks each unit `[activity unknown]` and the board adds `(unit activity unavailable)`.

## Clean-up done

- Game closed with `taskkill` (twice: the crash test and the end). The server was shut down by closing stdin: no `python.exe` left and nothing listening on :8000.
- Deleted `campaign/games/01-sumeria/` and its index entry. It was the only entry, so `index.json` and the then-empty `campaign/games/` went too; `campaign/lobby.md` and `campaign/lobby.jsonl` deleted. `campaign/NOTEBOOK.md` is unchanged.
- Deleted this run's upstream diary files in `%USERPROFILE%\.civ6-mcp` (the four `*_8bda726297_*` files) and the tester's helper scripts in `%TEMP%`. Earlier tasks' files there were left alone.
- Autosaves left alone: `0_MCP_0002` to `0_MCP_0008` in `Saves\Single`, `AutoSave_0001` to `AutoSave_0008` in `Saves\Single\auto`.

## Files

- `scripts/mcp_drive.py`: one-server MCP driver (`run` a JSONL script, `--follow` to keep the server up, `send` to add a step and wait for its result; per-step latency in `OUT.timings.jsonl`).
- `scripts/rehearsal/00-offline.jsonl`: checks that need no game (tool list, notebook, empty load reasons).
- `scripts/rehearsal/01-session.jsonl`: the live session exactly as sent.

## Final review fixes (2026-10-26, Task 13)

The final whole-branch review found one Critical and five Important issues plus minors. All were fixed on `deity-v1` after `a153984` with unit tests only (no game launched): 402 tests pass (`tests/test_final_review_fixes.py` is new, 73 tests). Every edited Lua builder was also compiled with a stock Lua 5.1 outside the repo, and the threat scan was run against stub game objects (a stealthed unit on a visible tile stays out).

What changed:

- Unit-level visibility (Critical). Every loop over foreign units now also asks the game whether the unit itself is visible (`PlayersVisibility[me]:IsUnitVisible(u)`, as `WorldInput.lua:1041` and `MapSearchPanel.lua:475` do): units-query attack targets, attack, attack follow-up, combat estimate (defender and the support bonus, which now counts only visible supporters on visible tiles), threat scan, post-move visibility, map area, cities-query targets and city attack. A move becomes a capture move only for a unit on a visible tile that passes the same check. The blocked-move reason reads terrain only on a revealed tile and the owner only on a visible one; an unrevealed target reads `unit did not move (tile not revealed)`. `IsUnitVisible` sits in the same binding list as `IsVisible`/`IsRevealed` in `GameCore_XP2_FinalRelease.dll` (`Cache_Lua_IPlayerVisibility.cpp`), whose functions GameCore scenario scripts also call, so it is expected to work in the GameCore queries too (threat scan, map area, post-move scan); not yet run live.
- Refused attacks (Important). `attack_unit` puts the outcome first and the combat estimate after it, and drops the estimate when the game refuses (`NO_MOVES`, `OUT_OF_RANGE`, `NO_LOS`, `ZOC`, `STOPPED_SHORT`, ...). `is_failure` flags any line that starts `Error:`, `FAIL` or `ERR:`; `batch_orders` shows the outcome line, so a refused attack is `#n FAIL Error: ...`, the camera stays and the log says `refused: ...`.
- Recovery at the menu (Important). Connection errors while a fresh state list shows `FrontEnd` and `MainMenu` (and no `InGame`), or while `new_game` runs (`AppContext.new_game_active`), return `Error: game is at the main menu; call new_game or load_game_save` (or a new_game-in-progress error) and never count towards the 5-error recovery. A successful `new_game` resets the logger and spatial turn, rebinds them (and the heartbeat) to the new game, and clears the save-load history.
- Recovery saves and identity (Important). Every successful `end_turn` writes `last_save` (`0_MCP_NNNN`, only when the save call reported success) and `turn` into the game's `meta.json` (`Broadcast.update_meta`). `restart_and_load`, `load_game_save` and `load_save_from_menu` with no save name, and the connection-loss recovery, load that save of the campaign game (the bound game, else the latest `campaign/games/NN-*`), and refuse when there is none; they never pick the newest file in `Saves\Single`. After every load (all four tools, connection and hang recovery) the live identity is compared with the campaign game's: a different game returns `ERROR: WRONG GAME LOADED` first and the load is not recorded in that game's log. Binding to an existing game directory seeds the turn high-water mark from `meta.json`. `load_game_save`'s `save_name` is now optional and `reason` keyword-only (still required). PLAYBOOK step 7 and rule 11 name the game's own last save and no longer offer the unnamed form.
- Queued moves (Important). A unit that stays put after a move order but has a queued destination (`UnitManager.GetQueuedDestination`, read in InGame) now returns `QUEUED: path to (x,y) continues next turn (...)`, an OK result; `BLOCKED` is kept for a move with no queued path. The units query, both skip builders and the `end_turn` auto-skip use one helper, `_queuedDest(u)`, which checks `Map.IsPlot` as `WorldInput.lua` does.
- Situation board (Important). EMPIRE gains `era score N (dark < D, golden >= G)`. ATTENTION gains `upgrades available:` (from the units query's `CanStartCommand(UPGRADE)` flag), `amenities short:` (net amenities as the city panel computes them, `GetAmenities() - GetAmenitiesNeeded()`, a new cities-query field) and `housing low:` (housing at or below population + 1). THREATS gains `war declared:` and `denounced:` lines from the notification panel (the notifications query now also sends the panel's summary text), `cities under siege:` (the city centre's `IsUnderSiege()`, as `CitySupport.lua` reads it), and lists units only of barbarians and players we are at war with (the threat scan sends `war:0/1`). Still capped at 40 lines.
- Minors. FAIRNESS hard-stop wording (only `end_turn` refuses; an identity change clears it), pathing row (the game's own path preview, no extra IsRevealed filter), stale line removed, a note on the deal screen's directly-read resource list; `skip_remaining_units` docstring; RUNTIME bind address (`127.0.0.1`) and the Steam dialog (ticked "always"). Log text has no em dashes, `ALREADY_FORTIFIED`, `PROMOTED|X`, `COUNTER: ...`, `ACCEPTED|...`, `UPGRADED`, `ENVOY_SENT` read as plain words, and city actions name the city from the last city list. `log.md` uses local time; `log.jsonl` keeps ISO with the offset. `narrate` accepts only plan, read, decision, reaction, lesson, pivot, postmortem. `_take_snapshot` reads units in InGame. `new_game` has its own `why` message.

Still to check live (the first real sitting, April told):

- An attack that lands (melee and ranged) and one the game refuses (no moves or out of range): batch line, log line, camera.
- A civic through `batch_orders`/`set_research`, a unit promotion, and a `propose_trade` that comes back `COUNTER`.
- `IsUnitVisible` on a real stealth unit (an enemy submarine next to a visible tile), in the InGame queries and the GameCore ones (threat scan, `get_map_area`, the post-move scan).
- A move whose next tile costs more than the moves left: `QUEUED`, and the unit moving on at the end of the turn.
- The new board lines: era score, an unhappy city, housing low, an upgrade, a war declaration or denouncement notice (the summary text is the panel's), a city under siege.
- One recovery end to end: `restart_and_load(reason)` with no name after a crash, picking the game's `last_save` from `meta.json`; and a tool call at the main menu returning the menu error instead of restarting the game.
- Deal options (deferred Minor 11): whether `get_trade_options` lists a rival's strategic stockpile before its revealing tech (it reads `GetResourceAmount`, not `GetPossibleDealItems`).

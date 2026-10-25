# Civ 6 Deity Campaign: design

Date: 2026-10-25. Author: Claude (Fable 5.1), for April.

## Intent

April wants to watch Claude try to beat Civilization VI on Deity, fairly, and learn
from the attempt. The deliverable is not a bot. It is a set of tools ("cybernetics")
that let Claude, as the player, read the game and give orders through the game's own
rule-checked command layer; a live commentary log she reads while watching; and a
campaign notebook that carries lessons from one game to the next.

Claude plays; April watches the game window and the log. If one approach fails,
Claude says so in the log, writes a post-mortem, and starts a new game with a
different approach (civ, opening, strategy).

## Fixed game settings

- Difficulty Deity. Never lower.
- Map Fractal, size Standard (8 civs), speed Standard (my call; April did not specify).
- Opponents random. Claude chooses its own civ at game start; the choice and the
  reasoning go in the log. Civ choice is a play-time decision, not a build decision.
- Ruleset Gathering Storm (Expansion2), all owned DLC on, no mods.
- Game runs on April's Windows PC in windowed mode on her main monitor. The camera
  follows the action (upstream's spectator camera).
- Auto End Turn off (the tools own turn ending). Achievements off (tuner on); agreed.

## Goals

1. Claude plays complete Deity games via the FireTuner interface, start to finish,
   across several sittings and sessions.
2. A live plain-English log interleaving Claude's reasoning (plans, decisions and
   why, reads of the AI, reactions, lessons) with what happened each turn. Readable
   while the game runs and afterwards.
3. Fairness: Claude knows only what a human player at the screen could know and does
   only what a human could do with mouse and keyboard.
4. A campaign across games with a persistent notebook of lessons and post-mortems.
5. Efficiency: few tool calls per turn (Fable usage). Batch orders; the end-turn
   report surfaces everything needing attention so Claude does not poll.

## Non-goals

- Upstream's benchmark platform (Next.js `web/`, Convex sync, evals, HF dataset
  publishing, ELO). Leave in place, do not maintain, do not use.
- Multi-agent or agent-vs-agent play.
- A polished UI. The log viewer is a plain page.

## Anti-goals (failure even if a game is won)

- Reading anything a human could not see: unrevealed tiles, units outside visibility,
  unmet civs, resources before their revealing tech, AI internal plans or
  intentions, the game's `Logs/AI_*.csv` files, process memory, save-file internals.
- Any action outside the rule-checked InGame command layer. No GameCore mutations,
  no debug or cheat commands.
- Reloading a save to undo an outcome. Loading is allowed only to resume after a
  crash or a closed game (latest autosave or the session save). Every load is
  logged with its reason.

## How it gets made (decided)

- **Base:** fork of lmwilki/civ6-mcp v1.1.11 (MIT) at SalukiSwift/civ6-deity,
  cloned to `C:\Users\April Tomlinson\civ6-deity` (WSL path
  `/mnt/c/Users/April Tomlinson/civ6-deity`). One checkout shared by Windows
  (runtime) and WSL (editing, git). `upstream` remote kept for cherry-picks.
  Git identity: Swift / 301704424+SalukiSwift@users.noreply.github.com.
- **Runtime placement:** the MCP server runs as a Windows process using Windows
  `uv.exe` (`C:\Users\April Tomlinson\.local\bin\uv.exe`, uv 0.11.2, cpython
  3.12.13 installed) and connects to the game's tuner at 127.0.0.1:4318 natively.
  Claude Code in WSL launches it over the WSL-Windows interop as a stdio MCP
  server (`.mcp.json` in the repo root). Reason: WSL networking is NAT mode (no
  shared localhost) and upstream warns the tuner locks up after failed WSL
  connections.
- **Tuner enablement:** set `EnableTuner 1` in
  `C:\Users\April Tomlinson\AppData\Local\Firaxis Games\Sid Meier's Civilization VI\AppOptions.txt`
  (currently 0). If the port does not open without the Civ 6 SDK (upstream says the
  tuner server ships with the SDK on Windows; the SDK is not installed), April
  installs the SDK from Steam (Library, filter Tools), or we open a
  `steam://install/<appid>` link for her.
- **Language and tooling:** Python 3.12, uv, pytest. Lua snippets run in the game's
  GameCore (reads) and InGame (commands) states, as upstream does.
- **Process:** plan via superpowers:writing-plans; implement with subagents
  (Sonnet for well-specified tasks, Opus for live-game testing and the final
  review). Live-game checks happen in short windows announced to April first.

## Components

### 1. Fog seal (fairness audit and fixes)

Audit every Lua query in `src/civ_mcp/lua/*.py` and every tool in `server.py`.

Rules:
- Tiles: terrain, features, resources, improvements and ownership only for plots
  where the local player's `PlayersVisibility` reports revealed. Units, city
  population, HP and defence only where currently visible. Resources only once
  the revealing tech is known (the game's own resource-visibility check).
- Players: nothing about civs not yet met. For met civs, match the in-game
  screens: World Rankings victory progress, score, era, and deal-screen values
  (gold, gold per turn, tradeable items) only inside a deal query. Agendas:
  historical after meeting; hidden only when the game grants the access level
  (upstream already does this in `get_diplomacy`).
- Never read AI planning state, other civs' production or research queues, or
  unit lists beyond visible units. Never read files under `Logs/` from tool code.
- Mutations only via InGame `UnitManager`, `CityManager`, `DiplomacyManager` and
  the local-player governance APIs. Grep for direct GameCore mutation and remove
  or gate it. Tools that expose hidden info with no fair variant are removed from
  the tool list.
- Save-scum: keep upstream's detector. `load_*` tools refuse while a live game is
  in progress unless given an explicit crash-recovery reason; every load is
  logged.
- Deliver `docs/FAIRNESS.md`: each tool, what it exposes, and the in-game screen it
  corresponds to. Unit tests assert generated Lua contains the visibility guards.

### 2. Broadcast (commentary log)

- Every action tool gains an optional keyword-only `why: str` (one line). It is
  logged with the action and its result.
- New tool `narrate(kind, text)`; kinds: `plan`, `read` (what I see, what I think
  the AI is doing), `decision`, `reaction`, `lesson`, `pivot`, `postmortem`.
- `end_turn` writes an automatic plain-English "what happened" entry from the diff
  it already computes.
- Storage per game: `campaign/games/<NN>-<civ>-<YYYY-MM-DD>/log.md` (append-only,
  turn-headed, human-readable) and `log.jsonl` (same events, structured).
- Live viewer: a small local web page served from the Windows side (inside the MCP
  process or a sibling script), plain HTML and JS, auto-refresh or SSE, no
  framework, no build step. Shows the log as it grows with turn headers, kind
  badges, and the current situation board. April opens it on her second monitor.
- Readability bar: a Civ player who does not know the tools can follow it. Hex
  coordinates are fine; add a landmark where cheap ("north of the capital").

### 3. Situation board (anti-sensorium)

`end_turn` and a `get_situation` tool return a compact board, all from
player-visible information, target 40 lines or fewer:
- Empire: turn, era and era score against thresholds, yields, gold and income,
  faith, amenity and housing warnings.
- Attention: idle units, cities with empty or finished production, unassigned
  envoys and governor titles, pending pantheon, great person, government and
  policy choices, open trade-route slots, research or civic not set, available
  promotions, affordable upgrades.
- Threats: visible enemy and barbarian units near my cities, denouncements, war
  declarations, cities under siege.
- Rivals: score ranking and victory progress for met civs as on World Rankings,
  with a one-line warning when any met civ is close to any victory.

### 4. Order batching (efficiency)

- `batch_orders([...])` runs a list of unit and city orders in one call: move,
  attack, fortify, skip, found city, build improvement, set production, set
  research or civic. Each order reports ok or a failure reason; one failure does
  not stop the rest.
- Camera hops for batched orders as it does for single ones.
- `end_turn` reports remaining blockers rather than silently resolving choices.

### 5. Campaign layer

- `campaign/NOTEBOOK.md`: standing Deity lessons, per-game summaries, current
  hypothesis. Tools `read_notebook` and `append_notebook(section, text)`.
- The per-game directory also holds upstream's diary (resume after context
  compaction) and the save names used.
- `PLAYBOOK.md` (replaces upstream `AGENTS.md`/`CLAUDE.md` for this fork): rules of
  engagement, the turn loop (situation, narrate plan, batch orders, end turn,
  narrate reaction), resume protocol (read notebook, then the game directory, then
  `get_situation`; after a crash, load the latest autosave and log it), Deity
  opening knowledge, victory-type watch rules, call-budget guidance, and the
  commitments to April (log quality, camera, pivots). Keep upstream's pitfalls
  from `AGENTS.md` and the devlogs where still relevant.
- Loss handling: when eliminated or clearly lost (Claude's judgment, logged as
  `postmortem`), write the post-mortem into the notebook and start a new game.

### 6. Game creation

- Preferred: Lua in the FrontEnd state via the tuner to configure the game
  (Deity, Fractal, Standard size and speed, chosen civ, random others, Expansion2
  ruleset) and start it. Spike first: confirm the FrontEnd state is listed by the
  tuner and that `GameConfiguration` setters plus the host-game call work from it.
- Fallback: April creates the game from the menu with the settings Claude states.
  Upstream's OCR launcher is a last resort.
- Launching the game: whatever upstream's Windows launcher does (Steam URL or exe).

## Testing

- Unit tests without the game: generated Lua carries visibility guards; log
  writers produce well-formed md and jsonl; batch order parsing; situation-board
  formatting from fixture snapshots.
- Live spike (game running, April told first): tuner connects from Windows
  Python; MCP stdio over the interop works from WSL; a unit moves; the camera
  hops; the viewer updates; `end_turn` completes; game creation from FrontEnd Lua.
- Live checks are run by an Opus tester in a short announced window.

## Left to the builder

- Exact `campaign/` layout, viewer port, SSE versus polling.
- How `why` threads through existing tool signatures.
- Which upstream pieces to delete versus leave (lean toward leaving, except
  tools that fail the fairness rules).
- Viewer inside the MCP process or a separate script.

## Open risks

- The Windows tuner may need the SDK (spike resolves).
- MCP stdio over the WSL interop to a Windows `uv.exe` (spike resolves). Fallback:
  a small WSL-side stdio proxy to a Windows-side TCP server bound on the WSL NAT
  gateway address, with timeouts on every call.
- Fable usage over a 300-turn game. Batching and the situation board are the
  mitigations; report usage to April after the first ten turns.

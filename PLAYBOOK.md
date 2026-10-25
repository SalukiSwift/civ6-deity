# Playbook

You are Claude, playing Civilization VI on Deity through this repo's MCP tools, over several games and sessions. April watches. Strategy is yours to write in `campaign/NOTEBOOK.md` as you learn; this file is only the rules, the loop and the mechanics.

## Rules of engagement

- Only player-visible information. The tools enforce this. Do not read the game's `Logs/` directory, save files or game memory, and do not work around a tool that withholds something.
- Only the game's own player commands, through the tools. No cheats, no console, no editing files to change the game.
- Never reload to undo an outcome. A loss, a bad battle or a bad treaty stands. Load a save only to recover from a crash or a closed game, always with a `reason`; every load is logged publicly.
- Deity only, never lower, in every game of the campaign.
- The camera follows the action. Do not fight it.
- The log must be readable by a Civ player who does not know the tools. Say what you are doing in plain Civ terms. Hex coordinates are fine; add a landmark where it is cheap ("(34,12), the hills east of Kumasi").
- When an approach fails, say so in a `narrate("pivot", ...)` entry: what you tried, why it failed, what you do instead.
- Narrate kinds: `plan`, `read`, `decision`, `reaction`, `lesson`, `pivot`, `postmortem`. One to three sentences each.
- Do not use em dashes or decorative emoji in anything you write (log, notebook, commits).

## Session start and resume

1. This is a Claude Code session started in the repo directory, so `.mcp.json` is picked up.
2. `read_notebook` first.
3. Find the current game: the latest `campaign/games/NN-civ/` that has a `meta.json`. Its `log.md` is the record of what happened. Skim its tail.
4. `get_situation` for the board.
5. The viewer is at `http://127.0.0.1:8000/` (port from `CIV_MCP_WEB_PORT`).
6. The game must be launched with Steam's DX12 option (already set to always).
7. After a crash or a closed game: `restart_and_load(reason, save_name="0_MCP_NNNN")` (or `restart_and_load(reason)` for the latest autosave), then say so in a `narrate` entry. It takes about 5 minutes (the intro movies hold the main menu back for 3 to 4 minutes); wait for it. Never load to undo something that went badly.
8. No game exists yet (main menu): `new_game(leader, why)` creates the Deity Fractal Standard game from the main menu; if it cannot, it returns the settings for April to click.

## Turn loop

1. `get_situation`: the board, everything needing attention, threats, rival scores. It is also appended after every `end_turn`, so do not poll it twice.
2. `narrate("plan", ...)`: what this turn is for. Skip on quiet turns.
3. `batch_orders(orders, why)`: all unit, city, research and civic orders for the turn in one call. Order shapes:
   - `{"unit_id", "action", "x", "y", "improvement", "why"}`
   - `{"city_id", "production"}`
   - `{"city_id", "purchase"}`
   - `{"research"}`
   - `{"civic"}`
4. `end_turn(summary)`: `summary` is required, one line saying what you did and why.
5. `narrate("reaction", ...)` when something surprising happened (a war, a lost unit, a wonder lost). Use `lesson` when you learned something general, and `append_notebook` for the ones worth keeping.

Other tools you will use: `get_map_area`, `get_units`, `get_cities`, `get_diplomacy`, `get_victory_progress`, `get_tech_civics`, `set_policies`, `respond_to_trade`, `propose_trade`, `get_world_congress`, `queue_wc_votes`, `read_notebook`, `append_notebook`.

## Call budget

- Early game: 3 to 6 tool calls per turn.
- Mid game: 8 to 15 tool calls per turn.
- Batch orders. Read the board instead of polling individual tools. Spend extra calls on wars, treaties and city placement, which are where games are decided.

## Mechanics kept from upstream

Full upstream notes are in `docs/upstream-AGENTS.md` for reference. The rules that bite:

1. Higher Y is north (up the screen), X increases east. (Upstream's notes say the opposite; the screen settles it.)
2. Unit ids are composite. Tools take `id % 65536`. Move responses show the target tile, not the arrival tile; check `get_units` for where the unit really is.
3. A move longer than one turn keeps its path: the game moves the unit on along it when you END each turn, so at the start of the next turn it is still where it stopped, with full moves, shown as `[moving to (x,y)]` and left off the idle list. Leave it alone unless you want a new target; a new order or Skip Turn replaces the path.
4. `end_turn` takes one `summary`. It replaces upstream's five reflections.
5. After you declare war, attacks fail (`NO_ENEMY`) until next turn. Do not retry and do not reload; end the turn and attack next turn.
6. The World Congress fires inside `end_turn`. When one is due, call `get_world_congress`, then `queue_wc_votes`, BEFORE `end_turn`.
7. `propose_trade` with mode `"test"` first, then `"send"`. If the AI changes the deal the response comes back as `COUNTER:`; read it before answering. Duplicate luxuries give 0 amenities, so trade them away.
8. Buy religious units only in cities where your religion is the majority.
9. Never delete Great People (0 charges is normal). Activate them on a matching completed district.
10. Call `get_map_area` with radius 2 before moving civilians. Forest and hills movement costs stack.
11. Recovery: `load_game_save("0_MCP_NNNN", reason)` or `restart_and_load(reason, save_name="0_MCP_NNNN")` (`reason` comes first; `restart_and_load(reason)` loads the latest autosave). Save names have no extension. Autosaves are named `0_MCP_NNNN` and the last 8 are kept. A turn regression gives a CRITICAL warning; treat it as a signal, not a license to reload for advantage.

Also: `get_tech_civics` flags items 2 turns or fewer away as "!! GRAB THIS"; take those.

## Deity facts

Extracted read-only from the game's own data files under `Sid Meier's Civilization VI/` (paths below are relative to that folder). Only what the files state is listed. Magnitudes marked "scaled" depend on engine code that is not in the data.

Confirmed:

- Deity is the top difficulty, `DIFFICULTY_DEITY` (`Base/Assets/Gameplay/Data/Difficulties.xml`). The default game difficulty is `DIFFICULTY_PRINCE` (`Base/Assets/Configuration/Data/SetupParameters.xml`).
- AI civs on high difficulty get per-turn yield bonuses scaled linearly from the default handicap (`Base/Assets/Gameplay/Data/Leaders.xml`, `TRAIT_LEADER_MAJOR_CIV`, modifiers gated by `PLAYER_IS_HIGH_DIFFICULTY_AI`). Per difficulty step above Prince, the data gives these step sizes (`Extra` values):
  - science 8, culture 8, faith 8
  - production 20, gold 20
  - unit XP 10
  - combat strength +1 (`HIGH_DIFFICULTY_COMBAT_SCALING`)
  - free tech boosts +1 and free civic boosts +1, applied immediately
- Human players on LOW difficulty get the inverse combat and XP modifiers (`LOW_DIFFICULTY_COMBAT_SCALING`, `LOW_DIFFICULTY_UNIT_XP_SCALING`, both anchored at `DIFFICULTY_PRINCE`). On Deity you get none of this.
- AI-only extra starting units (`Base/Assets/Gameplay/Data/Eras.xml`, `MajorStartingUnits`, `AiOnly="true"`): from `DIFFICULTY_KING` up, an extra era-appropriate melee unit (Warrior in the Ancient era) with `DifficultyDelta="1"`, and an extra Builder with `DifficultyDelta="0.5"`; from `DIFFICULTY_EMPEROR` up, an extra Settler with `DifficultyDelta="0.5"`. The same pattern repeats for later eras (a new AI civ founded in a later era also gets them).
- Barbarian raids and attack forces get larger from `DIFFICULTY_IMMORTAL` up (`Base/Assets/Gameplay/Data/Barbarians.xml`: `HighDifficultyStandardRaid` 3 melee + 2 ranged, `HighDifficultyStandardAttack` 4 melee + 3 ranged + 2 siege + 1 ram).
- Barbarian camp gold reward is reduced as difficulty rises (`BARBARIAN_CAMP_GOLD_SCALING`, `Extra` -5 per step above Prince).

Not found in the data:

- The exact multiplier at Deity (steps above Prince, and whether `DifficultyDelta` multiplies the extra units). The `LinearScaleFromDefaultHandicap` formula is engine code. If Deity is 4 steps above Prince (King, Emperor, Immortal, Deity) the yield bonuses would be 32 percent science, culture and faith and 80 percent production and gold, but this is an inference, not a file fact. Verify in play with `get_diplomacy` and rival scores.
- Any AI discount on unit or building costs, any AI vision bonus, any diplomacy-attitude bonus, or any extra starting gold or tiles. Nothing in `Difficulties.xml` (it only lists the difficulty names) or the Expansion1/Expansion2 data mentions them.

Practical reading: expect AIs ahead in science and production from the first turns, with more early units and settlers, and tougher barbarians. The notebook, not this file, decides what to do about it.

## Victory watch

Every 10 turns, run `get_victory_progress` and write one `narrate("read", ...)` line on it. Check every rival, not only the leader:

- Science: spaceport and project progress.
- Culture: tourism against domestic tourists.
- Religion: how many civs follow a religion that is not the leading one.
- Diplomatic: diplomatic victory points and World Congress timing.
- Domination: any civ holding capitals.
- Score: the turn limit, if any.

If a rival is within reach of a victory you cannot stop by building, make it a `plan` entry and act early; the notebook's standing lessons say how.

## Loss protocol

When the game is lost (capital taken, a rival's victory, or the game-over screen):

1. `narrate("postmortem", ...)`: what happened, the turning point, what you would do differently. Plain Civ terms.
2. `append_notebook("Game log", ...)`: the same post-mortem as a short entry: game number, civ and leader, how it ended and on which turn, the cause.
3. Add any general lessons with `append_notebook("Standing lessons", ...)` and revise `Current hypothesis`.
4. Start a new game with `new_game(leader, why)`, still on Deity. Do not load an earlier save to retry the lost game.

`new_game` called mid-game exits the current game WITHOUT saving; only call it after the postmortem entry.

# Fairness

This fork lets Claude play Civilization VI on Deity under the same information and the same commands a human player has. This file records how, tool by tool, and the rules that hold the line.

R = read only, M = mutates the game, L = game or save lifecycle (loading, launching).

## Standing rules

1. Visibility. Anything about a tile is read only when the game would show it to a human: live owner, improvement, units and yields only on IsVisible tiles; terrain and revealed features on IsRevealed tiles; strategic resources only when the player's tech reveals them; foreign cities only when their plot is revealed. A foreign unit is also checked on its own with the game's unit-visibility test (`PlayersVisibility:IsUnitVisible`, as `WorldInput.lua` and `MapSearchPanel.lua` use it), so a stealthed unit on a visible tile (a submarine) is not listed, targeted, counted for a capture move or shown on the board. Fogged tiles come from last-seen memory (fog.json), never from live state. Rival civ names appear only after HasMet; others read "an unmet civilization".
2. Rival data. Rival numbers come from the World Rankings values (score, era, techs, military strength, tourism, victory points) and revealed cities. No rival yields, civics, faith, stockpiles or population.
3. Deal screen (controller ruling R7). The Make Deal data (rival gold, gold per turn, favor, tradeable resources, city list) is fair for any met civ because a human can open Make Deal at will. There is no session guard.
4. InGame-only mutations. Every change to the game goes through the InGame Lua state using the same player commands the UI issues (UnitManager.RequestOperation, UnitManager.RequestCommand, CityManager.RequestOperation, and so on). GameCore (GameCore_Tuner) is read only. Promote uses `UnitCommandTypes.PROMOTE` exactly as the promotion popup does (promotion index as the parameter, availability from the PROMOTIONS result of CanStartCommand), with no free heal. Skip uses the stock `UNITOPERATION_SKIP_TURN` operation, as the unit panel's Skip Turn button does. Research and civics have no GameCore fallback: if the game refuses, the tool reports the error.
5. No reads of Logs/ or any game-written log files. The agent gets game information only through the tools above.
6. The map seed is hidden. Run identity uses a hash of the seed, never the seed.
7. No auto-answers to strategic prompts. Raze, keep, liberate and disloyal-city decisions are reported as blockers with the city_action hint; end_turn never answers them. An AI counter-offer is returned as `COUNTER:` text and the session stays open for respond_to_trade; the tool never accepts a changed deal on the agent's behalf. Only an unchanged acceptance (the AI agrees to exactly the terms sent) is confirmed automatically.
8. The raw Lua escape hatch `run_lua` is not registered unless the server is started with `CIV_MCP_DEV_LUA=1`. It is a developer tool and is not part of any fair run.

## Save-load policy

- Every load tool (`load_save`, `load_game_save`, `load_save_from_menu`, `restart_and_load`) requires a non-empty `reason`. An empty reason returns an error and nothing is loaded.
- A load with no save name loads the campaign game's own last `0_MCP_NNNN` save, which every successful `end_turn` writes into that game's `meta.json` (`last_save`, `turn`). It never picks the newest file in `Saves\Single`, which April's own games and earlier campaign games share. If the game has no recorded save yet, the tool refuses and asks for a name. Connection recovery ("connection lost 5x") uses the same rule and is never attempted while the game is at the main menu or while `new_game` runs.
- After every load the live game's identity is compared with the campaign game's (the bound game, or in a fresh session the latest game directory). A different game returns a loud `ERROR: WRONG GAME LOADED` and the load is not recorded as that game's.
- Every load that gets as far as loading (launcher results starting or containing FAILED or ABORTED are not recorded) and passes that check is recorded, whether it comes from a tool, from the automatic boot (`CIV_MCP_SAVE_FILE`), from connection recovery ("connection lost 5x") or from hang recovery. Each one appends a `load` entry to the loaded game's broadcast log: `loaded <save>: <reason>`. Binding to an existing game directory seeds the turn high-water mark from its `meta.json`, so a turn regression is caught from the first `end_turn` of a fresh session.
- Upstream's save-scum thresholds are kept: a minor warning at 3 loads across 3 distinct turns (span 10), a strong warning at 5 and 5 (span 20), and a hard stop at 8 and 8 (span 30), counting only loads made during play.
- The hard stop makes `end_turn` refuse to advance the turn; other tools still answer. A change of game identity (`new_game`, or a different game loaded) clears it, together with the load history.

## Tool table

Guard column: IsRevealed, IsVisible and HasMet are the game's own visibility and diplomacy checks; "tech-visible" means a resource is shown only if the player's tech reveals it; "deal screen" is the Make Deal data (rule 3); "game rules" means the game's own CanStart checks decide legality; "none needed" means the tool touches only our own empire or UI state.

| Tool | Kind | What it exposes or changes | In-game screen or button | Guard |
|---|---|---|---|---|
| `get_game_overview` | R | Score, turn, yields, gold, research summary for our empire; share of land explored with our own revealed-tile count (the map's land total is not shown) | Top bar and Info screens | none needed (own empire) |
| `get_units` | R | Our units with their order status (fortified, sleeping, moving to a tile), plus enemy units on visible tiles that threaten them | Unit list, map view | IsVisible, IsUnitVisible |
| `get_spies` | R | Our spies, ranks, missions | Espionage screen | none needed |
| `spy_action` | M | Moves a spy or starts a mission | Espionage screen, spy panel | game rules (CanStartOperation) |
| `get_cities` | R | Our cities (including net amenities and whether the city is under siege, as the city panel shows them) and visible nearby targets | City panels, Cities report | IsVisible and IsUnitVisible for foreign targets |
| `get_city_production` | R | What a city can build now | City production panel | none needed |
| `get_map_area` | R | Terrain around a point; live owner, improvement, units and yields only on visible tiles; fogged tiles show terrain and last-seen memory | Map, tile tooltips, fog of war | IsVisible, IsRevealed, IsUnitVisible, tech-visible resources |
| `get_settle_advisor` | R | Settle candidates near a settler | Settler lens (settle hints) | IsRevealed, IsVisible |
| `get_pathing_estimate` | R | Turns a unit needs to reach a tile, and the path's tiles | Move preview on the map (the path the game draws when you hover a target) | game rules: the game's own path preview (`UnitManager.GetMoveToPath`, as `WorldInput.lua` draws it); no extra IsRevealed filter |
| `get_global_settle_advisor` | R | Settle candidates over the revealed map | Settler lens | IsRevealed |
| `get_builder_tasks` | R | Task board for our builders | Builder lens, improvement hints | tech-visible resources |
| `get_empire_resources` | R | Resources in and near our empire | Resources report | tech-visible resources |
| `get_strategic_map` | R | Fog boundaries and unclaimed resources | Strategic view and map pins | IsRevealed, IsVisible |
| `get_diplomacy` | R | Met civs, relationship, World Rankings values (score, era, techs, military, tourism, victory points) | Diplomacy screen, World Rankings | HasMet |
| `get_tech_civics` | R | Our research and civic status | Tech and Civics trees | none needed |
| `get_pending_trades` | R | Incoming deals | Diplomacy popup for a pending deal | none needed |
| `get_policies` | R | Our government and policy cards | Government screen | none needed |
| `get_notifications` | R | Active notifications, with the panel's message and summary text | Notification panel | none needed (owners masked via HasMet) |
| `get_pending_diplomacy` | R | Pending diplomacy encounters | Diplomacy popup | none needed |
| `get_governors` | R | Our governors | Governors screen | none needed |
| `appoint_governor` | M | Appoints a governor | Governors screen | game rules |
| `assign_governor` | M | Assigns a governor to a city | Governors screen | game rules |
| `promote_governor` | M | Promotes a governor | Governors screen | game rules |
| `get_unit_promotions` | R | Promotions a unit can take | Promotion popup | none needed |
| `promote_unit` | M | Promotes a unit (stock PROMOTE command with the promotion index, checked against the game's offered list, no heal) | Unit panel promotion popup | game rules (CanStartCommand) |
| `get_city_states` | R | Met city-states, envoys, types | City-States screen | HasMet |
| `send_envoy` | M | Sends an envoy | City-States screen | game rules |
| `get_pantheon_beliefs` | R | Pantheon status and choices | Pantheon popup | none needed |
| `choose_pantheon` | M | Founds a pantheon | Pantheon popup | game rules |
| `get_religion_beliefs` | R | Religion founding status and choices | Religion screen | none needed |
| `found_religion` | M | Founds a religion | Religion screen | game rules |
| `upgrade_unit` | M | Upgrades a unit | Unit panel Upgrade button | game rules |
| `get_dedications` | R | Era dedications and active ones | Era popup | none needed |
| `choose_dedication` | M | Chooses a dedication | Era popup | game rules |
| `get_trade_options` | R | Rival gold, gold per turn, favor, tradeable resources and city list for a met civ. The resource list is read directly (`GetResourceAmount`), not through the deal screen's item list, so it may include a stockpile the player's tech does not reveal yet (to be verified live) | Make Deal screen (see R7) | HasMet, deal screen |
| `respond_to_trade` | M | Accepts or rejects a pending deal | Diplomacy deal popup | game rules |
| `propose_trade` | M | Proposes a deal; unchanged acceptance is confirmed, a changed deal is returned as COUNTER text and left for respond_to_trade. The resource list the agent picks from (`get_trade_options`) is read directly (`GetResourceAmount`) and may include a stockpile before its revealing tech (to be verified live) | Make Deal screen | HasMet, deal screen |
| `propose_peace` | M | Proposes white peace | Diplomacy screen | HasMet |
| `set_policies` | M | Slots policy cards | Government screen | game rules |
| `respond_to_diplomacy` | M | Answers a diplomacy encounter | Diplomacy popup | game rules |
| `send_diplomatic_action` | M | Sends a diplomatic action | Diplomacy screen | HasMet |
| `form_alliance` | M | Proposes an alliance | Diplomacy screen | HasMet |
| `city_action` | M | City commands, including keep/raze/liberate decisions | City panel, captured-city popup | game rules |
| `unit_action` | M | Unit commands (move, attack, fortify, skip, found city...). A move only becomes a capture move (ATTACK modifier) for a unit the player can see; a move the unit could not make reports its reason only from revealed terrain and visible ownership; a move left queued (the next tile costs more than the moves left) is reported as `QUEUED` | Unit panel and map clicks | IsVisible and IsUnitVisible for targets, IsRevealed for the blocked-move reason, game rules |
| `batch_orders` | M | Runs a list of the existing unit/city/research orders in sequence through the same player commands; no new capability | Unit panel, city panel and research screens, one after another | same as the single tools |
| `skip_remaining_units` | M | Fortify or heal, then stock Skip Turn for the rest; units on a queued move are left alone | Unit panel Skip Turn button | game rules |
| `set_city_production` | M | Sets city production | City production panel | game rules |
| `purchase_item` | M | Buys an item | City panel purchase buttons | game rules |
| `set_research` | M | Chooses research or a civic; no GameCore fallback | Tech and Civics trees | game rules (error returned if refused) |
| `narrate` | M | Writes commentary to the broadcast log (kinds plan, read, decision, reaction, lesson, pivot, postmortem only; turn, event, action, load and game entries are the server's) | none (not a game action) | none needed |
| `read_notebook` | R | Claude's own notes, no game data | none (agent memory) | none needed |
| `append_notebook` | M | Claude's own notes, no game data; written to disk only | none (not a game action) | none needed |
| `end_turn` | M | Ends the turn; reports blockers; never auto-answers raze or disloyal-city prompts | Next Turn button | game rules |
| `get_diary` | R | Our own diary entries | none (agent memory) | none needed (seed shown as a hash) |
| `get_trade_routes` | R | Our trade routes and capacity | Trade Routes screen | none needed (owners masked via HasMet) |
| `get_trade_destinations` | R | Valid destinations for a trader | Trade route chooser | IsRevealed, HasMet |
| `get_district_advisor` | R | Best district tiles | District placement lens | IsRevealed |
| `get_wonder_advisor` | R | Best wonder tiles | Wonder placement lens | IsRevealed, tech-visible resources |
| `get_purchasable_tiles` | R | Tiles a city can buy | Purchase tile lens | IsRevealed |
| `purchase_tile` | M | Buys a tile | City panel Purchase Tile | game rules |
| `change_government` | M | Switches government | Government screen | game rules |
| `get_great_people` | R | Great People and recruitment progress | Great People screen | none needed |
| `get_gp_advisor` | R | Best city for a Great Person | Great People screen | none needed |
| `recruit_great_person` | M | Recruits a Great Person | Great People screen | game rules |
| `patronize_great_person` | M | Buys a Great Person | Great People screen | game rules |
| `reject_great_person` | M | Passes on a Great Person | Great People screen | game rules |
| `get_world_congress` | R | Congress status and resolutions | World Congress screen | none needed (only when in session or announced) |
| `queue_wc_votes` | M | Pre-sets Congress votes | World Congress screen | game rules |
| `get_victory_progress` | R | Victory progress; rivals via World Rankings values and revealed cities only | World Rankings, Victory screens | HasMet, IsRevealed |
| `get_situation` | R | Situation board; aggregates existing player-visible queries, no new data: era score against its thresholds, amenity and housing warnings, available upgrades, war declarations and denouncements from the notification panel, cities under siege, and enemy units only of barbarians and players we are at war with | Top bar, era panel, unit list, city list, notification panel, World Rankings | none needed (each source query keeps its own guard) |
| `get_religion_spread` | R | Religion by city | Religion lens | IsVisible for foreign cities |
| `set_city_focus` | M | Sets citizen yield focus | City panel citizen management | game rules |
| `dismiss_popup` | M | Closes a blocking popup | Close button on the popup | none needed |
| `list_saves` | R | Lists save files | Load Game screen | none needed |
| `load_save` | L | Loads a save by index; reason required, identity checked, logged | Load Game screen | reason required, identity checked, logged |
| `load_game_save` | L | Loads a save by name (no name: the campaign game's own last 0_MCP save); reason required, identity checked, logged | Load Game screen | reason required, identity checked, logged |
| `kill_game` | L | Closes the game process | Exit to desktop | none needed |
| `launch_game` | L | Starts the game | Steam launch | none needed |
| `load_save_from_menu` | L | Loads a save from the main menu (no name: the campaign game's own last 0_MCP save); reason required, identity checked, logged | Main menu Load Game | reason required, identity checked, logged |
| `restart_and_load` | L | Kill, relaunch and load (no name: the campaign game's own last 0_MCP save); reason required, identity checked, logged | Exit, relaunch, Load Game | reason required, identity checked, logged |
| `new_game` | L | Creates the fixed Deity/Fractal/Standard/Gathering Storm game for a chosen leader: the same setup calls the Play Now button makes (run in the MainMenu context, settings fixed in code), then one BEGIN GAME click, sent only while Civ 6 is the foreground window; settings are checked in the new game. A game in progress is first left without saving via the Exit to Main Menu call | Exit to Main Menu, Advanced Setup, Begin Game | `why` required, logged; settings not choosable |

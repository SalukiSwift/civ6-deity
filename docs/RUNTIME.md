# Runtime: Civ 6 tuner + MCP server on Windows, driven from WSL

Verified live on 2026-10-25 (Civ 6 Steam build, DX12, no SDK installed).

Layout:

- The game runs on Windows and exposes the FireTuner Lua socket on Windows loopback `127.0.0.1:4318`.
- The MCP server (`civ-mcp`) runs under **Windows** Python 3.12 via `uv.exe`, so it can reach that loopback port. WSL's `127.0.0.1` is not Windows' `127.0.0.1` under NAT networking, so the server must not run in WSL.
- Claude Code runs in WSL and talks to the server over stdio through WSL-Windows interop (`.mcp.json`).

## One-time setup

### 1. Enable the tuner (no SDK needed)

Edit `C:\Users\April Tomlinson\AppData\Local\Firaxis Games\Sid Meier's Civilization VI\AppOptions.txt` (keep a backup; ours is `AppOptions.txt.bak-2026-10-25`) and set:

```
EnableTuner 1
```

The Civilization VI SDK / Development Tools are **not** required. With only the base game installed the game logs `Running SetupTuner() ... Setting up tuner.` in `Logs\Startup.log` and listens on `127.0.0.1:4318`. (Upstream's README says the SDK is needed; on this machine it was not.)

Also keep `AutoEndTurn 0` in `UserOptions.txt`.

### 2. Windows virtualenv

Always use the Windows `uv.exe`. `python.exe` on the Windows PATH is the Microsoft Store stub; never use it. Never run WSL's `uv run` / `uv sync` in this repo: the Windows `uv.exe` owns `.venv`. Pass the Windows path via `--directory` (cmd.exe refuses a WSL cwd).

```bash
"/mnt/c/Users/April Tomlinson/.local/bin/uv.exe" sync --directory 'C:\Users\April Tomlinson\civ6-deity' --extra launcher-windows
```

Result: `.venv` with CPython 3.12.13, including `pywin32` and `Pillow` (needed by the OCR launcher fallback).

### 3. MCP config

`.mcp.json` (repo root):

```json
{
  "mcpServers": {
    "civ6": {
      "command": "/mnt/c/Users/April Tomlinson/.local/bin/uv.exe",
      "args": ["run", "--directory", "C:\\Users\\April Tomlinson\\civ6-deity", "civ-mcp"]
    }
  }
}
```

### Windows firewall

At startup the server also starts the web dashboard API (uvicorn) bound to `0.0.0.0:8000`. Windows Defender Firewall may show a prompt for `python.exe` once. The MCP stdio path does not need inbound access, so either answer works for MCP; allow it on private networks only if the dashboard must be reachable from another machine.

## Every session

### Launch the game

```bash
cmd.exe /c start steam://run/289070
```

Steam then shows a **"Select launch option"** dialog (DX12 preselected / DX11). Someone has to click Play. Ticking "Always use this option" in that dialog (April's call) would let scripted launches skip it.

Starting the DX12 executable directly skips that dialog (checked 2026-10-25 with Steam running: no dialog, process up at +20 s, port 4318 listening at +25 s):

```bash
cd /mnt/c && cmd.exe /c start "" /D "C:\Program Files (x86)\Steam\steamapps\common\Sid Meier's Civilization VI\Base\Binaries\Win64Steam" "C:\Program Files (x86)\Steam\steamapps\common\Sid Meier's Civilization VI\Base\Binaries\Win64Steam\CivilizationVI_DX12.exe"
```

The port opens long before the main menu. Until the menu is up the state list has only `Main State` and `DebugHotloadCache`; `FrontEnd` and `MainMenu` were there about 3 to 4 minutes after launch on that run (intro and shader warm-up). Wait for `FrontEnd` before calling `new_game`.

Check the tuner port (from WSL):

```bash
netstat.exe -ano | grep -E ":431[89] "
```

Expected at the main menu: `TCP    127.0.0.1:4318    0.0.0.0:0    LISTENING    <civ pid>`.

### Handshake test

```bash
"/mnt/c/Users/April Tomlinson/.local/bin/uv.exe" run --directory 'C:\Users\April Tomlinson\civ6-deity' python scripts/test_connection.py
```

The script prints the raw `LSQ:` reply, which alternates `index, name`: `[0] 0`, `[1] Main State`, `[2] 1`, `[3] DebugHotloadCache`, ... Read it as pairs. Its step 4 runs `Game.GetCurrentGameTurn()` in state 0 (`Main State`), which errors with `attempt to index a nil value` even in a live game; that is expected, since `Game` lives in `GameCore_Tuner`. (Checked: in `GameCore_Tuner` it returned `turn=1 localPlayer=0`.)

### MCP smoke test (game may be closed)

```bash
python3 scripts/mcp_smoke.py
```

Expected output: `serverInfo.name: Civilization VI` and `tool count: 81` (76 at Task 1; later tasks added tools), exit 0. The server starts fine with the game closed: the lifespan does not connect eagerly; the popup watcher retries `127.0.0.1:4318` about every 2 s and ignores the refusal. Closing stdin shuts it down cleanly (no leftover `python.exe`, nothing left on :8000).

Against a live game the server logged `Connected: Civ6 ...` and `Discovered 99 Lua states (GameCore=2, InGame=94)`.

### Quit

```bash
taskkill.exe /IM CivilizationVI_DX12.exe /F
```

Nothing needs saving for test runs.

### Saves

`end_turn` writes one `0_MCP_NNNN.Civ6Save` per turn (the last 8 are kept) to `Documents\My Games\Sid Meier's Civilization VI\Saves\Single`, next to April's own named saves. The game's own per-turn autosaves (`AutoSave_NNNN`) go to `Saves\Single\auto`; `restart_and_load(reason)` without a save name loads the newest of those.

### Crash recovery (`restart_and_load`)

`restart_and_load(reason)` kills the game, relaunches it, waits for the main menu through the tuner (a fresh state list with `FrontEnd` and `MainMenu`, polled every 5 s for up to 6 minutes, with the server's popup watcher, camera and watchdog paused so the game has one tuner client), then clicks through Single Player, Load Game, the save and CONTINUE GAME with OCR (120 s backstop for "Single Player"), and finally polls the port for up to 60 s. `load_save_from_menu` does the same wait (launching the game first if it is closed).

Measured 2026-10-25 (game killed on turn 6, `restart_and_load(reason="fix verification")`): launch 23:51:17, process up +2 s, port open +18 s, main menu states after 187.9 s of waiting (about 3 min 26 s after launch), "Single Player" read by OCR 1.0 s later, CONTINUE GAME clicked 45 s after that, port open at once, tool returned after 263.5 s. Result: `Load: Save loading (49s). Steps: Main menu up after 188s (tuner), Clicked Single Player, Clicked Load Game, Clicked Autosaves filter, Selected save AutoSave_0006, Clicked Load Game button, Clicked CONTINUE, FireTuner port open (0s after CONTINUE).`

## Lua state lists (verbatim, index name)

Indexes are **not stable**. The game pushes unsolicited `L` messages (tag -1) with the whole list whenever a state is created or destroyed, and it renumbers the list each time. Always resolve states by name after (re)connecting.

### Main menu (32 states)

`FrontEnd` (30) is the shell context; its presence means "at the main menu". `MainMenu` (24) is the main menu screen context (`ContextPtr:IsHidden()` returned `false` there) and the one that includes `PlayerSetupLogic`/`GameSetupLogic`: `BuildHeadlessGameSetup`, `RebuildPlayerParameters` and `GameSetup_RefreshParameters` are functions in `MainMenu` (and `HostGame`) but `nil` in `FrontEnd`. So game setup runs in `MainMenu`. `GameCore_Tuner` and `InGame` do not exist yet.

```
 0 Main State
 1 DebugHotloadCache
 2 Options
 3 CreditsScreen
 4 My2K
 5 LoadGameMenu
 6 PBCNotifyRemind
 7 Lobby
 8 Mods
 9 LoadGameMenu
10 SaveGameMenu
11 MultiSelectWindow
12 CityStatePicker
13 LeaderPicker
14 HostGame
15 ConfirmKick
16 EditHotseatPlayer
17 StagingRoom
18 LoadGameMenu
19 SaveGameMenu
20 FrontEndPopup
21 CivRoyaleIntro
22 PiratesIntro
23 CrossPlayLogin
24 MainMenu
25 StateTransition
26 JoiningRoom
27 UserSetupWarning
28 SwitchLayoutPopup
29 Test
30 FrontEnd
31 InvitePopup
```

### In game, map up (99 states)

```
 0 Main State
 1 DebugHotloadCache
 2 GameCore_Tuner
 3 WorldInput
 4 StrategicView
 5 ARXManager
 6 WorldViewIconsManager
 7 DistrictPlotIconManager
 8 PlotInfo
 9 CityBannerManager
10 UnitFlagManager
11 TourismBannerManager
12 MapPinManager
13 SelectedUnit
14 WorldViewPlotMessages
15 GenVFX
16 CameraManager
17 PlotToolTip
18 StatusMessagePanel
19 NotificationPanel
20 ConfirmKick
21 ChatPanel
22 TutorialGoals
23 WorldTracker
24 MapPinListPanel
25 MapSearchPanel
26 MinimapPanel
27 CityPanel
28 UnitPanel
29 ActionPanel
30 MultiplayerTurnManager
31 TopPanel
32 DiplomacyRibbon
33 CityStates
34 EspionageOverview
35 WorldRankings
36 TradeOverview
37 ReportsList
38 PartialScreenHooks
39 ProductionManager
40 ProductionPanel
41 EdgeScroll
42 FullscreenMapPopup
43 TechTree
44 CivicsTree
45 GovernmentScreen
46 ReligionScreen
47 GreatWorksOverview
48 GreatWorkShowcase
49 GreatPeoplePopup
50 GlobalResourcePopup
51 ReportScreen
52 LaunchBar
53 TradeRouteChooser
54 TradeOriginChooser
55 CivicsChooser
56 ResearchChooser
57 EspionageChooser
58 PantheonChooser
59 CityPanelOverview
60 ModalLensPanel
61 EraCompletePopup
62 NaturalWonderPopup
63 WonderBuiltPopup
64 ProjectBuiltPopup
65 MapPinPopup
66 PBCNotificationPopup
67 RazeCity
68 BoostUnlockedPopup
69 TechCivicCompletedPopup
70 UnitPromotionPopup
71 EspionagePopup
72 EventPopup
73 Automation_NarrationPopup
74 ChooseArtifact
75 EspionageEscape
76 UnitCaptured
77 InGamePopup
78 LeaderScene
79 DiplomacyActionView
80 DiplomacyDealView
81 DeclareWarPopup
82 EndGameMenu
83 SaveGameMenu
84 PlayerChange
85 NewTurnPanel
86 CivilopediaScreen
87 PausePanel
88 SwitchLayoutPopup
89 HotSeatBackground
90 LoadGameMenu
91 SaveGameMenu
92 Options
93 InGameTopOptionsMenu
94 InGame
95 AdvisorPopup
96 TutorialWorldAnchor
97 TutorialUIRoot
98 InvitePopup
```

## Starting a game from the main menu through the tuner (`new_game`)

Verified live 2026-10-25 through the MCP tool, three times: `LEADER_GILGAMESH` (from the main menu), `LEADER_AMANITORE` (from inside a running game) and `LEADER_TOMYRIS` (from the main menu after the server had idled there for 25 s). Every check passed each time.

### What `new_game(leader, why)` does

1. Pauses the popup watcher, camera and game-over watchdog (they poll and reconnect on their own; see below), then connects fresh, retrying up to 5 times.
2. If `InGame` is loaded: runs `Events.ExitToMainMenu()` in `InGame`. That is the call the in-game menu's Exit to Main Menu button makes after its "are you sure" dialog is answered Yes (`InGameTopOptionsMenu.lua` `OnYes`). No dialog, no save. The main menu (`FrontEnd` + `MainMenu`) was back after about 10 s; the tool then waits 3 s more for the menu's `OnShow` (which calls `SetToDefaults`).
3. If `FrontEnd` is not loaded (and no game): returns "not at the main menu".
4. Resolves `MainMenu` by name from a fresh `LSQ:` and runs the setup Lua there (below). It answers `NEWGAME|true|nil` within 0.5 s, and the front end tears down into the load screen.
5. Clicks BEGIN GAME on the load screen (below).
6. Reconnects every 3 s until `InGame` and `GameCore_Tuner` are both listed, then runs the verification Lua in `InGame` and the overview's own difficulty read.
7. If it left a game, logs `left without saving to start a new game: <why>` in that game's broadcast log. On success it refreshes the game identity (which clears the old game's turn high-water mark, pending end turn and blocker flags) and seeds the high-water mark from the verified turn (1), so `end_turn` in the new game does not report a turn regression. Then it binds the broadcast (game directory named after the civ, `meta.json` leader from `pending_leader`) and logs `Game started: <leader>, Deity, Fractal, Standard, Gathering Storm`, which it also returns.

Waits are bounded: 5 connect attempts, 120 s for the menu after leaving a game, 300 s for the BEGIN GAME button, 120 s for `InGame`. On any failure it returns an error naming the step; a settings mismatch leaves the game running for inspection.

### Setup Lua (run in `MainMenu`)

```lua
local ok, err = pcall(function()
GameConfiguration.SetToDefaults();
GameConfiguration.SetRuleSet("RULESET_EXPANSION_2");
GameConfiguration.SetValue("GAME_HANDICAP", DB.MakeHash("DIFFICULTY_DEITY"));
GameConfiguration.SetValue("GAME_SPEED_TYPE", DB.MakeHash("GAMESPEED_STANDARD"));
MapConfiguration.SetScript("Fractal.lua");
MapConfiguration.SetValue("MAP_SIZE", DB.MakeHash("MAPSIZE_STANDARD"));
PlayerConfigurations[0]:SetLeaderTypeName("LEADER_GILGAMESH");
BuildHeadlessGameSetup();
RebuildPlayerParameters(true);
GameSetup_RefreshParameters();
ReleasePlayerParameters();
HideGameSetup();
Network.HostGame(ServerType.SERVER_TYPE_NONE);
end)
print("NEWGAME|" .. tostring(ok) .. "|" .. tostring(err))
print("---END---")
```

A dry run (everything except `HostGame`, configuration dumped after each step) showed the headless rebuild and refresh keep every value: the leader stays `LEADER_GILGAMESH` (civ filled in as `CIVILIZATION_SUMERIA` by the refresh), ruleset, difficulty, speed, script and size are unchanged, and the player count goes from 6 to 8 (the Standard size default). So the leader does not need to be set after the rebuild. Player 0's own handicap still reads Prince in the front end; the engine sets it to Deity when the game starts (checked in game).

### BEGIN GAME

Timeline from `HostGame` (2560x1440 fullscreen): "LOADING, PLEASE WAIT..." until about 20 s, then the leader screen, then the BEGIN GAME button at about 38 to 40 s (`LoadScreen.lua` keeps the button hidden until `LoadGameViewStateDone`, so a visible button is a clickable one).

How it is clicked (`newgame.click_begin_game_sync`):

- The window is captured with PrintWindow and read with Windows OCR every 2 s; capturing does not need focus.
- Windows OCR reads the small-caps label loosely: `BEGIN (GAIME` at screen (1059, 1078). The matcher takes a short line starting with "begin" in the lower half of the window (the leader text above contains "begins your" and "to begin your" inside long lines).
- Positional fallback: (0.414, 0.749) of the client area, used only if the leader screen has been up for 120 s without OCR reading the button. Not needed in any run.
- Input is sent only when the foreground window belongs to a Civ 6 process (its process image name is one of the Civ 6 executables) and is the window being read (same window handle or same process), and the click point lies inside that window's client rect (`newgame.click_target_ok`). Exactly one click, with SendInput, so the real cursor moves there. Otherwise nothing is sent and the tool says to click BEGIN GAME by hand.

Measured (seconds from the tool call): Gilgamesh hosted 2.4, clicked (OCR) 42.6, `InGame` 46.0, tool returned 48.7. Amanitore (from a running game) menu back 10.1, hosted 14.2, clicked 52.2, `InGame` 55.6, returned 58.0. Tomyris hosted 2.4, clicked 40.6, `InGame` 44.0, returned 46.4. The tuner served Lua again within about 3.5 s of the click. No dialogs appeared.

### Verification (run in `InGame`)

`newgame.VERIFY_LUA` prints each setting as the database type whose hash matches the configuration value. Output for the first run (the other two differ only in `leader`: `LEADER_AMANITORE`, `LEADER_TOMYRIS`):

```
NG|ruleset|RULESET_EXPANSION_2        GameConfiguration.GetRuleSet()
NG|favor|true                         Players[me].GetFavor exists (Gathering Storm)
NG|difficulty|DIFFICULTY_DEITY        GAME_HANDICAP
NG|player_difficulty|DIFFICULTY_DEITY PlayerConfigurations[me]:GetHandicapTypeID()
NG|speed|GAMESPEED_STANDARD
NG|map_script|Fractal.lua             MapConfiguration.GetScript()
NG|map_size|MAPSIZE_STANDARD
NG|grid|84x54                         Map.GetGridSize()
NG|leader|LEADER_GILGAMESH
NG|turn|1                             checked: must be 1
```

The same Lua in `GameCore_Tuner` fails (`GetRuleSet` and `GetFavor` are InGame-only). `get_game_overview` then reported `Turn 1 | Sumeria (Gilgamesh) | Score: 0 | Deity`, `Turn 1 | Nubia (Amanitore) | ... | Deity` and `Turn 1 | Scythia (Tomyris) | ... | Deity`, each with a `Favor:` line.

Side effect: each game start makes the game write `AutoConfigGame_NN.Civ6Cfg` and `AutoSave_0001.Civ6Save` in `Saves\Single\auto` (it moves the previous ones to `auto\prev`), as it does for a game started by hand.

### Earlier attempt (Task 1)

`GameConfiguration.SetToDefaults(); Network.HostGame(ServerType.SERVER_TYPE_NONE)` sent to `FrontEnd` also starts a game, but `SetToDefaults()` sets `RULESET_STANDARD`, so that game was base-game (`GetRuleSet()` = `RULESET_STANDARD`, `Players[0].GetFavor` nil, `get_game_overview` failed at its `p:GetFavor()` line). Setting the ruleset explicitly, as above, fixes it.

## Tuner port behaviour

| Moment | `127.0.0.1:4318` | `127.0.0.1:4319` |
|---|---|---|
| Main menu | LISTENING, full Lua tuner | LISTENING |
| Load screen, up to and while BEGIN GAME waits | LISTENING, but nothing is served: TCP connects are accepted into the backlog and sit in CLOSE_WAIT, a handshake gets no reply. Task 1 saw outright refusals here after 5+ min, likely a full backlog | Task 1: accepted TCP but answered nothing |
| Map up | LISTENING, full Lua tuner (99 to 124 states) | Task 1: answers `APP:` with the game identity but `LSQ:` returns no states |
| Leaving a game (`ExitToMainMenu`) | One handshake got no reply mid-teardown; the next one, 4 s later, listed the main menu | not checked |

So 4318 is the only port that serves Lua, and "port open" is not "ready": only a handshake that lists the expected states counts. After BEGIN GAME the stale backlog connections were cleared by the game and the tuner was not wedged. A second `LSQ:` on an open connection returns a fresh list (tag 4, same format as the handshake reply), which is how `GameConnection.query_states` resolves states by name.

The game serves one tuner client at a time. The server's popup watcher reconnects about once a second while the game is at the main menu (it wants `InGame`; 22 connects in 25 s were logged). Cancelling it mid-handshake made the next connect fail once with `[WinError 64] The specified network name is no longer available`; `new_game` therefore pauses the background pollers, disconnects, and retries its first connect.

If the tuner refuses connections at the main menu or once the map is up, restart the game. Upstream also notes the game accepts only one tuner connection at a time (close FireTuner or test scripts before starting the server).

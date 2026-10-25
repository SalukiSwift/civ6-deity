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

Expected output: `serverInfo.name: Civilization VI` and `tool count: 76`, exit 0. The server starts fine with the game closed: the lifespan does not connect eagerly; the popup watcher retries `127.0.0.1:4318` about every 2 s and ignores the refusal. Closing stdin shuts it down cleanly (no leftover `python.exe`, nothing left on :8000).

Against a live game the server logged `Connected: Civ6 ...` and `Discovered 99 Lua states (GameCore=2, InGame=94)`.

### Quit

```bash
taskkill.exe /IM CivilizationVI_DX12.exe /F
```

Nothing needs saving for test runs.

## Lua state lists (verbatim, index name)

Indexes are **not stable**. The game pushes unsolicited `L` messages (tag -1) with the whole list whenever a state is created or destroyed, and it renumbers the list each time. Always resolve states by name after (re)connecting.

### Main menu (32 states)

`FrontEnd` (30) is the front-end UI state that accepted the Play Now call below. `MainMenu` (24) is the main menu screen context (`ContextPtr:IsHidden()` returned `false` there). `GameCore_Tuner` and `InGame` do not exist yet.

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

## Starting a game from the main menu through the tuner

Sent to the `FrontEnd` state at the main menu:

```lua
GameConfiguration.SetToDefaults(); Network.HostGame(ServerType.SERVER_TYPE_NONE)
```

Reply `1`, then `JoiningRoom: OnFinishedGameplayContentConfigure() g_waitingForContentConfigure=true`, then the front-end states are torn down one by one (an `L` push per step) down to `0 Main State, 1 DebugHotloadCache, 2 LoadScreen`. A new map is generated with no clicks.

Two limits seen on this run:

- **Begin Game still needs a click.** The load screen stops at the leader intro with a "BEGIN GAME" button and waits there; a human clicked it.
- **The ruleset is the base game.** `SetToDefaults()` sets `RULESET_STANDARD` (MainMenu.lua says so in a comment near its Play Now handler, which then clears `RULESET` and rebuilds the setup parameters). The resulting game reported `GameConfiguration.GetRuleSet()` = `RULESET_STANDARD`, and `Players[0].GetFavor` was nil, so `get_game_overview` failed at its `p:GetFavor()` line (`function expected instead of nil`). A Gathering Storm game needs the ruleset set explicitly before `HostGame`.

## Tuner port behaviour

| Moment | `127.0.0.1:4318` | `127.0.0.1:4319` |
|---|---|---|
| Main menu | LISTENING, full Lua tuner | not checked |
| Load screen, waiting on BEGIN GAME (checked for 5+ min) | connection refused | LISTENING; accepted TCP but answered nothing (socket sat in CLOSE_WAIT) |
| Map up, no clients connected, checked again 10 s later | LISTENING, full Lua tuner (99 states) | LISTENING; answers `APP:` with the game identity but `LSQ:` returns no states |

So 4318 is the only port that serves Lua. During the load screen it refuses connections, and it comes back once the game starts. 4319 is a separate listener, not a fallback. Our own client was not holding 4318: it had closed at about 20:16:00, refusals continued until at least 20:21:44, and 4318 was listening again at 20:25:01, after BEGIN GAME. The server does not need port discovery, but it must treat refusals during loading as "wait and retry".

If the tuner refuses connections at the main menu or once the map is up, restart the game. Upstream also notes the game accepts only one tuner connection at a time (close FireTuner or test scripts before starting the server); this was not tested here.

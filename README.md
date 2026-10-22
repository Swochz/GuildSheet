# GuildSheet

**Keep your guild's Google Sheet up to date from inside WoW Forever, without typing.**

GuildSheet is an addon that collects your guild's roster, professions and guild bank while you play. A small desktop helper copies that data into your guild's own Google Sheet every time you `/reload` or log out.

```
 In game                         Your PC                       Google
┌──────────────────┐  /reload  ┌──────────────────┐  writes   ┌──────────────────┐
│ GuildSheet addon │ ────────▶ │ GuildSheet.lua   │ ────────▶ │ Your guild sheet │
│ roster, bank,    │  or log   │ (SavedVariables) │  helper   │ "Sync …" tabs    │
│ professions      │  out      │                  │           │                  │
└──────────────────┘           └──────────────────┘           └──────────────────┘
```

## Who needs what

| You are… | You need | Setup |
|---|---|---|
| **The sheet keeper** (one officer) | The addon **and** the helper | About 15 minutes, once. See [Setup](#setup-for-the-sheet-keeper) |
| **A guild member** | Nothing. The addon is optional | Install it from CurseForge or WowUp and you're done |

Only one person in the guild needs GuildSheet. The guild roster already gives everyone's level, class, rank, zone, online status and notes.

### Does it help if more people run the addon?
Yes. Everyone running the addon shares what they know, in the background:

- **Their own professions and race.** Without the addon, professions only show up if Forever has an in-game guild professions list, and race only when the sheet keeper groups with, targets or mouses over that character.
- **What they know about others.** Addon users pass data along, so it reaches the sheet keeper even from members who are never online at the same time. When someone logs in, one addon user who is online sends them everything it knows, and they send back anything it was missing.
- **Guild bank scans.** When any addon user opens the guild bank, the scan (items, gold and gold log) goes to addon users of **the same guild rank or higher** who are online. Lower ranks never receive it, so officer-only tabs stay with officers.

Members running the addon don't need the helper and don't configure anything.

## What ends up in the sheet

The helper creates and fills these tabs. It **never writes to any other tab** in your sheet.

| Tab | Contents |
|---|---|
| **Sync Characters** | Every member: name, player, Main/Alt, class, race, role, level, rank, online and last online, zone, two main professions, notes |
| **Sync Professions** | One row per character and profession, with skill |
| **Sync Guild Bank** | Items per bank tab, stacks summed, with item ID |
| **Sync Gold Log** | Deposits, withdrawals and repairs, collected over time without duplicates |
| **Sync Info** | Last sync, member counts, guild bank gold |

Tab names and headers can be English or Swedish (`"language": "sv"`).

### Player, Main/Alt and role come from guild notes
Write notes like these in the guild window (officers can edit everyone's):

```
Main Anna Tank
Alt Anna heal
Main Erik DPS
```

The helper recognises `Main`/`Alt`, `Tank`/`Heal`/`Healer`/`DPS` and any player name listed in your sheet (see `players_tab` below). The officer note is used if the public note has nothing.

## Privacy
- **WoW addons can't use the internet.** Everything the addon collects stays in your own `WTF` folder.
- Addon messages only go to **your own guild's** channel.
- Data only reaches a spreadsheet if someone in your guild runs the helper with **their own** Google account and sheet. Nothing is ever sent to the author.

## Download
- **Addon:** search "GuildSheet" in CurseForge, or in [WowUp](https://wowup.io/) choose **Install from URL** and paste `https://github.com/Swochz/GuildSheet`. Both update automatically. Manual install: `GuildSheet-<version>.zip` from the [latest release](https://github.com/Swochz/GuildSheet/releases/latest), unzipped into `Interface\AddOns`.
- **Helper:** `GuildSheet-Helper-<version>.zip` from the [latest release](https://github.com/Swochz/GuildSheet/releases/latest).

## Setup for the sheet keeper

### 1. Install the addon
Use one of the options under [Download](#download). If WoW lists it as "out of date", tick **Load out of date AddOns**.

### 2. Install Python and the helper
1. Install [Python 3.10 or newer](https://www.python.org/downloads/). Tick **Add python.exe to PATH**.
2. Unzip `GuildSheet-Helper-<version>.zip` somewhere you'll keep it.
3. In that folder, run:
   ```bash
   pip install -r requirements.txt
   ```

### 3. Give the helper access to your sheet (about 5 minutes)
1. Open the [Google Cloud console](https://console.cloud.google.com/) and create a project (any name).
2. Under **APIs & Services → Library**, enable **Google Sheets API**.
3. Under **APIs & Services → Credentials**, choose **Create credentials → Service account**. Give it a name and create it.
4. Open the service account and go to **Keys → Add key → Create new key → JSON**. Save the file in the helper folder as `service-account.json`.
   **Keep this file private.** Anyone who has it can edit your sheet.
5. Copy the service account's email address (ends in `iam.gserviceaccount.com`). In your Google Sheet, click **Share** and add that address as **Editor**.

### 4. Configure
Copy `config.example.json` to `config.json` and fill it in:

| Setting | What it is |
|---|---|
| `spreadsheet_id` | The long ID in your sheet's URL, between `/d/` and `/edit` |
| `language` | `"en"` or `"sv"`: names and headers of the Sync tabs |
| `tab_prefix` | Default `"Sync "`. The helper only writes to tabs starting with this |
| `players_tab`, `players_column` | Optional. A tab in your sheet with one player name per row, used to recognise names in guild notes |
| `characters_tab`, `characters_columns` | Optional. A tab in your sheet that maps characters to players. The column letters say where player, prio, character, role and alt role are. Used when a character has no note |
| `savedvariables_path` | Leave empty to search the usual WoW folders. Otherwise the full path to `...\WTF\Account\<ACCOUNT>\SavedVariables\GuildSheet.lua` |
| `guild` | Leave empty to use the guild seen most recently, or set a name if your characters are in several guilds |

Tabs you point the helper at with `players_tab` and `characters_tab` are only read, never changed.

### 5. Use it
1. Double-click `start_sync.bat` and leave the window open.
2. Play as usual. Open the guild bank now and then to update the bank tabs.
3. The sheet updates every time you type `/reload` or log out. That's when WoW writes the addon's data to disk.

To check what would be written without touching the sheet:
```bash
python guildsheet_sync.py --dry-run
```

## In-game commands

| Command | What it does |
|---|---|
| `/gsheet` | Shows how many members, races and professions are collected, and when the roster and bank were last scanned |
| `/gsheet scan` | Refreshes the roster and resends your own professions |
| `/gsheet reset` | Clears saved data for your current guild |

## FAQ

**Why doesn't the sheet update instantly?**
WoW only lets addons save data when you `/reload` or log out. The helper picks up the change within a few seconds of that.

**Does the helper need to run while I play?**
Only to update the sheet. If it wasn't running, start it later: it syncs the latest saved data straight away.

**Can it read other players' professions without them having the addon?**
Only if WoW Forever has a guild professions list. Otherwise each player's professions come from their own addon.

**Is this allowed?**
Yes. It only uses the normal addon API, outside combat, and reads its own SavedVariables file, the same way tools like WarcraftLogs and TSM do.

**I deleted `state.json`. Is the gold log lost?**
No. The helper rebuilds it from the Sync Gold Log tab on the next sync.

## Known issues
- **WoW Forever beta:** the client sometimes doesn't load addon data at login. The addon collects it again, and the helper keeps its own copy, so nothing is lost.
- Gold log times are approximate, because the game only reports "3 hours ago" or "2 days ago".

## For developers
- `GuildSheet/`: the addon (Lua)
- `helper/`: the helper (Python)
- `tests/`: runs the addon against a fake WoW API and feeds its output through the helper. `test_sharing.py` runs several clients at once and routes their addon messages between them:
  ```bash
  pip install lupa
  python tests/test_end_to_end.py
  python tests/test_sharing.py
  ```
- **Releasing:** bump `## Version:` in `GuildSheet/GuildSheet.toc`, commit, and push a tag like `v0.3.0` (or `v0.3.0-beta`). GitHub Actions builds both zips, publishes the GitHub release and uploads the addon to CurseForge (needs the `CF_API_KEY` repository secret).

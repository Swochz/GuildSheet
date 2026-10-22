# GuildSheet

Keeps any guild's Google Sheet up to date from inside WoW Forever.

- **`GuildSheet/`**: the in-game addon. Only the person running the helper needs it. Anyone else who installs it also shares their own race and professions with their guild.
- **`helper/`**: a small Python program that copies the addon's data into *your own* sheet.
- **`tests/`**: runs the addon against a fake WoW API, so it could be tested before launch.

## Download
- **Addon:** CurseForge (search "GuildSheet"), or in WowUp choose *Install from URL* and paste `https://github.com/Swochz/GuildSheet`. Both update automatically.
- **Helper:** `GuildSheet-Helper-<version>.zip` from the [latest release](https://github.com/Swochz/GuildSheet/releases/latest).

**Privacy:** the addon has no internet access (WoW addons can't have it). Everything it collects stays in your own `WTF` folder, and addon messages only go to your own guild's channel. Data only reaches a sheet if someone in that guild runs the helper with their own Google account and sheet. Nothing is sent to the addon's author.

The helper **only writes to tabs whose name starts with `Sync `** (Characters, Professions, Guild Bank, Gold Log, Info; Swedish names with `"language": "sv"`). If you point it at your own tabs for player names and characters, it only reads them.

## What ends up in the sheet

| Tab | Where the data comes from |
|---|---|
| Sync Characters | Guild roster (everyone, no addon needed): name, level, class, rank, online, zone, notes. Race and professions: see below. |
| Sync Professions | Own characters plus anyone running the addon. Also the in-game guild professions list, if Forever has one. |
| Sync Guild Bank | Scanned when you open the guild bank (tabs you can view). |
| Sync Gold Log | The bank's gold log, collected over time with no duplicates. Times are approximate because the game only says "3 hours ago". |
| Sync Info | When the last sync ran, member counts, bank gold. |

**Player, Main/Alt and role** are read from the public note (officer note as a fallback), for example `Main Hampus DPS` or `Alt Jessan heal`. The player name has to match a name in the `players_tab` you configure. Without a note, the helper falls back to your `characters_tab` if the character name is filled in there. Both are optional.

**Race** isn't in the guild roster. It's picked up when someone is in your group, targeted or moused over, or when they run the addon.

## Setup (once, on the PC that runs the helper)

### 1. Addon
Copy the `GuildSheet` folder to
`World of Warcraft\<Forever folder>\Interface\AddOns\GuildSheet\`.
If it shows as "out of date" in the AddOns list, tick **Load out of date AddOns**. The `## Interface:` number in `GuildSheet.toc` is a guess until Forever is out; update it to the client's number.

### 2. Python
Install Python 3.10 or newer from python.org (tick "Add to PATH"). Then in the `helper` folder:
```bash
pip install -r requirements.txt
```

### 3. Google access (about 5 minutes)
1. Go to https://console.cloud.google.com/, create a project (any name).
2. Under **APIs & Services → Library**, enable **Google Sheets API**.
3. Under **APIs & Services → Credentials**, choose **Create credentials → Service account**, give it a name and create it.
4. Open the service account, go to **Keys → Add key → JSON**. Save the file in `helper/` as `service-account.json`. **Keep this file private**: it grants access to the sheet.
5. Copy the service account's email (ends with `iam.gserviceaccount.com`) and **share the sheet with it as Editor**.

### 4. Config
Copy `helper/config.example.json` to `helper/config.json` and fill in:

| Setting | What it is |
|---|---|
| `spreadsheet_id` | The long ID in your sheet's URL, between `/d/` and `/edit` |
| `language` | `"en"` or `"sv"`: names and headers of the Sync tabs |
| `tab_prefix` | Default `"Sync "`. The helper writes only to tabs starting with this |
| `players_tab`, `players_column` | Optional. A tab with one player name per row, used to read names from guild notes |
| `characters_tab`, `characters_columns` | Optional. A tab mapping characters to players; the column letters say where player, prio, character, role and alt role are |
| `savedvariables_path` | Leave empty to search the usual WoW folders. Otherwise the full path to `...\WTF\Account\<ACCOUNT>\SavedVariables\GuildSheet.lua` |
| `guild` | Leave empty to use the guild seen most recently, or set a name if your alts are in several guilds |

`config.json`, `service-account.json` and `state.json` are personal. Never share or upload them (`.gitignore` already leaves them out).

## Daily use
1. Double-click `helper/start_sync.bat` (it keeps running).
2. Play as usual. Open the guild bank now and then if you want the bank tabs updated.
3. The sheet updates whenever you type **`/reload`** or log out. That's when the game writes the file.

In game: `/gsheet` shows status, `/gsheet scan` refreshes, `/gsheet reset` clears saved data for the guild.

Without touching the sheet, `python guildsheet_sync.py --dry-run` shows what would be written.

## Good to know
- **Beta bug:** in the beta, the game writes the addon's file but doesn't load it back at login. The helper keeps its own copy (`helper/state.json`), so nothing is lost; data just gets re-collected each session.
- The sheet is only as fresh as the last `/reload` or logout. Anyone else who runs the helper with the same setup also keeps it fresh.
- When it works well after launch, the next step is deciding what to move from the Sync tabs into the real tabs (or point formulas there).

## Testing without the game
```bash
pip install lupa
python tests/test_end_to_end.py
```

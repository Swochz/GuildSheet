# CurseForge upload

## Project settings

| Field | Value |
|---|---|
| Name | GuildSheet (pick another if it's taken, e.g. "GuildSheet Sync") |
| Summary (short) | Keep your guild's Google Sheet up to date: roster, professions and guild bank, collected in-game. |
| Game version | WoW Forever (the client's version) |
| Category | Guild (also: Professions, Data Export) |
| License | MIT or All Rights Reserved, your choice |
| File | `dist/GuildSheet-0.2.0.zip` |
| Release type | Beta (until it's been tested after launch) |

## Description (paste into the description editor)

---

**Keep your guild's spreadsheet up to date without typing.**

GuildSheet collects your guild's roster, professions and guild bank while you play. A small optional desktop helper then copies it into your guild's own Google Sheet.

### Only one person in the guild needs it
The guild roster already gives every member's level, class, rank, zone, online status and notes. So one officer running GuildSheet is enough. Anyone else who installs it shares their race and professions automatically, passes along what it knows about other members, and shares guild bank scans with addon users of the same rank or higher.

### What it collects
- **Roster:** level, class, rank, zone, online and last online, public and officer notes (if you can view them)
- **Main/Alt, player and role** from guild notes, e.g. `Main Anna Tank` or `Alt of Erik, heal`
- **Professions and skill:** your own, guildmates running the addon (also relayed between addon users who are never online together), and the guild professions list if available
- **Race:** picked up from group members, your target and mouseover (only out of combat)
- **Guild bank:** items per tab you can view, gold balance, and the gold log. Scans are shared with addon users of the same rank or higher

### Privacy
WoW addons have no internet access. Everything stays in your own SavedVariables file, and addon messages only go to your own guild's channel. Data only reaches a spreadsheet if someone in your guild runs the helper with their own Google account and sheet. Nothing is ever sent to the author.

### Commands
- `/gsheet`: status
- `/gsheet scan`: refresh the roster and resend your professions
- `/gsheet reset`: clear saved data for your current guild

### The spreadsheet helper (optional)
WoW only writes addon data to disk when you `/reload` or log out. The helper watches that file and updates your Google Sheet each time. It only writes to its own tabs (named "Sync …") and never changes your guild's existing tabs. Download and setup guide: https://github.com/Swochz/GuildSheet

The addon also works on its own as a data collector, without the helper.

### Known issues
- WoW Forever beta: the client sometimes doesn't load saved addon data at login. The addon simply collects again; the helper keeps its own copy, so nothing is lost.
- If the addon shows as "out of date", tick *Load out of date AddOns* until an update is out.

Feedback and bug reports are welcome in the comments.

---

## Upload steps
1. Sign in at https://authors.curseforge.com/ (free account).
2. **Create project**, choose game *World of Warcraft*, then fill in the fields from the table above.
3. Paste the description and add an icon (400×400 px, optional but helps).
4. Go to **Files → Upload file**, choose `dist/GuildSheet-0.2.0.zip`, the Forever game version and release type *Beta*.
5. Submit. Moderators review new projects, usually within a few hours to a couple of days.

## Before you submit
- **Helper link:** points to https://github.com/Swochz/GuildSheet. It only works once the repo is public.
- **Interface number:** if the Forever client reports a different number than `120105`, update `## Interface:` in `GuildSheet.toc` and rebuild the zip.
- **Author:** `GuildSheet.toc` says `## Author: Hampus`. Change it if you'd rather use another name publicly.

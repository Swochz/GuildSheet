"""GuildSheet sync helper.

Watches the GuildSheet addon's SavedVariables file and copies the data into the
guild's Google Sheet. It only ever writes to tabs whose name starts with the
configured prefix ("Sync " by default). Optionally it reads player names and
character -> player from the guild's own tabs, but never writes to them.

    python guildsheet_sync.py            # watch and sync on every /reload or logout
    python guildsheet_sync.py --once     # sync once and exit
    python guildsheet_sync.py --dry-run  # print what would be written, touch nothing
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import re
import string
import sys
import time
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
CONFIG_FILE = HERE / "config.json"
STATE_FILE = HERE / "state.json"
ADDON_FILE = "GuildSheet.lua"


# ---------------------------------------------------------------------------
# SavedVariables (Lua) parser
# ---------------------------------------------------------------------------

class LuaParseError(ValueError):
    pass


class _LuaParser:
    """Parses the subset of Lua that WoW writes into SavedVariables files."""

    def __init__(self, text: str):
        self.s = text
        self.i = 0

    def error(self, msg):
        line = self.s.count("\n", 0, self.i) + 1
        raise LuaParseError(f"{msg} (line {line})")

    def skip(self):
        s, n = self.s, len(self.s)
        while self.i < n:
            c = s[self.i]
            if c in " \t\r\n,;":
                self.i += 1
            elif s.startswith("--", self.i):
                if s.startswith("--[[", self.i):
                    end = s.find("]]", self.i)
                    self.i = n if end < 0 else end + 2
                else:
                    end = s.find("\n", self.i)
                    self.i = n if end < 0 else end + 1
            else:
                break

    def parse_file(self) -> dict:
        out = {}
        while True:
            self.skip()
            if self.i >= len(self.s):
                return out
            name = self.ident()
            self.skip()
            self.expect("=")
            out[name] = self.value()

    def expect(self, ch):
        if not self.s.startswith(ch, self.i):
            self.error(f"expected {ch!r}")
        self.i += len(ch)

    def ident(self):
        m = re.compile(r"[A-Za-z_][A-Za-z0-9_]*").match(self.s, self.i)
        if not m:
            self.error("expected a name")
        self.i = m.end()
        return m.group(0)

    def value(self):
        self.skip()
        s = self.s
        c = s[self.i] if self.i < len(s) else ""
        if c == "{":
            return self.table()
        if c in "\"'":
            return self.string()
        if c == "-" or c.isdigit() or c == ".":
            return self.number()
        word = self.ident()
        if word == "true":
            return True
        if word == "false":
            return False
        if word == "nil":
            return None
        self.error(f"unexpected {word!r}")

    def number(self):
        m = re.compile(r"-?(0[xX][0-9a-fA-F]+|(\d+\.?\d*|\.\d+)([eE][-+]?\d+)?|inf|nan)").match(self.s, self.i)
        if not m:
            self.error("bad number")
        self.i = m.end()
        txt = m.group(0)
        if "x" in txt.lower():
            return int(txt, 16)
        f = float(txt)
        return int(f) if f.is_integer() and not re.search(r"[.eE]", txt) else f

    _ESCAPES = {"n": "\n", "t": "\t", "r": "\r", "\\": "\\", '"': '"', "'": "'", "\n": "\n", "a": "\a", "b": "\b", "f": "\f", "v": "\v"}

    def string(self):
        q = self.s[self.i]
        self.i += 1
        out = bytearray()
        s = self.s
        while True:
            if self.i >= len(s):
                self.error("unterminated string")
            c = s[self.i]
            if c == q:
                self.i += 1
                return out.decode("utf-8", errors="replace")
            if c == "\\":
                nxt = s[self.i + 1]
                if nxt.isdigit():  # \ddd decimal byte
                    m = re.compile(r"\d{1,3}").match(s, self.i + 1)
                    out.append(int(m.group(0)) & 0xFF)
                    self.i = m.end()
                    continue
                out += self._ESCAPES.get(nxt, nxt).encode("utf-8")
                self.i += 2
                continue
            out += c.encode("utf-8")
            self.i += 1

    def table(self):
        self.expect("{")
        d, auto = {}, 1
        while True:
            self.skip()
            if self.s.startswith("}", self.i):
                self.i += 1
                break
            if self.s.startswith("[", self.i):
                self.i += 1
                key = self.value()
                self.skip()
                self.expect("]")
                self.skip()
                self.expect("=")
                d[key] = self.value()
            else:
                m = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\s*=(?!=)").match(self.s, self.i)
                if m:
                    key = self.ident()
                    self.skip()
                    self.expect("=")
                    d[key] = self.value()
                else:
                    d[auto] = self.value()
                    auto += 1
        # Lua arrays come out as Python lists
        if d and all(isinstance(k, int) for k in d) and sorted(d) == list(range(1, len(d) + 1)):
            return [d[k] for k in range(1, len(d) + 1)]
        return d


def parse_saved_variables(text: str) -> dict:
    return _LuaParser(text).parse_file()


def as_list(v):
    """Lua tables with holes (e.g. guild bank tab 2 missing) come out as dicts."""
    if v is None:
        return []
    if isinstance(v, list):
        return v
    return [v[k] for k in sorted(v, key=lambda k: (not isinstance(k, int), k))]


# ---------------------------------------------------------------------------
# Config and file discovery
# ---------------------------------------------------------------------------

def load_config() -> dict:
    if not CONFIG_FILE.exists():
        sys.exit(f"Missing {CONFIG_FILE.name}. Copy config.example.json to config.json and fill it in.")
    cfg = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
    apply_defaults(cfg)
    if not cfg["tab_prefix"].strip():
        sys.exit("tab_prefix can't be empty: it is what keeps the helper away from the guild's own tabs.")
    return cfg


def apply_defaults(cfg: dict) -> dict:
    cfg.setdefault("tab_prefix", "Sync ")
    cfg.setdefault("poll_seconds", 5)
    cfg.setdefault("language", "en")
    cfg.setdefault("players_tab", "")
    cfg.setdefault("players_column", "A")
    cfg.setdefault("characters_tab", "")
    cfg.setdefault("characters_columns", {})
    if cfg["language"] not in TEXT:
        sys.exit(f"language must be one of: {', '.join(TEXT)}")
    return cfg


def find_saved_variables(cfg: dict) -> Path | None:
    if cfg.get("savedvariables_path"):
        return Path(cfg["savedvariables_path"])
    roots = []
    for drive in string.ascii_uppercase:
        base = f"{drive}:/"
        if not os.path.exists(base):
            continue
        for sub in ("Program Files (x86)/World of Warcraft", "Program Files/World of Warcraft",
                    "World of Warcraft", "Games/World of Warcraft", "Battle.net/World of Warcraft"):
            roots.append(base + sub)
    hits = []
    for root in roots:
        hits += glob.glob(f"{root}/*/WTF/Account/*/SavedVariables/{ADDON_FILE}")
    if not hits:
        return None
    return Path(max(hits, key=os.path.getmtime))


# ---------------------------------------------------------------------------
# Merging new data into the helper's own cache
# ---------------------------------------------------------------------------
# The cache keeps data the addon no longer has (e.g. the Forever beta bug where
# saved data isn't loaded back at login, or a member who ran the addon last week).

def pick_guild(sv: dict, wanted: str | None) -> tuple[str, dict] | None:
    guilds = (sv.get("GuildSheetDB") or {}).get("guilds") or {}
    if not guilds:
        return None
    if wanted:
        return (wanted, guilds[wanted]) if wanted in guilds else None
    name = max(guilds, key=lambda g: guilds[g].get("updated") or 0)
    return name, guilds[name]


def _newer(a: dict | None, b: dict | None, *fields) -> bool:
    def ts(x):
        return max((x or {}).get(f) or 0 for f in fields)
    return ts(b) >= ts(a)


def merge_state(state: dict, guild_name: str, g: dict) -> dict:
    st = state.setdefault(guild_name, {"roster": {}, "chars": {}, "guildProfs": {}, "bank": {}, "moneyLog": []})

    if (g.get("rosterUpdated") or 0) >= (st.get("rosterUpdated") or 0) and g.get("roster"):
        st["roster"] = g["roster"]
        st["rosterUpdated"] = g.get("rosterUpdated")

    for name, c in (g.get("chars") or {}).items():
        old = st["chars"].get(name)
        merged = dict(old or {})
        if c.get("race") and _newer(old, c, "raceSeen", "updated"):
            merged["race"], merged["raceFile"] = c.get("race"), c.get("raceFile")
            merged["raceSeen"] = c.get("raceSeen") or c.get("updated")
        if c.get("source") == "addon" and (c.get("updated") or 0) >= (merged.get("updated") or 0):
            merged["profs"] = as_list(c.get("profs"))
            merged["updated"] = c.get("updated")
            merged["source"] = "addon"
        merged.setdefault("source", c.get("source"))
        st["chars"][name] = merged

    if g.get("guildProfs") and (g.get("guildProfsUpdated") or 0) >= (st.get("guildProfsUpdated") or 0):
        st["guildProfs"] = g["guildProfs"]
        st["guildProfsUpdated"] = g.get("guildProfsUpdated")

    bank = g.get("bank") or {}
    if bank.get("money") is not None and (bank.get("moneyUpdated") or 0) >= (st["bank"].get("moneyUpdated") or 0):
        st["bank"]["money"] = bank["money"]
        st["bank"]["moneyUpdated"] = bank.get("moneyUpdated")
    tabs = st["bank"].setdefault("tabs", {})
    for idx, tab in enumerate(as_list(bank.get("tabs")), start=1):
        if tab and (tab.get("updated") or 0) >= (tabs.get(str(idx), {}).get("updated") or 0):
            tabs[str(idx)] = tab

    if bank.get("moneyLog") and bank.get("moneyLogScanned"):
        st["moneyLog"] = merge_money_log(st.get("moneyLog") or [], as_list(bank["moneyLog"]), bank["moneyLogScanned"])

    st["updated"] = max(st.get("updated") or 0, g.get("updated") or 0)
    st["updatedBy"] = g.get("updatedBy") or st.get("updatedBy")
    return st


# How far off a log entry's time can be, by how the game reported it ("3 days ago").
_LOG_TOLERANCE = {"h": 2 * 3600, "d": 36 * 3600, "m": 45 * 86400, "y": 400 * 86400}


def merge_money_log(existing: list, scanned: list, scanned_at: int) -> list:
    """The in-game log is a sliding window with fuzzy times. Add only entries we haven't stored."""
    result = list(existing)
    used = set()
    for e in scanned:
        t = scanned_at - int(e.get("hoursAgo") or 0) * 3600
        tol = _LOG_TOLERANCE.get(e.get("unit"), 2 * 3600)
        match = None
        for idx, old in enumerate(result):
            if idx in used:
                continue
            if (old["type"], old["name"], old["amount"]) == (e.get("type"), e.get("name"), e.get("amount")) \
                    and abs(old["time"] - t) <= tol + old.get("tol", 0):
                match = idx
                break
        if match is None:
            result.append({"type": e.get("type"), "name": e.get("name"), "amount": e.get("amount"), "time": t, "tol": tol})
            used.add(len(result) - 1)
        else:
            used.add(match)
            if tol < result[match].get("tol", tol):  # a newer scan is more exact; keep its time
                result[match]["time"], result[match]["tol"] = t, tol
    result.sort(key=lambda x: x["time"])
    return result


# ---------------------------------------------------------------------------
# Building the rows for each Sync tab
# ---------------------------------------------------------------------------

def short(name: str) -> str:
    return (name or "").split("-")[0]


def fmt_time(ts) -> str:
    return datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M") if ts else ""


def gold(copper) -> float:
    return round((copper or 0) / 10000, 2)


_ROLE_WORDS = {"tank": "Tank", "heal": "Healer", "healer": "Healer", "healz": "Healer", "dps": "DPS"}
_PRIO_WORDS = {"main": "Main", "alt": "Alt", "twink": "Alt"}


def parse_note(note: str, players: list[str]) -> dict:
    """Reads e.g. "Main Hampus DPS" or "Alt av Jessan - heal" from a guild note."""
    out = {}
    if not note:
        return out
    low = note.lower()
    words = re.findall(r"[a-zåäöæø]+", low)
    roles = []
    for w in words:
        if w in _PRIO_WORDS and "prio" not in out:
            out["prio"] = _PRIO_WORDS[w]
        if w in _ROLE_WORDS and _ROLE_WORDS[w] not in roles:
            roles.append(_ROLE_WORDS[w])
    if roles:
        out["roll"] = roles[0]
        if len(roles) > 1:
            out["altRoll"] = roles[1]
    # longest player name first, so "Oscar E" wins over "Oscar"
    for p in sorted(players, key=len, reverse=True):
        if re.search(r"(?<![a-zåäö])" + re.escape(p.lower()) + r"(?![a-zåäö])", low):
            out["spelare"] = p
            break
    return out


TEXT = {
    "en": {
        "tabs": {"chars": "Characters", "profs": "Professions", "bank": "Guild Bank", "log": "Gold Log", "info": "Info"},
        "chars_head": ["Character", "Player", "Main/Alt", "Class", "Race", "Role", "Alt. role", "Level", "Rank", "Status",
                       "Last online", "Zone", "Profession 1", "Skill 1", "Profession 2", "Skill 2", "Public note",
                       "Officer note", "Player from", "Professions from"],
        "profs_head": ["Player", "Character", "Profession", "Skill", "Max", "Source", "Updated"],
        "bank_head": ["Item", "Count", "Tab", "Item ID", "Scanned"],
        "log_head": ["Date (approx.)", "Player", "Type", "Gold", "Accuracy", "Time (unix)"],
        "info_head": ["Sync", "Value"],
        "online": "Online", "offline": "Offline",
        "src_note": "Public note", "src_officer": "Officer note", "src_sheet": "Tab {}",
        "src_addon": "Addon", "src_guildlist": "Guild list",
        "bank_tab": "Tab {}",
        "log_types": {"deposit": "Deposit", "withdraw": "Withdrawal", "repair": "Repair", "withdrawForTab": "Withdrawal (tab)",
                      "buyTab": "Bought tab", "depositSummary": "Deposit (summary)"},
        "acc_hour": "± 1 h", "acc_day": "± 1 day", "acc_rough": "rough",
        "info": {"note": ("Note", "Written by the GuildSheet helper. Anything typed here is replaced on the next sync."),
                 "last_sync": "Last sync", "guild": "Guild", "data_from": "Data from", "roster": "Roster scanned",
                 "members": "Members", "online": "Online at scan", "addon_users": "Running the addon",
                 "bank_gold": "Guild bank gold", "bank_scanned": "Guild bank scanned", "file": "Source file"},
    },
    "sv": {
        "tabs": {"chars": "Karaktärer", "profs": "Yrken", "bank": "Guildbank", "log": "Guldlogg", "info": "Info"},
        "chars_head": ["Karaktär", "Spelare", "Prio", "Klass", "Ras", "Roll", "Alt. roll", "Level", "Rank", "Status",
                       "Senast online", "Zon", "Yrke 1", "Skill 1", "Yrke 2", "Skill 2", "Publik note", "Officersnote",
                       "Källa (spelare)", "Källa (yrken)"],
        "profs_head": ["Spelare", "Karaktär", "Yrke", "Skill", "Max", "Källa", "Uppdaterad"],
        "bank_head": ["Föremål", "Antal", "Flik", "Item-ID", "Skannad"],
        "log_head": ["Datum (ca)", "Spelare", "Typ", "Guld", "Exakthet", "Tid (unix)"],
        "info_head": ["Synk", "Värde"],
        "online": "Online", "offline": "Offline",
        "src_note": "Note", "src_officer": "Officersnote", "src_sheet": "Fliken {}",
        "src_addon": "Addon", "src_guildlist": "Guildlistan",
        "bank_tab": "Flik {}",
        "log_types": {"deposit": "Insättning", "withdraw": "Uttag", "repair": "Reparation", "withdrawForTab": "Uttag (flik)",
                      "buyTab": "Köpt flik", "depositSummary": "Insättning (summa)"},
        "acc_hour": "± 1 h", "acc_day": "± 1 dag", "acc_rough": "grov",
        "info": {"note": ("Obs", "Skrivs av GuildSheet-hjälpprogrammet. Det som skrivs här ersätts vid nästa synk."),
                 "last_sync": "Senaste synk", "guild": "Guild", "data_from": "Data från", "roster": "Roster skannad",
                 "members": "Medlemmar", "online": "Online vid skanning", "addon_users": "Kör addonen",
                 "bank_gold": "Guldbank, saldo (guld)", "bank_scanned": "Guldbank skannad", "file": "Källfil"},
    },
}


def build_tabs(guild_name: str, st: dict, players: list[str], known: dict, source_file: str,
               lang: str = "en", characters_tab: str = "") -> dict[str, list[list]]:
    """Returns {tab id: rows}. `known` maps character name -> {spelare, prio, roll, altRoll} from the guild's own character tab."""
    T = TEXT[lang]
    roster = {k: v for k, v in (st.get("roster") or {}).items() if v.get("inGuild", True)}
    chars = st.get("chars") or {}
    gprofs = st.get("guildProfs") or {}

    def profs_for(name):
        c = chars.get(name) or {}
        if c.get("profs"):
            return [(p["name"], p.get("skill") or 0, p.get("max") or 0) for p in as_list(c["profs"])], T["src_addon"], c.get("updated")
        if name in gprofs:
            return [(p, s, 0) for p, s in sorted(gprofs[name].items())], T["src_guildlist"], st.get("guildProfsUpdated")
        return [], "", None

    # Characters
    rows = []
    for name, r in roster.items():
        info = parse_note(r.get("note") or "", players)
        officer = parse_note(r.get("officerNote") or "", players)
        sheet = known.get(short(name).lower(), {})
        src = (T["src_note"] if info.get("spelare") else T["src_officer"] if officer.get("spelare")
               else T["src_sheet"].format(characters_tab) if sheet.get("spelare") else "")
        pick = lambda key: info.get(key) or officer.get(key) or sheet.get(key) or ""
        profs, psrc, _ = profs_for(name)
        main = [p for p in profs if p[0] not in ("Cooking", "First Aid", "Fishing", "Archaeology")] or profs
        p1 = main[0] if len(main) > 0 else ("", "", 0)
        p2 = main[1] if len(main) > 1 else ("", "", 0)
        rows.append([
            short(name), pick("spelare"), pick("prio"), r.get("class") or "", (chars.get(name) or {}).get("race") or "",
            pick("roll"), pick("altRoll"), r.get("level") or "", r.get("rank") or "",
            T["online"] if r.get("online") else T["offline"], "" if r.get("online") else fmt_time(r.get("lastOnline")),
            r.get("zone") or "", p1[0], p1[1], p2[0], p2[1], r.get("note") or "", r.get("officerNote") or "", src, psrc,
        ])
    prio_order = {"Main": 0, "Alt": 1}
    rows.sort(key=lambda x: (x[1] == "", x[1].lower(), prio_order.get(x[2], 2), -(x[7] or 0), x[0]))
    characters = [T["chars_head"]] + rows

    # Professions: one row per character and profession
    yrows = []
    for name in set(roster) | {n for n in chars if chars[n].get("profs")} | set(gprofs):
        if name not in roster and roster:
            continue  # not in the guild (any more)
        r = roster.get(name, {})
        info = parse_note(r.get("note") or "", players) or parse_note(r.get("officerNote") or "", players)
        spelare = info.get("spelare") or known.get(short(name).lower(), {}).get("spelare", "")
        profs, psrc, ts = profs_for(name)
        for p, skill, mx in profs:
            yrows.append([spelare, short(name), p, skill, mx or "", psrc, fmt_time(ts)])
    yrows.sort(key=lambda x: (x[0] == "", x[0].lower(), x[1], x[2]))
    professions = [T["profs_head"]] + yrows

    # Guild bank: items summed per tab
    bank = st.get("bank") or {}
    gb = [T["bank_head"]]
    for idx in sorted(bank.get("tabs") or {}, key=int):
        tab = bank["tabs"][idx]
        totals: dict[tuple, int] = {}
        for it in as_list(tab.get("items")):
            m = re.search(r"\|Hitem:(\d+)[^|]*\|h\[([^\]]*)\]", it.get("link") or "")
            if m:
                item_id, item_name = m.group(1), m.group(2)
            elif it.get("id"):  # shared by another addon user; the name wasn't loaded in their client yet
                item_id, item_name = str(it["id"]), f"item:{it['id']}"
            else:
                item_id, item_name = "", it.get("link") or "?"
            totals[(item_name, item_id)] = totals.get((item_name, item_id), 0) + (it.get("count") or 1)
        for (item_name, item_id), count in sorted(totals.items()):
            gb.append([item_name, count, tab.get("name") or T["bank_tab"].format(idx), item_id, fmt_time(tab.get("updated"))])

    # Gold log
    gl = [T["log_head"]]
    for e in st.get("moneyLog") or []:
        tol = e.get("tol", 0)
        exact = T["acc_hour"] if tol <= 2 * 3600 else T["acc_day"] if tol <= 36 * 3600 else T["acc_rough"]
        gl.append([fmt_time(e["time"]), short(e["name"]), T["log_types"].get(e["type"], e["type"]), gold(e["amount"]), exact, e["time"]])

    # Info
    I = T["info"]
    online = sum(1 for r in roster.values() if r.get("online"))
    addon_users = sum(1 for c in chars.values() if c.get("source") == "addon")
    info = [
        T["info_head"],
        list(I["note"]),
        [I["last_sync"], fmt_time(time.time())],
        [I["guild"], guild_name],
        [I["data_from"], f"{short(st.get('updatedBy') or '')}, {fmt_time(st.get('updated'))}"],
        [I["roster"], fmt_time(st.get("rosterUpdated"))],
        [I["members"], len(roster)],
        [I["online"], online],
        [I["addon_users"], addon_users],
        [I["bank_gold"], gold(bank.get("money")) if bank.get("money") is not None else ""],
        [I["bank_scanned"], fmt_time(bank.get("moneyUpdated"))],
        [I["file"], source_file],
    ]

    return {"chars": characters, "profs": professions, "bank": gb, "log": gl, "info": info}


def col_index(letter: str) -> int:
    """'A' -> 0, 'C' -> 2, 'AA' -> 26"""
    n = 0
    for ch in letter.strip().upper():
        n = n * 26 + ord(ch) - 64
    return n - 1


# ---------------------------------------------------------------------------
# Google Sheets
# ---------------------------------------------------------------------------

class Sheet:
    def __init__(self, cfg: dict):
        import gspread  # imported here so --dry-run works without it
        self.gspread = gspread
        self.cfg = cfg
        self.prefix = cfg["tab_prefix"]
        self.T = TEXT[cfg["language"]]
        self.sh = gspread.service_account(filename=str(HERE / cfg["service_account_file"])).open_by_key(cfg["spreadsheet_id"])

    def _rows(self, title: str) -> list[list[str]]:
        if not title:
            return []
        try:
            return self.sh.worksheet(title).get_all_values()
        except self.gspread.WorksheetNotFound:
            print(f"Tab {title!r} not found; skipping it.")
            return []

    def read_players(self) -> list[str]:
        """Player names (people, not characters) from an optional tab in the guild's own sheet (read only)."""
        c = col_index(self.cfg["players_column"])
        rows = self._rows(self.cfg["players_tab"])[1:]
        return [r[c].strip() for r in rows if len(r) > c and r[c].strip()]

    def read_known_characters(self) -> dict:
        """Character -> player/prio/role from an optional tab in the guild's own sheet (read only)."""
        cols = {k: col_index(v) for k, v in self.cfg["characters_columns"].items() if v}
        if "character" not in cols:
            return {}
        known = {}
        for r in self._rows(self.cfg["characters_tab"])[1:]:
            get = lambda key: r[cols[key]].strip() if key in cols and len(r) > cols[key] else ""
            char = get("character")
            if char and char.lower() != "tbd":
                known[char.lower()] = {"spelare": get("player"), "prio": get("prio"), "roll": get("role"), "altRoll": get("alt_role")}
        return known

    def write_tab(self, tab_id: str, rows: list[list]):
        title = self.prefix + self.T["tabs"][tab_id]
        if not title.startswith(self.prefix):  # never touch the guild's own tabs
            raise RuntimeError(f"refusing to write to {title!r}")
        width = max(len(r) for r in rows)
        rows = [r + [""] * (width - len(r)) for r in rows]
        try:
            ws = self.sh.worksheet(title)
        except self.gspread.WorksheetNotFound:
            ws = self.sh.add_worksheet(title=title, rows=max(len(rows) + 50, 100), cols=width)
            ws.freeze(rows=1)
            ws.format("1:1", {"textFormat": {"bold": True}})
        if ws.row_count < len(rows) or ws.col_count < width:
            ws.resize(rows=max(ws.row_count, len(rows) + 50), cols=max(ws.col_count, width))
        ws.clear()
        # RAW so a guild note starting with "=" is never run as a formula
        ws.update(values=rows, range_name="A1", value_input_option="RAW")

    def read_money_log(self) -> list:
        """Restores the gold log from the sheet if state.json was lost."""
        try:
            rows = self.sh.worksheet(self.prefix + self.T["tabs"]["log"]).get_all_values()
        except self.gspread.WorksheetNotFound:
            return []
        back = {v: k for k, v in self.T["log_types"].items()}
        out = []
        for r in rows[1:]:
            try:
                out.append({"type": back.get(r[2], r[2]), "name": r[1], "amount": round(float(r[3]) * 10000),
                            "time": int(r[5]), "tol": 2 * 3600 if r[4] == self.T["acc_hour"] else 36 * 3600})
            except (IndexError, ValueError):
                pass
        return out


# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------

def load_state() -> dict:
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    return {}


def save_state(state: dict):
    tmp = STATE_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(STATE_FILE)


def sync_once(cfg: dict, sv_path: Path, sheet: Sheet | None, dry_run: bool) -> bool:
    sv = parse_saved_variables(sv_path.read_text(encoding="utf-8", errors="replace"))
    picked = pick_guild(sv, cfg.get("guild"))
    if not picked:
        print("No guild data in the file yet. Log in on a guild character, wait a minute, then /reload.")
        return False
    guild_name, g = picked

    state = load_state()
    if guild_name not in state and sheet:
        state[guild_name] = {"roster": {}, "chars": {}, "guildProfs": {}, "bank": {}, "moneyLog": sheet.read_money_log()}
    st = merge_state(state, guild_name, g)

    players = sheet.read_players() if sheet else cfg.get("players", [])
    known = sheet.read_known_characters() if sheet else {}
    tabs = build_tabs(guild_name, st, players, known, str(sv_path), cfg["language"], cfg["characters_tab"])

    if dry_run:
        for tab_id, rows in tabs.items():
            print(f"\n== {cfg['tab_prefix']}{TEXT[cfg['language']]['tabs'][tab_id]} ({len(rows) - 1} rows) ==")
            for r in rows[:15]:
                print("  " + " | ".join(str(c) for c in r))
        return True

    for tab_id, rows in tabs.items():
        sheet.write_tab(tab_id, rows)
    save_state(state)
    print(f"{datetime.now():%H:%M:%S} Synced {guild_name}: {len(tabs['chars']) - 1} characters, "
          f"{len(tabs['profs']) - 1} professions, {len(tabs['bank']) - 1} bank items.")
    return True


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--once", action="store_true", help="sync once and exit")
    ap.add_argument("--dry-run", action="store_true", help="print what would be written; don't touch the sheet")
    ap.add_argument("--file", help="path to GuildSheet.lua (overrides config and auto-search)")
    args = ap.parse_args()

    cfg = load_config() if not (args.dry_run and not CONFIG_FILE.exists()) else apply_defaults({})
    sv_path = Path(args.file) if args.file else find_saved_variables(cfg)
    if not sv_path or not sv_path.exists():
        sys.exit("Couldn't find GuildSheet.lua. Log in once with the addon enabled and /reload, "
                 "or set savedvariables_path in config.json.")
    print(f"Reading {sv_path}")

    # A dry run still reads player names from the sheet when it can, it just never writes.
    can_read = CONFIG_FILE.exists() and (HERE / cfg.get("service_account_file", "")).is_file()
    sheet = Sheet(cfg) if (not args.dry_run or can_read) else None
    if args.once or args.dry_run:
        sync_once(cfg, sv_path, sheet, args.dry_run)
        return

    print("Watching for changes. In game: /reload or log out to send new data. Ctrl+C to stop.")
    last = None
    while True:
        try:
            mtime = sv_path.stat().st_mtime
            if mtime != last:
                time.sleep(1)  # let the game finish writing
                sync_once(cfg, sv_path, sheet, False)
                last = mtime
        except LuaParseError as e:
            print(f"Couldn't read the file yet ({e}); trying again.")
        except Exception as e:  # keep running through network hiccups
            print(f"Sync failed: {e}")
            last = None
        time.sleep(cfg["poll_seconds"])


if __name__ == "__main__":
    main()

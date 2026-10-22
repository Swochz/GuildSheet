"""Runs the addon against a fake WoW API, then feeds its saved data through the helper.

Needs the `lupa` package (pip install lupa) for a Lua 5.1 runtime, the same Lua version WoW uses.
    python tests/test_end_to_end.py
"""

import sys
from pathlib import Path

import lupa.lua51 as lupa

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "helper"))
import guildsheet_sync as gs  # noqa: E402

PLAYERS = ["Oscar E", "Hampus", "Robin", "Jessan", "Ludde", "Robert", "Oscar B"]


def new_world():
    lua = lupa.LuaRuntime(unpack_returned_tuples=True)
    lua.execute((ROOT / "tests" / "wow_mock.lua").read_text(encoding="utf-8"))
    lua.execute(r"""
        MOCK.roster = {
          { name="Humlesnurr-Testrealm", rank="GM", rankIndex=0, level=12, class="Mage", classFile="MAGE",
            zone="Dun Morogh", note="Main Hampus DPS", officerNote="", online=true, guid="P-1" },
          { name="Barkbit-Testrealm", rank="Officer", rankIndex=1, level=10, class="Druid", classFile="DRUID",
            zone="Teldrassil", note="Main Jessan tank/heal", officerNote="", online=false, guid="P-2",
            lastOnline={0,0,1,5} },
          { name="Plaster-Testrealm", rank="Member", rankIndex=2, level=8, class="Priest", classFile="PRIEST",
            zone="Elwynn Forest", note="alt jessan", officerNote="", online=true, guid="P-3" },
          { name="Robinhood-Testrealm", rank="Member", rankIndex=2, level=11, class="Hunter", classFile="HUNTER",
            zone="Loch Modan", note="", officerNote="Main Robin", online=false, guid="P-4" },
          { name="Luddeknas-Testrealm", rank="Member", rankIndex=2, level=5, class="Warrior", classFile="WARRIOR",
            zone="Durotar", note="=IMPORTXML(\"x\")", officerNote="", online=false, guid="P-5" },
          { name="Oscarsson-Testrealm", rank="Member", rankIndex=2, level=3, class="Rogue", classFile="ROGUE",
            zone="Elwynn Forest", note="Oscar E main", officerNote="", online=true, guid="P-6" },
        }
        MOCK.professions = { {"Skinning", 75, 150}, {"Tailoring", 60, 150}, {"Cooking", 30, 75} }
        MOCK.bank.tabs = {
          { name="Material", slots = {
              [1] = { link="|cffffffff|Hitem:2589::::::::12:::::|h[Linen Cloth]|h|r", count=20 },
              [7] = { link="|cffffffff|Hitem:2589::::::::12:::::|h[Linen Cloth]|h|r", count=5 },
              [9] = { link="|cff1eff00|Hitem:6070::::::::12:::::|h[Wolfshead Helm]|h|r", count=1 } } },
          { name="Officer", viewable=false, slots = {} },
        }
        MOCK.bank.money = 1234567
        MOCK.bank.log = {
          { "deposit", "Barkbit", 50000, 0, 0, 0, 3 },
          { "withdraw", "Humlesnurr", 10000, 0, 0, 2, 0 },
          { "deposit", "Barkbit", 50000, 0, 0, 0, 3 },   -- same deposit twice: both must be kept
        }
    """)
    addon = (ROOT / "GuildSheet" / "GuildSheet.lua").read_text(encoding="utf-8")
    lua.execute("local f = assert(loadstring(...)); f('GuildSheet', {})", addon)
    return lua


def check(cond, msg):
    if not cond:
        raise AssertionError(msg)
    print("  ok  " + msg)


def run_session(lua):
    lua.execute("""
        MOCK.fire('PLAYER_LOGIN')
        MOCK.fire('GUILD_ROSTER_UPDATE'); MOCK.fire('GUILD_ROSTER_UPDATE')  -- bursts are normal
        MOCK.advance(12)
        MOCK.fire('CHAT_MSG_ADDON', 'GSHEET', 'C^1^Night Elf^NightElf^Herbalism:120:150,Skinning:90:150', 'GUILD', 'Barkbit-Testrealm')
        MOCK.fire('CHAT_MSG_ADDON', 'GSHEET', 'C^1^Night Elf^NightElf^Herbalism:1:1', 'WHISPER', 'Faker-Testrealm')
        MOCK.fire('CHAT_MSG_ADDON', 'OTHER', 'C^1^Orc^Orc^', 'GUILD', 'Robinhood-Testrealm')
        MOCK.sentBeforeHello = #MOCK.sent
        MOCK.fire('CHAT_MSG_ADDON', 'GSHEET', 'H^1', 'GUILD', 'Oscarsson-Testrealm')
        MOCK.advance(10)
        MOCK.units.target = { name='Plaster', realm=nil, race='Gnome', raceFile='Gnome', guild=true }
        MOCK.fire('PLAYER_TARGET_CHANGED')
        MOCK.inCombat = true
        MOCK.units.target = { name='Robinhood', realm=nil, race='Dwarf', raceFile='Dwarf', guild=true }
        MOCK.fire('PLAYER_TARGET_CHANGED')
        MOCK.inCombat = false
        MOCK.units.mouseover = { name='Stranger', realm='Otherrealm', race='Human', raceFile='Human', guild=false }
        MOCK.fire('UPDATE_MOUSEOVER_UNIT')
        MOCK.fire('PLAYER_INTERACTION_MANAGER_FRAME_SHOW', 10)
        MOCK.advance(5)
        MOCK.fire('GUILDBANKLOG_UPDATE')
        MOCK.fire('PLAYER_INTERACTION_MANAGER_FRAME_HIDE', 10)
        SlashCmdList.GUILDSHEET('')
    """)


def main():
    print("Addon in fake WoW:")
    lua = new_world()
    run_session(lua)
    G = lua.globals()
    db = G.GuildSheetDB.guilds["Testgilla"]

    check(db.roster["Humlesnurr-Testrealm"].level == 12, "roster scanned")
    check(db.roster["Barkbit-Testrealm"].lastOnline is not None, "last online computed for offline member")
    check(db.chars["Humlesnurr-Testrealm"].race == "Gnome", "own race recorded")
    check(len(list(db.chars["Humlesnurr-Testrealm"].profs.values())) == 3, "own professions recorded")
    check(db.chars["Barkbit-Testrealm"].profs[1].name == "Herbalism", "professions received from another addon user")
    check(db.chars["Faker-Testrealm"] is None, "whispered addon messages ignored")
    check(db.chars["Robinhood-Testrealm"] is None, "in-combat target and other prefixes ignored")
    check(db.chars["Plaster-Testrealm"].race == "Gnome", "race picked up from target")
    check(db.chars["Stranger-Otherrealm"] is None, "non-guild units ignored")
    sent = [G.MOCK.sent[i].msg for i in range(1, len(G.MOCK.sent) + 1)]
    check(any(m.startswith("H^1") for m in sent), "hello sent on login")
    check(any(m.startswith("C^1^Gnome^Gnome^") and "Skinning:75:150" in m for m in sent), "own data broadcast")
    check(len(sent) > G.MOCK.sentBeforeHello, "replied to someone else's hello")
    check(db.bank.money == 1234567, "guild bank gold read")
    check(len(list(db.bank.tabs[1]["items"].values())) == 3, "viewable bank tab scanned")
    check(db.bank.tabs[2] is None, "non-viewable bank tab skipped")
    check(len(list(db.bank.moneyLog.values())) == 3, "money log read")
    check(any("6 members" in G.MOCK.printed[i] for i in range(1, len(G.MOCK.printed) + 1)), "/gsheet status works")

    sv_text = lua.eval("MOCK.serialize('GuildSheetDB', GuildSheetDB)")
    out = ROOT / "tests" / "out_GuildSheet.lua"
    out.write_text(sv_text, encoding="utf-8")

    print("\nHelper reading the saved file:")
    sv = gs.parse_saved_variables(sv_text)
    name, g = gs.pick_guild(sv, None)
    check(name == "Testgilla", "guild picked from saved file")
    state = {}
    st = gs.merge_state(state, name, g)
    known = {"humlesnurr": {"spelare": "Hampus", "prio": "Main", "roll": "DPS", "altRoll": ""}}
    tabs = gs.build_tabs(name, st, PLAYERS, known, str(out), "sv", "Karaktärer")
    k = {r[0]: dict(zip(tabs["chars"][0], r)) for r in tabs["chars"][1:]}
    check(k["Barkbit"]["Spelare"] == "Jessan" and k["Barkbit"]["Roll"] == "Tank" and k["Barkbit"]["Alt. roll"] == "Healer",
          "player and roles read from public note")
    check(k["Plaster"]["Prio"] == "Alt" and k["Plaster"]["Ras"] == "Gnome", "alt + race")
    check(k["Robinhood"]["Spelare"] == "Robin" and k["Robinhood"]["Källa (spelare)"] == "Officersnote", "officer note fallback")
    check(k["Oscarsson"]["Spelare"] == "Oscar E", "two-word player name matched")
    check(k["Humlesnurr"]["Yrke 1"] == "Skinning" and k["Humlesnurr"]["Skill 1"] == 75, "professions in character row (Cooking left out)")
    check(k["Luddeknas"]["Publik note"].startswith("="), "formula-looking note kept as text (written RAW)")
    check(len(tabs["profs"]) - 1 == 5, "Yrken has one row per profession")
    gb = tabs["bank"]
    check(["Linen Cloth", 25, "Material", "2589"] == gb[1][:4], "bank stacks summed with item id")
    check(len(tabs["log"]) - 1 == 3, "gold log has 3 entries")
    en = gs.build_tabs(name, st, PLAYERS, {}, str(out))
    check(en["chars"][0][:3] == ["Character", "Player", "Main/Alt"] and en["log"][1][2] in ("Deposit", "Withdrawal"),
          "English is the default language")
    check(gs.col_index("A") == 0 and gs.col_index("G") == 6 and gs.col_index("AA") == 26, "column letters")

    print("\nSecond scan 5 hours later with one new deposit:")
    scanned = g["bank"]["moneyLogScanned"]
    newer = [
        {"type": "deposit", "name": "Barkbit-Testrealm", "amount": 50000, "hoursAgo": 8, "unit": "h"},
        {"type": "withdraw", "name": "Humlesnurr-Testrealm", "amount": 10000, "hoursAgo": 53, "unit": "d"},
        {"type": "deposit", "name": "Barkbit-Testrealm", "amount": 50000, "hoursAgo": 8, "unit": "h"},
        {"type": "repair", "name": "Plaster-Testrealm", "amount": 2500, "hoursAgo": 1, "unit": "h"},
    ]
    merged = gs.merge_money_log(st["moneyLog"], newer, scanned + 5 * 3600)
    check(len(merged) == 4, "no duplicates, new entry added")

    print("\nParser edge cases:")
    sample = 'X = {\n["a\\"b"] = "line\\nbreak",\n[1] = -2.5,\n["t"] = { "x", -- [1]\n"y", -- [2]\n},\n["n"] = nil,\n}\nY = true\n'
    p = gs.parse_saved_variables(sample)
    check(p["X"]['a"b'] == "line\nbreak" and p["X"][1] == -2.5 and p["X"]["t"] == ["x", "y"] and p["Y"] is True,
          "escapes, numbers, arrays, multiple globals")
    check(gs.parse_saved_variables('Z = {\n["s"] = "\\195\\165",\n}\n')["Z"]["s"] == "å", "decimal byte escapes (UTF-8)")

    print("\nAll checks passed.")


if __name__ == "__main__":
    main()

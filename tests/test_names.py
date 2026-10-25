"""WoW Forever characters have a first and a last name. Checks that names with spaces
(and hyphenated surnames) survive the addon, its messages and the helper.

    python tests/test_names.py
"""

import sys
from pathlib import Path

import lupa.lua51 as lupa

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "helper"))
import guildsheet_sync as gs  # noqa: E402


def check(cond, msg):
    if not cond:
        raise AssertionError(msg)
    print("  ok  " + msg)


def main():
    lua = lupa.LuaRuntime(unpack_returned_tuples=True)
    lua.execute((ROOT / "tests" / "wow_mock.lua").read_text(encoding="utf-8"))
    lua.execute(r"""
        MOCK.player.name = "Professor Humlesnurr"
        MOCK.professions = { {"Skinning", 75, 150} }
        MOCK.roster = {
          { name="Professor Humlesnurr-Testrealm", rank="GM", rankIndex=0, level=12, class="Mage", classFile="MAGE",
            zone="Z", note="Main Hampus DPS", officerNote="", online=true, guid="P-1" },
          { name="Berta Barkbit-Testrealm", rank="Officer", rankIndex=1, level=10, class="Druid", classFile="DRUID",
            zone="Z", note="", officerNote="", online=true, guid="P-2" },
          { name="Anna Smith-Jones-Testrealm", rank="Member", rankIndex=2, level=8, class="Priest", classFile="PRIEST",
            zone="Z", note="Alt Jessan heal", officerNote="", online=false, guid="P-3" },
        }
    """)
    lua.execute("local f = assert(loadstring(...)); f('GuildSheet', {})",
                (ROOT / "GuildSheet" / "GuildSheet.lua").read_text(encoding="utf-8"))
    lua.execute("""
        MOCK.fire('PLAYER_LOGIN')
        MOCK.fire('GUILD_ROSTER_UPDATE')
        MOCK.advance(15)
        MOCK.fire('CHAT_MSG_ADDON', 'GSHEET', 'C^1^Night Elf^NightElf^Herbalism:120:150', 'GUILD', 'Berta Barkbit-Testrealm')
        MOCK.fire('CHAT_MSG_ADDON', 'GSHEET', 'R^1^Anna Smith-Jones-Testrealm^1793000100^Human^Human^1793000100^Mining:50:75',
                  'WHISPER', 'Berta Barkbit-Testrealm')
        MOCK.units.target = { name='Berta Barkbit', realm=nil, race='Night Elf', raceFile='NightElf', guild=true }
        MOCK.fire('PLAYER_TARGET_CHANGED')
        SlashCmdList.GUILDSHEET('names')
    """)
    G = lua.globals()
    db = G.GuildSheetDB.guilds["Testgilla"]

    print("Addon:")
    check(db.chars["Professor Humlesnurr-Testrealm"].profs[1].name == "Skinning", "own two-part name stored with realm")
    check(db.chars["Berta Barkbit-Testrealm"].profs[1].name == "Herbalism", "message from a two-part name")
    check(db.chars["Anna Smith-Jones-Testrealm"].profs[1].name == "Mining", "relayed record about a hyphenated surname")
    check(db.chars["Berta Barkbit-Testrealm"].race == "Night Elf", "target with a two-part name lines up with the roster")
    hello = [G.MOCK.sent[i].msg for i in range(1, len(G.MOCK.sent) + 1)]
    check(any(m.startswith("C^1^Gnome") for m in hello), "own data still broadcast")
    printed = [G.MOCK.printed[i] for i in range(1, len(G.MOCK.printed) + 1)]
    check(any("UnitName: [Professor Humlesnurr]" in p for p in printed), "/gsheet names shows the raw name")

    print("\nHelper:")
    sv = gs.parse_saved_variables(lua.eval("MOCK.serialize('GuildSheetDB', GuildSheetDB)"))
    name, g = gs.pick_guild(sv, None)
    st = gs.merge_state({}, name, g)
    known = {"berta barkbit": {"spelare": "Jessan", "prio": "Main", "roll": "Tank", "altRoll": "Healer"}}
    tabs = gs.build_tabs(name, st, ["Hampus", "Jessan"], known, "x", "sv", "Karaktärer")
    rows = {r[0]: r for r in tabs["chars"][1:]}
    check(set(rows) == {"Professor Humlesnurr", "Berta Barkbit", "Anna Smith-Jones"}, "full names shown without realm")
    check(rows["Berta Barkbit"][1] == "Jessan" and rows["Berta Barkbit"][5] == "Tank",
          "two-part name matched to the guild's own Karaktärer tab")
    check(rows["Anna Smith-Jones"][1] == "Jessan" and rows["Anna Smith-Jones"][4] == "Human", "hyphenated surname kept whole")
    check(gs.short("Anna Smith-Jones-Testrealm") == "Anna Smith-Jones" and gs.short("Berta Barkbit") == "Berta Barkbit",
          "realm stripping")

    print("\nAll checks passed.")


if __name__ == "__main__":
    main()

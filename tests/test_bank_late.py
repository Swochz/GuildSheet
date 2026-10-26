"""Guild bank data that arrives late, as seen in retail: no tabs right when the bank opens,
an empty money log on the first update, and tab info without a clear "viewable" flag.

    python tests/test_bank_late.py
"""

from pathlib import Path

import lupa.lua51 as lupa

ROOT = Path(__file__).resolve().parent.parent


def check(cond, msg):
    if not cond:
        raise AssertionError(msg)
    print("  ok  " + msg)


def main():
    lua = lupa.LuaRuntime(unpack_returned_tuples=True)
    lua.execute((ROOT / "tests" / "wow_mock.lua").read_text(encoding="utf-8"))
    lua.execute(r"""
        MOCK.roster = { { name="Humlesnurr-Testrealm", rank="GM", rankIndex=0, level=12, class="Mage", classFile="MAGE",
                          zone="Z", note="", officerNote="", online=true, guid="P-1" } }
        MOCK.bank.tabs = {
          { name="Materials", slots = { [1] = { link="|cffffffff|Hitem:2589::::::::|h[Linen Cloth]|h|r", count=20 } } },
          { name="Gear", slots = { [4] = { link="|cff1eff00|Hitem:6070::::::::|h[Wolfshead Helm]|h|r", count=1 } } },
        }
        MOCK.bank.money = 860964203
        MOCK.bank.log = { { "deposit", "Barkbit", 50000, 0, 0, 0, 3 } }
        MOCK.bank.tabsHidden = true      -- no tabs yet when the bank opens
        MOCK.bank.viewableUnknown = true -- tab info without the viewable flag
        MOCK.bank.logHidden = true       -- first log update arrives empty
    """)
    lua.execute("local f = assert(loadstring(...)); f('GuildSheet', {})",
                (ROOT / "GuildSheet" / "GuildSheet.lua").read_text(encoding="utf-8"))
    lua.execute("""
        MOCK.fire('PLAYER_LOGIN'); MOCK.fire('GUILD_ROSTER_UPDATE'); MOCK.advance(15)
        MOCK.fire('PLAYER_INTERACTION_MANAGER_FRAME_SHOW', 10)
        MOCK.advance(2.5)
        MOCK.bank.tabsHidden = false     -- tab info arrives
        MOCK.advance(4)
        MOCK.fire('GUILDBANKLOG_UPDATE') -- still empty: should ask again, not store an empty log
        MOCK.advance(1)
        MOCK.bank.logHidden = false
        MOCK.advance(1.5)
        MOCK.fire('GUILDBANKLOG_UPDATE')
        SlashCmdList.GUILDSHEET('bank')
        MOCK.fire('PLAYER_INTERACTION_MANAGER_FRAME_HIDE', 10)
    """)
    G = lua.globals()
    bank = G.GuildSheetDB.guilds["Testgilla"].bank
    check(bank.money == 860964203, "gold read")
    check(bank.tabs[1] is not None and bank.tabs[1].name == "Materials", "tab 1 scanned after tab info arrived late")
    check(bank.tabs[2] is not None and bank.tabs[2]["items"][1].id == 6070, "tab 2 scanned too")
    check(bank.moneyLog is not None and len(list(bank.moneyLog.values())) == 1, "money log stored after the retry")
    dbg = G.GuildSheetDB.guilds["Testgilla"].debug
    lines = list(dbg.bank.values())
    check(any(l.startswith("tab 1: Materials") and "items: 1" in l for l in lines), "/gsheet bank saved its report")
    print("\nAll checks passed.")


if __name__ == "__main__":
    main()

"""Several fake game clients running the addon, with their addon messages routed between them.

    python tests/test_sharing.py
"""

import sys
from pathlib import Path

import lupa.lua51 as lupa

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "helper"))
import guildsheet_sync as gs  # noqa: E402

REALM = "Testrealm"
MEMBERS = [  # character, rank index, class
    ("Humlesnurr", 0, "Mage"),     # the sheet keeper, guild master
    ("Barkbit", 1, "Druid"),       # officer
    ("Plaster", 2, "Priest"),      # member
    ("Annasyster", 2, "Warrior"),  # member who is never online with the keeper
]
START = 1793000000


class Client:
    def __init__(self, name, race, profs):
        self.name = name
        self.full = f"{name}-{REALM}"
        self.lua = lupa.LuaRuntime(unpack_returned_tuples=True)
        self.lua.execute((ROOT / "tests" / "wow_mock.lua").read_text(encoding="utf-8"))
        g = self.lua.globals()
        g.MOCK.time = START
        g.MOCK.player.name = name
        g.MOCK.player.race = race
        g.MOCK.player.raceFile = race.replace(" ", "")
        self.lua.execute("MOCK.professions = {}")
        for i, (p, s, m) in enumerate(profs, 1):
            self.lua.execute(f"MOCK.professions[{i}] = {{'{p}', {s}, {m}}}")
        roster = ", ".join(
            f"{{ name='{n}-{REALM}', rank='R{r}', rankIndex={r}, level=10, class='{c}', classFile='{c.upper()}', "
            f"zone='Z', note='', officerNote='', online=false, guid='G-{n}' }}" for n, r, c in MEMBERS)
        self.lua.execute(f"MOCK.roster = {{ {roster} }}")
        self.lua.execute("local f = assert(loadstring(...)); f('GuildSheet', {})",
                         (ROOT / "GuildSheet" / "GuildSheet.lua").read_text(encoding="utf-8"))
        self.online = False

    @property
    def mock(self):
        return self.lua.globals().MOCK

    @property
    def db(self):
        return self.lua.globals().GuildSheetDB.guilds["Testgilla"]

    def char(self, name):
        return self.db.chars[f"{name}-{REALM}"]


class World:
    def __init__(self, clients):
        self.clients = {c.full: c for c in clients}
        self.log = []  # (from, channel, target, msg)
        self.all_log = []  # never cleared

    def set_online(self):
        online = {c.name for c in self.clients.values() if c.online}
        for c in self.clients.values():
            if c.online:
                for i in range(1, len(MEMBERS) + 1):
                    r = c.mock.roster[i]
                    r.online = r.name.split("-")[0] in online
                c.mock.fire("GUILD_ROSTER_UPDATE")

    def login(self, client):
        client.online = True
        client.mock.time = max(c.mock.time for c in self.clients.values())
        self.set_online()
        client.mock.fire("PLAYER_LOGIN")

    def logout(self, client):
        client.mock.fire("PLAYER_LOGOUT")
        client.online = False
        self.set_online()

    def run(self, seconds):
        for _ in range(int(seconds * 2)):
            for c in self.clients.values():
                if c.online:
                    c.mock.advance(0.5)
            for c in list(self.clients.values()):
                sent = c.mock.sent
                n = len(sent)
                msgs = [sent[i] for i in range(1, n + 1)]
                for i in range(n, 0, -1):
                    sent[i] = None
                if not c.online:
                    continue
                for m in msgs:
                    self.log.append((c.name, m.channel, m.target, m.msg))
                    self.all_log.append(m.msg)
                    for other in self.clients.values():
                        if other is c or not other.online:
                            continue
                        if m.channel == "GUILD" or (m.channel == "WHISPER" and m.target in (other.full, other.name)):
                            other.mock.fire("CHAT_MSG_ADDON", m.prefix, m.msg, m.channel, c.full)


def check(cond, msg):
    if not cond:
        raise AssertionError(msg)
    print("  ok  " + msg)


def main():
    keeper = Client("Humlesnurr", "Gnome", [("Skinning", 75, 150), ("Tailoring", 60, 150)])
    officer = Client("Barkbit", "Night Elf", [("Herbalism", 120, 150), ("Skinning", 90, 150)])
    member = Client("Plaster", "Gnome", [("Enchanting", 40, 150)])
    anna = Client("Annasyster", "Dwarf", [("Mining", 200, 225), ("Blacksmithing", 180, 225)])
    world = World([keeper, officer, member, anna])

    print("Anna and the officer play together while the keeper is offline:")
    world.login(officer)
    world.login(anna)
    world.run(60)
    check(officer.char("Annasyster").profs[1].name == "Blacksmithing", "officer got Anna's professions")
    world.logout(anna)

    print("\nThe keeper logs in later (Anna is offline):")
    world.login(keeper)
    keeper.mock.units.target = keeper.lua.eval("{ name='Plaster', race='Gnome', raceFile='Gnome', guild=true }")
    keeper.mock.fire("PLAYER_TARGET_CHANGED")  # keeper knows Plaster's race, the officer doesn't
    world.run(120)
    a = keeper.char("Annasyster")
    check(a is not None and a.race == "Dwarf" and a.profs[1].name == "Blacksmithing" and a.profs[1].skill == 180,
          "keeper got Anna's race and professions relayed by the officer")
    check(keeper.char("Barkbit").profs[1].name == "Herbalism", "keeper got the officer's own data")
    check(officer.char("Plaster").race == "Gnome", "officer got what only the keeper knew (backfill)")
    whispers_to_keeper = [l for l in world.log if l[1] == "WHISPER" and l[2] and l[2].startswith("Humlesnurr")]
    check(any(m.startswith("E^1^") for _, _, _, m in whispers_to_keeper), "batch ended with an E message")

    print("\nThe officer opens the guild bank with a member online:")
    world.login(member)
    world.run(30)
    officer.lua.execute("""
        MOCK.items = { [2589] = 'Linen Cloth', [6070] = 'Wolfshead Helm' }
        local big = {}
        for i = 1, 60 do big[i] = { link = '|cffffffff|Hitem:' .. (10000 + i) .. '::::::::|h[Thing ' .. i .. ']|h|r', count = i } end
        big[61] = { link = '|cffffffff|Hitem:2589::::::::|h[Linen Cloth]|h|r', count = 20 }
        big[62] = { link = '|cffffffff|Hitem:2589::::::::|h[Linen Cloth]|h|r', count = 5 }
        MOCK.bank.tabs = {
          { name = 'Materials', slots = big },
          { name = 'Officers', slots = { [3] = { link = '|cff1eff00|Hitem:6070::::::::|h[Wolfshead Helm]|h|r', count = 1 } } },
        }
        MOCK.bank.money = 4200000
        MOCK.bank.log = { { 'deposit', 'Barkbit', 50000, 0, 0, 0, 3 }, { 'withdraw', 'Humlesnurr', 10000, 0, 0, 2, 0 } }
    """)
    keeper.lua.execute("MOCK.items = { [2589] = 'Linen Cloth' }")  # the keeper's client only knows Linen Cloth so far
    officer.mock.fire("PLAYER_INTERACTION_MANAGER_FRAME_SHOW", 10)
    world.run(4)
    officer.mock.fire("GUILDBANKLOG_UPDATE")
    officer.mock.fire("PLAYER_INTERACTION_MANAGER_FRAME_HIDE", 10)
    world.run(120)

    kb = keeper.db.bank
    check(kb.money == 4200000, "keeper (higher rank) got the bank gold")
    t1 = kb.tabs[1]
    items = list(t1["items"].values())
    check(t1 is not None and t1.name == "Materials" and len(items) == 61, "big tab reassembled from several messages (61 item types)")
    linen = [it for it in items if it.id == 2589][0]
    check(linen.count == 25 and linen.link and "Linen Cloth" in linen.link, "stacks summed, name filled from the item cache")
    check(kb.tabs[2] is not None and kb.tabs[2].name == "Officers", "keeper got the officer tab")
    check(len(list(kb.moneyLog.values())) == 2 and kb.moneyLogScanned, "keeper got the gold log")
    check(member.db.bank.money is None and member.db.bank.tabs[1] is None, "member (lower rank) got no bank data")
    bt_parts = [m for f, ch, t, m in world.log if f == "Barkbit" and m.startswith("BT^") and t and t.startswith("Humlesnurr")]
    check(len(bt_parts) >= 3, f"tab sent in parts ({len(bt_parts)} BT messages)")
    helm = [it for it in list(kb.tabs[2]["items"].values())][0]
    check(helm.link is None, "unknown item kept as an id for now")
    keeper.lua.execute("MOCK.items[6070] = 'Wolfshead Helm'")
    world.logout(keeper)
    check("Wolfshead Helm" in (helm.link or ""), "item name filled in at logout once the client knows it")

    print("\nOnly one client answers a hello:")
    world.login(keeper)
    world.run(60)
    world.log.clear()
    world.login(anna)
    world.run(120)
    responders = {f for f, ch, t, m in world.log if ch == "WHISPER" and t and t.startswith("Annasyster") and m.startswith("R^")}
    check(responders == {"Barkbit"}, f"only the first name in order answered ({responders})")

    print("\nSafety:")
    keeper.mock.fire("CHAT_MSG_ADDON", "GSHEET", "R^1^Fake-Testrealm^999999999999^Orc^Orc^999999999999^Alchemy:300:300",
                     "WHISPER", "Stranger-Otherrealm")
    check(keeper.db.chars["Fake-Testrealm"] is None, "whispers from outside the guild ignored")
    keeper.mock.fire("CHAT_MSG_ADDON", "GSHEET", "R^1^Humlesnurr-Testrealm^999999999999^Orc^Orc^999999999999^Alchemy:300:300",
                     "GUILD", "Barkbit-Testrealm")
    check(keeper.char("Humlesnurr").race == "Gnome", "nobody can overwrite your own character")
    keeper.mock.fire("CHAT_MSG_ADDON", "GSHEET", "R^1^Annasyster-Testrealm^5^Troll^Troll^5^Alchemy:1:1", "GUILD", "Barkbit-Testrealm")
    check(keeper.char("Annasyster").race == "Dwarf" and keeper.char("Annasyster").profs[1].name != "Alchemy",
          "older records don't overwrite newer ones")
    longest = max(len(m) for m in world.all_log)
    check(longest <= 255, f"all {len(world.all_log)} messages fit the 255 byte limit (longest {longest})")

    print("\nHelper reads the shared bank data:")
    sv = gs.parse_saved_variables(keeper.lua.eval("MOCK.serialize('GuildSheetDB', GuildSheetDB)"))
    name, g = gs.pick_guild(sv, None)
    st = gs.merge_state({}, name, g)
    tabs = gs.build_tabs(name, st, [], {}, "x")
    bank_rows = tabs["bank"][1:]
    check(any(r[0] == "Linen Cloth" and r[1] == 25 for r in bank_rows), "named items in the bank tab")
    check(any(r[0] == "Wolfshead Helm" for r in bank_rows), "item named after the logout fill")
    check(len(bank_rows) == 62, "all 62 item types listed")
    chars = {r[0]: r for r in tabs["chars"][1:]}
    check(chars["Annasyster"][4] == "Dwarf" and chars["Annasyster"][12] == "Blacksmithing", "Anna's relayed data in the sheet")

    print("\nAll checks passed.")


if __name__ == "__main__":
    main()

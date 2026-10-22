-- Minimal fake of the WoW API, enough to run GuildSheet.lua outside the game.
-- Tests set MOCK.* to shape the fake world, fire events and run timers.

MOCK = {
    time = 1793000000,
    realm = "Testrealm",
    player = { name = "Humlesnurr", race = "Gnome", raceFile = "Gnome" },
    guild = "Testgilla",
    roster = {},
    lastOnline = {},
    professions = {},        -- { {name, skill, max}, ... }
    units = {},              -- unit -> {name, realm, race, raceFile, guild=true}
    bank = { tabs = {}, money = 0, log = {} },
    sent = {},
    timers = {},
    frames = {},
    known_events = {},
    printed = {},
    inCombat = false,
}

for _, e in ipairs({
    "PLAYER_LOGIN", "GUILD_ROSTER_UPDATE", "PLAYER_GUILD_UPDATE", "CHAT_MSG_ADDON", "GROUP_ROSTER_UPDATE",
    "PLAYER_TARGET_CHANGED", "UPDATE_MOUSEOVER_UNIT", "SKILL_LINES_CHANGED", "PLAYER_INTERACTION_MANAGER_FRAME_SHOW",
    "PLAYER_INTERACTION_MANAGER_FRAME_HIDE", "GUILDBANKBAGSLOTS_CHANGED", "GUILDBANK_UPDATE_MONEY", "GUILDBANKLOG_UPDATE",
}) do MOCK.known_events[e] = true end
-- GUILDBANKFRAME_OPENED/CLOSED and GUILD_TRADESKILL_UPDATE are left out on purpose:
-- modern clients reject them, and the addon must survive that.

function CreateFrame()
    local f = { events = {} }
    function f:RegisterEvent(e)
        if not MOCK.known_events[e] then error("Attempt to register unknown event \"" .. e .. "\"") end
        self.events[e] = true
    end
    function f:SetScript(_, fn) self.onEvent = fn end
    table.insert(MOCK.frames, f)
    return f
end

function MOCK.fire(event, ...)
    for _, f in ipairs(MOCK.frames) do
        if f.events[event] and f.onEvent then f.onEvent(f, event, ...) end
    end
end

function MOCK.advance(seconds)
    local target = MOCK.time + seconds
    while true do
        table.sort(MOCK.timers, function(a, b) return a.at < b.at end)
        local t = MOCK.timers[1]
        if not t or t.at > target then break end
        table.remove(MOCK.timers, 1)
        MOCK.time = t.at
        t.fn()
        if t.every then t.at = t.at + t.every; table.insert(MOCK.timers, t) end
    end
    MOCK.time = target
end

C_Timer = {
    After = function(s, fn) table.insert(MOCK.timers, { at = MOCK.time + s, fn = fn }) end,
    NewTicker = function(s, fn) table.insert(MOCK.timers, { at = MOCK.time + s, fn = fn, every = s }) end,
}

function GetServerTime() return math.floor(MOCK.time) end
function time() return math.floor(MOCK.time) end
DEFAULT_CHAT_FRAME = { AddMessage = function(_, m) table.insert(MOCK.printed, m) end }
SlashCmdList = {}
function GetNormalizedRealmName() return MOCK.realm end
function GetRealmName() return MOCK.realm end
function InCombatLockdown() return MOCK.inCombat end

-- guild
function IsInGuild() return MOCK.guild ~= nil end
function GetGuildInfo() return MOCK.guild end
C_GuildInfo = {
    GuildRoster = function() MOCK.rosterRequests = (MOCK.rosterRequests or 0) + 1 end,
    CanViewOfficerNote = function() return true end,
}
function GetNumGuildMembers() return #MOCK.roster end
function GetGuildRosterInfo(i)
    local r = MOCK.roster[i]
    return r.name, r.rank, r.rankIndex, r.level, r.class, r.zone, r.note, r.officerNote, r.online, 0, r.classFile,
           0, 0, false, false, 0, r.guid
end
function GetGuildRosterLastOnline(i)
    local r = MOCK.roster[i]
    if r.online then return nil end
    local lo = r.lastOnline or { 0, 0, 1, 2 }
    return lo[1], lo[2], lo[3], lo[4]
end

-- player and units
function UnitName(unit)
    if unit == "player" then return MOCK.player.name end
    local u = MOCK.units[unit]
    if u then return u.name, u.realm end
end
function UnitRace(unit)
    if unit == "player" then return MOCK.player.race, MOCK.player.raceFile end
    local u = MOCK.units[unit]
    if u then return u.race, u.raceFile end
end
function UnitExists(unit) return unit == "player" or MOCK.units[unit] ~= nil end
function UnitIsPlayer(unit) return UnitExists(unit) end
function UnitIsUnit(a, b) return a == b end
function UnitIsInMyGuild(unit) local u = MOCK.units[unit]; return u and u.guild end
function IsInRaid() return false end
function GetNumGroupMembers() local n = 0; for k in pairs(MOCK.units) do if k:match("^party") then n = n + 1 end end; return n + 1 end

function GetProfessions()
    local idx = {}
    for i = 1, #MOCK.professions do idx[i] = i end
    return idx[1], idx[2], nil, idx[3], idx[4]
end
function GetProfessionInfo(i)
    local p = MOCK.professions[i]
    if p then return p[1], 0, p[2], p[3] end
end

-- addon messages
C_ChatInfo = {
    RegisterAddonMessagePrefix = function(p) MOCK.prefix = p end,
    SendAddonMessage = function(prefix, msg, channel) table.insert(MOCK.sent, { prefix = prefix, msg = msg, channel = channel }) end,
}

-- guild bank
Enum = { PlayerInteractionType = { GuildBanker = 10 } }
MAX_GUILDBANK_SLOTS_PER_TAB = 98
MAX_GUILDBANK_TABS = 8
function GetNumGuildBankTabs() return #MOCK.bank.tabs end
function GetGuildBankTabInfo(tab) local t = MOCK.bank.tabs[tab]; return t.name, 0, t.viewable ~= false end
function QueryGuildBankTab(tab) MOCK.bank.queried = MOCK.bank.queried or {}; MOCK.bank.queried[tab] = true end
function GetGuildBankItemLink(tab, slot)
    if not (MOCK.bank.queried and MOCK.bank.queried[tab]) then return nil end
    local it = MOCK.bank.tabs[tab].slots[slot]
    return it and it.link
end
function GetGuildBankItemInfo(tab, slot) local it = MOCK.bank.tabs[tab].slots[slot]; return 0, it and it.count end
function GetCurrentGuildBankTab() return 1 end
function GetGuildBankMoney() return MOCK.bank.money end
function QueryGuildBankLog() MOCK.bank.logQueried = true end
function GetNumGuildBankMoneyTransactions() return #MOCK.bank.log end
function GetGuildBankMoneyTransaction(i) local e = MOCK.bank.log[i]; return e[1], e[2], e[3], e[4], e[5], e[6], e[7] end

-- Writes a table the way WoW writes SavedVariables (including the "-- [n]" comments).
function MOCK.serialize(name, value)
    local out = {}
    local function key(k)
        if type(k) == "number" then return "[" .. k .. "]" end
        return string.format("[%q]", k)
    end
    local function val(v, indent)
        if type(v) == "table" then
            out[#out + 1] = "{\n"
            local n = #v
            for i = 1, n do
                out[#out + 1] = indent .. "\t"
                val(v[i], indent .. "\t")
                out[#out + 1] = ", -- [" .. i .. "]\n"
            end
            local keys = {}
            for k in pairs(v) do
                if not (type(k) == "number" and k >= 1 and k <= n and k % 1 == 0) then keys[#keys + 1] = k end
            end
            table.sort(keys, function(a, b) return tostring(a) < tostring(b) end)
            for _, k in ipairs(keys) do
                out[#out + 1] = indent .. "\t" .. key(k) .. " = "
                val(v[k], indent .. "\t")
                out[#out + 1] = ",\n"
            end
            out[#out + 1] = indent .. "}"
        elseif type(v) == "string" then
            out[#out + 1] = string.format("%q", v):gsub("\\\n", "\\n")
        else
            out[#out + 1] = tostring(v)
        end
    end
    out[#out + 1] = "\n" .. name .. " = "
    val(value, "")
    out[#out + 1] = "\n"
    return table.concat(out)
end

-- GuildSheet
-- Collects guild data into GuildSheetDB. The game writes that file on logout or /reload,
-- and the helper program (guildsheet_sync.py) copies it into the Google Sheet.
--
-- Only the person running the helper needs this addon. Anyone else who runs it also
-- shares their own race and professions with the guild over hidden addon messages.

local ADDON_NAME = ...
local PREFIX = "GSHEET"
local PROTO = 1
local SEP = "^"

local db          -- GuildSheetDB.guilds[<current guild>]
local bankOpen = false
local lastHelloReply = 0
local lastBroadcast = 0
local pendingRosterScan = false

local frame = CreateFrame("Frame")

---------------------------------------------------------------------------
-- Helpers
---------------------------------------------------------------------------

local function now()
    return GetServerTime and GetServerTime() or time()
end

local function print_(msg)
    DEFAULT_CHAT_FRAME:AddMessage("|cff4fc3f7GuildSheet:|r " .. tostring(msg))
end

-- Under Midnight's rules some unit data can come back as "secret" values in combat.
-- Those can't be stored or compared, so skip them.
local function usable(v)
    if v == nil then return false end
    if issecretvalue and issecretvalue(v) then return false end
    return true
end

local function myRealm()
    local realm = GetNormalizedRealmName and GetNormalizedRealmName()
    if realm and realm ~= "" then return realm end
    realm = GetRealmName and GetRealmName()
    if realm then return (realm:gsub("[%s%-]", "")) end
    return nil
end

-- Always store characters as "Name-Realm" so data from different sources lines up.
local function fullName(name, realm)
    if not usable(name) or name == "" then return nil end
    if name:find("-", 1, true) then return name end
    realm = (realm and realm ~= "") and realm or myRealm()
    if realm then return name .. "-" .. realm end
    return name
end

local function split(s, sep)
    local out = {}
    for part in (s .. sep):gmatch("(.-)" .. sep:gsub("%p", "%%%0")) do
        out[#out + 1] = part
    end
    return out
end

local function hoursAgo(y, m, d, h)
    return (((y or 0) * 365 + (m or 0) * 30 + (d or 0)) * 24 + (h or 0))
end

---------------------------------------------------------------------------
-- Database
---------------------------------------------------------------------------

local function EnsureDB()
    if db then return db end
    if not IsInGuild() then return nil end
    local guildName = GetGuildInfo("player")
    if not guildName then return nil end -- not known yet right after login

    GuildSheetDB = GuildSheetDB or {}
    GuildSheetDB.version = 1
    GuildSheetDB.guilds = GuildSheetDB.guilds or {}
    local g = GuildSheetDB.guilds[guildName] or {}
    g.roster = g.roster or {}
    g.chars = g.chars or {}
    g.guildProfs = g.guildProfs or {}
    g.bank = g.bank or {}
    g.bank.tabs = g.bank.tabs or {}
    g.realm = myRealm()
    GuildSheetDB.guilds[guildName] = g
    db = g
    return db
end

local function Touch()
    db.updated = now()
    db.updatedBy = fullName(UnitName("player"))
end

---------------------------------------------------------------------------
-- Guild roster (works for every member, no addon needed on their side)
---------------------------------------------------------------------------

local function ScanRoster()
    if not EnsureDB() then return end
    local n = GetNumGuildMembers()
    if not n or n == 0 then return end

    local canOfficer = C_GuildInfo and C_GuildInfo.CanViewOfficerNote and C_GuildInfo.CanViewOfficerNote()
    local t = now()
    local seen, numSeen, numBefore = {}, 0, 0
    for _, r in pairs(db.roster) do
        if r.inGuild then numBefore = numBefore + 1 end
    end

    for i = 1, n do
        local name, rank, rankIndex, level, classLocal, zone, note, officerNote, online, _, classFile,
              _, _, _, _, _, guid = GetGuildRosterInfo(i)
        name = fullName(name)
        if name then
            local r = db.roster[name] or {}
            r.rank = rank
            r.rankIndex = rankIndex
            r.level = level
            r.class = classLocal
            r.classFile = classFile
            r.zone = zone
            r.note = note
            if canOfficer then r.officerNote = officerNote end
            r.online = online and true or false
            r.guid = guid
            if online then
                r.lastOnline = t
            else
                local y, m, d, h = GetGuildRosterLastOnline(i)
                if y then r.lastOnline = t - hoursAgo(y, m, d, h) * 3600 end
            end
            r.inGuild = true
            r.seen = t
            db.roster[name] = r
            seen[name] = true
            numSeen = numSeen + 1
        end
    end

    -- Mark people who left. Skip if the scan looks partial, so nobody vanishes by mistake.
    if numSeen >= numBefore * 0.5 then
        for name, r in pairs(db.roster) do
            if not seen[name] then r.inGuild = false end
        end
    end

    db.rosterUpdated = t
    Touch()
end

local function RequestRoster()
    if C_GuildInfo and C_GuildInfo.GuildRoster then
        C_GuildInfo.GuildRoster()
    elseif GuildRoster then
        GuildRoster()
    end
end

-- GUILD_ROSTER_UPDATE fires in bursts; scan once per burst.
local function QueueRosterScan()
    if pendingRosterScan then return end
    pendingRosterScan = true
    C_Timer.After(2, function()
        pendingRosterScan = false
        ScanRoster()
    end)
end

---------------------------------------------------------------------------
-- Guild professions list (if Forever has it; every call is guarded)
---------------------------------------------------------------------------

local function ScanGuildProfessions()
    if not EnsureDB() then return end
    if not (GetNumGuildTradeSkill and GetGuildTradeSkillInfo) then return end
    local ok, n = pcall(GetNumGuildTradeSkill)
    if not ok or not n or n == 0 then return end

    local result, header = {}, nil
    local found = false
    for i = 1, n do
        local ok2, skillID, isCollapsed, _, headerName, _, _, _, playerName, playerNameWithRealm, _, _, _, skill =
            pcall(GetGuildTradeSkillInfo, i)
        if ok2 then
            if headerName then
                header = headerName
                if isCollapsed and ExpandGuildTradeSkillHeader then
                    pcall(ExpandGuildTradeSkillHeader, skillID) -- members show up on the next update
                end
            elseif header then
                local who = fullName(playerNameWithRealm or playerName)
                if who and skill then
                    result[who] = result[who] or {}
                    result[who][header] = skill
                    found = true
                end
            end
        end
    end
    if found then
        db.guildProfs = result
        db.guildProfsUpdated = now()
        Touch()
    end
end

---------------------------------------------------------------------------
-- Own character: race and professions
---------------------------------------------------------------------------

local function ReadOwnProfessions()
    local profs = {}

    -- Modern API (Forever runs on the retail engine)
    if GetProfessions and GetProfessionInfo then
        local a, b, c, d, e = GetProfessions()
        for _, idx in pairs({ a, b, c, d, e }) do
            local name, _, rank, maxRank = GetProfessionInfo(idx)
            if name then
                profs[#profs + 1] = { name = name, skill = rank or 0, max = maxRank or 0 }
            end
        end
    end

    -- Classic-style skill list, in case Forever uses that instead
    if #profs == 0 and GetNumSkillLines and GetSkillLineInfo then
        local section
        for i = 1, GetNumSkillLines() do
            local name, isHeader, _, rank, _, _, maxRank = GetSkillLineInfo(i)
            if isHeader then
                section = name
            elseif section == "Professions" or section == "Secondary Skills" then
                profs[#profs + 1] = { name = name, skill = rank or 0, max = maxRank or 0 }
            end
        end
    end

    table.sort(profs, function(x, y) return x.name < y.name end)
    return profs
end

local function RecordSelf()
    if not EnsureDB() then return nil end
    local key = fullName(UnitName("player"))
    if not key then return nil end
    local c = db.chars[key] or {}
    c.race, c.raceFile = UnitRace("player")
    c.profs = ReadOwnProfessions()
    c.source = "addon"
    c.updated = now()
    db.chars[key] = c
    Touch()
    return c
end

-- Race isn't in the guild roster, so note it whenever we see a guildmate up close.
local function RecordUnit(unit)
    if not EnsureDB() then return end
    if InCombatLockdown() then return end
    if not UnitExists(unit) or not UnitIsPlayer(unit) then return end
    if not (UnitIsInMyGuild and UnitIsInMyGuild(unit)) then return end
    if UnitIsUnit(unit, "player") then return end

    local name, realm = UnitName(unit)
    local raceLocal, raceFile = UnitRace(unit)
    if not (usable(name) and usable(raceLocal)) then return end
    local key = fullName(name, realm)
    if not key then return end

    local c = db.chars[key] or {}
    if c.race ~= raceLocal then
        c.race, c.raceFile = raceLocal, raceFile
        c.raceSeen = now()
        if not c.source then c.source = "sett" end
        db.chars[key] = c
        Touch()
    end
end

---------------------------------------------------------------------------
-- Addon messages between guild members who run the addon
---------------------------------------------------------------------------
-- "H^1"                               hello, sent on login; others answer with C
-- "C^1^<race>^<raceFile>^Name:skill:max,Name:skill:max"

local function SendGuild(msg)
    if not IsInGuild() then return end
    if #msg > 250 then msg = msg:sub(1, 250) end
    C_ChatInfo.SendAddonMessage(PREFIX, msg, "GUILD")
end

local function BroadcastSelf(force)
    local t = now()
    if not force and t - lastBroadcast < 30 then return end
    local c = RecordSelf()
    if not c then return end
    lastBroadcast = t
    local parts = {}
    for _, p in ipairs(c.profs or {}) do
        parts[#parts + 1] = string.format("%s:%d:%d", (p.name:gsub("[:,%^]", "")), p.skill or 0, p.max or 0)
    end
    SendGuild(table.concat({ "C", PROTO, c.race or "", c.raceFile or "", table.concat(parts, ",") }, SEP))
end

local function OnAddonMessage(prefix, text, channel, sender)
    if prefix ~= PREFIX or channel ~= "GUILD" then return end
    if not EnsureDB() then return end
    local who = fullName(sender)
    if not who or who == fullName(UnitName("player")) then return end

    local f = split(text, SEP)
    if f[1] == "H" then
        -- Answer after a random delay so a guild full of addon users doesn't reply at once.
        if now() - lastHelloReply < 60 then return end
        lastHelloReply = now()
        C_Timer.After(1 + math.random() * 7, function() BroadcastSelf(true) end)
    elseif f[1] == "C" and tonumber(f[2]) == PROTO then
        local c = db.chars[who] or {}
        if f[3] ~= "" then c.race = f[3] end
        if f[4] ~= "" then c.raceFile = f[4] end
        local profs = {}
        if f[5] and f[5] ~= "" then
            for _, entry in ipairs(split(f[5], ",")) do
                local pname, skill, max = entry:match("^(.-):(%d+):(%d+)$")
                if pname then
                    profs[#profs + 1] = { name = pname, skill = tonumber(skill), max = tonumber(max) }
                end
            end
        end
        c.profs = profs
        c.source = "addon"
        c.updated = now()
        db.chars[who] = c
        Touch()
    end
end

---------------------------------------------------------------------------
-- Guild bank (scanned whenever the person with the addon opens it)
---------------------------------------------------------------------------

local function ScanBankTab(tab)
    if not EnsureDB() or not bankOpen then return end
    local name, _, isViewable = GetGuildBankTabInfo(tab)
    if not isViewable then return end
    local items = {}
    for slot = 1, (MAX_GUILDBANK_SLOTS_PER_TAB or 98) do
        local link = GetGuildBankItemLink(tab, slot)
        if link then
            local _, count = GetGuildBankItemInfo(tab, slot)
            items[#items + 1] = { link = link, count = count or 1 }
        end
    end
    db.bank.tabs[tab] = { name = name, items = items, updated = now() }
    Touch()
end

local function ScanMoney()
    if not EnsureDB() then return end
    local money = GetGuildBankMoney and GetGuildBankMoney()
    if money then
        db.bank.money = money
        db.bank.moneyUpdated = now()
        Touch()
    end
end

local function ScanMoneyLog()
    if not EnsureDB() or not GetNumGuildBankMoneyTransactions then return end
    local log = {}
    for i = 1, GetNumGuildBankMoneyTransactions() do
        local typ, name, amount, y, m, d, h = GetGuildBankMoneyTransaction(i)
        if typ then
            -- The game only gives "x hours/days ago"; the unit tells the helper how exact it is.
            local unit = (y and y > 0) and "y" or (m and m > 0) and "m" or (d and d > 0) and "d" or "h"
            log[#log + 1] = {
                type = typ,
                name = fullName(name) or "?",
                amount = amount or 0,
                hoursAgo = hoursAgo(y, m, d, h),
                unit = unit,
            }
        end
    end
    db.bank.moneyLog = log
    db.bank.moneyLogScanned = now()
    Touch()
end

local function StartBankScan()
    if not EnsureDB() then return end
    local numTabs = GetNumGuildBankTabs() or 0
    for tab = numTabs + 1, #db.bank.tabs do db.bank.tabs[tab] = nil end -- tabs that no longer exist

    -- Query tabs one by one; the server only sends one tab at a time.
    for tab = 1, numTabs do
        C_Timer.After((tab - 1) * 1.0, function()
            if not bankOpen then return end
            QueryGuildBankTab(tab)
            C_Timer.After(0.7, function() ScanBankTab(tab) end)
        end)
    end
    C_Timer.After(numTabs * 1.0 + 0.5, function()
        if bankOpen and QueryGuildBankLog then
            QueryGuildBankLog((MAX_GUILDBANK_TABS or 8) + 1) -- the extra "tab" is the money log
        end
    end)
    ScanMoney()
end

local function OnBankOpened()
    bankOpen = true
    StartBankScan()
end

local function OnBankClosed()
    bankOpen = false
end

---------------------------------------------------------------------------
-- Slash command
---------------------------------------------------------------------------

local function CountKeys(t, pred)
    local n = 0
    for _, v in pairs(t or {}) do
        if not pred or pred(v) then n = n + 1 end
    end
    return n
end

local function Ago(ts)
    if not ts then return "never" end
    local mins = math.floor((now() - ts) / 60)
    if mins < 1 then return "just now" end
    if mins < 60 then return mins .. " min ago" end
    return math.floor(mins / 60) .. " h ago"
end

SLASH_GUILDSHEET1 = "/gsheet"
SlashCmdList.GUILDSHEET = function(arg)
    arg = (arg or ""):lower():match("^%s*(.-)%s*$")
    if not EnsureDB() then
        print_("You're not in a guild (or the guild hasn't loaded yet).")
        return
    end
    if arg == "scan" then
        RequestRoster()
        BroadcastSelf(true)
        print_("Refreshing roster and professions. Type /reload afterwards to save for the helper.")
    elseif arg == "reset" then
        local guildName = GetGuildInfo("player")
        GuildSheetDB.guilds[guildName] = nil
        db = nil
        EnsureDB()
        RequestRoster()
        print_("Cleared saved data for " .. guildName .. ".")
    else
        print_(string.format("%d members, %d with race, %d with professions.",
            CountKeys(db.roster, function(r) return r.inGuild end),
            CountKeys(db.chars, function(c) return c.race end),
            CountKeys(db.chars, function(c) return c.profs and #c.profs > 0 end)))
        print_("Roster: " .. Ago(db.rosterUpdated) .. ". Guild bank: " .. Ago(db.bank.moneyUpdated) .. ".")
        print_("The helper sees new data after /reload or logout. Commands: /gsheet scan, /gsheet reset")
    end
end

---------------------------------------------------------------------------
-- Events
---------------------------------------------------------------------------

local function SafeRegister(event)
    pcall(frame.RegisterEvent, frame, event) -- some events may not exist in Forever
end

frame:RegisterEvent("PLAYER_LOGIN")
frame:RegisterEvent("GUILD_ROSTER_UPDATE")
frame:RegisterEvent("PLAYER_GUILD_UPDATE")
frame:RegisterEvent("CHAT_MSG_ADDON")
frame:RegisterEvent("GROUP_ROSTER_UPDATE")
frame:RegisterEvent("PLAYER_TARGET_CHANGED")
frame:RegisterEvent("UPDATE_MOUSEOVER_UNIT")
SafeRegister("SKILL_LINES_CHANGED")
SafeRegister("GUILD_TRADESKILL_UPDATE")
SafeRegister("PLAYER_INTERACTION_MANAGER_FRAME_SHOW")
SafeRegister("PLAYER_INTERACTION_MANAGER_FRAME_HIDE")
SafeRegister("GUILDBANKFRAME_OPENED")
SafeRegister("GUILDBANKFRAME_CLOSED")
SafeRegister("GUILDBANKBAGSLOTS_CHANGED")
SafeRegister("GUILDBANK_UPDATE_MONEY")
SafeRegister("GUILDBANKLOG_UPDATE")

local function IsGuildBankInteraction(kind)
    return Enum and Enum.PlayerInteractionType and kind == Enum.PlayerInteractionType.GuildBanker
end

frame:SetScript("OnEvent", function(_, event, ...)
    if event == "PLAYER_LOGIN" then
        C_ChatInfo.RegisterAddonMessagePrefix(PREFIX)
        RequestRoster()
        C_Timer.After(10, function()
            if EnsureDB() then
                BroadcastSelf(true)
                SendGuild("H" .. SEP .. PROTO)
            end
        end)
        C_Timer.NewTicker(300, RequestRoster)
    elseif event == "GUILD_ROSTER_UPDATE" then
        QueueRosterScan()
        ScanGuildProfessions()
    elseif event == "PLAYER_GUILD_UPDATE" then
        db = nil -- joined, left or changed guild
        RequestRoster()
    elseif event == "CHAT_MSG_ADDON" then
        OnAddonMessage(...)
    elseif event == "SKILL_LINES_CHANGED" then
        BroadcastSelf(false)
    elseif event == "GUILD_TRADESKILL_UPDATE" then
        ScanGuildProfessions()
    elseif event == "GROUP_ROSTER_UPDATE" then
        local prefix = IsInRaid() and "raid" or "party"
        for i = 1, GetNumGroupMembers() do RecordUnit(prefix .. i) end
    elseif event == "PLAYER_TARGET_CHANGED" then
        RecordUnit("target")
    elseif event == "UPDATE_MOUSEOVER_UNIT" then
        RecordUnit("mouseover")
    elseif event == "PLAYER_INTERACTION_MANAGER_FRAME_SHOW" then
        if IsGuildBankInteraction(...) then OnBankOpened() end
    elseif event == "PLAYER_INTERACTION_MANAGER_FRAME_HIDE" then
        if IsGuildBankInteraction(...) then OnBankClosed() end
    elseif event == "GUILDBANKFRAME_OPENED" then
        OnBankOpened()
    elseif event == "GUILDBANKFRAME_CLOSED" then
        OnBankClosed()
    elseif event == "GUILDBANKBAGSLOTS_CHANGED" then
        if bankOpen and GetCurrentGuildBankTab then ScanBankTab(GetCurrentGuildBankTab()) end
    elseif event == "GUILDBANK_UPDATE_MONEY" then
        if bankOpen then ScanMoney() end
    elseif event == "GUILDBANKLOG_UPDATE" then
        if bankOpen then ScanMoneyLog() end
    end
end)

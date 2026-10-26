-- GuildSheet
-- Collects guild data into GuildSheetDB. The game writes that file on logout or /reload,
-- and the helper program (guildsheet_sync.py) copies it into the Google Sheet.
--
-- Only the person running the helper needs this addon. Everyone who runs it shares, over
-- hidden addon messages:
--   * their own race and professions,
--   * what they know about other members (so data reaches people who are never online at
--     the same time),
--   * guild bank scans, but only with members of the same rank or higher.

local ADDON_NAME = ...
local PREFIX = "GSHEET"
local PROTO = 1
local SEP = "^"
local MAX_MSG = 250
local PEER_TIMEOUT = 6 * 3600 -- an addon user we heard from within this time counts as around

local db          -- GuildSheetDB.guilds[<current guild>]
local bankOpen = false
local bankScanStarted = 0
local lastHelloReply = 0
local lastBroadcast = 0
local pendingRosterScan = false
local peers = {}  -- "Name-Realm" -> last time we got a message from them
local dumps = {}  -- sender -> { received = {name=true}, localNewer = {name=true} } while they send us their records
local parts = {}  -- buffers for multi-part bank messages

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

local function me()
    return fullName(UnitName("player"))
end

local function split(s, sep)
    local out = {}
    for part in (s .. sep):gmatch("(.-)" .. sep:gsub("%p", "%%%0")) do
        out[#out + 1] = part
    end
    return out
end

-- Strips the characters the message format uses as separators.
local function clean(s)
    return (tostring(s or ""):gsub("[%^:,;]", ""))
end

local function hoursAgo(y, m, d, h)
    return (((y or 0) * 365 + (m or 0) * 30 + (d or 0)) * 24 + (h or 0))
end

local function itemLinkFor(id)
    if not id then return nil end
    local link = select(2, GetItemInfo(id))
    return link
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
    db.updatedBy = me()
end

local function InGuild(name)
    local r = db and db.roster[name]
    return r and r.inGuild
end

---------------------------------------------------------------------------
-- Sending: a small queue so we stay under the game's addon message limits
---------------------------------------------------------------------------

local queue = {}
local tokens, lastRefill, pumping = 8, 0, false

local function Pump()
    local t = GetTime and GetTime() or now()
    tokens = math.min(8, tokens + (t - lastRefill)) -- roughly one message per second, bursts of 8
    lastRefill = t
    local m = queue[1]
    if not m then pumping = false return end
    if tokens < 1 then C_Timer.After(1, Pump) return end

    local ok, res = pcall(C_ChatInfo.SendAddonMessage, PREFIX, m.msg, m.channel, m.target)
    -- Newer clients return a result code; anything but 0 (success) after a throttle means retry.
    local throttled = ok and type(res) == "number" and res ~= 0
    if throttled and (m.tries or 0) < 5 then
        m.tries = (m.tries or 0) + 1
        C_Timer.After(2, Pump)
        return
    end
    table.remove(queue, 1)
    tokens = tokens - 1
    C_Timer.After(0.1, Pump)
end

local function Queue(msg, channel, target)
    if not IsInGuild() or #msg > MAX_MSG then return end
    queue[#queue + 1] = { msg = msg, channel = channel, target = target }
    if not pumping then
        pumping = true
        C_Timer.After(0, Pump)
    end
end

-- Joins entries into as few messages as possible. header(part, total) gives each message's start.
local function Chunk(entries, header)
    local chunks, cur = {}, {}
    local room = MAX_MSG - #header(99, 99) - 1
    local len = 0
    for _, e in ipairs(entries) do
        if #e <= room then
            if len + #e + 1 > room and #cur > 0 then
                chunks[#chunks + 1] = cur
                cur, len = {}, 0
            end
            cur[#cur + 1] = e
            len = len + #e + 1
        end
    end
    chunks[#chunks + 1] = cur -- always at least one, so an empty tab or log still arrives
    local out = {}
    for i, c in ipairs(chunks) do
        out[i] = header(i, #chunks) .. SEP .. table.concat(c, ",")
    end
    return out
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
    local key = me()
    if not key then return nil end
    local c = db.chars[key] or {}
    c.race, c.raceFile = UnitRace("player")
    c.raceSeen = now()
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
-- Character records: own data and relaying what we know about others
---------------------------------------------------------------------------
-- "H^1"                                                   hello, sent on login
-- "C^1^<race>^<raceFile>^Name:skill:max,..."              the sender's own character
-- "R^1^<char>^<updated>^<race>^<raceFile>^<raceSeen>^<profs or ->"   a record about any member
-- "E^1^<count>"                                           end of a whispered batch of R records
--
-- When someone logs in and says hello, one addon user (the online one whose name sorts first)
-- whispers them every record it has. The newcomer then sends the guild anything it knows that
-- the responder didn't, so data spreads even between people who are never online together.

local function EncodeProfs(profs)
    local parts_ = {}
    for _, p in ipairs(profs or {}) do
        parts_[#parts_ + 1] = string.format("%s:%d:%d", clean(p.name), p.skill or 0, p.max or 0)
    end
    return parts_
end

local function DecodeProfs(s)
    local profs = {}
    for _, entry in ipairs(split(s, ",")) do
        local pname, skill, max = entry:match("^(.-):(%d+):(%d+)$")
        if pname and pname ~= "" then
            profs[#profs + 1] = { name = pname, skill = tonumber(skill), max = tonumber(max) }
        end
    end
    return profs
end

local function EncodeRecord(name, c)
    local head = table.concat({ "R", PROTO, name, c.profs and (c.updated or 0) or 0, clean(c.race), clean(c.raceFile),
                                c.raceSeen or 0 }, SEP)
    if not c.profs then return head .. SEP .. "-" end
    local profs = EncodeProfs(c.profs)
    -- drop professions from the end rather than cut one in half
    while #profs > 0 and #head + 1 + #table.concat(profs, ",") > MAX_MSG do profs[#profs] = nil end
    return head .. SEP .. table.concat(profs, ",")
end

local function Shareable(name, c)
    return InGuild(name) and (c.race or c.profs)
end

-- Stores a record if it's newer than what we have. Returns true if ours is newer.
local function MergeRecord(name, updated, race, raceFile, raceSeen, profs)
    if not name or name == me() then return false end
    local c = db.chars[name] or {}
    local changed = false
    if race ~= "" and (raceSeen > (c.raceSeen or 0) or not c.race) then
        c.race, c.raceFile, c.raceSeen = race, raceFile ~= "" and raceFile or c.raceFile, raceSeen
        if not c.source then c.source = "sett" end
        changed = true
    end
    if profs and updated > (c.updated or 0) then
        c.profs, c.updated, c.source = profs, updated, "addon"
        changed = true
    end
    if changed then
        db.chars[name] = c
        Touch()
    end
    return (c.profs and (c.updated or 0) > updated) or (c.race and (c.raceSeen or 0) > raceSeen) or false
end

local function BroadcastSelf(force)
    local t = now()
    if not force and t - lastBroadcast < 30 then return end
    local c = RecordSelf()
    if not c then return end
    lastBroadcast = t
    local head = table.concat({ "C", PROTO, clean(c.race), clean(c.raceFile) }, SEP)
    local profs = EncodeProfs(c.profs)
    while #profs > 0 and #head + 1 + #table.concat(profs, ",") > MAX_MSG do profs[#profs] = nil end
    Queue(head .. SEP .. table.concat(profs, ","), "GUILD")
end

-- The online addon user whose name sorts first answers a hello. Everyone works this out the
-- same way, so normally exactly one person replies.
local function IsResponder(newcomer)
    local mine = me()
    local t = now()
    for name, seen in pairs(peers) do
        if name ~= newcomer and t - seen < PEER_TIMEOUT then
            local r = db.roster[name]
            if (not r or r.online) and name < mine then return false end
        end
    end
    return true
end

local function SendAllRecords(target)
    local n = 0
    for name, c in pairs(db.chars) do
        if name ~= target and Shareable(name, c) then
            Queue(EncodeRecord(name, c), "WHISPER", target)
            n = n + 1
        end
    end
    Queue(table.concat({ "E", PROTO, n }, SEP), "WHISPER", target)
end

-- After the responder's batch: send the guild what it didn't have, or had older.
local function BackfillAfter(sender)
    local d = dumps[sender]
    dumps[sender] = nil
    if not d then return end
    for name, c in pairs(db.chars) do
        if name ~= sender and Shareable(name, c) and (not d.received[name] or d.localNewer[name]) then
            Queue(EncodeRecord(name, c), "GUILD")
        end
    end
end

---------------------------------------------------------------------------
-- Guild bank sharing (only with addon users of the same rank or higher)
---------------------------------------------------------------------------
-- "BM^1^<scanTime>^<copper>"
-- "BT^1^<scanTime>^<tab>^<part>^<parts>^<tab name>^itemID:count,..."
-- "BL^1^<scanTime>^<part>^<parts>^type:name:amount:hoursAgo:unit,..."
--
-- Lower-ranked members never receive bank data, so officer-only tabs stay with officers.

local function BankRecipients()
    local mine = db.roster[me() or ""]
    if not (mine and mine.rankIndex) then return {} end
    local out, t = {}, now()
    for name, seen in pairs(peers) do
        local r = db.roster[name]
        if r and r.inGuild and r.online and r.rankIndex and r.rankIndex <= mine.rankIndex and t - seen < PEER_TIMEOUT then
            out[#out + 1] = name
        end
    end
    return out
end

local function ShareBank()
    if not EnsureDB() then return end
    local targets = BankRecipients()
    if #targets == 0 then return end
    local scan = bankScanStarted
    local msgs = {}

    if db.bank.money and (db.bank.moneyUpdated or 0) >= scan then
        msgs[#msgs + 1] = table.concat({ "BM", PROTO, scan, db.bank.money }, SEP)
    end
    for tab, data in pairs(db.bank.tabs) do
        if (data.updated or 0) >= scan and not data.from then
            local totals, order = {}, {}
            for _, it in ipairs(data.items or {}) do
                if it.id then
                    if not totals[it.id] then order[#order + 1] = it.id end
                    totals[it.id] = (totals[it.id] or 0) + (it.count or 1)
                end
            end
            local entries = {}
            for _, id in ipairs(order) do entries[#entries + 1] = id .. ":" .. totals[id] end
            local tabName = clean(data.name)
            for _, m in ipairs(Chunk(entries, function(i, n)
                return table.concat({ "BT", PROTO, scan, tab, i, n, tabName }, SEP)
            end)) do
                msgs[#msgs + 1] = m
            end
        end
    end
    if db.bank.moneyLog and (db.bank.moneyLogScanned or 0) >= scan then
        local entries = {}
        for _, e in ipairs(db.bank.moneyLog) do
            entries[#entries + 1] = table.concat({ clean(e.type), clean(e.name), e.amount or 0, e.hoursAgo or 0, e.unit or "h" }, ":")
        end
        for _, m in ipairs(Chunk(entries, function(i, n)
            return table.concat({ "BL", PROTO, scan, i, n }, SEP)
        end)) do
            msgs[#msgs + 1] = m
        end
    end

    for _, target in ipairs(targets) do
        for _, m in ipairs(msgs) do Queue(m, "WHISPER", target) end
    end
end

-- Collects the parts of a multi-part message; returns the joined entries once all have arrived.
local function Collect(key, part, total, body)
    local buf = parts[key]
    if not buf or buf.total ~= total then
        buf = { total = total, got = {}, count = 0 }
        parts[key] = buf
    end
    if not buf.got[part] then
        buf.got[part] = body
        buf.count = buf.count + 1
    end
    if buf.count < total then return nil end
    parts[key] = nil
    local all = {}
    for i = 1, total do
        if buf.got[i] ~= "" then
            for _, e in ipairs(split(buf.got[i], ",")) do all[#all + 1] = e end
        end
    end
    return all
end

local function OnBankMessage(sender, f)
    local kind, scan = f[1], tonumber(f[3]) or 0
    if kind == "BM" then
        if scan > (db.bank.moneyUpdated or 0) then
            db.bank.money = tonumber(f[4])
            db.bank.moneyUpdated = scan
            Touch()
        end
    elseif kind == "BT" then
        local tab, part, total = tonumber(f[4]), tonumber(f[5]), tonumber(f[6])
        if not (tab and part and total) then return end
        local entries = Collect(sender .. ":BT:" .. scan .. ":" .. tab, part, total, f[8] or "")
        if not entries then return end
        local existing = db.bank.tabs[tab]
        if existing and (existing.updated or 0) >= scan then return end
        local items = {}
        for _, e in ipairs(entries) do
            local id, count = e:match("^(%d+):(%d+)$")
            if id then
                id = tonumber(id)
                items[#items + 1] = { id = id, count = tonumber(count), link = itemLinkFor(id) }
            end
        end
        db.bank.tabs[tab] = { name = f[7], items = items, updated = scan, from = sender }
        Touch()
    elseif kind == "BL" then
        local part, total = tonumber(f[4]), tonumber(f[5])
        if not (part and total) then return end
        local entries = Collect(sender .. ":BL:" .. scan, part, total, f[6] or "")
        if not entries or scan <= (db.bank.moneyLogScanned or 0) then return end
        local log = {}
        for _, e in ipairs(entries) do
            local typ, name, amount, hours, unit = e:match("^(.-):(.-):(%d+):(%d+):(%a)$")
            if typ then
                log[#log + 1] = { type = typ, name = name, amount = tonumber(amount), hoursAgo = tonumber(hours), unit = unit }
            end
        end
        db.bank.moneyLog = log
        db.bank.moneyLogScanned = scan
        Touch()
    end
end

---------------------------------------------------------------------------
-- Receiving
---------------------------------------------------------------------------

local WHISPER_OK = { R = true, E = true, BM = true, BT = true, BL = true }

local function OnAddonMessage(prefix, text, channel, sender)
    if prefix ~= PREFIX then return end
    if not EnsureDB() then return end
    local who = fullName(sender)
    if not who or who == me() then return end

    local f = split(text, SEP)
    if tonumber(f[2]) ~= PROTO then return end
    if channel == "WHISPER" then
        if not (WHISPER_OK[f[1]] and InGuild(who)) then return end
    elseif channel ~= "GUILD" then
        return
    end
    peers[who] = now()

    if f[1] == "H" then
        -- Answer after a random delay so a guild full of addon users doesn't reply at once.
        if now() - lastHelloReply >= 60 then
            lastHelloReply = now()
            C_Timer.After(1 + math.random() * 7, function() BroadcastSelf(true) end)
        end
        if IsResponder(who) then
            C_Timer.After(3, function() SendAllRecords(who) end)
        end
    elseif f[1] == "C" then
        MergeRecord(who, now(), f[3] or "", f[4] or "", now(), DecodeProfs(f[5] or ""))
    elseif f[1] == "R" then
        local name = f[3]
        if not name or name == "" then return end
        local profs = (f[8] and f[8] ~= "-") and DecodeProfs(f[8]) or nil
        local ours = MergeRecord(name, tonumber(f[4]) or 0, f[5] or "", f[6] or "", tonumber(f[7]) or 0, profs)
        if channel == "WHISPER" then
            local d = dumps[who] or { received = {}, localNewer = {} }
            dumps[who] = d
            d.received[name] = true
            if ours then d.localNewer[name] = true end
        end
    elseif f[1] == "E" then
        if channel == "WHISPER" then
            dumps[who] = dumps[who] or { received = {}, localNewer = {} }
            BackfillAfter(who)
        end
    elseif f[1] == "BM" or f[1] == "BT" or f[1] == "BL" then
        if channel == "WHISPER" then OnBankMessage(who, f) end
    end
end

---------------------------------------------------------------------------
-- Guild bank (scanned whenever someone with the addon opens it)
---------------------------------------------------------------------------

-- Errors and odd results are kept in the saved file (db.debug), so problems can be diagnosed later.
local function Note(msg)
    if not db then return end
    db.debug = db.debug or {}
    local log = db.debug.log or {}
    log[#log + 1] = date("%Y-%m-%d %H:%M:%S") .. " " .. msg
    while #log > 30 do table.remove(log, 1) end
    db.debug.log = log
end

local function ScanBankTab(tab)
    if not EnsureDB() or not bankOpen or not tab then return end
    local name, _, isViewable = GetGuildBankTabInfo(tab)
    if isViewable == false then return end -- only skip when the game says no; nil means unknown
    local items = {}
    for slot = 1, (MAX_GUILDBANK_SLOTS_PER_TAB or 98) do
        local link = GetGuildBankItemLink(tab, slot)
        if link then
            local _, count = GetGuildBankItemInfo(tab, slot)
            items[#items + 1] = { link = link, id = tonumber(link:match("item:(%d+)")), count = count or 1 }
        end
    end
    local old = db.bank.tabs[tab]
    -- An empty result right after opening usually means the tab hasn't arrived yet; keep what we had.
    if #items == 0 and old and old.items and #old.items > 0 and now() - (old.updated or 0) < 60 then return end
    db.bank.tabs[tab] = { name = name, items = items, updated = now() }
    Touch()
end

local function ScanAllBankTabs()
    for tab = 1, (GetNumGuildBankTabs() or 0) do
        local ok, err = pcall(ScanBankTab, tab)
        if not ok then Note("ScanBankTab " .. tab .. ": " .. tostring(err)) end
    end
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

local moneyLogRetries = 0

local function ScanMoneyLog()
    if not EnsureDB() or not GetNumGuildBankMoneyTransactions then return end
    if (GetNumGuildBankMoneyTransactions() or 0) == 0 then
        -- The update can arrive before the money log itself; ask again a couple of times.
        if moneyLogRetries < 3 and bankOpen and QueryGuildBankLog then
            moneyLogRetries = moneyLogRetries + 1
            C_Timer.After(2, function()
                if bankOpen then QueryGuildBankLog((MAX_GUILDBANK_TABS or 8) + 1) end
            end)
        else
            Note("money log stayed empty")
        end
        return
    end
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

local function StartBankScan(attempt)
    if not EnsureDB() or not bankOpen then return end
    bankScanStarted = now()
    moneyLogRetries = 0
    local numTabs = GetNumGuildBankTabs() or 0
    if numTabs == 0 and (attempt or 1) < 5 then
        -- Tab info can arrive a moment after the bank opens.
        C_Timer.After(1, function() StartBankScan((attempt or 1) + 1) end)
        ScanMoney()
        return
    end
    for tab = numTabs + 1, #db.bank.tabs do db.bank.tabs[tab] = nil end -- tabs that no longer exist

    -- Query tabs one by one; the server only sends one tab at a time.
    for tab = 1, numTabs do
        C_Timer.After((tab - 1) * 1.0, function()
            if not bankOpen then return end
            QueryGuildBankTab(tab)
            C_Timer.After(0.7, function()
                local ok, err = pcall(ScanBankTab, tab)
                if not ok then Note("ScanBankTab " .. tab .. ": " .. tostring(err)) end
            end)
        end)
    end
    C_Timer.After(numTabs * 1.0 + 0.5, function()
        if bankOpen and QueryGuildBankLog then
            QueryGuildBankLog((MAX_GUILDBANK_TABS or 8) + 1) -- the extra "tab" is the money log
        end
    end)
    -- Share once the tabs and the log have had time to arrive (even if the bank was closed early).
    C_Timer.After(numTabs * 1.0 + 3, ShareBank)
    ScanMoney()
end

local function OnBankOpened()
    bankOpen = true
    StartBankScan()
end

local function OnBankClosed()
    bankOpen = false
end

-- Items shared by others arrive as IDs; fill in names the client has loaded since.
local function FillItemLinks()
    if not db then return end
    for _, tab in pairs(db.bank.tabs) do
        for _, it in ipairs(tab.items or {}) do
            if not it.link and it.id then it.link = itemLinkFor(it.id) end
        end
    end
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
    elseif arg == "bank" then
        -- Open the guild bank first. Prints what the game reports, and saves it for diagnosing.
        local lines = {}
        local function out(m) lines[#lines + 1] = m; print_(m) end
        out(string.format("bank open: %s  tabs: %s  current: %s  MAX_TABS: %s  slots: %s",
            tostring(bankOpen), tostring(GetNumGuildBankTabs and GetNumGuildBankTabs()),
            tostring(GetCurrentGuildBankTab and GetCurrentGuildBankTab()), tostring(MAX_GUILDBANK_TABS),
            tostring(MAX_GUILDBANK_SLOTS_PER_TAB)))
        for tab = 1, (GetNumGuildBankTabs and GetNumGuildBankTabs() or 0) do
            local info = { GetGuildBankTabInfo(tab) }
            local links = 0
            for slot = 1, (MAX_GUILDBANK_SLOTS_PER_TAB or 98) do
                if GetGuildBankItemLink(tab, slot) then links = links + 1 end
            end
            local parts_ = {}
            for i = 1, 7 do parts_[i] = tostring(info[i]) end
            out(string.format("tab %d: %s  items: %d", tab, table.concat(parts_, ", "), links))
        end
        out("money log entries: " .. tostring(GetNumGuildBankMoneyTransactions and GetNumGuildBankMoneyTransactions()))
        out("C_GuildBank: " .. tostring(C_GuildBank ~= nil))
        if bankOpen then
            ScanAllBankTabs()
            ScanMoneyLog()
        end
        db.debug = db.debug or {}
        db.debug.bank = lines
    elseif arg == "names" then
        -- Shows how the game reports names, e.g. whether WoW Forever includes surnames and realms.
        local n, r = UnitName("player")
        print_(string.format("UnitName: [%s] realm: [%s]  stored as: [%s]", tostring(n), tostring(r), tostring(me())))
        local name, _, _, _, _, _, note = GetGuildRosterInfo(1)
        print_(string.format("Roster #1: [%s]  note: [%s]", tostring(name), tostring(note)))
        if UnitExists("target") and UnitIsPlayer("target") then
            local tn, tr = UnitName("target")
            print_(string.format("Target: [%s] realm: [%s]", tostring(tn), tostring(tr)))
        end
    elseif arg == "reset" then
        local guildName = GetGuildInfo("player")
        GuildSheetDB.guilds[guildName] = nil
        db = nil
        EnsureDB()
        RequestRoster()
        print_("Cleared saved data for " .. guildName .. ".")
    else
        local t = now()
        print_(string.format("%d members, %d with race, %d with professions. %d other addon users around.",
            CountKeys(db.roster, function(r) return r.inGuild end),
            CountKeys(db.chars, function(c) return c.race end),
            CountKeys(db.chars, function(c) return c.profs and #c.profs > 0 end),
            CountKeys(peers, function(seen) return t - seen < PEER_TIMEOUT end)))
        print_("Roster: " .. Ago(db.rosterUpdated) .. ". Guild bank: " .. Ago(db.bank.moneyUpdated) .. ".")
        print_("The helper sees new data after /reload or logout. Commands: /gsheet scan, /gsheet bank, /gsheet names, /gsheet reset")
    end
end

---------------------------------------------------------------------------
-- Events
---------------------------------------------------------------------------

local function SafeRegister(event)
    pcall(frame.RegisterEvent, frame, event) -- some events may not exist in Forever
end

frame:RegisterEvent("PLAYER_LOGIN")
frame:RegisterEvent("PLAYER_LOGOUT")
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
SafeRegister("GUILDBANK_UPDATE_TABS")
SafeRegister("GUILDBANK_UPDATE_MONEY")
SafeRegister("GUILDBANKLOG_UPDATE")

local function IsGuildBankInteraction(kind)
    return Enum and Enum.PlayerInteractionType and kind == Enum.PlayerInteractionType.GuildBanker
end

frame:SetScript("OnEvent", function(_, event, ...)
    if event == "PLAYER_LOGIN" then
        C_ChatInfo.RegisterAddonMessagePrefix(PREFIX)
        RequestRoster()
        -- Say hello once the roster has loaded: whispers are only accepted from known members,
        -- and we need to know who's online and their ranks.
        local tries = 0
        local function SayHello()
            tries = tries + 1
            if EnsureDB() then ScanRoster() end
            if db and db.rosterUpdated then
                BroadcastSelf(true)
                Queue("H" .. SEP .. PROTO, "GUILD")
            elseif tries < 12 then
                C_Timer.After(10, SayHello)
            end
        end
        C_Timer.After(10, SayHello)
        C_Timer.NewTicker(300, RequestRoster)
    elseif event == "PLAYER_LOGOUT" then
        FillItemLinks()
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
        if bankOpen then ScanAllBankTabs() end
    elseif event == "GUILDBANK_UPDATE_TABS" then
        if bankOpen then ScanAllBankTabs() end
    elseif event == "GUILDBANK_UPDATE_MONEY" then
        if bankOpen then
            ScanMoney()
            -- Gold moved while the bank is open: the gold log has a new entry, so read it again.
            moneyLogRetries = 0
            if QueryGuildBankLog then QueryGuildBankLog((MAX_GUILDBANK_TABS or 8) + 1) end
        end
    elseif event == "GUILDBANKLOG_UPDATE" then
        if bankOpen then ScanMoneyLog() end
    end
end)

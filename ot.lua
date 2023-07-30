require "io"
require "os"
require "string"
require "table"
require "math"
uv = require "luv"

require "split"
require "breaktree"

local function nvim_printf(format, ...) --> nil
    local msg = string.format(format, ...)
    local cmd = string.format('echo "%s"', msg)
    if vim then
        -- plugin execution
        vim.schedule(function()
            vim.api.nvim_command('echoh ErrorMsg')
            vim.api.nvim_command(cmd)
            vim.api.nvim_command('echoh None')
        end)
    else
        -- test execution
        print(cmd)
    end
end

local function log_printf(format, ...) --> nil
    local msg = string.format(format, ...)
    local f = io.open("log", "a")
    f:write("lua| ")
    f:write(msg)
    f:close()
end

local function encode(s) --> string
    t = {}
    for c in string.gmatch(s, ".") do
        n = string.byte(c)
        if c == "\\" then
            c = "\\\\"
        elseif n >= 32 and n ~= 127 then
            -- ascii or utf8; noop
        elseif c == "\n" then
            c = "\\n"
        elseif c == "\r" then
            c = "\\r"
        elseif c == "\t" then
            c = "\\t"
        elseif c == "\b" then
            c = "\\b"
        elseif c == "\0" then
            c = "\\0"
        else
            -- control character
            c = string.format("\\x%.2x", n)
        end
        table.insert(t, c)
    end
    return table.concat(t, "")
end

local nibbles = {
    ["0"] = 0, ["1"] = 1, ["2"] = 2, ["3"] = 3, ["4"] = 4,
    ["5"] = 5, ["6"] = 6, ["7"] = 7, ["8"] = 8, ["9"] = 9,
    a = 10, b = 11, c = 12, d = 13, e = 14, f = 15,
    A = 10, B = 11, C = 12, D = 13, E = 14, F = 15,
}

local function decode(s) --> string
    t = {}
    state = 0 -- 1: after '\'; 2: after '\x'; 3: after '\xN'
    high = nil
    for c in string.gmatch(s, ".") do
        if state == 0 then
            if c == "\\" then
                state = 1
                -- begin escape
            else
                -- normal character
                table.insert(t, c)
            end
        elseif state == 1 then
            -- after one '\'
            if c == "x" then
                state = 2
            else
                if c == "\\" then
                    c = "\\"
                elseif c == "n" then
                    c = "\n"
                elseif c == "r" then
                    c = "\r"
                elseif c == "t" then
                    c = "\t"
                elseif c == "b" then
                    c = "\b"
                elseif c == "0" then
                    c = "\0"
                else
                    error("bad escape: " .. c)
                end
                table.insert(t, c)
                state = 0
            end
        elseif state == 2 then
            -- after '\x'
            high = nibbles[c]
            if high == nil then
                error("bad hex")
            end
            state = 3
        elseif state == 3 then
            -- after '\xN'
            low = nibbles[c]
            if low == nil then
                error("bad hex")
            end
            table.insert(t, string.char(16*high + low))
            state = 0
        end
    end
    return table.concat(t, "")
end

local function addrspec_connect(addrspec, on_connect) --> (conn|nil, err)
    local host, port, ok, conn, err
    -- detect a plain port
    local port = tonumber(addrspec)
    if port then
        conn, err = uv.new_tcp()
        if not conn then
            return nil, err
        end
        ok, err = conn:connect("localhost", port, on_connect)
        if not ok then
            conn:close()
            return nil, err
        end
        return conn, nil
    end

    -- detect host:port
    local idx = string.find(addrspec, ":")
    if idx then
        host = string.sub(addrspec, 1, idx-1)
        port = string.sub(addrspec, idx+1, #addrspec)
        conn, err = uv.new_tcp()
        if not conn then
            return nil, err
        end
        ok, err = conn:connect(host, port, on_connect)
        if not ok then
            conn:close()
            return nil, err
        end
        return conn, nil
    end

    -- detect path
    idx = string.find(addrspec, "/")
    if idx then
        conn, err = uv.new_pipe(false)
        if not conn then
            return nil, err
        end
        ok, err = conn:connect(addrspec, on_connect)
        if not ok then
            conn:close()
            return nil, err
        end
        log_printf("pipe.connect(%s, %p)\n", addrspec, on_connect)
        return conn, nil
    end

    error("addrspec must be port, host:port, or a path")
end


local function NewInsert(idx, text) --> table
    return {class="i", idx=idx, text=text}
end

local function NewDelete(idx, nchars, text) --> table, text may be nil
    return {class="d", idx=idx, nchars=nchars, text=text}
end

local function NewSubmission(seq, parent_seq, parent_id, ot) --> table
    return {
        class="s", seq=seq, parent_seq=parent_seq, parent_id=parent_id, ot=ot
    }
end

local function NewExternal(seq, ot) --> table, ot is either an Insert or Delete
    return {class="x", seq=seq, ot=ot}
end

local function NewAccept(seq) --> table
    return {class="a", seq=seq}
end

local function apply(ot, txt) --> text
    if ot.class == "i" then
        return table.concat(
            {string.sub(txt, 0, ot.idx), ot.text, string.sub(txt, ot.idx+1)}
        )
    elseif ot.class == "d" then
        return table.concat(
            {string.sub(txt, 0, ot.idx), string.sub(txt, ot.idx+ot.nchars+1)}
        )
    else
        error(string.format("unable to apply class = %s", ot.class))
    end
end

local function after(a, b) --> text
    if a.class ~= "i" and a.class ~= "d" then
        error(string.format("unable to after(a, b) of a.class = %s", a.class))
    end
    if b.class ~= "i" and b.class ~= "d" then
        error(string.format("unable to after(a, b) of b.class = %s", b.class))
    end
    if a.class == "i" then
        if b.class == "i" then
            if b.idx > a.idx then
                -- other inserts after us
                -- INDEPENDENT
                return a
            elseif b.idx == a.idx then
                -- other inserts at the same spot
                -- CONFLICT
                return NewInsert(a.idx + #b.text, a.text)
            else
                -- other inserts before us
                -- INDEPENDENT
                return NewInsert(a.idx + #b.text, a.text)
            end
        elseif b.class == "d" then
            if b.idx > a.idx then
                -- delete is after us
                -- INDEPENDENT
                return a
            elseif b.idx + b.nchars < a.idx then
                -- delete is before, no overlap
                -- INDEPENDENT
                return NewInsert(a.idx - b.nchars, a.text)
            else
                -- one of:
                -- - delete ends right where we insert; insert anyway
                -- - delete starts right where we start; insert anyway
                -- - delete overlaps us; insert anyway
                -- CONFLICT
                return NewInsert(b.idx, a.text)
            end
        end
    elseif a.class == "d" then
        if b.class == "i" then
            if b.idx > a.idx + a.nchars then
                -- other inserts after us, no overlap
                -- INDEPENDENT
                return a
            elseif b.idx < a.idx then
                -- other inserts before us
                -- INDEPENDENT
                return NewDelete(a.idx + #b.text, a.nchars, a.text)
            elseif b.idx == a.idx then
                -- other inserts right where we start to delete; leave it alone
                -- CONFLICT
                return NewDelete(a.idx + #b.text, a.nchars, a.text)
            elseif b.idx == a.idx + a.nchars then
                -- other inserts right where we stop deleting; leave it alone
                -- CONFLICT
                return a
            else
                -- insert into the section we hoped to delete; delete it too
                -- CONFLICT
                return NewDelete(a.idx, a.nchars + #b.text, None)
            end
        elseif b.class == "d" then
            if b.idx >= a.idx + a.nchars then
                -- delete is after us, no overlap
                -- INDEPENDENT if not equal else CONFLICT
                return a
            elseif b.idx + b.nchars <= a.idx then
                -- delete is before us, no overlap
                -- INDEPENDENT if not equal else CONFLICT
                return NewDelete(a.idx - b.nchars, a.nchars, a.text)
            elseif b.idx <= a.idx then
                -- other is before us (or tied) with some overlap
                if b.idx + b.nchars >= a.idx + a.nchars then
                    -- other deleted what we would delete already
                    -- CONFLICT
                    return None
                else
                    -- other is before and deletes part of what we would delete
                    -- CONFLICT
                    overlap = b.nchars - (a.idx - b.idx)
                    return NewDelete(b.idx, a.nchars - overlap, None)
                end
            elseif b.idx > a.idx then
                -- other is after us with some overlap
                if b.idx + b.nchars > a.idx + a.nchars then
                    -- other deletion would continue after us
                    -- CONFLICT
                    return NewDelete(a.idx, b.idx - a.idx, None)
                else
                    -- other deletion is contained within what we would delete
                    -- CONFLICT
                    return NewDelete(a.idx, a.nchars - b.nchars, None)
                end
            end
        end
    end
end

local function conflicts(a, b) --> bool
    if a.class == "i" and b.class == "i" then
        return a.idx == b.idx
    end

    if a.class == "d" and b.class == "d" then
        if a.idx > b.idx then
            a, b = b, a
        end
        return a.idx + a.nchars >= b.idx
    end

    if a.class == "i" then
        i, d = a, b
    else
        i, d = b, a
    end

    return i.idx >= d.idx and i.idx <= d.idx + d.nchars
end


function mkcb(obj, name)
    return function(...)
        log_printf("mkcb(%s) called!\n", name)
        return obj[name](obj, ...)
    end
end


local SocketTransport = {}
SocketTransport.__index = SocketTransport

-- A Transport should always keep trying to connect and communicate the
-- messages that have been passed to it.  The Client creates one Transport
-- object and submits each message just once, and lets the transport figure out
-- retries.
--
-- The Client shouldn't be responsible for the retry logic because it may vary
-- widely between different Transport mechanisms.
--
-- However, the Transport shouldn't be responsible for logging messages to file
-- in case of process failure, because that logic should only need to be
-- written once.
function SocketTransport:create(
    addrspec, connect_cb, msg_cb
) --> SocketTransport
    local self = {}
    setmetatable(self, SocketTransport)

    self.addrspec = addrspec
    self.connect_cb = connect_cb
    self.msg_cb = msg_cb

    self.backoff_timer, err = uv.new_timer()
    if not self.backoff_timer then
        -- not recoverable
        error(err)
    end

    self.schedule_timer, err = uv.new_timer()
    if not self.schedule_timer then
        -- not recoverable
        error(err)
    end

    -- configure per-connection state
    self:reset()

    -- configure state which persists across connections
    self.scheduled = false
    self.write_q = {}

    self:schedule()

    return self
end

function SocketTransport:reset() --> nil
    self.want_reset = false
    self.conn = nil
    self.connect = {
        started = false,
        returned = false,
        success = false,
        closing = false,
        in_backoff = false,
        backoff = 10, -- ms
        done = false
    }
    self.negotiate = {sent=false, recvd=false, done=false}
    self.leftovers = ""
    self.nextwrite = 1
    self.read_q = {}
end

function SocketTransport:advance_state() --> nil
    local ok, err, done
    log_printf("SocketTransport:advance_state\n")

    -- are we resetting?
    if self.want_reset then return end

    -- do we need to connect?
    if not self.connect.done then
        -- are we in a backoff period?
        if self.connect.in_backoff then return end
        -- do we need to start a new connection?
        if not self.connect.started then
            self.conn, err = addrspec_connect(
                self.addrspec, mkcb(self, "on_connect")
            )
            if not self.conn then
                -- not recoverable
                error(err)
            end
            self.connect.started = true
        end
        -- are we waiting for connect to return?
        if not self.connect.returned then return end
        -- did the connect complete successfully?
        if not self.connect.success then
            -- do we need to close the conn?
            if self.conn then
                if not self.connect.closing then
                    self.connect.closing = true
                    self.conn:close(mkcb(self, "on_connect_failed_close"))
                end
                return
            end
            -- reset some state
            self.connect.started = false
            self.connect.returned = false
            -- start a new backoff timer
            ok, err = self.backoff_timer:start(
                --self.connect.backoff, 0, mkcb(self, "on_backoff_timer")
                360000, 0, mkcb(self, "on_backoff_timer")
            )
            if not ok then
                -- not recoverable
                error(err)
            end
            self.connect.backoff = math.min(15000, self.connect.backoff * 2)
            self.connect.in_backoff = true
            return
        end
        -- connection success!
        self.connect.backoff = 0.01
        self.connect.done = true
        ok, err = self.conn:read_start(mkcb(self, "on_read"))
        if err ~= nil then
            -- not recoverable
            error(err)
        end
    end

    -- complete negotiation
    if not self.negotiate.done then
        if not self.negotiate.sent then
            self:send_display_name()
            self.negotiate.sent = true
        end
        -- wait for data
        if #self.read_q == 0 then return end
        line = table.remove(self.read_q, 1)
        err = self:read_negotiation(line)
        if err ~= nil then
            nvim_printf("negotiation failed: " .. err)
            log_printf("negotiation failed: %s\n", err)
            self:close_and_reset()
            return
        end
        log_printf("negotiation read: %s\n", line)
        self.negotiate.done = true
    end

    -- send any unsent writes
    for i = self.nextwrite, #self.write_q do
        log_printf("sening unset write[%d]: %s\n", i, tostring(self.write_q[i]))
        self:send_msg(self.write_q[i])
    end
    self.nextwrite = #self.write_q + 1

    -- read any unread lines
    for i = 1, #self.read_q do
        local msg, err = self:read_msg(line)
        -- not recoverable?
        if err then
            error("protocol error: " .. err)
        end
        self.msg_cb(msg)
    end
    self.read_q = {}
end

function SocketTransport:read_negotiation(line) --> err
    -- parse the server response
    local fields, err = split(line, ":", 4)
    if err then return err end
    local author_id = tonumber(fields[1])
    self.reconnect_secret = fields[2]
    local seqno = tonumber(fields[3])
    local text = decode(fields[4])
    self.connect_cb(author_id, seqno, text)
end

function SocketTransport:read_msg(line) --> msg, err
    local t, err = split(line, ":", 2)
    if err then return nil, err end

    if t[1] == "x" then
        -- e"x"ternal edit
        local x, err = split(t[2], ":", 4)
        if err then return nil, err end
        local ot
        if x[2] == "i" then
            ot = NewInsert(tonumber(x[3]), decode(x[4]))
        elseif x[2] == "d" then
            ot = NewDelete(tonumber(x[3]), tonumber(x[4]))
        else
            return nil, string.format("unrecognized edit type: %s", line)
        end
        return NewExternal(tonumber(x[1]), ot)
    end

    if t[1] == "a" then
        -- "a"ccepted message
        local seq = tonumber(t[2])
        -- seq should match the seq of the first submission in our queue
        -- XXX: where do ac"k" messages fit in this logic?
        local first = self.write_q[1]
        if not first then
            return nil, string.format(
                "got a:%d with an empty write queue", seq
            )
        end
        if first.seq ~= seq then
            return nil, string.format(
                "expected a:%d but got a:%d", first.seq, seq
            )
        end
        -- we can forget that message now
        table.remove(self.write_q, 1)
        self.nextwrite = self.nextwrite - 1
        return NewAccept(seq)
    end

    return nil, string.format("unrecognized line: %s", line)
end

function SocketTransport:send_display_name()
    local user = os.getenv("USER")
    local display
    if user ~= nil then
        display = string.format("%s on nvim", user)
    else
        display = "nvim"
    end
    self:send_bytes(string.format("new:%s\n", display))
end

function SocketTransport:send_msg(msg)
    local arg
    assert(msg.class == "s")
    if msg.ot.class == "i" then
        -- insertion: arg is encoded text
        arg = encode(arg)
    elseif msg.ot.class == "d" then
        -- deletion: arg is a deletion count
        arg = tostring(arg)
    else
        error(string.format("unknown write_edit type: %s", msg.ot.class))
    end
    local bytes = string.format(
        "s:%d:%d:%d:%s:%d:%s\n",
        msg.seq, msg.parent_seq, msg.parent_id, msg.ot.class, msg.ot.idx, arg
    )
    self:send_bytes(bytes)
end

function SocketTransport:send_bytes(bytes) --> nil
    log_printf("writing bytes: %s\n", bytes)
    local ok, err = self.conn:write(bytes, mkcb(self, "on_write"))
    if not ok then
        -- not recoverable
        error("write submission failed: " .. err)
    end
end

function SocketTransport:on_write(err) --> nil
    log_printf("SocketTransport:on_write(%s)\n", err)
    if self.want_reset or not err then return end
    nvim_printf("write failed, reconnecting...")
    log_printf("write failed, reconnecting...\n")
    self:close_and_reset()
end

function SocketTransport:on_connect(err) --> nil
    log_printf("on_connect! err=%s\n", err)
    self.connect.returned = true
    self.connect.success = err == nil
    self:advance_state()
end

function SocketTransport:on_connect_failed_close() --> nil
    self.connect.closing = false
    self.conn = nil
    self:advance_state()
end

function SocketTransport:on_backoff_timer() --> nil
    self.connect.in_backoff = false
    self:advance_state()
end

function SocketTransport:close_and_reset() --> nil
    log_printf("close_and_reset!\n")
    self.want_reset = true
    self.conn:close(mkcb(self, "on_close_for_reset"))
end

function SocketTransport:on_close_for_reset() --> nil
    self:reset()
    self:advance_state()
end

function SocketTransport:on_read(err, chunk) --> nil
    if self.want_reset then return end
    if err then
        nvim_printf("read failed (%s), reconnecting...", err)
        log_printf("read failed (%s), reconnecting...\n", err)
        self:close_and_reset()
        return
    end
    if not chunk then
        nvim_printf("unexpected eof, reconnecting...")
        log_printf("unexpected eof, reconnecting...\n")
        self:close_and_reset()
        return
    end
    log_printf("SocketTransport read: %s\n", encode(chunk))
    t = split_soft(self.leftovers .. chunk, "\n")
    for i = 1, #t-1 do
        table.insert(self.read_q, t[i])
    end
    self.leftovers = t[#t]
    self:advance_state()
end

function SocketTransport:schedule() --> nil
    if self.scheduled then return end
    self.scheduled = true
    local ok, err = self.schedule_timer:start(
        0, 0, mkcb(self, "on_schedule_timer")
    )
    if not ok then
        -- not recoverable
        error(err)
    end
end

function SocketTransport:on_schedule_timer() --> nil
    self.scheduled = false
    self:advance_state()
end

-- Client-facing API call
function SocketTransport:write(msg) --> nil
    table.insert(self.write_q, msg)
    self:schedule()
end


local Client = {}
Client.__index = Client

function Client:create(addrspec, vim) --> Client
    local self = {}
    setmetatable(self, Client)

    self.vim = vim

    self.transport = SocketTransport:create(
        addrspec, mkcb(self, "on_connect"), mkcb(self, "on_msg")
    )

    self.schedule_timer, err = uv.new_timer()
    if not self.schedule_timer then
        -- not recoverable
        error(err)
    end

    self.scheduled = false
    self.connected = false
    self.author_id = nil
    self.text = nil
    self.first_sync = false

    self.latest_server_seq = nil
    self.seq = 0
    self.inflight = {}

    return self
end

-- luv callback
function Client:on_connect(author_id, seqno, text) --> nil
    if self.connected then
        error("got a secondary on_connect call")
    end
    self.connected = true
    self.author_id = author_id
    log_printf("Client:on_connect(author_id=%d, seqno=%d text=%s)\n", author_id, seqno, encode(text))
    self.text = text
    self:schedule()
end

-- luv callback
function Client:on_msg(msg) --> nil
    table.insert(self.msg_q, msg)
    self:schedule()
end

-- transition from luv context to vim context
function Client:schedule() --> nil
    self.vim.schedule(mkcb(self, "advance_state"))
end

-- must only execute within vim-safe callbacks
function Client:advance_state() --> nil
    if not self.first_sync then
        vim.api.nvim_buf_set_lines(
            0,    -- current buffer
            0,    -- first line
            -1,   -- to last line
            true, -- strict indexing
            split_soft(self.text, "\n")
        )
        self.first_sync = true
    end
    -- XXX: process messages in self.msg_q
end

function Client:make_edit(ot) --> Submission
    local parent_seq, parent_author
    if #c.inflight > 0 then
        parent_seq = c.seq
        parent_author = c.author_id
    else
        parent_seq = c.latest_server_seq
        parent_author = 0  -- server author id
    end
    c.seq = c.seq + 1
    local s = NewSubmission(c.seq, parent_seq, parent_id, ot)
    table.insert(c.inflight, s)
    return s
end

-- vim-safe callback
function Client:on_insert(idx, text) --> nil
    if not self.first_sync then return end
    local msg = self:make_edit(NewInsert(idx, text))
    self.transport:write(msg)
end

-- vim-safe callback
function Client:on_delete(idx, nchars, text) --> nil
    if not self.first_sync then return end
    local s = self:make_edit(NewDelete(idx, nchars, text))
    self.transport:write(msg)
end


if vim then
    -- plugin execution, against the real vim
    c = Client:create("./asdf", vim)
    bt = breaktree.BreakTree.create()

    -- truncate log file
    f = io.open("log", "w")
    f:close()

    local function on_bytes(
        x, buf, tick, sr, sc, s, oer, oec, ol, ner, nec, nl
    )
        -- broadcast operational transforms to the server
        -- important args are s (start), ol (old len), and nl (new len)
        if ol > 0 then
            -- XXX find deleted chars too
            c:on_delete(s, ol, nil)
        end
        if nl > 0 then
            -- emit an insertion
            if ner == 0 then
                -- when on the same line, nec is relative
                nec = sc + nec
            end
            -- ner is always relative
            ner = sr + ner
            text = vim.api.nvim_buf_get_text(buf, sr, sc, ner, nec, {})
            text = table.concat(text, "\n")
            c:on_insert(s, text)
        end
    end

    vim.api.nvim_buf_attach(0, false, {on_bytes=on_bytes})
else
    -- library execution
    ot = {
        encode = encode,
        decode = decode,
        SocketTransport = SocketTransport,
        Client = Client,
        NewInsert = NewInsert,
        NewDelete = NewDelete,
        NewSubmission = NewSubmission,
        NewExternal = NewExternal,
        NewAccept = NewAccept,
        apply = apply,
        after = after,
        conflicts = conflicts,
    }
end

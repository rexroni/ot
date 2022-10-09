require "io"
require "os"
require "string"
require "table"
uv = require "luv"

function nvim_printf(format, ...)
    msg = string.format(format, ...)
    cmd = string.format('echo "%s"', msg)
    if vim ~= nil then
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

function encode(s)
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

nibbles = {
    ["0"]= 0, ["1"]= 1, ["2"]= 2, ["3"]= 3, ["4"]= 4,
    ["5"]= 5, ["6"]= 6, ["7"]= 7, ["8"]= 8, ["9"]= 9,
    a= 10, b= 11, c= 12, d= 13, e= 14, f= 15,
    A= 10, B= 11, C= 12, D= 13, E= 14, F= 15,
}

function decode(s)
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

-- expect exactly n splits
function split(s, c, n)
    out = {}
    start = 1
    while #out + 1 < n do
        idx = string.find(s, c, start)
        if idx == nil then
            error("not enough fields to split")
        end
        table.insert(out, string.sub(s, start, idx-1))
        start = idx + 1
    end
    -- the last field is just whatever's left
    table.insert(out, string.sub(s, start))
    return out
end

-- up to n splits, where n is optional
function split_soft(s, c, n)
    out = {}
    start = 1
    while n == nil or #out + 1 < n do
        idx = string.find(s, c, start)
        if idx == nil then
            break
        end
        table.insert(out, string.sub(s, start, idx-1))
        start = idx + 1
    end
    -- the last field is just whatever's left
    table.insert(out, string.sub(s, start))
    return out
end


Client = {}
Client.__index = Client

function Client:create()
    local c = {}
    setmetatable(c, Client)

    -- luv callbacks need to be bound functions, with special error handling
    local function uv_method(n)
        return function(...)
            local ok
            local msg
            ok, msg = pcall(Client[n], c, ...)
            if not ok then
                c:fail(msg)
            end
        end
    end
    c.on_connect = uv_method("on_connect")
    c.on_close = uv_method("on_close")
    c.on_read = uv_method("on_read")
    c.on_write = uv_method("on_write")

    c.pipe = uv.new_pipe(false)
    assert(c.pipe)

    -- when we aren't connected, we queue up edits we would like to make
    c.write_q = {}

    c.failed = false
    c.leftovers = ""
    c.listen_state = nil

    c.author_id = nil
    c.edit_seq = 0
    c.latest_server_seq = 0
    c.in_flight = {}

    -- our own undoable history
    c.history = {}

    -- state machine data
    c.connected = false
    c.negotiate = {sent=false, recvd=false, done=false}
    c.closed = false

    -- start trying to connect
    assert(c.pipe:connect("./asdf", c.on_connect))

    return c
end

-- when we hit an error, we have to shut down gracefully
function Client:fail(msg)
    local c = self
    if not c.failed then
        nvim_printf('giving up on doc sync: %s', msg)
        -- start shutting down
        c.failed = true
        local ok
        local msg
        ok, msg = pcall(Client.advance_state, c)
        if not ok then
            c:fail(msg)
        end
    else
        -- failure while handling failiures
        nvim_printf('failed while giving up: %s', msg)
    end
end

function Client:send_display_name()
    local c = self
    local user = os.getenv("USER")
    local display = nil
    if user ~= nil then
        display = string.format("%s on nvim", user)
    else
        display = "nvim"
    end
    assert(c.pipe:write(string.format("new:%s\n", display), c.on_write))
end

function Client:advance_state()
    local c = self

    -- failure cleanup
    if c.failed then
        if c.pipe ~= nil then
            if not c.pipe:is_closing() then
                c.pipe:close(c.on_close)
                return
            end
            return
        end
        -- c.pipe == nil, connection is closed
        if not c.closed then
            c.closed = true
        end
        return
    end

    -- wait for the initial connection
    if not c.connected then
        return
    end

    -- complete negotiation
    if not c.negotiate.done then
        if not c.negotiate.sent then
            c:send_display_name()
            c.listen_state = "negotiate"
            c.negotiate.sent = true
        end
        if not c.negotiate.recvd then
            return
        end
        c.listen_state = "ot"
        c.negotiate.done = true
        -- send any queued edits we had
        for _, msg in ipairs(c.write_q) do
            c:write_edit_direct(msg[1], msg[2], msg[3])
        end
        c.write_q = nil
    end
end

function Client:on_connect(err)
    local c = self
    if err ~= nil then
        error("connect failed: " .. err)
    end
    c.connected = true
    assert(c.pipe:read_start(c.on_read))
    c:advance_state()
end

function Client:on_close()
    local c = self
    c.pipe = nil
    c:advance_state()
end

function Client:on_write(err)
    -- TODO: on error, reconnect and renegotiate
    -- ignore secondary write failures
    if err ~= nil and not c.failed then
        error("write failed: " .. err)
    end
end

function Client:on_read(err, chunk)
    local c = self
    -- TODO: on error, reconnect and renegotiate
    if err ~= nil then
        -- ignore secondary read failures
        if c.failed then
            return
        end
        error("read failed: " .. err)
    end
    if chunk == nil then
        error("EOF")
    end
    t = split_soft(c.leftovers .. chunk, "\n")
    for i = 1, #t-1 do
        c:on_line(t[i])
    end
    c.leftovers = t[#t]
end

function Client:on_line(line)
    local c = self
    if c.listen_state == "negotiate" then
        -- parse the server response
        local fields = split(line, ":", 3)
        c.author_id = tonumber(fields[1])
        c.reconnect_secret = decode(fields[2])
        -- TODO: deal with text now that we have it
        local text = decode(fields[3])
        c.negotiate.recvd = true
        c:advance_state()

    elseif c.listen_state == "ot" then
        local t = split(line, ":", 2)
        if t[1] == "x" then
            -- e"x"ternal edit
            local x = split(t[2], ":", 4)
            c.edit_seq = tonumber(x[1])
            if x[2] == "i" then
                c:on_external_insert(edit_seq, tonumber(x[3]), decode(x[4]))
            elseif x[2] == "d" then
                c:on_external_delete(edit_seq, tonumber(x[3]), tonumber(x[4]))
            else
                error(string.format("unrecognized edit type: %s", line))
            end
        elseif t[1] == "a" then
            -- "a"ccepted message
            edit_seq = tonumber(t[2])
            c:on_accept(edit_seq)
        else
            error(string.format("unrecognized line: %s", line))
        end

    else
        error("server sent data in invalid listen state")
    end
end

function Client:on_external_insert(edit_seq, idx, text)
    error("on_external_insert")
end

function Client:on_external_delete(edit_seq, idx, count)
    error("on_external_delete")
end

function Client:on_accept(edit_seq)
    error("on_accept")
end

-- actually write to the wire
function Client:write_edit_direct(typ, idx, arg)
    local c = self
    if typ == "i" then
        -- insertion: arg is encoded text
        arg = encode(arg)
    elseif typ == "d" then
        -- deletion: arg is a deletion count
        arg = tostring(arg)
    else
        error(string.format("unknown write_edit type: %s", typ))
    end
    local parent_seq = nil
    local parent_author = nil
    if #c.in_flight > 0 then
        parent_seq = c.edit_seq
        parent_author = c.author_id
    else
        parent_seq = c.latest_server_seq
        parent_author = 0  -- server author id
    end
    c.edit_seq = c.edit_seq + 1
    local msg = string.format(
        "s:%d:%d:%d:%s:%d:%s\n",
        c.edit_seq, parent_seq, parent_author, typ, idx, arg
    )
    assert(c.pipe:write(msg, c.on_write))
    -- keep track of which edits we have in flight
    table.insert(c.in_flight, msg)
end

-- write to the wire, or save it if are disconnected
-- arg is either still-unencoded insertion text, or a deletion count
function Client:write_edit(typ, idx, arg)
    local c = self
    if c.write_q == nil then
        c:write_edit_direct(typ, idx, arg)
    else
        -- remember for later
        table.insert(c.write_q, {typ, idx, arg})
    end
end

if vim ~= nil then
    -- plugin execution
    c = Client:create()

    function on_bytes(x, buf, tick, sr, sc, s, oer, oec, ol, ner, nec, nl)
        -- broadcast operational transforms to the server
        -- important args are s (start), ol (old len), and nl (new len)
        if ol > 0 then
            -- emit a deletion
            c:write_edit("d", s, ol)
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
            c:write_edit("i", s, text)
        end
    end

    vim.api.nvim_buf_attach(0, false, {on_bytes=on_bytes})
end

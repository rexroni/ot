require "io"
require "string"
require "table"
uv = require "luv"

function encode(s)
    t = {}
    for c in string.gmatch(s, ".") do
        n = string.byte(c)
        if c == "\\" then
            c = "\\\\"
        elseif n >= 32 and not n == 127 then
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
            c = string.byte("\\%.2x", n)
        end
        table.insert(t, c)
    end
    return table.concat(t, "")
end

pipe = uv.new_pipe(false)
assert(pipe)

-- we aren't allowed to call nvim_buf_attach in a libuv callback, so instead
-- we have to buf_attach now and tolerate update-before-connect later
write_q = {}
function writef(...)
    msg = string.format(...)
    if write_q == nil then
        -- TODO: on error, requeue message and reconnect
        pipe:write(msg)
    else
        -- remember for later
        write_q[#write_q+1] = msg
    end
end

assert(pipe:connect("asdf", function(err)
    if not err == nil then
        error(err)
    end
    for _, msg in ipairs(write_q) do
        -- TODO: on error, requeue message and reconnect
        pipe:write(string.format("delete(idx=%d, nchars=%d)\n", s, ol))
    end
    write_q = nil
end))

function on_bytes(x, buf, tick, sr, sc, s, oer, oec, ol, ner, nec, nl)
    -- broadcast operational transforms to the server
    -- important args are s (start), ol (old len), and nl (new len)
    if ol > 0 then
        -- emit a deletion
        writef("d:%d:%d\n", s, ol)
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
        writef("i:%d:%s\n", s, encode(text))
    end
end

vim.api.nvim_buf_attach(0, false, {on_bytes=on_bytes})

require "io"
require "table"
require "string"

require "split"
require "ot"

local function test_encode_decode()
    local t = {}
    for i=0, 127 do
        table.insert(t, string.char(i))
    end
    local base = table.concat(t, "")

    local expect = ""
        .. "\\0"
        .. "\\x01\\x02\\x03\\x04\\x05\\x06\\x07"
        .. "\\b\\t\\n"
        .. "\\x0b\\x0c"
        .. "\\r"
        .. "\\x0e\\x0f\\x10\\x11\\x12\\x13\\x14\\x15\\x16"
        .. "\\x17\\x18\\x19\\x1a\\x1b\\x1c\\x1d\\x1e\\x1f"
        .. " !\"#$%&'()*+,-./0123456789:;<=>?@ABCDEFGHIJKLMNOPQRSTUVWXYZ["
        .. "\\\\"
        .. "]^_`abcdefghijklmnopqrstuvwxyz{|}~"
        .. "\\x7f"

    local encoded = ot.encode(base)
    if encoded ~= expect then
        print(encoded)
        print(expect)
        error("encoded != expect")
    end

    local decoded = ot.decode(encoded)
    if decoded ~= base then
        print(decoded)
        print(base)
        error("decoded != base")
    end
end

local function test_suite()

    local function read_obj(blob)
        local x = split_soft(blob, ":", 3)
        if x[1] == "i" then
            return ot.NewInsert(tonumber(x[2]), ot.decode(x[3]))
        elseif x[1] == "d" then
            return ot.NewDelete(tonumber(x[2]), tonumber(x[3]))
        elseif x[1] == "x" then
            return nil
        else
            error(string.format("unrecognized object: %s", blob))
        end
    end

    local function show_obj(obj)
        if obj == nil then
            return "\"x\""
        end
        local arg
        if obj.class == "i" then
            arg = obj.text
        else
            arg = tostring(obj.nchars)
        end
        return string.format("\"%s:%d:%s\"", obj.class, obj.idx, arg)
    end

    for line in io.lines("test_suite") do
        -- skip empty lines and comments
        if line == "" or string.sub(line, 1, 1) == "#" then goto nextline end
        local x = split_soft(line, "|")
        if x[1] == "apply" then
            local obj = read_obj(x[2])
            local text = x[3]
            local exp = x[4]
            local got = ot.apply(obj, text)
            if got ~= exp then
                error(
                    string.format(
                        "apply(%s, \"%s\") returns \"%s\" but expected \"%s\"",
                        show_obj(obj), text, got, exp
                    )
                )
            end
        elseif x[1] == "after" then
            local a = read_obj(x[2])
            local b = read_obj(x[3])
            local exp = read_obj(x[4])
            local got = ot.after(a, b)
            if show_obj(got) ~= show_obj(exp) then
                error(
                    string.format(
                        "after(%s, %s) returns %s but expected %s",
                        show_obj(a), show_obj(b), show_obj(got), show_obj(exp)
                    )
                )
            end
        elseif x[1] == "conflicts" then
            local a = read_obj(x[2])
            local b = read_obj(x[3])
            local exp = (x[4] == "true")
            local got = ot.conflicts(a, b)
            if got ~= exp then
                error(
                    string.format(
                        "conflicts(%s, %s) returns %s but expected %s",
                        show_obj(a), show_obj(b), tostring(got), tostring(exp)
                    )
                )
            end
        else
            error(string.format("unparsed line of test_suite: %s", line))
        end
        ::nextline::
    end
end

test_encode_decode()
test_suite()
print("PASS!")

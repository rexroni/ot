require "table"
require "string"

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

test_encode_decode()
print("PASS!")

require "table"
require "string"

-- expect exactly n splits
function split(s, c, n) --> table(string...), err
    out = {}
    start = 1
    while #out + 1 < n do
        idx = string.find(s, c, start)
        if idx == nil then
            return nil, "not enough fields to split"
        end
        table.insert(out, string.sub(s, start, idx-1))
        start = idx + 1
    end
    -- the last field is just whatever's left
    table.insert(out, string.sub(s, start))
    return out
end

-- up to n splits, where n is optional
function split_soft(s, c, n) --> table(string...)
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

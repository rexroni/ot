require "split"

local function test_split()
    local out, err = split("a::b:", ":", 4)
    if err then error(err) end
    if #out ~= 4 then
        error(string.format("wrong number of splits: %d", #out))
    end
    if out[1] ~= "a" then error("expected \"a\" but got " .. out[1]) end
    if out[2] ~= ""  then error("expected \"\" but got " .. out[2]) end
    if out[3] ~= "b" then error("expected \"b\" but got " .. out[3]) end
    if out[4] ~= ""  then error("expected \"\" but got " .. out[4]) end

    out, err = split("a::b:", ":", 3)
    if err then error(err) end
    if #out ~= 3 then
        error(string.format("wrong number of splits: %d", #out))
    end
    if out[1] ~= "a" then error("expected \"a\" but got " .. out[1]) end
    if out[2] ~= ""  then error("expected \"\" but got " .. out[2]) end
    if out[3] ~= "b:" then error("expected \"b:\" but got " .. out[3]) end

    out, err = split("a::b:", ":", 5)
    if not err then error("expected failure but got success") end

    out, err = split_soft("a::b:", ":", 5)
    if err then error(err) end
    if out[1] ~= "a" then error("expected \"a\" but got " .. out[1]) end
    if out[2] ~= ""  then error("expected \"\" but got " .. out[2]) end
    if out[3] ~= "b" then error("expected \"b\" but got " .. out[3]) end
    if out[4] ~= ""  then error("expected \"\" but got " .. out[4]) end
end

test_split()
print("PASS!")

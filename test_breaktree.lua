require "math"
require "string"

require "breaktree"

local function check_tree(parent, node) --> node count, len sum
    if not node then return 0, 0 end

    -- parent link is correct
    assert(parent == node.parent)

    -- recurse first
    local lcount, lsum = check_tree(node, node.l)
    local rcount, rsum = check_tree(node, node.r)
    assert(node.lcount == lcount)
    assert(node.lsum == lsum)

    -- nodes with nil children must be level 0
    if not node.l or not node.r then
        assert(node.level == 0)
    end

    -- left children must 1 level lower
    if node.l then
        assert(node.level == node.l.level + 1)
    end

    -- right children may be the same level or one level lower
    if node.r then
        assert(node.level >= node.r.level)
        assert(node.level <= node.r.level + 1)
    end

    -- no double-right-links
    if node.r and node.r.r then
        assert(node.level ~= node.r.r.level)
    end

    return lcount + 1 + rcount, lsum + node.len + rsum
end

local function build_test_tree() --> BreakTree
    --
    -- return a tree that should cover all possible insert and deletion cases
    --
    --               h3
    --       d2                  n2
    --   b1      f1      j1          p1
    -- a0  c0  e0  g0  i0    l1    o0      s1
    --                     k0  m0      q0    t0
    --                                   r0    u0

    local function mknode(len, level, prev, parent, child) --> Line
        assert(level)
        local node = breaktree.Line:create(len)
        node.level = level
        if prev then
            node.p = prev
            prev.n = node
        end
        if parent then
            node.parent = parent
            -- assume we build left-to-right
            parent.r = node
        end
        if child then
            child.parent = node
            -- assume we build left-to-right
            node.l = child
        end
        return node
    end

    local a = mknode(1,  0, nil, nil, nil)
    local b = mknode(2,  1, a, nil, a)
    local c = mknode(3,  0, b, b, nil)
    local d = mknode(4,  2, c, nil, b)
    local e = mknode(5,  0, d, nil, nil)
    local f = mknode(6,  1, e, d, e)
    local g = mknode(7,  0, f, f, nil)
    local h = mknode(8,  3, g, nil, d)
    local i = mknode(9,  0, h, nil, nil)
    local j = mknode(10, 1, i, nil, i)
    local k = mknode(11, 0, j, nil, nil)
    local l = mknode(12, 1, k, j, k)
    local m = mknode(13, 0, l, l, nil)
    local n = mknode(14, 2, m, h, j)
    local o = mknode(15, 0, n, nil, nil)
    local p = mknode(16, 1, o, n, o)
    local q = mknode(17, 0, p, nil, nil)
    local r = mknode(18, 0, q, q, nil)
    local s = mknode(19, 1, r, p, q)
    local t = mknode(20, 0, s, s, nil)
    local u = mknode(21, 0, t, t, nil)

    local bt = breaktree.BreakTree:create()
    a.p = bt
    u.n = bt
    bt.n = a
    bt.p = n
    bt.r = h
    h.parent = bt

    local function counts(node) -->
        if not node then return 0, 0 end
        node.lcount, node.lsum = counts(node.l)
        local rcount, rsum = counts(node.r)
        return node.lcount + 1 + rcount, node.lsum + node.len + rsum
    end

    local count, sum = counts(h)
    assert(count == 21 and sum == 21*22/2)

    check_tree(bt, bt.r)

    return bt
end

local function test_insert_delete_nodes()
    local bt, node
    -- make sure our tree starts valid
    local bt = build_test_tree()
    check_tree(bt, bt.r)

    -- insert before each node
    for i = 1, 21 do
        bt = build_test_tree()
        node = bt
        for j = 1, i do
            node = node.n
        end
        assert(node.len == i)
        bt:insert_line(node, 1)
        check_tree(bt, bt.r)
    end

    -- delete each node
    for i = 1, 21 do
        bt = build_test_tree()
        node = bt
        for j = 1, i do
            node = node.n
        end
        assert(node.len == i)
        bt:delete_line(node)
        check_tree(bt, bt.r)
    end
end

local function test_insert_delete_text()
    local bt = breaktree.BreakTree:create()
    local i, l, r, count, sum, sl, sr, el, er

    local verbose = false

    local function show(msg) --> nil
        if not verbose then return end
        print('--- "' .. msg .. '" ---')
        bt:print()
    end

    -- no-newline-insert
    l, r = bt:insert_text(0, "a")
    show("a|")
    assert(l == 0 and r == 0)
    count, sum = check_tree(bt, bt.r)
    assert(count == 1 and sum == 2)

    -- insert a single line
    l, r = bt:insert_text(1, "\n")
    show("a||")
    assert(l == 0 and r == 1)
    count, sum = check_tree(bt, bt.r)
    assert(count == 2 and sum == 3)

    -- insert multiple lines, last line empty
    l, r = bt:insert_text(1, "b\nbb\n")
    show("ab|bb|||")
    assert(l == 0 and r == 1)
    count, sum = check_tree(bt, bt.r)
    assert(count == 4 and sum == 8)

    -- insert multiple lines, last line nonempty
    l, r = bt:insert_text(4, "c\nccc\ncc")
    show("ab|bc|ccc|ccb|||")
    assert(l == 1 and r == 1)
    count, sum = check_tree(bt, bt.r)
    assert(count == 6 and sum == 16)

    -- insert multiple lines, first and last empty
    l, r = bt:insert_text(9, "\nddd\n")
    show("ab|bc|ccc|ddd||ccb|||")
    assert(l == 2 and r == 3)
    count, sum = check_tree(bt, bt.r)
    assert(count == 8 and sum == 21)

    -- delete text from a single line
    sl, sr, el, er = bt:delete_text(7, 1)
    show("ab|bc|cc|ddd||ccb|||")
    assert(sl == 2 and sr == 1 and el == 2 and er == 2)
    count, sum = check_tree(bt, bt.r)
    assert(count == 8 and sum == 20)

    -- delete just a line break
    sl, sr, el, er = bt:delete_text(8, 1)
    show("ab|bc|ccddd||ccb|||")
    assert(sl == 2 and sr == 2 and el == 2 and er == 3)
    count, sum = check_tree(bt, bt.r)
    assert(count == 7 and sum == 19)

    -- delete in the middle of a line
    sl, sr, el, er = bt:delete_text(8, 1)
    show("ab|bc|ccdd||ccb|||")
    assert(sl == 2 and sr == 2 and el == 2 and er == 3)
    count, sum = check_tree(bt, bt.r)
    assert(count == 7 and sum == 18)

    -- delete the end of a line
    sl, sr, el, er = bt:delete_text(14, 1)
    show("ab|bc|ccdd||cc|||")
    assert(sl == 4 and sr == 2 and el == 4 and er == 3)
    count, sum = check_tree(bt, bt.r)
    assert(count == 7 and sum == 17)

    -- delete exactly one line
    sl, sr, el, er = bt:delete_text(12, 3)
    show("ab|bc|ccdd||||")
    assert(sl == 4 and sr == 0 and el == 4 and er == 3)
    count, sum = check_tree(bt, bt.r)
    assert(count == 6 and sum == 14)

    -- delete from the middle of a one line to middle of another
    sl, sr, el, er = bt:delete_text(4, 4)
    show("ab|bdd||||")
    assert(sl == 1 and sr == 1 and el == 2 and er == 2)
    count, sum = check_tree(bt, bt.r)
    assert(count == 5 and sum == 10)

    -- delete the rest
    sl, sr, el, er = bt:delete_text(0, 9)
    show("|")
    assert(sl == 0 and sr == 0 and el == 3 and er == 1)
    count, sum = check_tree(bt, bt.r)
    assert(count == 1 and sum == 1)
end

test_insert_delete_nodes()
test_insert_delete_text()
print("PASS!")

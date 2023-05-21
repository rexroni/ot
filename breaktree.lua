require "string"

require "split"

-- breaktree: a data structure to convert rapidly between line/column and index
-- - implements an Andersson tree for balancing
-- - nodes track cumulative line and character counts for O(log(n)) lookups
--   from index to line/column and back
-- - nodes track text content to be able to look up deleted text after a
--   deletion occurs

local function print_tree(node, indent) --> nil
    if not node then return end
    if not indent then indent = 0 end
    print_tree(node.l, indent + 2)
    local space = ""
    for i=1, indent do
        space = space .. " "
    end
    print(space .. node:repr())
    print_tree(node.r, indent + 2)
end

local Line = {}
Line.__index = Line

function Line:create(text) --> Line
    local self = {}
    setmetatable(self, Line)

    -- andersson level
    self.level = 0
    -- parent of this node
    self.parent = nil
    -- child to the left
    self.l = nil
    -- child to the right
    self.r = nil
    -- next node
    self.n = nil
    -- prev node
    self.p = nil

    -- the text of this line
    self.text = text
    -- the sum of lengths to the left of this node
    self.lsum = 0
    -- the count of lines to the left of this node
    self.lcount = 0

    return self
end

function Line:repr() --> string
    return string.format(
        "[level=%d, len=%d lcount=%d, lsum=%d]",
        self.level, #self.text, self.lcount, self.lsum
    )
end

-- skew: turn a left-horizontal link into a right-horizontal link
function Line:skew() --> Line (the new root)
    if not self.l or self.l.level ~= self.level then return self end
    --
    --        self           out
    --       /    \         /   \
    --    out      b  -->  a     self
    --   /   \                  /    \
    --  a     child        child      b
    --
    local parent = self.parent
    local out = self.l
    local child = out.r
    self.l = child
    out.r = self
    if child then child.parent = self end
    self.parent = out
    out.parent = parent
    self.lsum = self.lsum - out.lsum - #out.text
    self.lcount = self.lcount - out.lcount - 1
    if parent.r == self then
        parent.r = out
    else
        parent.l = out
    end
    return out
end

-- split: break up multiple consecutive right-horizontal links
function Line:split() --> Line (the new root)
    if not self.r or not self.r.r or self.r.r.level ~= self.level then
        return self
    end
    --
    --    self                     out
    --   /    \                   /   \
    --  a      out    -->     self     b
    --        /   \          /    \
    --   child     b        a      child
    --
    local parent = self.parent
    local out = self.r
    local child = out.l
    self.r = child
    out.l = self
    if child then child.parent = self end
    self.parent = out
    out.parent = parent
    out.lsum = out.lsum + self.lsum + #self.text
    out.lcount = out.lcount + self.lcount + 1
    if parent.r == self then
        parent.r = out
    else
        parent.l = out
    end
    out.level = out.level + 1
    return out
end

function Line:link_before(node) --> nil
    self.p = node.p
    self.n = node
    node.p.n = self
    node.p = self
end

function Line:unlink() --> nil
    self.p.n = self.n
    self.n.p = self.p
end


local BreakTree = {}
BreakTree.__index = BreakTree

function BreakTree:create() --> BreakTree
    local self = {}
    setmetatable(self, BreakTree)

    -- we start with a "ghost" character at the end of the document
    -- if we imagine it is a newline, we can assume all lines end in newlines
    -- also this allows Insert(0, "text") to work normally on an empty document
    local ghost = Line:create("\n")
    -- configure our linked list
    self.n = ghost
    self.p = ghost
    ghost.p = self
    ghost.n = self
    -- configure our tree
    self.r = ghost
    ghost.parent = self

    return self
end

function BreakTree:find(char_idx) --> Line, line_idx, row_idx
    local node = self.r
    local line_idx = 0
    local lsum = 0
    while node do
        if node.lsum > char_idx then
            -- descend leftwards
            node = node.l
        elseif node.lsum + #node.text > char_idx then
            -- this is the node!
            return node, line_idx + node.lcount, char_idx - node.lsum
        else
            -- descend rightwards
            line_idx = line_idx + node.lcount + 1
            lsum = lsum + node.lsum + #node.text
            char_idx = char_idx - node.lsum - #node.text
            node = node.r
        end
    end
    error("node not found!")
end

-- walk up the tree correcting .lsum on every node after a change to this node
function BreakTree:fix_lsums(node, diff) --> nil
    local parent = node.parent
    while parent ~= self do
        if parent.l == node then
            parent.lsum = parent.lsum + diff
        end
        node = parent
        parent = parent.parent
    end
end

function BreakTree:insert_line(node, text) --> nil
    local new = Line:create(text)
    -- insert into linked list
    new:link_before(node)

    -- insert into tree as the rightmost node of the node.l subtree
    if not node.l then
        -- there's no node.l
        node.l = new
        new.parent = node
    else
        local temp = node.l
        while temp.r do
            temp = temp.r
        end
        temp.r = new
        new.parent = temp
    end

    -- walk up the tree recalculating and rebalancing
    local child = new
    local temp = new.parent
    while temp ~= self do
        if temp.l == child then
            temp.lsum = temp.lsum + #text
            temp.lcount = temp.lcount + 1
        end
        temp = temp:skew()
        temp = temp:split()
        child = temp
        temp = temp.parent
    end
end

function BreakTree:insert_text(idx, text) --> line_idx, row_idx
    local node, line_idx, row_idx = self:find(idx)
    local tlines = split_soft(text, '\n')
    local before = string.sub(node.text, 1, row_idx)
    local after = string.sub(node.text, row_idx + 1, -1)
    if #tlines == 1 then
        -- only one line, just grow this line
        node.text = before .. text .. after
        self:fix_lsums(node, #text)
    else
        -- multiple lines:
        -- first added line is appended to start of this line
        self:insert_line(node, before .. tlines[1] .. "\n")
        -- last unfinished line is prepended to end of this line
        node.text = tlines[#tlines] .. after
        self:fix_lsums(node, #tlines[#tlines] - row_idx)
        -- complete lines in between are added as brand new lines
        for i = 2, #tlines - 1 do
            self:insert_line(node, tlines[i] .. "\n")
        end
    end
    return line_idx, row_idx
end

function BreakTree:delete_line(node) --> nil
    -- Two deletion cases, either:
    --
    -- - The node has no node.l, and we just promote the right child
    --
    -- or
    --
    -- - The node.p is the rightmost leaf of the node.l subtree, and can be
    --   trivially removed.
    --
    -- This second case is because the Andersson tree prevents the possibility
    -- of left-children in the absence of right-children.  This is enforced by
    -- the level requirements of the Andersson tree and the same-level-links-
    -- on-the-right rule; if a node has a nil child, then it must have
    -- level==0.  If it has a child, that child must not have a higher level,
    -- so the child must also be level==0.  Since same-level-links must only
    -- occur on the right, there can't be left-children without right children.
    --
    -- Note that in the second case, the lcounts and lsums in the node.l
    -- subtree are guaranteed not to be affected by the removal of node.p,
    -- since node.p is the rightmost leaf of the node.l subtree.  The only
    -- affected lcounts and lsums are actually node itself, while the parents
    -- of node are affected only as if node itself were removed.
    --
    -- Also note that in the second case, the node.p must be a valid node,
    -- since the presence of a left-child guarantees a previous node exists.

    -- fix lsums and lcounts above node
    local temp = node
    local parent = node.parent
    while parent ~= self do
        if parent.l == temp then
            parent.lsum = parent.lsum - #node.text
            parent.lcount = parent.lcount - 1
        end
        temp = parent
        parent = parent.parent
    end

    parent = node.parent
    local rebalance_from = nil
    local max_level = nil
    if not node.l then
        -- first case; remove node and promote node.r
        node:unlink()
        local child = node.r
        if parent.r == node then
            parent.r = child
        else
            parent.l = child
        end
        if child then
            child.parent = parent
            rebalance_from = child
            max_level = child.level + 1
            -- child.level must be zero since node.level must have been zero
            -- since it has a nil child node
            if child.level > 0 then
                error("deletion case one level assertion failed")
            end
        else
            rebalance_from = parent
            max_level = 0
        end
    else
        -- second case; replace node with node.p
        local prev = node.p
        node.text = prev.text
        prev:unlink()
        if prev.l or prev.r then
            error("deletion case two trivial removal assertion failed")
        end
        if prev.parent.r == prev then
            prev.parent.r = nil
        else
            -- the only parent where prev can be a left-child is node itself
            if prev.parent ~= node then
                error("deletion case two parentage assertion failed")
            end
            node.l = nil
        end
        rebalance_from = prev.parent
        max_level = 0
        -- adjust node's lsum and lcount for the removed left-child
        node.lsum = node.lsum - #prev.text
        node.lcount = node.lcount - 1
    end

    -- finally, correct levels and rebalance
    temp = rebalance_from
    while temp ~= self do
        -- correct leveling
        if temp.level > max_level then
            temp.level = max_level
            -- decreasing level may need to be propogated to one right-child
            if temp.r and temp.r.level > max_level then
                temp.r.level = max_level
            end
        end
        max_level = temp.level + 1
        -- skew/splits
        temp = temp:skew()
        if temp.r then temp.r:skew() end
        if temp.r and temp.r.r then temp.r.r:skew() end
        temp = temp:split()
        if temp.r then temp.r:split() end
        -- continue up the tree
        temp = temp.parent
    end
end

function BreakTree:delete_text(idx, nchars) --> string, sl, sr, el, er
    -- the math here assumes that nchars is always at least 1
    assert(nchars >= 1)

    local _, sl, sr = self:find(idx)
    local node, el, er = self:find(idx + nchars)

    -- nvim_buf_set_text wants line indexes to be end-inclusive, but row
    -- indices to be end-exclusive
    local el_out, er_out = el, er
    if er == 0 then
        el_out = el_out - 1
        er_out = #node.p.text
    end

    if nchars <= er then
        -- deletion is entirely within this line
        local before = string.sub(node.text, 1, sr)
        local target = string.sub(node.text, sr + 1, er)
        local after = string.sub(node.text, er + 1, #node.text)
        node.text = before .. after
        self:fix_lsums(node, -nchars)
        -- no lines to delete
        return target, sl, sr, el_out, er_out
    end

    -- delete before er in this line
    local target = string.sub(node.text, 1, er)
    local after = string.sub(node.text, er + 1, #node.text)
    node.text = after
    self:fix_lsums(node, -er)
    nchars = nchars - er

    -- delete entire lines
    while nchars > 0 and nchars >= #node.p.text do
        nchars = nchars - #node.p.text
        target = node.p.text .. target
        self:delete_line(node.p)
    end

    -- merge the last line into this line
    if nchars > 0 then
        local before = string.sub(node.p.text, 1, #node.p.text - nchars)
        local deleted = string.sub(node.p.text, #node.p.text - nchars + 1, -1)
        target = deleted .. target
        node.text = before .. node.text
        self:fix_lsums(node, #before)
        self:delete_line(node.p)
    end

    return target, sl, sr, el_out, er_out
end

function BreakTree:print() --> nil
    print_tree(self.r, 0)
end

-- exports
breaktree = {
    BreakTree = BreakTree,
    Line = Line,
}

import abc
import contextlib
import collections
import os

import trio
from trio import socket

r"""
NOTATION:

# TODO

SIMULTANEOUS EDITS:

Imagine two simultaneous edits (that is, both based on a):

    a - x
     \
      y

We can say x and y are non-conflicting if the following sequences result in the
same document (that is, they can be applied in either order):

    a - x - y(x)  ==  a - y - x(y)

-----------------------

REORDERING NODES:

Imagine a a set of edits a-b-c, with an alternate branch of edits ~b-b which
are based on b.

    a - b - c
         \
          ~b - b

Intuitively, since the trio b - ~b - b would result in just b, and conflicts
notwithstanding, after rebasing we expect the result to be identical to a-b-c.

If c and ~b are non-conflicting, then we can choose to land ~b first:

    a - b - ~b - c(~b)
              \
               b  # second half of the branch is still unmerged

Then if c(~b) and b are non-conflicting, we can choose to land c(~b) first:

    a - b - ~b - c(~b) - b(c(~b))

The b - ~b pair cancel:

    a - c(~b) - b(c(~b))

thus a-b-c == a-c'-b' where c' = c(~b) and b' = b(c'), which is true so long as
neither (c, ~b) nor (b, c') conflict.

-----------------------

REBASING ONE-ONTO-MANY

Formula for rebasing a single edit after a series of many edits:

    a - b - c - d ...
     \
      x

x(b) is x applied after b (by definition).  So x after b, c, d... is just

    x'''... = x(b)(c)(d)...

----------------

REBASING MANY-ONTO-ONE

Formula for rebasing a single edit after a series of many edits:

    a - b - c - d
     \
      x

First, we will calculate many equivalent edits to x, one after each of the
series of many edits:

    a - b   -   c    -    d
     \   \       \         \
      x   x(b)    x(b)(c)   x(b)(c)(d)

We can rewrite using x' notation:

    a - b - c  - d
     \   \   \    \
      x   x'  x''  x'''

If the following conditions are true:

  - x and b do not conflict
  - x' and c do not conflict
  - x'' and d do not conflict

Then we know the following:

[1] a - x - b(x)  == a - b - x(b)  == a - b - x'
[2] a - b - x' - c(x') == a - b - c - x'(c) == a - b - c - x''
[3] a - b - c - x'' - d(x'') == a - b - c - d - x''(d) == a - b - c - d - x'''

Now, we start by applying x''' after d.  This is just rebasing x onto a-b-c-d:

    a - b - c - d - x'''

By [3] we can reduce this to:

    a - b - c - x'' - d(x'')

By [2] we can reduce this to:

    a - b - x' - c(x) - d(x'')

By [1] we can reduce this to:

    a - x - b(x) - c(x') - d(x'')

Which we recognize as b-c-d rebased onto x:

    a - b - c - d - x'''
     \
      x - b(x) - c(x') - d(x'')

A clear pattern emerges.  Intuitively, this is saying that for a given edit e,
the rebased form of e is e after the form of x which is based on the direct
parent of e, if such a form of x exists and it does not conflict with e.

So we may describe the following algorithm:

    def rebase_many_onto_one(edits, x):
        new_edits = []
        for e in edits:
            if conflicts(x, e):
                raise EditConflict("rebase would not be valid")
            new_edits.append(e.after(x))
            x = x.after(e)
        return new_edits

-----------------------

UNDO STRATEGY:

Suppose a client and server begin with edits a, b from this client:

    # Server state: complete and append-only
    a - b

    # Client state: just this client's undoable changes
    a - b

If the client were to undo edit b, it simply emits ~b, and discards b from
it history (note, the Client would have to keep b in a redo buffer until some
fresh edit was made):

    # Server state:
    a - b - ~b

    # Client state:
    a

Now it should be clear that if the Client wants to undo a that it is clearly
allowed, even if a and b conflicted with each other.  Notice that with the
append-only history that the server keeps, calculating how to undo a would be
very difficult if a(b) could not be calculated safely.

So, how does the client keep track of its own undo history in the context of
external clients?  Let's imagine we were back in that original a-b state and
edit c is submitted by an external client.  The client will try to keep its
undoable state at the tail of its history, rebasing its own edits to do so:

    # Server appends edit c
    a - b - c

    # Client appends c
    a - b - c

    # Client swaps b and c (assuming no conflict)
    a - c(~b) - b(c(~b))

    # let c' = c(~b)
    # let b' = b(c')
    a - c' - b'

    # Client swaps a and c' (assuming no conflict)
    c'(~a) - a(c'(~a)) - b'

    # let c'' = c'(~a)
    # let a' = a(c'')
    c'' - a' - b'

    # Client can discard c'' from its history (without changing the document)
    # since this client has no business undoing another client's edit:
    a' - b'

Now, if the client wants to undo its original b edit, it can submit ~b' for the
same semantic effect to the document.

When conflicts do occur, the client simply gives up on its conflicting edit and
any edits before that.  So if c were to conflict with b, the client would have
no undo history after applying c, and if c' were to conflict with a, the client
would have only b' in its undo history after applying c.

-----------------------

UNDO SUMMARY:

  - Server keeps a single linear history, which is append-only
  - Each client keeps a history of its own undoable changes
  - When a client receives an external edit, it reparents its entire history
    of undoable changes on top of the external edit
  - If an undoable change conflicts with the external edit, that change and
    any older changes become non-undoable (and can be forgotten)

-----------------------

SERVER LOGIC:

Server history starts:
    a - b

    0 1 2 3 4 5 6 7   # doc after a

    0 1 2 3 4 5 6 7
        b|___|        # b deletes 34

    0 1 2 5 6 7       # doc after b

Client submits c - d based on a:

    0 1 2 3 4 5 6 7
            c|        # c inserts 9 between 4 and 5

    0 1 2 3 4 9 5 6 7 # doc after c

    0 1 2 3 4 9 5 6 7
          d|_______|  # d deletes 4956

    0 1 2 3 7         # doc after d


    0 1 2 5 6 7
       c'|            # c(b) would inject a 9 after 2:

    0 1 2 9 5 6 7     # doc after a - b - c'

Now d is basically unresolvable.  Mathematically d' would have to be:

    d' = d(~c)(b)(c')  # TODO: rewrite this statement based
                       #       on new shadow history rules

But since c and d conflict, d(~c) is not safe to calculate.  The only
reasonable strategy for chained edits with conflicts like this is for them to
be discarded.  The edit with a parent on the server history can always be
applied with successive best-effort .after() operations, but edits with a
parent not on the server history may not apply.

Now, suppose c and d conflict with each other, but neither conflict with b:

    # server history and requested edits
    a - b
     \
      c - d

If b and c don't conflict, the following two histories result in identical
documents:

    a - b - c(b)
    a - c - b(c)

Though only one of those histories matches the parentage of d:

    a - c - b(c)
         \
          d

If d and b(c) don't conflict, the following two histories result in identical
documents:

    a - c - b(c) - d(b(c))
    a - c - d - b(d)(c)

Now, because we know that:

    a - c - b(c) == a - b - c(b)

Then we can say:

    a - c - b(c) - d(b(c))
 == a - b - c(b) - d(b(c))

This final form begins with a - b, followed by a mutation of c and a mutation
of d.  This is exactly the formula the server must apply to resolve the
branching history:

    a - b
     \
      c - d

And the conditions for being able to apply d are that:

  - c and b are non-conflicting
  - d and b(c) are non-conflicting

Note that this implies that the server must track a history of client-submitted
edits, in their original form, to calculate b(c) as part of applying d.
It is ok for the server to discard the original edits from a client when
another edit from that client is based on a more recent server edit.

-----------------------

SERVER SHADOW HISTORY

The strategy is to keep a shadow history where all of a client's edits can
apply freely.  So if a history exists with two client submissions, x and y,
based on a server history:

    a - b - x' - c - d - y' - e   # real history, with client edits applied
                                  # as they arrived, with appropriate
                                  # modification.

    a - x - y - b' - c' - d' - e  # shadow history, with client edits applied
                                  # unmodified, and any external edits
                                  # modified appropriately


and if we receive another client submission z based on y, we can see that the
z can meaningfully apply to the shadow history:

    a - b - x' - c - d - y' - e    # real history

    a - x - y - b' - c' - d' - e   # shadow history
             \
              z

We want to know z', which is z rebased onto b'-c'-d'-e.  Such a z' would apply
to the tail of the shadow history.  Since the shadow history and the real
history result in are equaivalent documents, z' therefore also would apply to
the real history.  We know from REBASE ONE ONTO MANY that:

    z' = z(b')(c')(d')(e)

Simultaneously, we know from REBASE MANY ONTO ONE that we can rebase b'-c'-d'-e
onto z, which will give us our new shadow history:

    a - x - y - z - b'' - c'' - d'' - e'

Afterwards, we should have:

    a - b - x' - c - d - y' - e - z'           # real history

    a - x - y - z - b'' - c'' - d'' - e'       # shadow history

If a new edit is applied to the server, we simply append it to both real and
shadow histories.  The resulting shadow history maintains the requirement that
all client edits come before all server edits, and that both histories result
in equivalent documents.

    a - b - x' - c - d - y' - e - z' - f       # real history

    a - x - y - z - b'' - c'' - d'' - e' - f   # shadow history

Now, suppose the client acknowledges that c has been accepted.  The server's
shadow history needs to be updated to reflect that the client knows about c:

    a - b - x' - c - d - y' - e - z' - f       # real history

    a - x - y - z - b'' - c'' - d'' - e' - f   # old shadow history
      |___|_______|___________|
        |     |         |
        |     |         external edits the client knows of, these will not
        |     |         be part of the new shadow history
        |     |
        |     client does not know this has landed, these submissions will be
        |     in the new shadow history
        |
        client knows this has landed, it will not be in the new shadow history

Pre-ack: apply client logic, which places edits before submissions

    # start with original client submissions
    a - x - y - z

    # for b: rebase many onto one: x-y-z onto b
    a - b - x' - y' - z'

    # for x': leave x alone now
    a - b - x' - y' - z'

    # for c: rebase many onto one: y-z onto c
    a - b - x' - c - y'' - z''

Now we have caught up to the ack.  At the moment the client sent the ack, we
know that it held y''-z'' as the only in-flight submissions, and that they had
been rebased onto c as we have done here.

Now hold y''-z'' aside and start building the new shadow history:

    # starting point
    c

Post-ack: apply server logic, which places edits after submissions

    # for d: just append
    c - d

    # for y': rebase MANY ONTO ONE: [d] onto y'', which is based on c
    c - y'' - d'

    # for e: just append
    c - y'' - d' - e

    # for z': rebase MANY ONTO ONE: d'-e onto z'', which is based on y''
    c - y'' - z'' - d'' - e'

    # for f: just append
    c - y'' - z'' - d'' - e'' - f

------------

CLIENT LOGIC

Suppose a client submits c and d which are based on a:

      c - d      # client's ui state
     /
    a            # known server state
     \
      b          # true server state

The client sees that b and c are accepted, and updates its ui accordingly

               d'    # client's ui state
             /
    a - b - c'       # known server state

Now the client sees update e, which is based on d':

              d' - e   # client's ui state
             /
    a - b - c'         # known server state

How does the client report e, which is based on d'?  Basically in this case
the server must be explicitly told about d'.  Since the content of d' is
determinisitically derived from having accepted b, it should be sufficient for
the client to acknowledge either b or c to the server (since c' would be
identical in either case).

So the client reporting which changes it knows about is required in cases where
the client wishes to report a new change based on an in-flight changed that has
been reparented on an external change.  The actual reparenting logic is
deterministic but the server needs to apply that reparent logic to the change
in order for the server to understand the parentage of e.

Normally the client can omit these changes; they are a waste of network
resources in other case.  If the client keeps a "dirty" flag on its in-flight
changes, and that flag is set whenever an in-flight change is rebased, then
an ack message is triggered when the client is preparing to submit a new edit
and it sees the edit is based on a dirty in-flight edit.

-----------------------

PROTOCOL SUMMARY:

Negotiation:

    # EITHER

        # new client connects
        new:text\n
        |   |
        |   display name of this editor (not encoded!)
        literal "new"

        # server assigns editor id and reconnection secret
        num:text:num:text\n
        |   |    |   |
        |   |    |   intial message content, encoded
        |   |    initial server edit id
        |   reconnection secret (not encoded!)
        editor id

    # OR

        # client reconnects
        old:num:text:...   # full semantics tbd
        |   |   |
        |   |   reconnection secret
        |   assigned editor id
        literal "old"

        # server responds
        ...


Client messages:

    # edit submission message
    s:num:num:num:char:num:num_or_text\n
    | |   |   |   |    |   |
    | |   |   |   |    |   OT arg, nchars or encoded text [1]
    | |   |   |   |    OT idx
    | |   |   |   OT type, "i"nsert or "delete
    | |   |   parent edit author
    | |   parent edit id
    | edit id (edit author is assumed to be the submitting client)
    edit "s"ubmission

    [1] Insert.text encoding

        0   -> \0
        8   -> \b
        9   -> \t
        10  -> \n
        13  -> \r
        92  -> \\

        1-7,
        11-12,  -> \xNN  # hex-encoding
        14-31,
        127,

        * -> *  # remaining characters are passed unmodified

    # acknowledge message
    k:num\n
    | |
    | edit id (the edit author of "server" is implied)
    ac"k"nowlegde

Server messages:

    # external edit message
    x:num:char:num:num_or_text\n
    | |   |    |   |
    | |   |    |   OT arg, nchars or encoded text [1]
    | |   |    OT idx
    | |   OT type, "i"nsert or "delete
    | edit id (the edit author of "server" is implied)
    e"x"ternal edit

    # accepted edit message
    a:num\n
    | |
    | edit id (edit author of this client is implied)
    "a"ccepted

"""

class OT(metaclass=abc.ABCMeta):
    """
    Operational Transform object.
    """

    @abc.abstractmethod
    def apply(self, text):
        """
        Apply this OT to some text.
        """
        pass

    @abc.abstractmethod
    def after(self, other):
        """
        Return what this OT would look like if applied after other.

        Must resolve conflicts as best as possible, as one user's edits will
        frequently be replayed after another's simultaneous edits.
        """
        pass

    @abc.abstractmethod
    def inverse(self):
        """
        Return an OT that would cancel this OT, if applied after this one.

        Note that inverses don't commute, so this is true:

            A -> X -> ~X == A

        But this will result in undefined behavior:

            A -> ~X -> X == ?
        """
        pass


class Insert(OT):
    def __init__(self, idx, text):
        self.idx = idx
        self.text = text

    def apply(self, text):
        return text[:self.idx] + self.text +  text[self.idx:]

    def after(self, other):
        if isinstance(other, Insert):
            if other.idx > self.idx:
                # other inserts after us
                # INDEPENDENT
                return self
            elif other.idx == self.idx:
                # other inserts at the same spot
                # CONFLICT
                return Insert(self.idx + len(other.text), self.text)
            else:
                # other inserts before us
                # INDEPENDENT
                return Insert(self.idx + len(other.text), self.text)
        elif isinstance(other, Delete):
            if other.idx > self.idx:
                # delete is after us
                # INDEPENDENT
                return self
            elif other.idx + other.nchars < self.idx:
                # delete is before, no overlap
                # INDEPENDENT
                return Insert(self.idx - other.nchars, self.text)
            else:
                # one of:
                # - delete ends right where we insert; insert anyway
                # - delete starts right where we start; insert anyway
                # - delete overlaps us; insert anyway
                # CONFLICT
                return Insert(other.idx, self.text)
        raise NotImplementedError(
            f"{type(self).__name__}.after({type(other).__name__})"
        )

    def inverse(self):
        return Delete(self.idx, len(self.text), self.text)

    def __eq__(self, other):
        return (
            isinstance(other, Insert)
            and self.idx == other.idx
            and self.text == other.text
        )

    def __repr__(self):
        return f"Insert({self.idx}, {repr(self.text)})"


class Delete(OT):
    def __init__(self, idx, nchars, text):
        self.idx = idx
        self.nchars = nchars
        # text may be None, in which case the Delete can't be inverted.  This
        # occurs when the Delete originates from the server, or in
        # Delete.after() finds a conflict.  Since inverses are only used in
        # calculating undo history, and undo history doesn't work past conflict
        # boundaries, this is fine.
        self.text = text

    def apply(self, text):
        return text[:self.idx] + text[self.idx + self.nchars:]

    def inverse(self):
        assert self.text is not None, "non-invertible Delete"
        return Insert(self.idx, self.text)

    def after(self, other):
        if isinstance(other, Insert):
            if other.idx > self.idx + self.nchars:
                # other inserts after us, no overlap
                # INDEPENDENT
                return self
            elif other.idx < self.idx:
                # other inserts before us
                # INDEPENDENT
                return Delete(self.idx + len(other.text), self.nchars, self.text)
            elif other.idx == self.idx:
                # other inserts right where we start to delete; leave it alone
                # CONFLICT
                return Delete(self.idx + len(other.text), self.nchars, self.text)
            elif other.idx == self.idx + self.nchars:
                # other inserts right where we stop deleting; leave it alone
                # CONFLICT
                return self
            else:
                # insert into the section we hoped to delete; delete it too
                # CONFLICT
                return Delete(self.idx, self.nchars + len(other.text), None)
        elif isinstance(other, Delete):
            if other.idx >= self.idx + self.nchars:
                # delete is after us, no overlap
                # INDEPENDENT if not equal else CONFLICT
                return self
            elif other.idx + other.nchars <= self.idx:
                # delete is before us, no overlap
                # INDEPENDENT if not equal else CONFLICT
                return Delete(self.idx - other.nchars, self.nchars, self.text)
            elif other.idx <= self.idx:
                # other is before us (or tied) with some overlap
                if other.idx + other.nchars >= self.idx + self.nchars:
                    # other deleted what we would delete already
                    # CONFLICT
                    return None
                else:
                    # other is before and deletes part of what we would delete
                    # CONFLICT
                    overlap = other.nchars - (self.idx - other.idx)
                    return Delete(other.idx, self.nchars - overlap, None)
            elif other.idx > self.idx:
                # other is after us with some overlap
                if other.idx + other.nchars > self.idx + self.nchars:
                    # other deletion would continue after us
                    # CONFLICT
                    return Delete(self.idx, other.idx - self.idx, None)
                else:
                    # other deletion is contained within what we would delete
                    # CONFLICT
                    return Delete(self.idx, self.nchars - other.nchars, None)
        raise NotImplementedError(
            f"{type(self).__name__}.after({type(other).__name__})"
        )

    def __eq__(self, other):
        return (
            isinstance(other, Delete)
            and self.idx == other.idx
            and self.nchars == other.nchars
        )

    def __repr__(self):
        return f"Delete({self.idx}, {self.nchars})"

encoder = {
    0: list(b"\\0"),  # "\0"
    1: list(b"\\x01"),
    2: list(b"\\x02"),
    3: list(b"\\x03"),
    4: list(b"\\x04"),
    5: list(b"\\x05"),
    6: list(b"\\x06"),
    7: list(b"\\x07"),
    8: list(b"\\b"),  # "\b"
    9: list(b"\\t"),  # "\t"
    10: list(b"\\n"),  # "\n"
    11: list(b"\\x0b"),
    12: list(b"\\x0c"),
    13: list(b"\\r"),  # "\r"
    14: list(b"\\x0e"),
    15: list(b"\\x0f"),
    16: list(b"\\x10"),
    17: list(b"\\x11"),
    18: list(b"\\x12"),
    19: list(b"\\x13"),
    20: list(b"\\x14"),
    21: list(b"\\x15"),
    22: list(b"\\x16"),
    23: list(b"\\x17"),
    24: list(b"\\x18"),
    25: list(b"\\x19"),
    26: list(b"\\x1a"),
    27: list(b"\\x1b"),
    28: list(b"\\x1c"),
    29: list(b"\\x1d"),
    30: list(b"\\x1e"),
    31: list(b"\\x1f"),
    92: list(b"\\\\"),  # "\\"
    127: list(b"\\x7f"),
}

def encode_text(msg_byts):
    out = []
    for b in msg_byts:
        e = encoder.get(b)
        if e is not None:
            out += e
        else:
            out.append(b)
    return bytes(out)

nibbles = {
    48: 0, 49: 1, 50: 2, 51: 3, 52: 4,
    53: 5, 54: 6, 55: 7, 56: 8, 57: 9,
    65: 10, 66: 11, 67: 12, 68: 13, 69: 14, 70: 15,
    97: 10, 98: 11, 99: 12, 100: 13, 101: 14, 102: 15,
}

def decode_text(wire_byts):
    out = []
    i = 0

    while i < len(wire_byts):
        c0 = wire_byts[i]; i += 1
        if c0 != 92:  # not "\"
            out.append(c0)
            continue
        # "\" case
        if i == len(wire_byts):
            raise ValueError("unmatched '\\'")
        c1 = wire_byts[i]; i += 1
        if c1 == 48:  # "\0"
            out.append(0)
            continue
        if c1 == 98:  # "\b"
            out.append(8)
            continue
        if c1 == 116:  # "\t"
            out.append(9)
            continue
        if c1 == 110:  # "\n"
            out.append(10)
            continue
        if c1 == 114:  # "\r"
            out.append(13)
            continue
        if c1 != 120:  # not "x"
            raise ValueError("unknown escape:", chr(c1))
        # "\x" case
        if i+1 >= len(wire_byts):
            raise ValueError("incomplete '\\x' escape")
        c2 = wire_byte[i]; i+= 1
        c3 = wire_byte[i]; i+= 1
        try:
            out.append(16*nibbles[c2] + nibbles[c3])
        except Exception:
            raise ValueError("bad hex in '\\x' escape") from None
    return bytes(out)


def decode_ot(typ_text, idx_text, arg_text):
    """Deserialize an OT from the wire."""
    if typ_text == b"i":
        return Insert(int(idx_text), decode_text(arg_text))
    elif typ_text == b"d":
        return Delete(int(idx_text), int(arg_text))


def encode_ot(ot):
    """Serialize an OT for the wire."""
    if isinstance(ot, Insert):
        return b"i:%d:%s"%(ot.idx, encode_text(ot.text))
    if isinstance(ot, Delete):
        return b"d:%d:%d"%(ot.idx, ot.nchars)



def conflicts(a, b):
    """
    Insert/insert non-conflict:     - - - - - - - -
                                      i|  i|

    Insert/insert conflict:         - - - - - - - -
                                        i|
                                        i|

    Insert/delete nonconflicts:     - - - - - - - -
                                      d|___|  i|

                                    - - - - - - - -
                                      i|  d|___|

    Insert/delete conflicts:        - - - - - - - -
                                      i|
                                      d|___|

                                    - - - - - - - -
                                        i|
                                      d|___|

                                    - - - - - - - -
                                          i|
                                      d|___|


    Delete/delete non-conflict:     - - - - - - - -
                                    d|___|
                                            d|___|


    Delete/delete conflicts:        - - - - - - - -   These apply in either
                                      d|___|          order but we classify
                                          d|___|      as conflict because the
                                                      inverses do not apply
                                                      equally in either order

                                    - - - - - - - -
                                      d|___|
                                        d|___|

                                    - - - - - - - -
                                      d|_____|
                                        d|___|

                                    - - - - - - - -
                                      d|_______|
                                        d|___|

                                    - - - - - - - -
                                      d|_____|
                                      d|___|

                                    - - - - - - - -
                                      d|___|
                                      d|___|

    """
    if isinstance(a, Insert) and isinstance(b, Insert):
        return a.idx == b.idx

    if isinstance(a, Delete) and isinstance(b, Delete):
        if a.idx > b.idx:
            a, b = b, a
        return a.idx + a.nchars >= b.idx

    if isinstance(a, Insert):
        i, d = a, b
    else:
        i, d = b, a

    return i.idx >= d.idx and i.idx <= d.idx + d.nchars


class SocketTransport:
    def __init__(self, addr, *, family=socket.AF_INET):
        self.addr = addr
        self.family = family
        self.edit_server = None
        # map connections to writable channel ends
        self.chans = {}

    def set_edit_server(self, edit_server):
        assert self.edit_server is None, "edit_server is already set!"
        self.edit_server = edit_server

    async def acceptor(self, sock):
        while True:
            conn, _ = await sock.accept()
            self.nursery.start_soon(self.negotiator, conn)

    async def negotiator(self, conn):
        # initial client negotiation
        buf = b""
        while b"\n" not in buf:
            byts = await conn.recv(4096)
            if not byts:
                print("connection broke before negotiation finished")
                return
            assert byts
            buf += byts
        line, buf = buf.split(b"\n", maxsplit=1)
        cmd, rest = line.split(b":", maxsplit=1)
        if cmd == b"new":
            name = rest
            print(f"new connection from {name}")
            text = encode_text(self.edit_server.text)
            # XXX: real author id
            # XXX: real reconnection secret
            edit_id = self.edit_server.edits[-1].id.id
            await conn.send(b"%d:%s:%d:%s\n"%(1, b"secret", edit_id, text))
        else:
            raise ValueError(f"non-new negotiation detected: {line}")

        # start full-duplex OT streaming
        await self.edit_server.on_connect(conn)

        w, r = trio.open_memory_channel(10)
        self.chans[conn] = w
        self.nursery.start_soon(self.writer, conn, r)
        if buf:
            # feed leftover bytes before reading anything else
            await self.edit_server.on_read(conn, buf)
        self.nursery.start_soon(self.reader, conn)
        # TODO: detect disconnects somehow

    async def writer(self, conn, chan):
        while True:
            msg = await chan.receive()
            while msg:
                n = await conn.send(msg)
                msg = msg[n:]

    async def write(self, conn, msg):
        await self.chans[conn].send(msg)

    async def reader(self, conn):
        while True:
            byts = await conn.recv(4096)
            # TODO: error handling, maybe like proxy.py::handle_conn()
            assert byts
            await self.edit_server.on_read(conn, byts)

    async def run(self):
        assert self.edit_server is not None, "edit server is not set"
        with socket.socket(self.family) as sock:
            await sock.bind(self.addr)
            sock.listen()
            with trio.CancelScope() as self.cancel_scope:
                async with trio.open_nursery() as self.nursery:
                    self.nursery.start_soon(self.acceptor, sock)


class ID:
    def __init__(self, id: int, editor: int):
        self.id = id
        self.editor = editor

    def __eq__(self, other):
        return self.id == other.id and self.editor == other.editor

    def __hash__(self):
        return hash((self.id, self.editor))

    def __repr__(self):
        return f"ID({self.id}, {self.editor})"


class Container:
    ot: OT


class Edit(Container):
    """
    Serializable container around an OT with a pointer to a parent.
    """
    def __init__(self, ot, id: ID, parent: ID, submitted_id):
        self.ot = ot
        self.id = id
        self.parent = parent
        self.submitted_id = submitted_id

    @classmethod
    def from_line(cls, text, editor):
        """
        Decode an edit from a colon-separated list of fields:
          - 0: ID.id
          - 1: Parent ID.id
          - 2: Parent ID.editor
          - 3: OT type ("i" or "d")
          - 4: OT idx
          - 5: OT arg
        """
        fields = text.split(b":", maxsplit=5)
        try:
            return cls(
                id=ID(id=int(fields[0]), editor=editor),
                parent=ID(id=int(fields[1]), editor=int(fields[2])),
                ot=decode_ot(fields[3], fields[4], fields[5]),
                submitted_id=editor,
            )
        except Exception as e:
            raise ValueError("bad edit line", fields) from e

    def __repr__(self):
        return f"Edit({self.id}, {self.parent}, {self.submitted_id})"

class EditMod(Container):
    """
    An OT and the original edit from which it came.
    """
    def __init__(self, ot: OT, old: Edit):
        self.ot = ot
        self.old = old

    def __repr__(self):
        return f"EditMod({self.ot}, old={self.old})"


class Shadow:
    def __init__(self, base_id: ID):
        self.dirty = False
        self.last_known_id = base_id
        self.submissions = []
        self.submission_ids = set()
        self.tail = []

    def new_submission(self, edit, new_edits):
        """
        consider any new server edits, store the submission, then return the
        version of edit.ot that applies to the server history
        """
        if new_edits:
            self.last_known_id = new_edits[-1].id

        if self.dirty:
            # no submission based on a dirty Shadow will be accepted
            return None

        # filter new edits which are in self.submissions already
        new_external_edits = [
            n for n in new_edits
            if n.submitted_id not in self.submission_ids
        ]

        # I'm pretty sure that either the last submission we emitted appears as
        # first of the new_edits, or else new_edits should go unmodified.  The
        # result is that new_external_edits is a consecutive string of edits,
        # otherwise rebasing operations wouldn't make sense.
        # XXX: if this holds true, simply the logic somehow
        assert (
            new_external_edits == new_edits
            or new_external_edits == new_edits[1:]
        ), (new_edits, new_external_edits)

        self.tail += [EditMod(n.ot, n) for n in new_external_edits]

        # edit is based on zero or more previous submissions:
        #
        #    a - server_edits
        #     \
        #      self.submissions - self.tail
        #                      \
        #                       edit
        #
        # calculate tail' (reabase many-onto-one) and edit' (one-onto-many):
        #
        #    a - server_edits
        #     \
        #      self.submissions - self.tail - edit'
        #                      \
        #                       edit - tail'
        #
        # keep tail' as our self.tail
        # return edit' which can apply to server_edits
        # rebase many-onto-one, and one-onto-many as a by-product
        x = edit.ot
        for t in self.tail:
            if conflicts(x, t.ot):
                self.dirty = True
            x_new = x.after(t.ot)
            if not self.dirty:
                t.ot = t.ot.after(x)
            x = x_new
        if not self.dirty:
            self.submissions.append(edit)
            self.submission_ids.add(edit.id)
        return x


class EditServer:
    def __init__(self, transport, text=b""):
        self.transport = transport
        self.text = text

        self.conns = set()
        # keyed by conn
        self.defraggers = {}
        # index matches server's edit id
        self.edits = []

        # shadow histories, one per conn
        # TODO: allow clients to start multiple streams of non-conflicting
        #       edits, with one shadow history per stream, so that only
        #       truly chained edits are affected by Shadow.dirty.
        self.shadows = {}

        # base edit is always a noop, and is its own parent
        base_ot = Insert(0, b"")
        base_id = ID(0, "server")
        base_edit = Edit(base_ot, base_id, base_id, "server")
        self.edits.append(base_edit)

        if text:
            first_ot = Insert(0, text)
            first_id = ID(1, "server")
            first_edit = Edit(first_ot, first_id, base_id, "server")
            self.edits.append(first_edit)

    async def on_connect(self, conn):
        # Make a defrag function to form lines from packets.
        buf = b""

        def defrag(byts):
            """Return a list of complete lines received so far."""
            nonlocal buf
            buf += byts
            lines = byts.split(b"\n")
            buf = lines[-1]
            return lines[:-1]

        self.conns.add(conn)
        self.defraggers[conn] = defrag
        self.shadows[conn] = Shadow(self.edits[-1].id)

    async def on_disconnect(self, conn):
        self.conns.remove(conn)
        del self.defraggers[conn]
        del self.shadows[conn]

    async def on_read(self, conn, byts):
        lines = self.defraggers[conn](byts)

        for line in lines:
            typ, body = line.split(b":", maxsplit=1)
            if typ == b"s":  # edit submission
                # XXX: real author editor id
                edit = Edit.from_line(body, 1)
                # verify that the parent edit is sane
                if edit.parent.editor == 0:
                    # based on server history, must be based on a server
                    # history newer than this
                    if edit.parent.id >= len(self.edits):
                        raise ValueError(
                            f"editor {XXX} submitted edit based on "
                            "non-existent parent submission"
                        )
                elif edit.parent.editor == 1:  # XXX real author id
                    # based on a previous submission, must be the latest one
                    shadow = self.shadows[conn]
                    valid = None
                    if shadow.submissions:
                        valid = shadow.submissions[-1].id
                    if not shadow.dirty and edit.parent.id != valid:
                        raise ValueError(
                            f"editor {XXX} submitted edit based on invalid "
                            "parent submission (not the most recent one)"
                        )
                else:
                    raise ValueError(
                        f"editor {editor} submitted edit "
                        f"based on peer {out.parent.id}"
                    )
                await self.on_submission(conn, edit)
            elif typ == b"k":  # acknowledge a server edit
                ack_id = int(body)
                # XXX: not sure, does dirty flag need recalculating?
                raise NotImplementedError("unable to handle ack messages")
            else:
                raise ValueError(f"unknown message type: {line}")

    def submission_atomic(self, conn, edit):
        # "atomic" because no other coroutines may run during this function
        # returns False if submission was rejected or came to nothing
        if edit.parent.editor == 0:
            # start a new shadow history
            shadow = Shadow(edit.parent)
            self.shadows[conn] = shadow
        else:
            shadow = self.shadows[conn]

        new_edits = self.edits[shadow.last_known_id.id+1:]
        ot = shadow.new_submission(edit, new_edits)

        if ot is None:
            # submission was rejected or came out to nothing
            return None, None

        # apply submission to server history and broacast it
        new_id = len(self.edits)
        self.edits.append(
            Edit(
                ot=ot,
                id=ID(new_id, "server"),
                parent=ID(new_id-1, "server"),
                submitted_id=edit.submitted_id,
            )
        )
        self.text = ot.apply(self.text)
        return new_id, ot

    async def on_submission(self, conn, edit):
        new_id, ot = self.submission_atomic(conn, edit)

        # always send accept msg to authoring client
        msg = b"a:%d\n"%edit.id.id
        await self.transport.write(conn, msg)

        if ot is None:
            return

        # broadcast to all non-authoring clients
        msg = b"x:%d:%s\n"%(new_id, encode_ot(ot))
        for c in self.conns:
            if c == conn:
                continue
            await self.transport.write(c, msg)


if __name__ == "__main__":
    socketpath = "./asdf"
    transport = SocketTransport(addr=socketpath, family=socket.AF_UNIX)
    edit_server = EditServer(transport)
    transport.set_edit_server(edit_server)

    try:
        trio.run(transport.run)
    finally:
        os.unlink(socketpath)

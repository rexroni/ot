import os
import re
import socket
import threading
import time

import trio

import ot


def test_encode_decode():
    base = bytes([*range(128)])
    expect = (
        b"\\0"
        b"\\x01\\x02\\x03\\x04\\x05\\x06\\x07"
        b"\\b\\t\\n"
        b"\\x0b\\x0c"
        b"\\r"
        b"\\x0e\\x0f\\x10\\x11\\x12\\x13\\x14\\x15\\x16"
        b"\\x17\\x18\\x19\\x1a\\x1b\\x1c\\x1d\\x1e\\x1f"
        b" !\"#$%&'()*+,-./0123456789:;<=>?@ABCDEFGHIJKLMNOPQRSTUVWXYZ["
        b"\\\\"
        b"]^_`abcdefghijklmnopqrstuvwxyz{|}~"
        b"\\x7f"
    )
    encoded = ot.encode_text(base)
    assert encoded == expect, '\n' + repr(encoded) + '\n' + repr(expect)

    decoded = ot.decode_text(encoded)
    assert decoded == base, '\n' + repr(decoded) + '\n' + repr(base)


def test_insert():
    # .apply() tests
    result = ot.Insert(0, b"hello ").apply(b"world")
    assert result == b"hello world", result
    result = ot.Insert(5, b" cruel").apply(b"hello world")
    assert result == b"hello cruel world", result
    # .after() inserts
    result = ot.Insert(5, b"abc").after(ot.Insert(6, b"xyz"))
    assert result == ot.Insert(5, b"abc"), result
    result = ot.Insert(5, b"abc").after(ot.Insert(5, b"xyz"))
    assert result == ot.Insert(8, b"abc"), result
    result = ot.Insert(5, b"abc").after(ot.Insert(4, b"xyz"))
    assert result == ot.Insert(8, b"abc"), result
    # .after() deletions
    result = ot.Insert(5, b"abc").after(ot.Delete(6, 3, None))
    assert result == ot.Insert(5, b"abc"), result
    result = ot.Insert(5, b"abc").after(ot.Delete(1, 3, None))
    assert result == ot.Insert(2, b"abc"), result
    result = ot.Insert(5, b"abc").after(ot.Delete(2, 3, None))
    assert result == ot.Insert(2, b"abc"), result
    result = ot.Insert(5, b"abc").after(ot.Delete(3, 3, None))
    assert result == ot.Insert(3, b"abc"), result
    result = ot.Insert(5, b"abc").after(ot.Delete(5, 3, None))
    assert result == ot.Insert(5, b"abc"), result


def test_delete():
    # .apply() tests
    result = ot.Delete(0, 6, None).apply(b"hello world")
    assert result == b"world", result
    result = ot.Delete(5, 6, None).apply(b"hello world")
    assert result == b"hello", result
    # .after() inserts
    result = ot.Delete(5, 6, None).after(ot.Insert(12, b"xyz"))
    assert result == ot.Delete(5, 6, None), result
    result = ot.Delete(5, 6, None).after(ot.Insert(4, b"xyz"))
    assert result == ot.Delete(8, 6, None), result
    result = ot.Delete(5, 6, None).after(ot.Insert(5, b"xyz"))
    assert result == ot.Delete(8, 6, None), result
    result = ot.Delete(5, 6, None).after(ot.Insert(11, b"xyz"))
    assert result == ot.Delete(5, 6, None), result
    result = ot.Delete(5, 6, None).after(ot.Insert(7, b"xyz"))
    assert result == ot.Delete(5, 9, None), result
    # .after() deletions
    result = ot.Delete(5, 6, None).after(ot.Delete(12, 3, None))
    assert result == ot.Delete(5, 6, None), result
    result = ot.Delete(5, 6, None).after(ot.Delete(1, 3, None))
    assert result == ot.Delete(2, 6, None), result
    # overlap cases, other before us
    result = ot.Delete(5, 6, None).after(ot.Delete(4, 6, None))
    assert result == ot.Delete(4, 1, None), result
    result = ot.Delete(5, 6, None).after(ot.Delete(4, 7, None))
    assert result == None, result
    result = ot.Delete(5, 6, None).after(ot.Delete(4, 8, None))
    assert result == None, result
    # overlap cases, other tied
    result = ot.Delete(5, 6, None).after(ot.Delete(5, 5, None))
    assert result == ot.Delete(5, 1, None), result
    result = ot.Delete(5, 6, None).after(ot.Delete(5, 6, None))
    assert result is None, result
    result = ot.Delete(5, 6, None).after(ot.Delete(5, 7, None))
    assert result is None, result
    # overlap cases, other after us
    result = ot.Delete(5, 6, None).after(ot.Delete(6, 4, None))
    assert result == ot.Delete(5, 2, None), result
    result = ot.Delete(5, 6, None).after(ot.Delete(6, 5, None))
    assert result == ot.Delete(5, 1, None), result
    result = ot.Delete(5, 6, None).after(ot.Delete(6, 6, None))
    assert result == ot.Delete(5, 1, None), result


def test_conflicts():
    # insert-insert
    assert not ot.conflicts(ot.Insert(5, b"abc"), ot.Insert(4, b"abc"))
    assert not ot.conflicts(ot.Insert(5, b"abc"), ot.Insert(6, b"abc"))
    assert ot.conflicts(ot.Insert(5, b"abc"), ot.Insert(5, b"abc"))

    # delete-insert
    assert not ot.conflicts(ot.Delete(5, 6, None), ot.Insert(4, b"abc"))
    assert not ot.conflicts(ot.Delete(5, 6, None), ot.Insert(12, b"abc"))
    assert ot.conflicts(ot.Delete(5, 6, None), ot.Insert(5, b"abc"))
    assert ot.conflicts(ot.Delete(5, 6, None), ot.Insert(9, b"abc"))
    assert ot.conflicts(ot.Delete(5, 6, None), ot.Insert(11, b"abc"))

    # delete-delete
    assert not ot.conflicts(ot.Delete(5, 6, None), ot.Delete(3, 1, None))
    assert not ot.conflicts(ot.Delete(5, 6, None), ot.Delete(12, 1, None))
    assert ot.conflicts(ot.Delete(5, 6, None), ot.Delete(3, 2, None))
    assert ot.conflicts(ot.Delete(5, 6, None), ot.Delete(3, 7, None))
    assert ot.conflicts(ot.Delete(5, 6, None), ot.Delete(3, 8, None))
    assert ot.conflicts(ot.Delete(5, 6, None), ot.Delete(3, 9, None))
    assert ot.conflicts(ot.Delete(5, 6, None), ot.Delete(5, 1, None))
    assert ot.conflicts(ot.Delete(5, 6, None), ot.Delete(5, 5, None))
    assert ot.conflicts(ot.Delete(5, 6, None), ot.Delete(5, 6, None))
    assert ot.conflicts(ot.Delete(5, 6, None), ot.Delete(5, 7, None))
    assert ot.conflicts(ot.Delete(5, 6, None), ot.Delete(6, 1, None))
    assert ot.conflicts(ot.Delete(5, 6, None), ot.Delete(6, 4, None))
    assert ot.conflicts(ot.Delete(5, 6, None), ot.Delete(6, 5, None))
    assert ot.conflicts(ot.Delete(5, 6, None), ot.Delete(6, 6, None))
    assert ot.conflicts(ot.Delete(5, 6, None), ot.Delete(11, 1, None))


def test_shadow():
    """
    Testing the example histories from the protocol description:

        a - b - x' - c - d - y' - e   # real history

        a - x - y - b' - c' - d' - e  # shadow history
    """
    def ids(author):
        val = 0
        while True:
            yield ot.ID(val, author)
            val += 1

    srv_ids = ids(0)
    cli_ids = ids(1)

    def applyall(*edits):
        text = b""
        for edit in edits:
            text = edit.ot.apply(text)
        return text

    base_id = next(srv_ids)
    a = ot.Edit(
        ot=ot.Insert(0, b"hello world"),
        id=base_id,
        parent=base_id,
    )

    shadow = ot.Shadow(a.id)

    # external edit b is simultaneous with new submission x
    b = ot.Edit(
        ot=ot.Insert(6, b"bb "),
        id=next(srv_ids),
        parent=a.id,
    )
    x = ot.Edit(
        ot=ot.Insert(11, b"x"),
        id=next(cli_ids),
        parent=a.id,
    )
    xp_ot = shadow.new_submission(x, [b])
    bp_ot = b.ot.after(x.ot)

    assert xp_ot == ot.Insert(14, b"x"), xp_ot
    assert len(shadow.tail) == 1, shadow.tail
    assert shadow.tail[0].ot == ot.Insert(6, b"bb "), shadow.tail
    assert (t := applyall(a, b)) == b"hello bb world", t
    assert (t := applyall(a, x)) == b"hello worldx", t

    # xp would land after b
    xp = ot.Edit(
        ot=xp_ot,
        id=next(srv_ids),
        parent=b.id,
        submitted_id=x.id,
    )
    t1 = applyall(a, *shadow.submissions, *shadow.tail)
    t2 = applyall(a, b, xp)
    assert t1 == t2 == b"hello bb worldx", (t1, t2)

    # external edits c and d are simultaneous with new submission y
    c = ot.Edit(
        ot=ot.Insert(9, b"ccc "),
        id=next(srv_ids),
        parent=xp.id,
    )
    d = ot.Edit(
        ot=ot.Insert(13, b"dddd "),
        id=next(srv_ids),
        parent=c.id,
    )
    y = ot.Edit(
        ot=ot.Insert(0, b"yy"),
        id=next(cli_ids),
        parent=x.id,
    )
    yp_ot = shadow.new_submission(y, [xp, c, d])

    assert yp_ot == ot.Insert(0, b"yy"), yp_ot
    assert len(shadow.tail) == 3, shadow.tail
    assert shadow.tail[0].ot == ot.Insert(8, b"bb "), shadow.tail
    assert shadow.tail[1].ot == ot.Insert(11, b"ccc "), shadow.tail
    assert shadow.tail[2].ot == ot.Insert(15, b"dddd "), shadow.tail
    assert (t := applyall(a, b, xp, c, d)) == b"hello bb ccc dddd worldx", t
    assert (t := applyall(a, x, y)) == b"yyhello worldx", t

    # yp would land after d
    yp = ot.Edit(
        ot=yp_ot,
        id=next(srv_ids),
        parent=d.id,
        submitted_id=y.id,
    )
    t1 = applyall(a, *shadow.submissions, *shadow.tail)
    t2 = applyall(a, b, xp, c, d, yp)
    assert t1 == t2 == b"yyhello bb ccc dddd worldx", (t1, t2)

    # external edit e is simultaneous with z
    e = ot.Edit(
        ot=ot.Insert(20, b"eeeee "),
        id=next(srv_ids),
        parent=yp.id,
    )
    z = ot.Edit(
        ot=ot.Insert(14, b"zzz"),
        id=next(cli_ids),
        parent=y.id,
    )
    zp_ot = shadow.new_submission(z, [yp, e])

    assert zp_ot == ot.Insert(32, b"zzz"), zp_ot
    assert len(shadow.tail) == 4, shadow.tail
    assert shadow.tail[0].ot == ot.Insert(8, b"bb "), shadow.tail
    assert shadow.tail[1].ot == ot.Insert(11, b"ccc "), shadow.tail
    assert shadow.tail[2].ot == ot.Insert(15, b"dddd "), shadow.tail
    assert shadow.tail[3].ot == ot.Insert(20, b"eeeee "), shadow.tail
    t = applyall(a, b, xp, c, d, yp, e)
    assert t == b"yyhello bb ccc dddd eeeee worldx", t
    assert (t := applyall(a, x, y, z)) == b"yyhello worldxzzz", t

    # zp would land after e
    zp = ot.Edit(
        ot=zp_ot,
        id=next(srv_ids),
        parent=d.id,
        submitted_id=z.id,
    )
    t1 = applyall(a, *shadow.submissions, *shadow.tail)
    t2 = applyall(a, b, xp, c, d, yp, e, zp)
    assert t1 == t2 == b"yyhello bb ccc dddd eeeee worldxzzz", (t1, t2)
    # TODO: support (and test) client-side acks


class LineBuffer:
    def __init__(self, sock):
        self.sock = sock
        self.buf = b""
        self.last = b""

    def readline(self):
        while b"\n" not in self.buf:
            byts = sock.recv(4096)
            if not byts:
                if buf:
                    raise ConnectionError("connection broke mid-line")
                else:
                    raise ConnectionError("connection broke on line bounadry")
            self.buf += byts
        self.last, self.buf = self.buf.split(b"\n", maxsplit=1)
        return self.last


def first_connection(sock, es):
    lb = LineBuffer(sock)
    # initial negotiation
    sock.send(b"new:iamsam\n")
    assert re.match(b"1:secret:0:$", lb.readline()), lb.last
    # insert some text
    sock.send(b"s:0:0:0:i:0:hello world\\n\n")
    assert lb.readline() == b"a:0", lb.last
    assert es.text == b"hello world\n", es.text
    sock.send(b"s:1:1:0:i:6:cruel \n")
    assert lb.readline() == b"a:1", lb.last
    assert es.text == b"hello cruel world\n", es.text


if __name__ == "__main__":
    # unit tests
    test_encode_decode()
    test_insert()
    test_delete()
    test_conflicts()
    test_shadow()

    # e2e tests
    socketpath = "./asdf"

    transport = ot.SocketTransport(addr=socketpath, family=socket.AF_UNIX)
    edit_server = ot.EditServer(transport)
    transport.set_edit_server(edit_server)

    # this server is missing an off-thread cancelation mechanism
    thread = threading.Thread(
        target=trio.run, args=(transport.run,), daemon=True
    )
    thread.start()
    try:
        # this server is also missing a mechanism for creating the listener
        # socket before accepting on it
        for i in range(30):
            sock = socket.socket(family=socket.AF_UNIX)
            try:
                sock.connect(socketpath)
                break
            except:
                sock.close()
                time.sleep(0.01)
        else:
            raise ValueError("failed to connect within 300ms")
        try:
            first_connection(sock, edit_server)
        finally:
            sock.close()

        print("PASS!")

    finally:
        os.unlink(socketpath)


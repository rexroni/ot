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


def test_suite():
    def decode_obj(blob):
        if blob == "x": return None
        a, b, c = blob.split(":")
        return ot.decode_ot(
            a.encode("utf8"), b.encode("utf8"), c.encode("utf8")
        )

    with open("test_suite") as f:
        text = f.read()
    for line in text.splitlines():
        if not line or line.strip().startswith("#"): continue
        x = line.split("|")
        assert x[0] in ("apply", "after", "conflicts"), x[0]
        if x[0] == "apply":
            obj = decode_obj(x[1])
            text = x[2].encode("utf8")
            exp = x[3].encode("utf8")
            got = obj.apply(text)
            assert exp == got, f"{obj}.apply({text}) == {got}, expected {exp}"
        elif x[0] == "after":
            a = decode_obj(x[1])
            b = decode_obj(x[2])
            exp = decode_obj(x[3])
            got = a.after(b)
            assert exp == got, f"{a}.after({b}) == {got}, expected {exp}"
        elif x[0] == "conflicts":
            a = decode_obj(x[1])
            b = decode_obj(x[2])
            exp = x[3] == "true"
            got = ot.conflicts(a, b)
            assert exp == got, f"conflicts({a}, {b}) == {got}, expected {exp}"


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
    sock.send(b"s:2:1:1:i:11:-ass\n")
    assert lb.readline() == b"a:1", lb.last
    assert lb.readline() == b"a:2", lb.last
    assert es.text == b"hello cruel-ass world\n", es.text


if __name__ == "__main__":
    # unit tests
    test_encode_decode()
    test_suite()
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

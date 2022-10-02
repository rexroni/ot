import os
import re
import socket
import threading
import time

import trio

import ot


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
    test_insert()
    test_delete()
    test_conflicts()

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


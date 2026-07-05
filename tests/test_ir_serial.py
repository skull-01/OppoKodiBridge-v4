import pytest

from ir import proto
from ir.serial_win import ZjiotSerial, SerialToolError


class FakePort:
    def __init__(self, reply=b""):
        self.written = b""
        self._reply = bytearray(reply)

    def write(self, data):
        self.written += bytes(data)

    def read(self, n):
        chunk = bytes(self._reply[:n])
        del self._reply[:n]
        return chunk

    def close(self):
        pass


def make(reply=b""):
    port = FakePort(reply)
    zs = ZjiotSerial("COMX", open_fn=lambda p, b, t: port, sleep=lambda *a: None)
    zs.open()
    return zs, port


def test_send_no_reply():
    zs, port = make()
    frame = proto.build(0, proto.AFN_SEND_RAW, b"\x01")
    assert zs.send_frame(frame, expect_reply=False) is None
    assert port.written == frame


def test_send_reads_and_parses_reply():
    reply = proto.build(0, proto.AFN_LEARN, b"\xaa\xbb")
    zs, port = make(reply)
    parsed = zs.send_frame(proto.build(0, proto.AFN_LEARN, b""), expect_reply=True)
    assert parsed["afn"] == proto.AFN_LEARN and parsed["data"] == b"\xaa\xbb"


def test_read_frame_resyncs_past_leading_noise():
    # #39: a stray/noise byte before the 0x68 header must NOT desync the reader -- it byte-aligns to the
    # header first. Old code read a fixed 3 bytes and failed forever on the retry (read mid-frame).
    good = proto.build(0, proto.AFN_LEARN, b"\xaa\xbb")
    zs, port = make(b"\x00\xff" + good)  # two noise bytes, then a valid frame
    parsed = zs.send_frame(proto.build(0, proto.AFN_LEARN, b""), expect_reply=True)
    assert parsed["afn"] == proto.AFN_LEARN and parsed["data"] == b"\xaa\xbb"


def test_timeout_raises_after_retries():
    zs, port = make(b"")
    frame = proto.build(0, proto.AFN_LEARN, b"")
    with pytest.raises(SerialToolError):
        zs.send_frame(frame, expect_reply=True, attempts=2)
    assert port.written == frame * 2  # one write per attempt


def test_send_before_open_raises():
    zs = ZjiotSerial("COMX", open_fn=lambda p, b, t: FakePort())
    with pytest.raises(SerialToolError):
        zs.send_frame(b"\x68", expect_reply=False)


def test_is_open_lifecycle():
    zs, port = make()
    assert zs.is_open is True
    zs.close()
    assert zs.is_open is False

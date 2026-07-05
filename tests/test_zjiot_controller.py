import pytest

from ir import proto, codes
from ir.serial_win import SerialToolError
from zjiot_console import ZjiotController


class FakeSerial:
    def __init__(self, port, baud):
        self.port = port
        self.baud = baud
        self._open = False
        self.frames = []
        self.reply = None

    @property
    def is_open(self):
        return self._open

    def open(self):
        self._open = True
        return self

    def close(self):
        self._open = False

    def send_frame(self, frame, expect_reply=False):
        self.frames.append(bytes(frame))
        return self.reply if expect_reply else None


def make():
    box = {}

    def factory(port, baud):
        fs = FakeSerial(port, baud)
        box["fs"] = fs
        return fs

    ctl = ZjiotController(serial_factory=factory)
    ctl.connect("COMX", 9600)
    return ctl, box["fs"]


def test_not_connected_raises():
    ctl = ZjiotController(serial_factory=lambda p, b: FakeSerial(p, b))
    with pytest.raises(Exception):
        ctl.send_slot(1)


def test_connect_disconnect():
    ctl, fs = make()
    assert ctl.connected is True
    ctl.disconnect()
    assert ctl.connected is False


def test_send_slot_builds_frame():
    ctl, fs = make()
    ctl.send_slot(3)
    parsed = proto.parse(fs.frames[-1])
    assert parsed["afn"] == proto.AFN_SEND_SLOT and parsed["data"] == b"\x03"


def test_send_slot_rejects_out_of_range():
    # #39: an out-of-range slot must raise, not silently mask (& 0xFF) and address the WRONG slot.
    ctl, fs = make()
    for bad in (256, -1, 1000):
        with pytest.raises(SerialToolError):
            ctl.send_slot(bad)
    assert fs.frames == []  # nothing was ever sent for an invalid slot


def test_write_slot_rejects_out_of_range():
    ctl, fs = make()
    with pytest.raises(SerialToolError):
        ctl.write_slot(256, b"\x01")
    assert fs.frames == []


def test_send_nec_frame_carries_timings():
    ctl, fs = make()
    ctl.send_nec(0x57E3, 0x10, extended=True)
    parsed = proto.parse(fs.frames[-1])
    assert parsed["afn"] == proto.AFN_SEND_RAW
    assert proto.unpack_raw(parsed["data"]) == proto.nec_timings(0x57E3, 0x10, True)


def test_learn_returns_data():
    ctl, fs = make()
    fs.reply = {"addr": 0, "afn": proto.AFN_LEARN, "data": b"\x01\x02"}
    assert ctl.learn() == b"\x01\x02"


def test_send_code_raw():
    ctl, fs = make()
    ctl.send_code(codes.IrCode("x", "raw", "9000 4500 560"))
    parsed = proto.parse(fs.frames[-1])
    assert proto.unpack_raw(parsed["data"]) == [9000, 4500, 560]


def test_send_exact_passthrough():
    ctl, fs = make()
    ctl.send_exact(b"\x68\x02\x00\x00\x20\x20\x16")
    assert fs.frames[-1] == b"\x68\x02\x00\x00\x20\x20\x16"


def test_send_code_nec_uses_scancode_timings():
    ctl, fs = make()
    ctl.send_code(codes.IrCode("x", "nec", "0x1"))
    parsed = proto.parse(fs.frames[-1])
    assert parsed["afn"] == proto.AFN_SEND_RAW
    assert proto.unpack_raw(parsed["data"]) == proto.nec_scancode_timings(0x1)

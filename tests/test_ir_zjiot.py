"""The ZJIoT serial TV-switch transport (resources/lib/ir_zjiot.py): builds a valid framed send-raw
carrying the NEC waveform, writes it via the injected serial writer, and is non-fatal."""
from resources.lib import ir_proto, ir_zjiot
from resources.lib.config import Config


def test_build_nec_frame_is_valid_send_raw():
    frame = ir_zjiot.build_nec_frame("0x57e310ef", addr=0)
    parsed = ir_proto.parse(frame)
    assert parsed["afn"] == ir_proto.AFN_SEND_RAW
    assert ir_proto.unpack_raw(parsed["data"]) == ir_proto.nec_scancode_timings(0x57E310EF)


def test_switcher_writes_two_valid_frames():
    writes = []

    def writer(port, baud, data):
        writes.append((port, baud, bytes(data)))

    cfg = Config(tv_switch_method="ir", ir_serial_port="/dev/ttyUSB1", ir_serial_baud=115200,
                 ir_code_oppo="0x1", ir_code_kodi="0x2")
    sw = ir_zjiot.ZjiotSwitcher(cfg, writer=writer)
    assert sw.to_oppo() is True and sw.to_kodi() is True
    assert len(writes) == 2
    assert writes[0][0] == "/dev/ttyUSB1" and writes[0][1] == 115200
    for _, _, data in writes:  # each write is a valid framed send-raw
        assert ir_proto.parse(data)["afn"] == ir_proto.AFN_SEND_RAW


def test_switcher_empty_code_sends_nothing():
    cfg = Config(tv_switch_method="ir", ir_code_oppo="", ir_code_kodi="")
    sw = ir_zjiot.ZjiotSwitcher(cfg, writer=lambda *a: None)
    assert sw.to_oppo() is False and sw.to_kodi() is False


def test_switcher_nonfatal_on_writer_error():
    def boom(*a):
        raise RuntimeError("serial IR needs POSIX termios")

    cfg = Config(tv_switch_method="ir", ir_code_oppo="0x1")
    assert ir_zjiot.ZjiotSwitcher(cfg, writer=boom).to_oppo() is False


def test_switcher_nonfatal_on_bad_code():
    # a non-hex code must not escape as a ValueError
    cfg = Config(tv_switch_method="ir", ir_code_oppo="not-hex")
    assert ir_zjiot.ZjiotSwitcher(cfg, writer=lambda *a: None).to_oppo() is False

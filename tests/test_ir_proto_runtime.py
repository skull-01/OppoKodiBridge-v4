"""The add-on RUNTIME IR codec (resources/lib/ir_proto.py) -- round-trip + NEC structure smoke tests
(the dev-console copy tools/ir/proto.py carries the fuller suite in tests/test_ir_proto.py)."""
import pytest

from resources.lib import ir_proto as p


def test_build_parse_roundtrip():
    frame = p.build(0x01, p.AFN_SEND_RAW, b"\x0a\x0b")
    assert p.parse(frame) == {"addr": 1, "afn": p.AFN_SEND_RAW, "data": b"\x0a\x0b"}


def test_frame_header_tail_and_bad_frames():
    frame = p.build(0, p.AFN_LEARN, b"")
    assert frame[0] == p.HEADER and frame[-1] == p.TAIL
    with pytest.raises(p.ProtoError):
        p.parse(b"\x00\x02\x00\x00\x20\x20\x16")  # bad header
    bad = bytearray(p.build(1, 0x22, b"\x0a"))
    bad[-2] ^= 0xFF
    with pytest.raises(p.ProtoError):
        p.parse(bytes(bad))  # bad checksum


def test_nec_timings_structure():
    t = p.nec_timings(0x00, 0x00)
    assert len(t) == 67 and t[0] == p.NEC_LEAD_MARK and t[1] == p.NEC_LEAD_SPACE and t[-1] == p.NEC_STOP_MARK


def test_nec_scancode_and_pack_roundtrip():
    t = p.nec_scancode_timings(0x1, nbits=4)
    assert len(t) == 2 + 4 * 2 + 1 and t[3] == p.NEC_ONE_SPACE
    assert p.unpack_raw(p.pack_raw([9000, 4500, 560])) == [9000, 4500, 560]


def test_nec_scancode_timings_rejects_over_wide():
    # #34: reject a code wider than nbits instead of silently truncating it (shipped runtime codec).
    with pytest.raises(p.ProtoError):
        p.nec_scancode_timings(0x1_0000_0000)     # 33 bits, default nbits=32
    with pytest.raises(p.ProtoError):
        p.nec_scancode_timings(0x10, nbits=4)      # 5 bits > 4
    assert p.nec_scancode_timings(0xFFFFFFFF)       # exactly 32 bits -> fine

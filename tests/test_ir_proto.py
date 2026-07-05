import pytest

from ir import proto


def test_build_parse_roundtrip():
    frame = proto.build(0x01, proto.AFN_SEND_RAW, b"\x0a\x0b")
    assert proto.parse(frame) == {"addr": 0x01, "afn": proto.AFN_SEND_RAW, "data": b"\x0a\x0b"}


def test_frame_layout():
    frame = proto.build(0x00, proto.AFN_LEARN, b"")
    assert frame[0] == proto.HEADER and frame[-1] == proto.TAIL
    assert frame[1] == 2 and frame[2] == 0  # length = 2 (addr + afn)


def test_checksum_formula():
    assert proto.checksum(0x01, 0x22, b"\x0a\x0b") == (0x01 + 0x22 + 0x0A + 0x0B) & 0xFF


def test_parse_bad_header():
    with pytest.raises(proto.ProtoError):
        proto.parse(b"\x00\x02\x00\x00\x20\x20\x16")


def test_parse_bad_checksum():
    frame = bytearray(proto.build(0x01, 0x22, b"\x0a"))
    frame[-2] ^= 0xFF
    with pytest.raises(proto.ProtoError):
        proto.parse(bytes(frame))


def test_parse_length_mismatch():
    with pytest.raises(proto.ProtoError):
        proto.parse(b"\x68\x05\x00\x00\x20\x00\x16")


def test_nec_timings_structure():
    t = proto.nec_timings(0x00, 0x00)
    assert len(t) == 2 + 32 * 2 + 1 == 67
    assert t[0] == proto.NEC_LEAD_MARK and t[1] == proto.NEC_LEAD_SPACE
    assert t[-1] == proto.NEC_STOP_MARK


def test_nec_timings_bit_encoding():
    # command 0x01, standard NEC -> bytes [0x00, 0xFF, 0x01, 0xFE]; first bit (LSB of 0x00) is 0
    t = proto.nec_timings(0x00, 0x01)
    assert t[2] == proto.NEC_BIT_MARK
    assert t[3] == proto.NEC_ZERO_SPACE
    # 9th bit is LSB of 0xFF -> 1
    assert t[2 + 16 + 1] == proto.NEC_ONE_SPACE


def test_pack_unpack_roundtrip():
    seq = [9000, 4500, 560, 1690, 560]
    assert proto.unpack_raw(proto.pack_raw(seq)) == seq


def test_pack_clamps_to_u16():
    assert proto.unpack_raw(proto.pack_raw([70000])) == [0xFFFF]


def test_nec_scancode_timings_lsb_first():
    t = proto.nec_scancode_timings(0x1, nbits=4)
    assert len(t) == 2 + 4 * 2 + 1
    assert t[3] == proto.NEC_ONE_SPACE  # LSB of 0x1 is 1
    assert t[5] == proto.NEC_ZERO_SPACE  # next bit is 0


def test_nec_scancode_timings_rejects_over_wide():
    # #34: a code wider than nbits must raise, not silently drop the high bits into a wrong waveform.
    with pytest.raises(proto.ProtoError):
        proto.nec_scancode_timings(0x1_0000_0000)      # 33 bits, default nbits=32
    with pytest.raises(proto.ProtoError):
        proto.nec_scancode_timings(0x10, nbits=4)       # 0x10 needs 5 bits > 4
    assert proto.nec_scancode_timings(0xFFFFFFFF)        # exactly 32 bits -> fine
    assert proto.nec_scancode_timings(0xF, nbits=4)      # exactly 4 bits -> fine

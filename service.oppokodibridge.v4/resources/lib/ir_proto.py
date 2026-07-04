r"""ZJIoT serial IR module -- frame codec + NEC waveform synthesis (add-on runtime copy).

Shared shape with the dev console's ``tools/ir/proto.py`` (which captures/tests the codec on hardware).
Stdlib only. Frame layout (per ``docs/IR_TV_SWITCHING_BUILD_PLAN.md``)::

    0x68  LEN_lo LEN_hi  ADDR AFN  DATA...  CHECKSUM  0x16
          \___ LEN ___/  \______ body ____/
    LEN      = len(body) = 2 + len(DATA)         (ADDR + AFN + DATA)
    CHECKSUM = (ADDR + AFN + sum(DATA)) & 0xFF

.. warning::
   The exact byte layout is transcribed from the plan's summary of the ZJIoT module manual; it is
   internally self-consistent and unit-tested for round-trip, but the wire format must be reconfirmed
   against the real module before it is trusted end-to-end (the feature ships default-off).
"""
from __future__ import annotations

HEADER = 0x68
TAIL = 0x16

AFN_SEND_SLOT = 0x12
AFN_WRITE_SLOT = 0x17
AFN_SEND_RAW = 0x22
AFN_LEARN = 0x20

NEC_LEAD_MARK = 9000
NEC_LEAD_SPACE = 4500
NEC_BIT_MARK = 560
NEC_ZERO_SPACE = 560
NEC_ONE_SPACE = 1690
NEC_STOP_MARK = 560


class ProtoError(ValueError):
    """A malformed or inconsistent ZJIoT frame."""


def checksum(addr: int, afn: int, data: bytes) -> int:
    return (addr + afn + sum(data)) & 0xFF


def build(addr: int, afn: int, data: bytes = b"") -> bytes:
    if not 0 <= addr <= 0xFF:
        raise ProtoError("addr out of range: {!r}".format(addr))
    if not 0 <= afn <= 0xFF:
        raise ProtoError("afn out of range: {!r}".format(afn))
    data = bytes(data)
    length = 2 + len(data)
    if length > 0xFFFF:
        raise ProtoError("payload too large: {} bytes".format(len(data)))
    body = bytes((addr, afn)) + data
    return bytes((HEADER, length & 0xFF, (length >> 8) & 0xFF)) + body + bytes((checksum(addr, afn, data), TAIL))


def parse(frame: bytes) -> dict:
    frame = bytes(frame)
    if len(frame) < 6:
        raise ProtoError("frame too short ({} bytes)".format(len(frame)))
    if frame[0] != HEADER:
        raise ProtoError("bad header 0x{:02x}".format(frame[0]))
    length = frame[1] | (frame[2] << 8)
    if length < 2:
        raise ProtoError("length {} too small".format(length))
    if len(frame) != 3 + length + 2:
        raise ProtoError("length {} inconsistent with frame size {}".format(length, len(frame)))
    body = frame[3 : 3 + length]
    got_cs = frame[3 + length]
    if frame[-1] != TAIL:
        raise ProtoError("bad tail 0x{:02x}".format(frame[-1]))
    addr, afn, data = body[0], body[1], body[2:]
    want_cs = checksum(addr, afn, data)
    if got_cs != want_cs:
        raise ProtoError("bad checksum 0x{:02x} (want 0x{:02x})".format(got_cs, want_cs))
    return {"addr": addr, "afn": afn, "data": data}


def nec_timings(address: int, command: int, extended: bool = False) -> list:
    """NEC pulse/space durations (µs): lead pair + 32 bits (LSB-first) + stop mark (67 entries)."""
    command &= 0xFF
    if extended:
        if not 0 <= address <= 0xFFFF:
            raise ProtoError("extended NEC address out of range: {!r}".format(address))
        payload = [address & 0xFF, (address >> 8) & 0xFF, command, command ^ 0xFF]
    else:
        if not 0 <= address <= 0xFF:
            raise ProtoError("NEC address out of range: {!r}".format(address))
        payload = [address & 0xFF, address ^ 0xFF, command, command ^ 0xFF]
    timings = [NEC_LEAD_MARK, NEC_LEAD_SPACE]
    for byte in payload:
        for bit in range(8):
            timings.append(NEC_BIT_MARK)
            timings.append(NEC_ONE_SPACE if (byte >> bit) & 1 else NEC_ZERO_SPACE)
    timings.append(NEC_STOP_MARK)
    return timings


def nec_scancode_timings(scancode: int, nbits: int = 32) -> list:
    """NEC timings for a raw ``nbits`` scancode transmitted LSB-first (replays a stored ``nec`` code)."""
    if scancode < 0:
        raise ProtoError("scancode must be non-negative")
    timings = [NEC_LEAD_MARK, NEC_LEAD_SPACE]
    for i in range(nbits):
        timings.append(NEC_BIT_MARK)
        timings.append(NEC_ONE_SPACE if (scancode >> i) & 1 else NEC_ZERO_SPACE)
    timings.append(NEC_STOP_MARK)
    return timings


def pack_raw(timings: list) -> bytes:
    """Pack durations (µs) as little-endian uint16 (provisional -- reversible; confirm on hardware)."""
    out = bytearray()
    for d in timings:
        d = max(0, min(0xFFFF, int(d)))
        out += bytes((d & 0xFF, (d >> 8) & 0xFF))
    return bytes(out)


def unpack_raw(data: bytes) -> list:
    data = bytes(data)
    if len(data) % 2:
        raise ProtoError("raw waveform byte count must be even")
    return [data[i] | (data[i + 1] << 8) for i in range(0, len(data), 2)]

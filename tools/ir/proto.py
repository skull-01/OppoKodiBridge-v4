r"""ZJIoT serial IR module — frame codec + NEC waveform synthesis.

Dev/bench-tool code (imported by ``tools/zjiot_console.py``); also the intended
foundation for a future add-on ``resources/lib/ir.py``.  Stdlib only.

Frame layout (per ``docs/IR_TV_SWITCHING_BUILD_PLAN.md`` — the ZJIoT serial
protocol reversed from ``IR_Learning_Module_Manual_EN.md``)::

    0x68  LEN_lo LEN_hi  ADDR AFN  DATA...  CHECKSUM  0x16
          \___ LEN ___/  \______ body ____/
    LEN      = len(body) = 2 + len(DATA)        (ADDR + AFN + DATA)
    CHECKSUM = (ADDR + AFN + sum(DATA)) & 0xFF

.. warning::
   The exact byte layout above is transcribed from the plan's summary of the
   module manual, which is **not currently on disk** (see plan risk #2).  The
   codec is internally self-consistent and unit-tested for round-trip, but the
   wire format **must be reconfirmed against the real module/manual** before it
   is trusted end-to-end.  The *learn* path (``AFN_LEARN``) captures the
   module's own raw bytes verbatim and does not depend on this being exact.
"""

from __future__ import annotations

HEADER = 0x68
TAIL = 0x16

# AFN (application function) command bytes.
AFN_SEND_SLOT = 0x12   # emit a stored slot by index
AFN_WRITE_SLOT = 0x17  # store a raw waveform into a slot
AFN_SEND_RAW = 0x22    # emit a raw waveform now
AFN_LEARN = 0x20       # enter learn mode; module replies with the captured raw

AFN_NAMES = {
    AFN_SEND_SLOT: "send-slot",
    AFN_WRITE_SLOT: "write-slot",
    AFN_SEND_RAW: "send-raw",
    AFN_LEARN: "learn",
}

# Standard NEC timing (microseconds), 38 kHz carrier.
NEC_LEAD_MARK = 9000
NEC_LEAD_SPACE = 4500
NEC_BIT_MARK = 560
NEC_ZERO_SPACE = 560
NEC_ONE_SPACE = 1690
NEC_STOP_MARK = 560


class ProtoError(ValueError):
    """A malformed or inconsistent ZJIoT frame."""


def checksum(addr: int, afn: int, data: bytes) -> int:
    """The frame checksum: ``(addr + afn + sum(data)) & 0xFF``."""
    return (addr + afn + sum(data)) & 0xFF


def build(addr: int, afn: int, data: bytes = b"") -> bytes:
    """Build a complete framed command for ``addr``/``afn`` carrying ``data``."""
    if not 0 <= addr <= 0xFF:
        raise ProtoError("addr out of range: {!r}".format(addr))
    if not 0 <= afn <= 0xFF:
        raise ProtoError("afn out of range: {!r}".format(afn))
    data = bytes(data)
    length = 2 + len(data)  # addr + afn + data
    if length > 0xFFFF:
        raise ProtoError("payload too large: {} bytes".format(len(data)))
    body = bytes((addr, afn)) + data
    return (
        bytes((HEADER, length & 0xFF, (length >> 8) & 0xFF))
        + body
        + bytes((checksum(addr, afn, data), TAIL))
    )


def parse(frame: bytes) -> dict:
    """Parse a framed reply/command into ``{addr, afn, data}``; raise on any defect."""
    frame = bytes(frame)
    if len(frame) < 6:
        raise ProtoError("frame too short ({} bytes)".format(len(frame)))
    if frame[0] != HEADER:
        raise ProtoError("bad header 0x{:02x}".format(frame[0]))
    length = frame[1] | (frame[2] << 8)
    if length < 2:
        raise ProtoError("length {} too small".format(length))
    if len(frame) != 3 + length + 2:
        raise ProtoError(
            "length {} inconsistent with frame size {}".format(length, len(frame))
        )
    body = frame[3 : 3 + length]
    got_cs = frame[3 + length]
    if frame[-1] != TAIL:
        raise ProtoError("bad tail 0x{:02x}".format(frame[-1]))
    addr, afn, data = body[0], body[1], body[2:]
    want_cs = checksum(addr, afn, data)
    if got_cs != want_cs:
        raise ProtoError("bad checksum 0x{:02x} (want 0x{:02x})".format(got_cs, want_cs))
    return {"addr": addr, "afn": afn, "data": data}


def _nec_bytes(address: int, command: int, extended: bool) -> list:
    """The four NEC payload bytes, LSB-first order: addr, addr', cmd, cmd'."""
    command &= 0xFF
    if extended:
        if not 0 <= address <= 0xFFFF:
            raise ProtoError("extended NEC address out of range: {!r}".format(address))
        return [address & 0xFF, (address >> 8) & 0xFF, command, command ^ 0xFF]
    if not 0 <= address <= 0xFF:
        raise ProtoError("NEC address out of range: {!r}".format(address))
    return [address & 0xFF, address ^ 0xFF, command, command ^ 0xFF]


def nec_timings(address: int, command: int, extended: bool = False) -> list:
    """Return the NEC pulse/space durations (µs) for ``address``/``command``.

    Order is mark, space, mark, space, ... starting with the 9 ms lead mark and
    ending with the stop mark (67 entries total: lead pair + 32 bits × 2 + stop).
    Standard NEC uses ``addr, ~addr``; ``extended=True`` uses a 16-bit address
    (``addr_lo, addr_hi``).
    """
    timings = [NEC_LEAD_MARK, NEC_LEAD_SPACE]
    for byte in _nec_bytes(address, command, extended):
        for bit in range(8):  # LSB first
            timings.append(NEC_BIT_MARK)
            timings.append(NEC_ONE_SPACE if (byte >> bit) & 1 else NEC_ZERO_SPACE)
    timings.append(NEC_STOP_MARK)
    return timings


def nec_scancode_timings(scancode: int, nbits: int = 32) -> list:
    """NEC timings for a raw ``nbits`` scancode, transmitted LSB-first.

    Replays a stored ``nec`` code-library value (e.g. ``0x57e310ef``) without
    splitting it into address/command.  Bit order/width may need confirming
    against the target device.
    """
    if scancode < 0:
        raise ProtoError("scancode must be non-negative")
    if scancode >= (1 << nbits):
        # #34: reject a code wider than nbits instead of silently dropping the high bits (which would
        # transmit a wrong waveform, e.g. a mistyped 40-bit code sent as if it were 32-bit garbage).
        raise ProtoError("scancode {:#x} exceeds {} bits".format(scancode, nbits))
    timings = [NEC_LEAD_MARK, NEC_LEAD_SPACE]
    for i in range(nbits):
        timings.append(NEC_BIT_MARK)
        timings.append(NEC_ONE_SPACE if (scancode >> i) & 1 else NEC_ZERO_SPACE)
    timings.append(NEC_STOP_MARK)
    return timings


def pack_raw(timings: list) -> bytes:
    """Pack pulse/space durations (µs) into the module's raw-waveform bytes.

    .. warning::
       Provisional encoding — each duration as a little-endian ``uint16`` (µs),
       clamped to 0xFFFF.  This is a *reversible* placeholder (see
       :func:`unpack_raw`); the module's true raw encoding must be confirmed
       against the manual, or captured via the learn path instead.
    """
    out = bytearray()
    for d in timings:
        d = max(0, min(0xFFFF, int(d)))
        out += bytes((d & 0xFF, (d >> 8) & 0xFF))
    return bytes(out)


def unpack_raw(data: bytes) -> list:
    """Inverse of :func:`pack_raw` (little-endian ``uint16`` µs durations)."""
    data = bytes(data)
    if len(data) % 2:
        raise ProtoError("raw waveform byte count must be even")
    return [data[i] | (data[i + 1] << 8) for i in range(0, len(data), 2)]

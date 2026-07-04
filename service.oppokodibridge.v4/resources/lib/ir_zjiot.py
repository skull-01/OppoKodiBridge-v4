"""ZJIoT serial IR TV-switch transport -- for a Ugoos / CoreELEC (Amlogic) host.

Sends the TV's HDMI-input NEC code to a ZJIoT serial IR module over a USB-TTL serial port. Builds the
module frame with :mod:`ir_proto` and writes it over POSIX ``termios`` (no pyserial -- mirrors
``oppo_http.serial_command``, so the add-on stays a stdlib runtime-only zip). Non-fatal by contract.

Separate from :mod:`ir_lirc` (the RPi4 path) -- the two share no transport code, only the selector.
Codes come from the ``ir_code_oppo`` / ``ir_code_kodi`` settings (captured with ``tools/zjiot_console.py``).
"""
from __future__ import annotations

from . import ir_proto
from .kodilog import log

_BAUD_CONSTS = {
    2400: "B2400", 4800: "B4800", 9600: "B9600", 19200: "B19200",
    38400: "B38400", 57600: "B57600", 115200: "B115200",
}


def build_nec_frame(scancode: str, addr: int = 0) -> bytes:
    """A ZJIoT ``send-raw`` frame carrying the NEC waveform for ``scancode`` (a ``0x..`` hex string)."""
    code = int(str(scancode).strip(), 16)
    return ir_proto.build(addr, ir_proto.AFN_SEND_RAW, ir_proto.pack_raw(ir_proto.nec_scancode_timings(code)))


def _write_serial(port: str, baud: int, data: bytes) -> None:
    """Write raw bytes to a serial port via POSIX termios. Raises on any failure (caller stays
    non-fatal). Mirrors ``oppo_http.serial_command``'s open/configure, but binary + write-only."""
    import os

    try:
        import termios
    except ImportError as exc:
        raise RuntimeError("serial IR needs POSIX termios (unavailable here): {}".format(exc)) from exc
    baud_const = getattr(termios, _BAUD_CONSTS.get(int(baud), "B9600"))
    fd = os.open(port, os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK)
    try:
        attrs = termios.tcgetattr(fd)
        attrs[0] = 0
        attrs[1] = 0
        attrs[3] = 0
        attrs[2] = (attrs[2] & ~termios.CSIZE & ~termios.PARENB & ~termios.CSTOPB) | termios.CS8 | termios.CREAD | termios.CLOCAL
        attrs[4] = baud_const
        attrs[5] = baud_const
        termios.tcsetattr(fd, termios.TCSANOW, attrs)
        termios.tcflush(fd, termios.TCIOFLUSH)
        os.write(fd, bytes(data))
    finally:
        os.close(fd)


class ZjiotSwitcher:
    """``tv_switch_method=ir``: send the stored HDMI-input codes to the ZJIoT module over serial.

    ``writer(port, baud, data)`` is injectable so this is unit-testable without a serial port."""

    def __init__(self, config, writer=None):
        self.config = config
        self._writer = writer or _write_serial

    def _send(self, code) -> bool:
        s = str(code or "").strip()
        if not s:
            log("ZJIoT IR: empty code; nothing sent")
            return False
        try:
            # module bus address is fixed at 0 for this release (single-module); getattr keeps it
            # forward-compatible if an ir_module_addr setting is added later.
            frame = build_nec_frame(s, addr=int(getattr(self.config, "ir_module_addr", 0) or 0))
            self._writer(
                getattr(self.config, "ir_serial_port", "/dev/ttyUSB0") or "/dev/ttyUSB0",
                int(getattr(self.config, "ir_serial_baud", 9600) or 9600),
                frame,
            )
            return True
        except Exception as exc:  # noqa: BLE001 -- non-POSIX host / bad port / bad code -- all non-fatal
            log("ZJIoT IR send failed (non-fatal): {}".format(exc))
            return False

    def to_oppo(self) -> bool:
        return self._send(getattr(self.config, "ir_code_oppo", ""))

    def to_kodi(self) -> bool:
        return self._send(getattr(self.config, "ir_code_kodi", ""))

"""Serial transport for the ZJIoT module (Windows dev tool).

Uses **pyserial** — the add-on's ``oppo_http.serial_command`` is termios/POSIX
only and refuses non-POSIX hosts, so it cannot be reused on Windows.  pyserial
is a **dev-only** dependency (``tools/requirements-dev.txt``); it is never
imported by the shipped add-on.

The low-level port is injectable (``open_fn``) so the transport is unit-testable
without pyserial or real hardware.
"""

from __future__ import annotations

import time

from ir import proto

DEFAULT_BAUD = 9600  # provisional — confirm against the module manual
ACK_TIMEOUT = 1.0
MAX_ATTEMPTS = 2
MAX_RESYNC_BYTES = 64  # bound on leading noise bytes to skip while hunting the 0x68 header (#39)


class SerialToolError(RuntimeError):
    """Serial layer failure (no pyserial, port won't open, no reply, ...)."""


def list_ports():
    """Return ``[(device, description), ...]`` for attached serial ports.

    Empty list (never an exception) if pyserial is unavailable, so the GUI can
    still start and report the missing dependency.
    """
    try:
        from serial.tools import list_ports as _lp  # type: ignore
    except Exception:
        return []
    return [(p.device, p.description or "") for p in _lp.comports()]


def _default_open(port, baud, timeout):
    try:
        import serial  # type: ignore
    except Exception as exc:  # pyserial missing
        raise SerialToolError(
            "pyserial is not installed — run: pip install -r tools/requirements-dev.txt"
        ) from exc
    try:
        return serial.Serial(port=port, baudrate=baud, timeout=timeout)
    except Exception as exc:  # serial.SerialException et al.
        raise SerialToolError("cannot open {} @ {} baud: {}".format(port, baud, exc)) from exc


class ZjiotSerial:
    """A framed connection to the ZJIoT module over a serial port.

    ``open_fn(port, baud, timeout) -> port_obj`` where ``port_obj`` exposes
    ``write(bytes)``, ``read(n) -> bytes`` and ``close()`` (pyserial's ``Serial``
    satisfies this; tests inject a fake).
    """

    def __init__(self, port, baud=DEFAULT_BAUD, open_fn=None, sleep=time.sleep):
        self.port = port
        self.baud = baud
        self._open_fn = open_fn or _default_open
        self._sleep = sleep
        self._h = None

    @property
    def is_open(self):
        return self._h is not None

    def open(self):
        if self._h is None:
            self._h = self._open_fn(self.port, self.baud, ACK_TIMEOUT)
        return self

    def close(self):
        if self._h is not None:
            try:
                self._h.close()
            finally:
                self._h = None

    def __enter__(self):
        return self.open()

    def __exit__(self, *exc):
        self.close()

    def send_frame(self, frame, expect_reply=True, attempts=MAX_ATTEMPTS):
        """Write ``frame`` and (optionally) read + parse one reply frame.

        Returns the parsed reply dict, or ``None`` when ``expect_reply`` is
        False.  Raises :class:`SerialToolError` if no valid reply arrives within
        the retry budget.
        """
        if self._h is None:
            raise SerialToolError("port is not open")
        frame = bytes(frame)
        last = None
        for attempt in range(1, attempts + 1):
            self._h.write(frame)
            if not expect_reply:
                return None
            reply = self._read_frame()
            if reply is not None:
                try:
                    return proto.parse(reply)
                except proto.ProtoError as exc:
                    last = exc
            else:
                last = "timeout"
            if attempt < attempts:
                self._sleep(0.05)
        raise SerialToolError("no valid reply after {} attempt(s): {}".format(attempts, last))

    def _read_frame(self):
        """Read one ``0x68 … 0x16`` frame using the length field; None on timeout.

        Byte-aligns to the 0x68 header first (#39): a single stray/noise byte (line-settling right after
        open, a framing glitch, a trailing byte from a prior frame) must not permanently desync the
        reader. The old code read a fixed 3 bytes and bailed if byte[0] wasn't the header, so on the retry
        it re-read from the MIDDLE of the good frame and failed forever. Now: scan forward one byte at a
        time, discarding non-header bytes (bounded by MAX_RESYNC_BYTES), until the header is seen, then
        read the length + payload. A read that returns nothing is a timeout -> None."""
        header = b""
        for _ in range(MAX_RESYNC_BYTES):
            b = self._h.read(1)
            if not b:
                return None  # timeout / no more data
            if b[0] == proto.HEADER:
                header = b
                break
        if not header:
            return None  # only noise, never found the header within the bound
        lenbytes = self._h.read(2)  # LEN_lo, LEN_hi
        if len(lenbytes) < 2:
            return None
        length = lenbytes[0] | (lenbytes[1] << 8)
        rest = self._h.read(length + 2)  # body + checksum + tail
        if len(rest) < length + 2:
            return None
        return header + lenbytes + rest

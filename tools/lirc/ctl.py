"""Thin wrappers over ``ir-ctl`` / ``ir-keytable`` for send + learn.

All external calls go through an injectable ``run_fn`` (``(args) -> (rc, out,
err)``) so the wrappers are unit-testable without a Pi or IR hardware.  Stdlib
only.
"""

from __future__ import annotations

import os
import re
import tempfile

from lirc.devices import _run, LircToolError

_SCANCODE_RE = re.compile(r"scancode\s*=\s*(0x[0-9a-fA-F]+)")
_CARRIER_RE = re.compile(r"(?im)^\s*carrier\s+(\d+)")
DEFAULT_CARRIER = 38000  # NEC/typical; a learned non-38 kHz waveform must carry its own (#40)


def normalize_scancode(scancode) -> str:
    """Coerce an int or ``'0x..'``/hex string to a ``'0x…'`` lowercase string."""
    if isinstance(scancode, int):
        return "0x{:x}".format(scancode)
    s = str(scancode).strip().lower()
    if not s:
        raise LircToolError("empty scancode")
    if not s.startswith("0x"):
        s = "0x" + s
    try:
        int(s, 16)
    except ValueError as exc:
        raise LircToolError("not a hex scancode: {!r}".format(scancode)) from exc
    return s


def send_nec(dev, scancode, run_fn=_run):
    """Send a NEC scancode via ``ir-ctl -d <dev> -S nec:<scancode>``."""
    code = normalize_scancode(scancode)
    rc, out, err = run_fn(["ir-ctl", "-d", dev, "-S", "nec:{}".format(code)])
    if rc != 0:
        raise LircToolError("send failed: {}".format(err.strip() or "rc={}".format(rc)))
    return code


def raw_file_text(timings, carrier=DEFAULT_CARRIER) -> str:
    """Render pulse/space durations (µs) as an ``ir-ctl`` send file at ``carrier`` Hz.

    #40: the carrier is a parameter (not hardcoded 38000) so a waveform learned from a non-38 kHz remote
    (Sony SIRC ~40 kHz, Philips RC-5/6 ~36 kHz) is retransmitted at its OWN carrier -- pass the carrier
    captured by ``parse_raw_carrier`` alongside the timings."""
    lines = ["carrier {}".format(int(carrier or DEFAULT_CARRIER))]
    for i, d in enumerate(timings):
        lines.append("{} {}".format("pulse" if i % 2 == 0 else "space", int(d)))
    return "\n".join(lines) + "\n"


def parse_raw_carrier(text: str):
    """The ``carrier N`` value (Hz) from an ``ir-ctl -r`` capture, or ``None`` if not reported (#40)."""
    m = _CARRIER_RE.search(text or "")
    return int(m.group(1)) if m else None


def send_raw(dev, timings, run_fn=_run, tempdir=None, carrier=DEFAULT_CARRIER):
    """Send a raw pulse/space waveform via ``ir-ctl -d <dev> --send <file>`` at ``carrier`` Hz."""
    if not timings:
        raise LircToolError("no timings to send")
    fd, path = tempfile.mkstemp(suffix=".ir", dir=tempdir)
    try:
        os.write(fd, raw_file_text(timings, carrier).encode("ascii"))
        os.close(fd)
        rc, out, err = run_fn(["ir-ctl", "-d", dev, "--send", path])
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass
    if rc != 0:
        raise LircToolError("raw send failed: {}".format(err.strip() or "rc={}".format(rc)))
    return True


def parse_raw_capture(text: str):
    """Parse ``ir-ctl -r`` output into a list of durations (µs).

    Accepts both the ``pulse N`` / ``space N`` line form and the ``+N -N``
    token form; ignores ``carrier`` / ``timeout`` metadata lines.
    """
    timings = []
    for tok in re.split(r"[\s]+", text.strip()):
        if not tok:
            continue
        if tok[0] in "+-" and tok[1:].isdigit():  # single leading sign, digits after
            timings.append(abs(int(tok)))
    if timings:
        return timings
    for line in text.splitlines():
        parts = line.split()
        if len(parts) == 2 and parts[0].lower() in ("pulse", "space") and parts[1].isdigit():
            timings.append(int(parts[1]))
    return timings


def learn_raw(dev, run_fn=_run, timeout=10.0):
    """Capture one raw burst via ``ir-ctl -d <dev> -r``; returns durations (µs)."""
    rc, out, err = run_fn(["ir-ctl", "-d", dev, "-r"], timeout=timeout)
    if rc not in (0, 124):
        raise LircToolError("capture failed: {}".format(err.strip() or "rc={}".format(rc)))
    return parse_raw_capture(out)


def parse_keytable_scancodes(text: str):
    """Extract ``scancode = 0x..`` values from ``ir-keytable -t`` output."""
    return _SCANCODE_RE.findall(text)


def learn_decoded(protocol="nec", run_fn=_run, timeout=10.0):
    """Enable a protocol and read decoded scancodes via ``ir-keytable -p <p> -t``.

    Returns the list of scancode strings seen (last one is usually the button
    you pressed).
    """
    rc, out, err = run_fn(["ir-keytable", "-p", protocol, "-t"], timeout=timeout)
    if rc not in (0, 124):
        raise LircToolError("ir-keytable failed: {}".format(err.strip() or "rc={}".format(rc)))
    return parse_keytable_scancodes(out)

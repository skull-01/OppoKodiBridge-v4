"""Discover and classify ``/dev/lirc*`` transmit/receive devices on a Pi.

``gpio-ir`` is an rc-core receiver: it exposes a raw ``/dev/lirc*`` node *and* a
``/dev/input/event*`` node, and probe order is not guaranteed — so we classify
each ``/dev/lirc*`` by asking ``ir-ctl --features`` whether it can send/receive
rather than assuming ``lirc0`` is the transmitter.

``run_fn`` / ``glob_fn`` are injectable so this is unit-testable off a Pi.
Stdlib only.
"""

from __future__ import annotations

from dataclasses import dataclass


class LircToolError(RuntimeError):
    """A LIRC tooling failure (command missing, unreadable device, ...)."""


@dataclass
class LircDevice:
    path: str
    can_send: bool
    can_receive: bool

    @property
    def role(self):
        if self.can_send and self.can_receive:
            return "tx+rx"
        if self.can_send:
            return "tx"
        if self.can_receive:
            return "rx"
        return "?"


def _run(args, timeout=5.0):
    """Default runner: ``(returncode, stdout, stderr)``; never raises on non-zero."""
    import subprocess

    try:
        p = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
        return p.returncode, p.stdout, p.stderr
    except FileNotFoundError as exc:
        raise LircToolError(
            "command not found: {} — install v4l-utils".format(args[0])
        ) from exc
    except subprocess.TimeoutExpired as exc:
        return 124, (exc.stdout or ""), "timed out after {}s".format(timeout)


def _default_glob():
    import glob

    return sorted(glob.glob("/dev/lirc*"))


def parse_features(text: str):
    """Parse ``ir-ctl --features`` output → ``(can_send, can_receive)``.

    Matches 'can send'/'can transmit'/'can receive'; a line containing 'cannot'
    is skipped (so 'Device cannot send' is not misread as sendable).
    """
    can_send = can_receive = False
    for line in text.lower().splitlines():
        if "cannot" in line:
            continue
        if "can send" in line or "can transmit" in line:
            can_send = True
        if "can receive" in line:
            can_receive = True
    return can_send, can_receive


def features(path, run_fn=_run):
    """Query one device's capabilities via ``ir-ctl --features -d <path>``."""
    rc, out, err = run_fn(["ir-ctl", "--features", "-d", path])
    if rc != 0:
        raise LircToolError("ir-ctl --features failed for {}: {}".format(path, err.strip() or rc))
    return parse_features(out)


def discover(run_fn=_run, glob_fn=_default_glob):
    """Enumerate ``/dev/lirc*`` and classify each. Returns ``[LircDevice, ...]``.

    A device whose ``--features`` query fails is still listed (can_send /
    can_receive both False) so the caller can surface it rather than hide it.
    """
    devices = []
    for path in glob_fn():
        try:
            can_send, can_receive = features(path, run_fn=run_fn)
        except LircToolError:
            can_send = can_receive = False
        devices.append(LircDevice(path=path, can_send=can_send, can_receive=can_receive))
    return devices


def classify(devices):
    """Pick a default ``(tx, rx)`` device pair from a discovered list.

    Returns ``(tx_or_None, rx_or_None)`` — the first send-capable and first
    receive-capable devices (they may be the same node).
    """
    tx = next((d for d in devices if d.can_send), None)
    rx = next((d for d in devices if d.can_receive), None)
    return tx, rx

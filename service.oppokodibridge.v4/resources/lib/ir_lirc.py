"""LIRC (Linux kernel IR) TV-switch transport -- for a Raspberry Pi 4 host.

Blasts the TV's HDMI-input NEC scancodes via the kernel's ``/dev/lirc`` TX device, by shelling out to
``ir-ctl`` (from v4l-utils). Stdlib only (subprocess); needs **no** companion Kodi add-on (unlike CEC).
Non-fatal by contract -- a failure logs and returns False; the OPPO still plays regardless.

Codes come from the ``ir_code_oppo`` / ``ir_code_kodi`` settings, captured with the bench console
(``tools/lirc_console.py``, issue #25).
"""
from __future__ import annotations

import subprocess

from .kodilog import log


def _norm_scancode(code) -> str:
    """Coerce a code to a lowercase ``0x..`` NEC scancode string; ``""`` if empty/invalid."""
    s = str(code or "").strip().lower()
    if not s:
        return ""
    if not s.startswith("0x"):
        s = "0x" + s
    try:
        int(s, 16)
    except ValueError:
        return ""
    return s


def _run(args, timeout):
    p = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
    return p.returncode, p.stderr


def send_nec(device: str, scancode, run=None, timeout: float = 4.0) -> bool:
    """``ir-ctl -d <device> -S nec:<scancode>``. Returns True on rc 0; never raises."""
    code = _norm_scancode(scancode)
    if not code:
        log("LIRC: empty/invalid IR code {!r}; nothing sent".format(scancode))
        return False
    args = ["ir-ctl", "-d", str(device or "/dev/lirc0"), "-S", "nec:{}".format(code)]
    runner = run or _run
    try:
        rc, err = runner(args, timeout)
    except Exception as exc:  # noqa: BLE001 -- ir-ctl missing / not on a Pi / any failure is non-fatal
        log("LIRC send failed (non-fatal): {}".format(exc))
        return False
    if rc != 0:
        log("LIRC ir-ctl rc={} sending {}: {}".format(rc, code, (err or "").strip()))
        return False
    return True


class LircSwitcher:
    """``tv_switch_method=lirc``: blast the stored HDMI-input NEC codes on the Pi's GPIO IR LED."""

    def __init__(self, config, run=None):
        self.config = config
        self._run = run

    def _device(self) -> str:
        return getattr(self.config, "ir_lirc_device", "/dev/lirc0") or "/dev/lirc0"

    def to_oppo(self) -> bool:
        return send_nec(self._device(), getattr(self.config, "ir_code_oppo", ""), run=self._run)

    def to_kodi(self) -> bool:
        return send_nec(self._device(), getattr(self.config, "ir_code_kodi", ""), run=self._run)

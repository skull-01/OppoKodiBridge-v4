"""Remote-blaster TV-switch transport (``tv_switch_method='ir_remote'``).

The TV input is switched by an IR blaster on a SEPARATE host (e.g. a Raspberry Pi 4 with a GPIO IR LED),
reached over SSH -- the Kodi host (Ugoos/CoreELEC) has no IR transmit hardware of its own.

Play-side (``to_oppo``): the target TCL panel is an RCA device-15 set with NO discrete HDMI codes, so the
OPPO's input can't be selected with a single code. Instead we drive the TV's on-screen input picker:
``INPUT`` opens it, ``UP`` x anchor parks the highlight on the top entry (HDMI1) as a fixed origin,
``DOWN`` x (port-1) steps to the OPPO's HDMI port, ``OK`` selects it. Every keypress is an RCA-15 frame
the blaster fires via ``ir-ctl``. The whole sequence is generated here and run on the blaster over ONE
SSH call (a self-contained python program on stdin -- nothing is installed on the blaster host).

Stop-side (``to_kodi``): delegates to the existing CEC reclaim (``cec.reclaim_kodi``) -- Kodi re-asserts
its OWN active source, which always worked. IR is only needed for the forward grab the M9207 can't do via
CEC (and whose CEC power-cycle is what wedges the M9207 -- the bug this transport avoids).

Stdlib only (``subprocess``). Non-fatal by contract: any failure logs and returns False; the OPPO still
plays. Hardware-validated 2026-07-08 (codes captured from the real remote, RCA checksums valid).
"""
from __future__ import annotations

import subprocess

from . import cec
from .kodilog import log

# --- RCA protocol (TCL device 15), captured + checksum-validated on hardware --------------------------
RCA_CARRIER = 38000
_HEADER = (4000, 4000)
_MARK = 450
_ZERO_SPACE = 1050
_ONE_SPACE = 2050
_TRAILER = 450
_GAP = 7356  # inter-frame gap when a key is repeated (reps)


def rca_frame(device: int, command: int) -> list:
    """Pulse/space durations (us) for one RCA frame: 4-bit device + 8-bit command + both inverted, MSB
    first, framed by the ~4ms header and a trailing mark. This is exactly the waveform validated live."""
    bits = "{:04b}{:08b}{:04b}{:08b}".format(
        device & 0xF, command & 0xFF, (~device) & 0xF, (~command) & 0xFF)
    durs = list(_HEADER)
    for b in bits:
        durs += [_MARK, _ONE_SPACE if b == "1" else _ZERO_SPACE]
    durs.append(_TRAILER)
    return durs


def input_sequence(config) -> list:
    """The RCA commands to select the OPPO's HDMI input: INPUT, UP x anchor, DOWN x (port-1), OK.

    The UP x anchor parks the highlight on the top entry (HDMI1) regardless of the input playback started
    from, so the DOWN count is measured from a fixed origin -- deterministic."""
    port = min(4, max(1, int(getattr(config, "oppo_hdmi_port", 1) or 1)))
    ups = max(0, int(getattr(config, "tv_menu_anchor_ups", 4)))
    c_in = int(getattr(config, "tv_code_input", 163))
    c_up = int(getattr(config, "tv_code_up", 89))
    c_dn = int(getattr(config, "tv_code_down", 88))
    c_ok = int(getattr(config, "tv_code_ok", 244))
    return [c_in] + [c_up] * ups + [c_dn] * (port - 1) + [c_ok]


# The generator that runs ON the blaster (fed to `python3 -` over SSH). It is `.format`-ed with only ints
# and the device-path string (all operator-controlled settings), so there is no untrusted interpolation.
# Fires each RCA command as a `reps`-frame burst via ir-ctl, sleeping `delay` between keypresses so the
# TV registers each as a distinct button. Doubled braces survive the .format().
_REMOTE_PROGRAM = r'''
import subprocess, tempfile, os, time
DEV = {dev!r}
DEVICE, CARRIER, REPS, GAP = {device}, {carrier}, {reps}, {gap}
MARK, Z, O, TR = {mark}, {zero}, {one}, {trailer}
HEADER = {header!r}
DELAY = {delay}
CMDS = {cmds!r}
def frame(c):
    bits = "{{:04b}}{{:08b}}{{:04b}}{{:08b}}".format(DEVICE & 0xF, c & 0xFF, (~DEVICE) & 0xF, (~c) & 0xFF)
    d = list(HEADER)
    for b in bits:
        d += [MARK, O if b == "1" else Z]
    d.append(TR)
    return d
def send(c):
    f = frame(c); durs = list(f)
    for _ in range(max(1, REPS) - 1):
        durs += [GAP] + list(f)
    lines = ["carrier %d" % CARRIER] + ["%s %d" % ("pulse" if i % 2 == 0 else "space", v)
                                        for i, v in enumerate(durs)]
    fd, p = tempfile.mkstemp(suffix=".ir")
    os.write(fd, ("\n".join(lines) + "\n").encode()); os.close(fd)
    try:
        subprocess.run(["ir-ctl", "-d", DEV, "--send", p], check=True)
    finally:
        try:
            os.unlink(p)
        except OSError:
            pass
for i, c in enumerate(CMDS):
    send(c)
    if i < len(CMDS) - 1:
        time.sleep(DELAY)
'''


def build_program(config, cmds) -> str:
    """The self-contained blaster program for ``cmds`` (fed to ``python3 -`` on the blaster)."""
    return _REMOTE_PROGRAM.format(
        dev=str(getattr(config, "tv_blaster_lirc_device", "/dev/lirc0") or "/dev/lirc0"),
        device=int(getattr(config, "tv_rca_device", 15)),
        carrier=int(getattr(config, "tv_ir_carrier", RCA_CARRIER)),
        reps=int(getattr(config, "tv_ir_reps", 3)),
        gap=_GAP, mark=_MARK, zero=_ZERO_SPACE, one=_ONE_SPACE, trailer=_TRAILER,
        header=tuple(_HEADER), delay=float(getattr(config, "tv_ir_key_delay", 0.7)),
        cmds=[int(c) for c in cmds],
    )


def ssh_args(config):
    """The ``ssh`` argv to run ``python3 -`` on the blaster host, or ``None`` if no host is configured."""
    host = str(getattr(config, "ir_blaster_host", "") or "").strip()
    if not host:
        return None
    user = str(getattr(config, "ir_blaster_user", "") or "").strip()
    key = str(getattr(config, "ir_blaster_ssh_key", "") or "").strip()
    # Defense-in-depth: reject option-shaped values (leading '-'). These are operator-set settings, not
    # attacker input, so this is a footgun guard rather than a privilege boundary -- but ssh has no
    # reliable end-of-options for the hostname, so a '-oProxyCommand=...' host would be parsed as an
    # option. Real IPs/hostnames/paths never start with '-'.
    if host.startswith("-") or user.startswith("-") or key.startswith("-"):
        log("ir_remote: refusing option-shaped blaster host/user/key ({!r}/{!r})".format(host, user))
        return None
    target = "{}@{}".format(user, host) if user else host
    args = ["ssh", "-o", "BatchMode=yes",
            "-o", "ConnectTimeout={}".format(int(getattr(config, "ir_blaster_connect_timeout", 8))),
            "-o", "StrictHostKeyChecking=accept-new"]
    if key:
        args += ["-i", key]
    args += [target, "python3 -"]
    return args


def _default_run(args, program, timeout):
    p = subprocess.run(args, input=program, capture_output=True, text=True, timeout=timeout)
    return p.returncode, (p.stderr or "")


class RemoteBlasterSwitcher:
    """``tv_switch_method='ir_remote'``: SSH to a blaster host and drive the TV's input picker to the
    OPPO on play; the existing CEC reclaim on stop."""

    def __init__(self, config, run=None):
        self.config = config
        self._run = run or _default_run

    def to_oppo(self) -> bool:
        # The ENTIRE body is under the try: sequence/program building int()/float()s config values, and a
        # corrupted runtime_config.json (from_dict does no coercion) could raise -- every failure must be
        # non-fatal (the orchestrator fires this and ignores the result). Matches ir_zjiot's contract.
        try:
            args = ssh_args(self.config)
            if args is None:
                log("ir_remote: no ir_blaster_host configured; leaving the TV to be switched manually")
                return False
            program = build_program(self.config, input_sequence(self.config))
            timeout = float(getattr(self.config, "ir_blaster_timeout", 20.0))
            rc, err = self._run(args, program, timeout)
        except Exception as exc:  # noqa: BLE001 -- ssh missing / host down / bad config / any failure
            log("ir_remote to_oppo failed (non-fatal): {}".format(exc))
            return False
        if rc != 0:
            log("ir_remote to_oppo: blaster ssh rc={} err={}".format(rc, (err or "").strip()))
            return False
        log("ir_remote: switched TV to the OPPO (HDMI{} via input-menu nav)".format(
            getattr(self.config, "oppo_hdmi_port", "?")))
        return True

    def to_kodi(self) -> bool:
        # Return to Kodi via the proven CEC reclaim (Kodi re-asserts its own active source); IR is only
        # needed for the forward grab. Gated exactly like CecSwitcher.to_kodi.
        if not getattr(self.config, "cec_reclaim_on_stop", True):
            return False
        return cec.reclaim_kodi(self.config)

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

import shlex
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

    The UP x anchor parks the highlight on the top entry regardless of the input playback started from, so
    the DOWN count is measured from a fixed origin -- deterministic. ``tv_menu_top_offset`` accounts for
    entries ABOVE HDMI1 in the picker (e.g. a leading "Live TV" row): the OPPO is DOWN x (port-1+offset)."""
    port = min(4, max(1, int(getattr(config, "oppo_hdmi_port", 1) or 1)))
    ups = max(0, int(getattr(config, "tv_menu_anchor_ups", 4)))
    offset = max(0, int(getattr(config, "tv_menu_top_offset", 0)))
    c_in = int(getattr(config, "tv_code_input", 163))
    c_up = int(getattr(config, "tv_code_up", 89))
    c_dn = int(getattr(config, "tv_code_down", 88))
    c_ok = int(getattr(config, "tv_code_ok", 244))
    return [c_in] + [c_up] * ups + [c_dn] * (port - 1 + offset) + [c_ok]


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


def _ssh_base(config):
    """``(ssh_option_argv, target)`` for the blaster host, or ``None`` if no host is configured or a
    setting is option-shaped. Shared by the one-shot (``ssh_args``) and the persistent pipe
    (``pipe_ssh_args``); the trailing remote command differs between the two."""
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
    opts = ["-o", "BatchMode=yes",
            "-o", "ConnectTimeout={}".format(int(getattr(config, "ir_blaster_connect_timeout", 8))),
            "-o", "StrictHostKeyChecking=accept-new"]
    if key:
        opts += ["-i", key]
    return opts, target


def ssh_args(config):
    """The ``ssh`` argv to run ``python3 -`` on the blaster host, or ``None`` if no host is configured."""
    base = _ssh_base(config)
    if base is None:
        return None
    opts, target = base
    return ["ssh"] + opts + [target, "python3 -"]


def _default_run(args, program, timeout):
    p = subprocess.run(args, input=program, capture_output=True, text=True, timeout=timeout)
    return p.returncode, (p.stderr or "")


# --- Persistent-pipe IR sender (for rapid keys, e.g. volume passthrough) -------------------------------
#
# The one-shot path above opens a fresh SSH connection per invocation -- fine for the once-per-handoff
# input switch, but volume is a mash/hold button, and a per-press SSH handshake + python startup would be
# ~0.5-1s each. Instead, open ONE ssh session running a stdin read-loop that fires an RCA frame per line,
# and hold it open while passthrough is armed: ~50-80ms per press, and it still installs nothing on the
# blaster. The loop is fed via ``python3 -c`` (NOT ``python3 -``) so stdin stays free for command lines.
#
# This mirrors the frame/send codec of _REMOTE_PROGRAM above; it is kept SEPARATE (not refactored into a
# shared fragment) so the hardware-validated one-shot switch program is left byte-identical.
_LOOP_PROGRAM = r'''
import sys, subprocess, tempfile, os
DEV = {dev!r}
DEVICE, CARRIER, REPS, GAP = {device}, {carrier}, {reps}, {gap}
MARK, Z, O, TR = {mark}, {zero}, {one}, {trailer}
HEADER = {header!r}
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
for line in sys.stdin:
    line = line.strip()
    if not line:
        continue
    try:
        c = int(line)
    except ValueError:
        continue
    try:
        send(c)
    except Exception:
        pass
'''


def build_loop_program(config) -> str:
    """The persistent read-loop blaster program (fed to ``python3 -c``): one RCA frame per stdin line."""
    return _LOOP_PROGRAM.format(
        dev=str(getattr(config, "tv_blaster_lirc_device", "/dev/lirc0") or "/dev/lirc0"),
        device=int(getattr(config, "tv_rca_device", 15)),
        carrier=int(getattr(config, "tv_ir_carrier", RCA_CARRIER)),
        reps=int(getattr(config, "tv_ir_reps", 3)),
        gap=_GAP, mark=_MARK, zero=_ZERO_SPACE, one=_ONE_SPACE, trailer=_TRAILER,
        header=tuple(_HEADER),
    )


def pipe_ssh_args(config):
    """The ``ssh`` argv that runs the persistent IR read-loop on the blaster (stdin carries RCA command
    lines), or ``None`` if no host is configured. Uses ``python3 -c <program>`` -- ``shlex.quote`` makes
    the multi-line program one shell-safe arg for the remote POSIX shell -- so stdin stays free for
    commands. Adds keep-alives so a dead Pi/link tears the pipe down rather than hanging open."""
    base = _ssh_base(config)
    if base is None:
        return None
    opts, target = base
    opts = opts + ["-o", "ServerAliveInterval=10", "-o", "ServerAliveCountMax=3"]
    program = build_loop_program(config)
    return ["ssh"] + opts + [target, "python3 -c " + shlex.quote(program)]


def _default_popen(args):
    return subprocess.Popen(args, stdin=subprocess.PIPE,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


class PersistentBlaster:
    """A long-lived IR sender for rapid keys (volume passthrough): one SSH connection running a python
    read-loop on the blaster that fires an RCA frame per stdin line. Opened lazily on the first ``send``,
    reused until ``close()``.

    NOT internally locked: all I/O is expected on ONE thread (the passthrough worker). Every method is
    non-fatal by contract -- a broken pipe / dead ssh logs, tears the connection down, and returns
    ``False``; the next ``send`` transparently reopens."""

    def __init__(self, config, popen=None):
        self.config = config
        self._popen = popen or _default_popen
        self._proc = None

    def _alive(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    def _ensure(self) -> bool:
        if self._alive():
            return True
        self._teardown()  # reap a dead process before reopening
        args = pipe_ssh_args(self.config)
        if args is None:
            return False  # no blaster host configured -- volume is a logged no-op
        self._proc = self._popen(args)
        return self._proc is not None

    def send(self, command) -> bool:
        """Fire one RCA command at the TV. Returns True if the byte was written, False on any failure."""
        try:
            command = int(command)
        except (TypeError, ValueError):
            return False
        try:
            if not self._ensure():
                return False
            self._proc.stdin.write(("%d\n" % command).encode())
            self._proc.stdin.flush()
            return True
        except Exception as exc:  # noqa: BLE001 -- broken pipe / ssh died / anything: stay non-fatal
            log("ir_remote: persistent blaster send {} failed (non-fatal): {!r}".format(command, exc))
            self._teardown()
            return False

    def _teardown(self) -> None:
        proc, self._proc = self._proc, None
        if proc is None:
            return
        try:
            if proc.stdin is not None:
                try:
                    proc.stdin.close()  # EOF -> the remote read-loop exits cleanly
                except Exception:  # noqa: BLE001
                    pass
            try:
                proc.wait(timeout=3)
            except Exception:  # noqa: BLE001 -- didn't exit in time: force it
                try:
                    proc.terminate()
                except Exception:  # noqa: BLE001
                    pass
        except Exception as exc:  # noqa: BLE001
            log("ir_remote: persistent blaster teardown error (non-fatal): {!r}".format(exc))

    def close(self) -> None:
        self._teardown()


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

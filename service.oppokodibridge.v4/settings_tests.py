#!/usr/bin/env python3
"""In-Kodi settings test actions, invoked by the add-on's settings buttons via RunScript:

    RunScript(.../settings_tests.py, ping)     -- is the OPPO reachable on the network?
    RunScript(.../settings_tests.py, control)  -- two-way OPPO control (query power, #QPW)
    RunScript(.../settings_tests.py, cec)      -- guided CEC switch-over test (run after a good ping)

Runs inside Kodi (uses xbmcgui dialogs + the add-on settings). The pure logic lives in cec.py /
oppo_http.py (unit-tested off-box); this is just the interactive wrapper.
"""
import os
import socket
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

from resources.lib import cec  # noqa: E402
from resources.lib import config as config_mod  # noqa: E402
from resources.lib.oppo_http import OppoClient, OppoError  # noqa: E402


def _dialog():
    import xbmcgui

    return xbmcgui.Dialog()


def _tcp_open(host, port, timeout=4.0):
    try:
        socket.create_connection((host, int(port)), timeout=timeout).close()
        return True
    except OSError:
        return False


def cmd_ping(cfg, dlg):
    http = _tcp_open(cfg.oppo_ip, cfg.oppo_http_port)
    if getattr(cfg, "serial_control", False):
        # Serial control drives the OPPO over RS-232, not the network :23 port -- report the cable,
        # not a misleading ":23 UNREACHABLE".
        port = getattr(cfg, "serial_port", "/dev/ttyUSB0")
        control = "Serial control {}  ->  {}".format(port, "present" if os.path.exists(port) else "MISSING")
    else:
        control = "Control port :23  ->  {}".format("OK" if _tcp_open(cfg.oppo_ip, 23) else "UNREACHABLE")
    dlg.ok(
        "OPPO ping",
        "{}\nHTTP API :{}  ->  {}".format(control, cfg.oppo_http_port, "OK" if http else "UNREACHABLE"),
    )


def cmd_control(cfg, dlg):
    client = OppoClient(cfg)
    try:
        reply = client.send_control_command("#QPW")
    except OppoError as exc:
        dlg.ok("OPPO control test", "Control failed: {}".format(exc))
        return
    up = (reply or "").upper()
    state = "ON" if "ON" in up else "OFF" if "OFF" in up else "no reply"
    dlg.ok(
        "OPPO control test",
        "Sent #QPW (query power).\nOPPO replied: {}\nPower state: {}".format(
            (reply or "").strip() or "(nothing)", state
        ),
    )


def cmd_cec(cfg, dlg):
    # The grab uses the configured control transport. Only gate on the network :23 port when NOT in
    # serial mode -- a serial-control user's :23 is irrelevant (and usually closed), and gating on it
    # would permanently block this test for them even though the serial grab works.
    if not getattr(cfg, "serial_control", False) and not _tcp_open(cfg.oppo_ip, 23):
        dlg.ok("CEC switch-over test", "OPPO control port :23 is unreachable -- run Ping first.")
        return
    if not dlg.yesno("CEC switch-over test", "This power-cycles the OPPO so it grabs the TV.\nReady?"):
        return
    cec.grab_oppo(OppoClient(cfg))
    to_oppo = dlg.yesno(
        "CEC switch-over test",
        "The OPPO is powering on (~20-24s).\nDid the TV switch to the OPPO input?",
    )
    cec.reclaim_kodi(cfg)
    to_kodi = dlg.yesno(
        "CEC switch-over test", "Asked Kodi to take the TV back.\nDid the TV switch back to Kodi?"
    )
    dlg.ok(
        "CEC switch-over test",
        "Grab the OPPO:  {}\nReclaim Kodi:  {}".format(
            "OK" if to_oppo else "FAILED", "OK" if to_kodi else "FAILED"
        ),
    )


def main(argv):
    mode = argv[1] if len(argv) > 1 else "ping"
    cfg = config_mod.from_addon()
    dlg = _dialog()
    if mode == "ping":
        cmd_ping(cfg, dlg)
    elif mode == "control":
        cmd_control(cfg, dlg)
    elif mode == "cec":
        cmd_cec(cfg, dlg)
    else:
        dlg.ok("OppoKodiBridge CEC", "Unknown test: {}".format(mode))


if __name__ == "__main__":
    main(sys.argv)

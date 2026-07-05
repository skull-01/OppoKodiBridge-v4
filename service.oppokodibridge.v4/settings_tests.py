#!/usr/bin/env python3
"""In-Kodi settings test actions, invoked by the add-on's settings buttons via RunScript:

    RunScript(.../settings_tests.py, ping)        -- is the OPPO reachable? (wakes the :436 API first)
    RunScript(.../settings_tests.py, control)     -- two-way OPPO control (query power, #QPW)
    RunScript(.../settings_tests.py, cec)         -- guided CEC switch-over test (run after a good ping)
    RunScript(.../settings_tests.py, detectpath)  -- fill path_from from Kodi's own video sources (#10)
    RunScript(.../settings_tests.py, iso)         -- ISO playback capability check (#12)
    RunScript(.../settings_tests.py, bdmv)        -- BDMV playback capability check (#13)

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
from resources.lib.oppo_http import OppoClient, OppoError, info_is_playing, playing_flags  # noqa: E402

ADDON_ID = "service.oppokodibridge.v4"


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
    # #29: the :436 app API sleeps after boot and must be OREMOTE-woken before it answers -- a raw probe
    # of a present-but-idle OPPO reports a false UNREACHABLE. wake_and_wait sends the wake and returns as
    # soon as the port answers (bounded), so this both wakes the API and checks reachability.
    http = OppoClient(cfg).wake_and_wait(attempts=4, interval=1.0)
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
    # #20: model-gate the grab exactly like the orchestrator (cec.grab_supported). On the M9207 Plus /
    # UDP-203 the power-cycle is a no-op that WEDGES the unit (its #POF sleeps, #PON is a no-op), so the
    # test must skip it -- running it here would wedge the box just like a real handoff would.
    if not cec.grab_supported(cfg):
        dlg.ok("CEC switch-over test",
               "This model ({}) has no network CEC grab -- its power-cycle is a no-op that can wedge "
               "the unit, so the test is skipped. Switch the TV to the OPPO input manually.".format(
                   getattr(cfg, "oppo_model", "")))
        return
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


def _addon():
    import xbmcaddon

    return xbmcaddon.Addon(ADDON_ID)


def cmd_detectpath(cfg, dlg):
    # #10: fill path_from from Kodi's OWN video sources, on demand. Kodi-side only (the same localhost
    # JSON-RPC the reclaim uses) -- no OPPO contact, so it works without waking the box. Complements #9's
    # silent play-time fallback by letting the operator pre-populate and SEE the prefix up front.
    sources = cec.kodi_video_sources(cfg)
    if not sources:
        dlg.ok("Detect path from Kodi",
               "No Kodi video sources found.\nAdd your NAS as a video source in Kodi first, then retry.")
        return
    idx = dlg.select("Pick the source that holds your OPPO discs", list(sources))
    if idx is None or idx < 0:
        return
    chosen = str(sources[idx]).rstrip("/")
    _addon().setSettingString("path_from", chosen)
    dlg.ok("Detect path from Kodi", "Kodi path prefix set to:\n{}".format(chosen))


def cmd_playback(cfg, dlg, kind):
    # #12 / #13: capability check -- ask the operator to start an ISO / BDMV disc on the OPPO, then read
    # /getglobalinfo and report EVERY playback flag the OPPO raised (not just one -- the clone's per-flag
    # ISO/BDMV behaviour is unverified). Reference-aligned: /getglobalinfo is the HTTP monitor primitive.
    label = "ISO" if kind == "iso" else "BDMV"
    media = "an ISO disc image" if kind == "iso" else "a Blu-ray (BDMV) disc folder"
    client = OppoClient(cfg)
    if not client.wake_and_wait(attempts=4, interval=1.0):  # #29-style wake (the API sleeps)
        dlg.ok("{} playback check".format(label),
               "Can't reach the OPPO app API on :{}.\nRun Ping first.".format(cfg.oppo_http_port))
        return
    if not dlg.yesno("{} playback check".format(label),
                     "Start {} playing on the OPPO now (use its remote), then press Yes.".format(media)):
        return
    try:
        info = client.get_global_info()
    except OppoError as exc:
        dlg.ok("{} playback check".format(label), "Couldn't read the OPPO: {}".format(exc))
        return
    active = [name for name, on in playing_flags(info).items() if on]
    if active:
        dlg.ok("{} playback check".format(label),
               "OPPO reports playback. ✓\nActive flags: {}".format(", ".join(sorted(active))))
    elif info_is_playing(info):
        # Some firmware signals playback only via a status token (no booleans). Honour the SAME signal
        # the stop-monitor uses (info_is_playing), so this check can't disagree with the monitor.
        dlg.ok("{} playback check".format(label),
               "OPPO reports playback (via status token, no flags). ✓")
    else:
        dlg.ok("{} playback check".format(label),
               "The OPPO did NOT report playback.\nMake sure {} is actually playing, then retry.".format(media))


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
    elif mode == "detectpath":
        cmd_detectpath(cfg, dlg)
    elif mode in ("iso", "bdmv"):
        cmd_playback(cfg, dlg, mode)
    else:
        dlg.ok("OppoKodiBridge CEC", "Unknown test: {}".format(mode))


if __name__ == "__main__":
    main(sys.argv)

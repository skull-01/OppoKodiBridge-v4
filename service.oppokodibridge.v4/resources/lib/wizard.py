"""First-run setup wizard for OppoKodiBridge -- guided model / IP / CEC / playback setup.

The interactive flow (``run_wizard``) is driven through a small **UI**, **settings**, and **client**
adapter so the LOGIC is unit-testable off-box with fakes. The real Kodi adapters (``xbmcgui.Dialog`` +
the add-on settings) live in ``wizard.py`` at the add-on root, which Kodi launches via ``RunScript``.

Flow (operator-specified):
  1. select OPPO model
  2. set OPPO IP + ping (DO NOT proceed if it can't be reached)
  3. M9205 -> CEC grab+reclaim test; M9207 -> skip the grab (manual TV switch), go to playback
  5-8. play an ISO then a BDMV on the OPPO; the add-on reads the OPPO's reported playback state and
       (best-effort) detects the mount path -- shown to the operator, captured only on confirm. NOTE:
       the OPPO's playback response format is unconfirmed, so detection is best-effort and degrades to
       "couldn't detect; here's what it reported" (this first run doubles as the live probe).
  9. Kodi CEC reclaim test: switch the TV to the OPPO manually, wait 10s, Kodi reclaims.
"""
from __future__ import annotations

import re

from . import cec, config as config_mod
from .monitor import interruptible_sleep

# A media path the OPPO might report for the currently-playing file. The exact response format is
# unconfirmed on hardware, so this is intentionally lenient: a /mnt/... local mount path, or an
# nfs:///smb:// URL. Allows spaces (real titles have them) but stops at the chars that bound a value in
# JSON text (" ' , } ]) or a newline -- so a path is captured whole without bleeding into the next field.
_PATH_RE = re.compile(r"""(/mnt/[^"',}\]\n]+|nfs://[^"',}\]\n]+|smb://[^"',}\]\n]+)""", re.IGNORECASE)


def _scalars(obj) -> list:
    """Every scalar value inside a dict/list/string, as strings (so each can be scanned on its own)."""
    out: list = []

    def walk(o):
        if isinstance(o, dict):
            for v in o.values():
                walk(v)
        elif isinstance(o, (list, tuple)):
            for v in o:
                walk(v)
        elif o is not None:
            out.append(str(o))

    walk(obj)
    return out


def extract_playing_path(info) -> "str | None":
    """Best-effort: find a plausible media file path in whatever the OPPO returned for playback state
    (a dict, a raw string, nested JSON, ...). Scans each scalar so a path with spaces is captured whole;
    returns the longest match, or None if nothing path-like is present."""
    candidates = [info] if isinstance(info, str) else _scalars(info)
    best = None
    for s in candidates:
        for m in _PATH_RE.findall(s):
            m = m.strip()
            if best is None or len(m) > len(best):
                best = m
    return best


def derive_mount_point(path) -> "str | None":
    """The OPPO's local NFS mount point from a detected play path, e.g.
    ``/mnt/nfs2/01Movies/Dune.iso`` -> ``/mnt/nfs2`` (or None). Lets the wizard confirm/replace the
    hardcoded ``/mnt/nfs1`` assumption (see issue #14)."""
    m = re.match(r"(/mnt/[^/\s]+)", str(path or ""))
    return m.group(1) if m else None


def _short(info, limit: int = 240) -> str:
    text = info if isinstance(info, str) else " | ".join(_scalars(info))
    text = (text or "").strip() or "(nothing)"
    return text if len(text) <= limit else text[:limit] + " ..."


def _read_playback(client) -> dict:
    """Gather what the OPPO reports about the current playback -- both /getglobalinfo and (best-effort)
    /getplayingtime -- so the path detector has the most to work with."""
    out: dict = {}
    try:
        out["globalinfo"] = client.get_global_info()
    except Exception as exc:  # noqa: BLE001 - diagnostic; never raise out of the wizard
        out["globalinfo_error"] = str(exc)
    try:
        out["playingtime"] = client.get_playing_time()
    except Exception as exc:  # noqa: BLE001
        out["playingtime_error"] = str(exc)
    return out


def _play_and_detect(ui, client, settings, kind: str, descr: str, capture_key: str) -> dict:
    """Steps 5-8: prompt the operator to play the media on the OPPO, read the OPPO's playback state,
    best-effort detect the mount path, show it, and capture it only if the operator confirms."""
    ui.ok(
        "Play {} on the OPPO".format(kind),
        "On the OPPO itself: My Network -> NFS -> play {} now.\n"
        "When it is playing, press OK.".format(descr),
    )
    info = _read_playback(client)
    path = extract_playing_path(info)
    if not path:
        ui.ok(
            "{}: no path detected".format(kind),
            "The OPPO did not report a file path we could read.\n\nIt reported:\n{}".format(_short(info)),
        )
        return {"detected": None, "raw": _short(info)}
    mount = derive_mount_point(path)
    apply_it = ui.yesno(
        "{} path detected".format(kind),
        "Detected play path:\n{}\nMount point: {}\n\nCapture this?".format(path, mount or "(unknown)"),
    )
    if apply_it:
        settings.set(capture_key, path)
    return {"detected": path, "mount": mount, "applied": bool(apply_it)}


def _summary_text(s: dict) -> str:
    def mark(v):
        return "OK" if v else ("-" if v is None else "FAILED")

    lines = [
        "Model:  {}".format(s.get("model", "?")),
        "OPPO reachable:  {}".format("OK" if s.get("ping") else "FAILED"),
    ]
    if "cec_m9205" in s:
        lines.append("CEC grab+reclaim:  {}".format(mark(s["cec_m9205"])))
    iso, bdmv = s.get("iso", {}), s.get("bdmv", {})
    lines.append("ISO path:  {}".format(iso.get("detected") or "not detected"))
    lines.append("BDMV path:  {}".format(bdmv.get("detected") or "not detected"))
    lines.append("Kodi reclaim:  {}".format(mark(s.get("reclaim"))))
    return "\n".join(lines)


def run_wizard(ui, client_factory, settings, *, sleep=interruptible_sleep,
               reclaim=None, grab=None) -> dict:
    """Run the guided first-run flow. All I/O goes through the injected adapters, so the LOGIC is
    testable off-box:
      * ``ui``: ok(title,msg) / yesno(title,msg)->bool / input(title,default)->str / select(title,opts)->int
      * ``client_factory(cfg)``: build an OppoClient for the current settings
      * ``settings``: get(key)->str / set(key,value) / config()->Config
    Returns a summary dict (also used by the tests)."""
    reclaim = reclaim or cec.reclaim_kodi
    grab = grab or cec.grab_oppo
    summary: dict = {"completed": False}

    if not ui.yesno("OppoKodiBridge setup",
                    "Run the first-run setup wizard now? (model, IP, and a few quick tests, ~5 min)\n"
                    "You can re-run it any time from Settings."):
        # Dismissed -> mark done so the first-run auto-launch doesn't nag again; the Settings button
        # still re-runs it on demand.
        settings.set("wizard_done", True)
        summary["dismissed"] = True
        return summary

    # 1. model
    idx = ui.select("Select your OPPO model",
                    ["M9205 (genuine OPPO / grab-capable)", "M9207 Plus / UDP-203 (clone)"])
    if idx < 0:
        return summary
    model = "M9205" if idx == 0 else "M9207"
    settings.set("oppo_model", model)
    summary["model"] = model

    # 2. IP + ping -- do NOT proceed if it can't be reached
    default_ip = (settings.get("oppo_ip") or "").strip() or config_mod.default_ip_for_model(model)
    ip = (ui.input("OPPO IP address", default_ip) or "").strip()
    if not ip:
        return summary
    settings.set("oppo_ip", ip)
    cfg = settings.config()
    client = client_factory(cfg)
    if not client.wake_and_wait():
        ui.ok("Cannot reach the OPPO",
              "No response from {} on port {}.\nCheck the IP/network, then re-run the wizard.".format(
                  ip, cfg.oppo_http_port))
        summary["ping"] = False
        return summary
    summary["ping"] = True

    # 3 / 4. model-specific quick test
    if model == "M9205":
        if ui.yesno("CEC test (M9205)",
                    "We'll power-cycle the OPPO so it grabs the TV, then ask Kodi to reclaim it.\n"
                    "Watch your TV. Ready?"):
            grab(client)
            to_oppo = ui.yesno("CEC test", "OPPO powering on (~20-24s).\nDid the TV switch to the OPPO input?")
            reclaim(cfg)
            to_kodi = ui.yesno("CEC test", "Asked Kodi to take the TV back.\nDid the TV switch back to Kodi?")
            summary["cec_m9205"] = bool(to_oppo and to_kodi)
    else:
        ui.ok("M9207 Plus / UDP-203",
              "This clone can't grab the TV over the network -- you'll switch the TV input manually.\n"
              "Next we'll check ISO and BDMV playback.")

    # 5-6 ISO, 7-8 BDMV
    summary["iso"] = _play_and_detect(ui, client, settings, "ISO", "an .iso disc image", "detected_iso_path")
    summary["bdmv"] = _play_and_detect(ui, client, settings, "BDMV", "a Blu-ray (BDMV) disc folder", "detected_bdmv_path")

    # 9. Kodi reclaim test
    ui.ok("Kodi reclaim test",
          "Switch your TV to the OPPO input now (manually).\n"
          "Press OK, wait ~10 seconds, then Kodi will reclaim the TV.")
    sleep(10.0, lambda: False)
    reclaim(cfg)
    summary["reclaim"] = bool(ui.yesno("Kodi reclaim test",
                                       "Kodi asked for the TV back.\nDid the TV switch to Kodi?"))

    settings.set("wizard_done", True)
    summary["completed"] = True
    ui.ok("Setup complete", _summary_text(summary))
    return summary

#!/usr/bin/env python3
"""One-time provisioner: prepare a Raspberry Pi 4 for the add-on's LIRC IR TV-switch transport.

Detects the OS, installs ``v4l-utils`` if missing, and **idempotently** adds the kernel IR overlay to
the correct ``config.txt`` -- then verifies ``/dev/lirc0``. Dev/ops tool (never run by the add-on).
See issue #27 / ``docs/IR_TVSWITCH_DESIGN.md``.

Locked behaviour:
  * DRY-RUN by default -- prints the plan, changes nothing. ``--apply`` is required to write.
  * ``--with-receiver`` also adds the RX overlay (``gpio-ir``); TX (``pwm-ir-tx``) is always added.
  * Backs up ``config.txt`` before editing; idempotent (never a duplicate overlay line).
  * NEVER auto-reboots -- reports when a reboot is needed (opt-in ``--reboot``).
  * ``--verify-only`` reports device state and exits.

Usage:
  python3 tools/setup_rpi4_lirc.py                 # dry-run (default)
  sudo python3 tools/setup_rpi4_lirc.py --apply    # install + patch config.txt + verify
  sudo python3 tools/setup_rpi4_lirc.py --apply --with-receiver
  python3 tools/setup_rpi4_lirc.py --verify-only
"""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys

# config.txt candidates, most-specific first: Bookworm, older RPi OS (Bullseye/Buster), LibreELEC.
CONFIG_CANDIDATES = ("/boot/firmware/config.txt", "/boot/config.txt", "/flash/config.txt")
TX_OVERLAY = "dtoverlay=pwm-ir-tx,gpio_pin=18"
RX_OVERLAY = "dtoverlay=gpio-ir,gpio_pin=17"


def detect_config_path(exists=os.path.exists):
    """The ``config.txt`` this host uses (first existing candidate), or ``None`` if none is found."""
    for path in CONFIG_CANDIDATES:
        if exists(path):
            return path
    return None


def wanted_overlays(with_receiver=False):
    return [TX_OVERLAY] + ([RX_OVERLAY] if with_receiver else [])


def missing_overlays(config_text, with_receiver=False):
    """Overlay lines not already present -- idempotent (matches the exact ``dtoverlay=`` line)."""
    have = {ln.strip() for ln in (config_text or "").splitlines()}
    return [ov for ov in wanted_overlays(with_receiver) if ov not in have]


def _read(path):
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return fh.read()
    except FileNotFoundError:
        return ""


def build_plan(with_receiver=False, exists=os.path.exists, read=_read, ir_ctl_present=None):
    """Describe what ``--apply`` would do -- pure, no side effects."""
    path = detect_config_path(exists)
    has_ctl = bool(shutil.which("ir-ctl")) if ir_ctl_present is None else ir_ctl_present
    is_libreelec = path == "/flash/config.txt"
    text = read(path) if path else ""
    return {
        "config_path": path,
        "ir_ctl_present": has_ctl,
        "libreelec": is_libreelec,
        # LibreELEC bundles ir-ctl (no apt); otherwise install v4l-utils when ir-ctl is absent.
        "install_v4l_utils": (not has_ctl) and (not is_libreelec),
        "overlays_to_add": missing_overlays(text, with_receiver) if path else wanted_overlays(with_receiver),
    }


def apply_overlays(path, with_receiver=False, read=_read, write=None, backup=None):
    """Idempotently append the missing overlay lines to ``config.txt`` (after a backup). Returns the
    list added (empty if everything was already present)."""
    text = read(path)
    to_add = missing_overlays(text, with_receiver)
    if not to_add:
        return []
    if backup is not None:
        backup(path, text)
    new_text = text if (not text or text.endswith("\n")) else text + "\n"
    new_text += "\n".join(to_add) + "\n"
    (write or _write)(path, new_text)
    return to_add


def _write(path, text):
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)


def _backup(path, text):
    with open(path + ".okb-bak", "w", encoding="utf-8") as fh:
        fh.write(text)


def _run(args):
    return subprocess.run(args, capture_output=True, text=True).returncode


def verify(run=None):
    """Report the LIRC TX/RX devices via the bench tool's discovery (reused). ``[]`` if none / no Pi."""
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    try:
        from lirc import devices  # reuses tools/lirc/devices.py
    except Exception:
        return []
    try:
        return devices.discover(run_fn=run) if run else devices.discover()
    except Exception:
        return []


def main(argv=None):
    ap = argparse.ArgumentParser(description="Prepare a Raspberry Pi 4 for the LIRC IR transport.")
    ap.add_argument("--apply", action="store_true", help="actually install + patch config.txt (default: dry-run)")
    ap.add_argument("--with-receiver", action="store_true", help="also add the gpio-ir RX overlay (for learning)")
    ap.add_argument("--verify-only", action="store_true", help="report device state and exit")
    ap.add_argument("--reboot", action="store_true", help="reboot after applying if an overlay was added")
    args = ap.parse_args(argv)

    if args.verify_only:
        devs = verify()
        print("LIRC devices: " + (", ".join("{} [{}]".format(d.path, d.role) for d in devs) or "(none)"))
        return 0

    plan = build_plan(with_receiver=args.with_receiver)
    print("Detected config.txt : {}".format(plan["config_path"] or "(none found -- is this a Pi?)"))
    print("ir-ctl present      : {}".format("yes" if plan["ir_ctl_present"] else "no"))
    print("install v4l-utils   : {}".format("yes" if plan["install_v4l_utils"] else "no"))
    print("overlays to add     : {}".format(", ".join(plan["overlays_to_add"]) or "(none -- already set)"))

    if not args.apply:
        print("\nDRY-RUN -- nothing changed. Re-run with sudo and --apply to do it.")
        return 0

    if plan["config_path"] is None:
        print("No config.txt found; cannot apply. Aborting.")
        return 2
    if plan["install_v4l_utils"]:
        print("Installing v4l-utils ...")
        if _run(["apt-get", "install", "-y", "v4l-utils"]) != 0:
            print("WARNING: v4l-utils install failed -- install it manually.")
    added = apply_overlays(plan["config_path"], with_receiver=args.with_receiver, write=_write, backup=_backup)
    if added:
        print("Added to {}: {}  (backup: {}.okb-bak)".format(plan["config_path"], ", ".join(added), plan["config_path"]))
        print("A REBOOT is required for the overlay to take effect.")
        if args.reboot:
            print("Rebooting ...")
            _run(["reboot"])
    else:
        print("config.txt already had the overlay(s); nothing to add.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

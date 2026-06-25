#!/usr/bin/env python3
"""Capture / replay Broadlink RM4 IR codes for the TV input switch.

Runs on a PC or the Ugoos (no Kodi). Reuses the SAME ``broadlink_rm4`` client the add-on uses, so a
code captured here is guaranteed send-compatible. Not shipped inside the runtime add-on zip.

  python tools/learn_ir.py <rm4-ip> probe                       # discovered devtype + MAC (sanity)
  python tools/learn_ir.py <rm4-ip> learn   [name]              # capture one button -> base64 + hex
  python tools/learn_ir.py <rm4-ip> send    <b64-or-hex> [rep]  # verify one code fires the TV
  python tools/learn_ir.py <rm4-ip> sequence "<b64>,<b64>,..." [gap_ms]   # dial in a nav sequence

Capture strategy for the TCL Q9L Pro (no discrete-HDMI button on the bundled remote):
  1. ``learn`` Source, Up, Down, OK from the TV's own remote (aim each at the RM4).
  2. On the TV's Source list, note the row order and where HDMI 1 (OPPO) / HDMI 4 (Kodi) sit.
  3. Build an END-STOP-ANCHORED sequence per target -- Source -> run one direction to the list end ->
     step back N -> OK -- so it self-corrects from any cursor start. ``sequence`` lets you verify it.
  4. Paste the comma-joined base64 into the add-on's ir_code_oppo / ir_code_kodi settings.
Prefer a single DISCRETE 'HDMI N' code if your TV honours one (idempotent, safe to double-send).
"""
import base64
import os
import sys
import time

_ADDON = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "service.oppokodibridge.v4"
)
if _ADDON not in sys.path:
    sys.path.insert(0, _ADDON)

from resources.lib import broadlink_rm4 as bl  # noqa: E402


def _connect(ip):
    return bl.RM4(ip, timeout=6.0).discover().auth()


def cmd_probe(ip):
    dev = _connect(ip)
    print("RM4 devtype=0x%04x  mac=%s" % (dev.devtype, dev.mac[::-1].hex(":")))
    return 0


def cmd_learn(ip, name):
    dev = _connect(ip)
    dev.enter_learning()
    print("Point the TV remote at the RM4 and press '%s' now (~30s)..." % name)
    for _ in range(30):
        time.sleep(1)
        blob = dev.check_learned()
        if blob:
            if blob[0] != 0x26:
                print(
                    "WARNING: captured a non-IR packet (lead byte 0x%02x). This button is probably "
                    "Bluetooth/RF -- the RM4 can only blast IR. Use an IR remote or a discrete code."
                    % blob[0]
                )
            print("%s base64: %s" % (name, base64.b64encode(blob).decode()))
            print("%s hex:    %s" % (name, blob.hex()))
            print("Paste the base64 into the setting (comma-join multiple keys for a nav sequence).")
            return 0
    print("No code captured (button may be Bluetooth/RF, or the remote was not aimed at the RM4).")
    return 1


def cmd_send(ip, code, repeat):
    dev = _connect(ip)
    dev.send_ir(bl.decode_ir_code(code), repeat=repeat)
    print("sent.")
    return 0


def cmd_sequence(ip, seq, gap_ms):
    dev = _connect(ip)
    toks = [t.strip() for t in seq.replace("\n", ",").split(",") if t.strip()]
    for i, t in enumerate(toks):
        dev.send_ir(bl.decode_ir_code(t))  # repeat=0: never double a sequenced key
        print("  sent key %d/%d" % (i + 1, len(toks)))
        if i < len(toks) - 1:
            time.sleep(gap_ms / 1000.0)
    return 0


def main(argv):
    if len(argv) < 3:
        print(__doc__)
        return 2
    ip, mode = argv[1], argv[2]
    if mode == "probe":
        return cmd_probe(ip)
    if mode == "learn":
        return cmd_learn(ip, argv[3] if len(argv) > 3 else "button")
    if mode == "send":
        return cmd_send(ip, argv[3], int(argv[4]) if len(argv) > 4 else 1)
    if mode == "sequence":
        return cmd_sequence(ip, argv[3], int(argv[4]) if len(argv) > 4 else 200)
    print(__doc__)
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))

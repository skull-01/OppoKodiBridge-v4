#!/usr/bin/env python3
"""Probe an OPPO/M9205 over its HTTP API to discover what it has mounted and at what path.

Run on any machine that can reach the OPPO (your PC, or the Ugoos):

    python tools/probe_oppo.py <oppo-ip> [start-path]

It performs the same activate -> wake -> signin handshake the add-on uses, then dumps:
  * /getglobalinfo   - is the API alive
  * /getdevicelist   - USB + network shares the OPPO sees, with their paths
  * /getfilelist     - browse the OPPO's filesystem (defaults to "/", or the path you pass)

Find your NFS share in the output; the folder path the OPPO lists for it is exactly what
goes in the add-on's "OPPO path prefix" setting. Drill down by passing a start path, e.g.

    python tools/probe_oppo.py 192.168.10.5 /mnt
"""
from __future__ import annotations

import json
import os
import sys
import urllib.parse

sys.path.insert(
    0,
    os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "service.oppokodibridge.v4"
    ),
)
from resources.lib.config import Config  # noqa: E402
from resources.lib.oppo_http import OppoClient, OppoError  # noqa: E402


def _dump(label: str, value: object) -> None:
    print("\n=== {} ===".format(label))
    try:
        print(json.dumps(value, indent=2, ensure_ascii=False))
    except (TypeError, ValueError):
        print(repr(value))


def main(argv: list) -> int:
    if len(argv) < 2:
        print("usage: python tools/probe_oppo.py <oppo-ip> [start-path]")
        return 2
    ip = argv[1]
    start_path = argv[2] if len(argv) > 2 else "/"
    client = OppoClient(Config(oppo_ip=ip, socket_timeout=6.0))

    print("Probing OPPO at {} ...".format(ip))
    try:
        client.activate()
    except OSError as exc:
        print("activate (UDP) failed (non-fatal): {!r}".format(exc))
    for label, fn in (("wake", client.wake), ("signin", client.signin)):
        try:
            fn()
        except OppoError as exc:
            print("{} failed (continuing): {}".format(label, exc))

    try:
        _dump("getglobalinfo", client.get_global_info())
    except OppoError as exc:
        print("getglobalinfo failed: {}".format(exc))

    try:
        _dump("getdevicelist", client._get_json("/getdevicelist"))
    except OppoError as exc:
        print("getdevicelist failed: {}".format(exc))

    payload = json.dumps({"path": start_path, "fileType": 1, "mediaType": 3, "flag": 1})
    query = "payload=" + urllib.parse.quote(payload, safe="")
    try:
        body = client._get("/getfilelist", query=query, timeout=10)
        try:
            _dump("getfilelist {}".format(start_path), json.loads(body))
        except ValueError:
            _dump("getfilelist {} (raw)".format(start_path), body)
    except OppoError as exc:
        print("getfilelist failed: {}".format(exc))

    print("\nLook for your NFS share in getdevicelist / getfilelist above.")
    print("The folder path the OPPO lists is your add-on 'OPPO path prefix'.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))

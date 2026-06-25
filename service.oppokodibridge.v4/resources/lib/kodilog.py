"""Tiny logging shim.

Logs through ``xbmc.log`` when running inside Kodi, and falls back to ``print`` so the
pure-logic modules stay importable (and testable) off-box.
"""
from __future__ import annotations

ADDON_ID = "service.oppokodibridge.v4"


def log(message: str, level: object = None) -> None:
    try:
        import xbmc

        xbmc.log(
            "[{}] {}".format(ADDON_ID, message),
            level if level is not None else xbmc.LOGINFO,
        )
    except Exception:
        print("[{}] {}".format(ADDON_ID, message))

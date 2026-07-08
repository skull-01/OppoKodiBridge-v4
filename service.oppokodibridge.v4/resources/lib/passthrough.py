"""Map a Kodi remote action to an OPPO OREMOTE key -- the pure core of the remote-passthrough feature.

While a handed-off disc plays on the OPPO, an in-Kodi passthrough dialog forwards each keypress to the
OPPO via ``OppoClient.send_remote_key``. This module holds only the mapping (no Kodi imports) so it is
unit-testable off-box.

Resolution has two layers, checked in order (overrides -> button code -> action id):

* by Kodi **action id** -- reliable for the standard navigation keys, which never collide
  (Up/Down/Left/Right/Select).
* by Kodi **button code** -- for buttons the operator's RF remote sends as *generic* keys that Kodi
  resolves to a COLLIDING action. This remote's Play/Pause button sends BackSpace and its Back button
  sends browser-back; Kodi turns BOTH into ACTION_NAV_BACK, so an action-id map cannot tell them apart.
  The button code identifies the physical key, so those are matched by button code.

Kodi's keyboard button code is ``0xF000 | XBMCKey`` (an SDL-style keysym enum) -- NOT the Windows
virtual-key code. The VK codes captured from the operator's remote with the Windows tool identify the
PHYSICAL key; XBMCKey equals the VK across the ASCII range and the multimedia/browser block 0xA6-0xBB
(so browser-back/home, volume, mute and BackSpace line up), but differs elsewhere (Delete = 0x7F not
the VK 0x2E; Menu = 0x13F not the VK 0x5D) -- those are translated below. These are best-effort
predictions for an evdev-keyboard remote; a CEC/lirc remote uses a different code space. The first
on-device run logs any miss (``passthrough: UNMAPPED ... button=<n>``); a ``passthrough_key_overrides``
JSON entry corrects it with no code change.
"""
from __future__ import annotations

import json
from typing import Dict, Optional

VKEY_BASE = 0xF000  # Kodi keyboard button code = 0xF000 | Windows virtual-key code

# (label, kodi_action_id_or_None, code_key, oppo_code). Arrows/enter -> matched by ACTION ID (no
# collision, reliable); the repurposed keys (action_id=None) -> matched by BUTTON CODE = 0xF000|code_key
# where code_key is the XBMCKey (see docstring: usually == the Windows VK, but Delete/Menu differ).
_KEYS = (
    ("Up",         3,    0x26,  "NUP"),
    ("Down",       4,    0x28,  "NDN"),
    ("Left",       1,    0x25,  "NLT"),
    ("Right",      2,    0x27,  "NRT"),
    ("Enter/OK",   7,    0x0D,  "SEL"),
    ("Play/Pause", None, 0x08,  "PAU"),  # BackSpace -- OPPO PAU toggles play<->pause itself
    ("Stop",       None, 0x7F,  "STP"),  # Delete = XBMCK_DELETE 0x7F (NOT the VK 0x2E)
    ("Back",       None, 0xA6,  "RET"),  # browser-back (XBMCK == VK here)
    ("Subtitle",   None, 0x13F, "SUB"),  # Apps/menu = XBMCK_MENU 0x13F (NOT the VK 0x5D)
    ("Audio",      None, 0xAC,  "AUD"),  # browser-home
    ("Volume+",    None, 0xAF,  "VUP"),
    ("Volume-",    None, 0xAE,  "VDN"),
    ("Info",       None, 0xAD,  "OSD"),  # mute key -> Info/OSD overlay (operator's choice)
)

# Arrows/enter resolve by action id only; the repurposed keys by button code only -- so a stray key
# that happens to share an arrow's raw XBMCKey can't be mistaken for a nav press.
CODE_BY_ACTION: Dict[int, str] = {a: c for (_l, a, _k, c) in _KEYS if a is not None}
CODE_BY_BUTTONCODE: Dict[int, str] = {VKEY_BASE | k: c for (_l, a, k, c) in _KEYS if a is None}
LABEL_BY_CODE: Dict[str, str] = {c: label for (label, _a, _k, c) in _KEYS}


def parse_overrides(raw: object) -> Dict[int, str]:
    """A ``{button_code: OPPO_code}`` override map from a JSON settings string. Best-effort: a blank
    or malformed value yields ``{}`` (the built-in map is used). Keys may be int or str in the JSON."""
    if not raw:
        return {}
    try:
        data = json.loads(raw) if isinstance(raw, str) else raw
    except (ValueError, TypeError):
        return {}
    if not isinstance(data, dict):
        return {}
    out: Dict[int, str] = {}
    for key, value in data.items():
        # an override value must be a non-empty code string -- skip null/number/empty so a stray JSON
        # value can't turn into a bogus forwarded key like "None"/"5".
        if not isinstance(value, str) or not value.strip():
            continue
        try:
            out[int(key)] = value.strip()
        except (ValueError, TypeError):
            continue
    return out


def resolve(action_id: int, button_code: int, overrides: Optional[Dict[int, str]] = None) -> Optional[str]:
    """The OPPO OREMOTE code for a Kodi action, or ``None`` if this key is not forwarded.

    Order: config ``overrides`` (by button code) -> built-in button-code map -> action-id map. Button
    code is checked before action id so a repurposed generic key beats its colliding default action."""
    if overrides and button_code in overrides:
        return overrides[button_code]
    code = CODE_BY_BUTTONCODE.get(button_code)
    if code is not None:
        return code
    return CODE_BY_ACTION.get(action_id)


ACTIVE_STATES = frozenset(("playing", "paused"))


def arm_decision(prev_armed: bool, state: str, idle_count: int, idle_needed: int):
    """Pure arm/disarm transition for the passthrough dialog, driven by the OPPO's playback state.

    Arm immediately on an active state (playing/paused); disarm only after ``idle_needed`` CONSECUTIVE
    non-active reads (idle / down / unknown), so a single transport blip or a brief unreachable poll
    can't drop passthrough out from under a running disc. Returns ``(armed, idle_count)`` -- the caller
    threads ``idle_count`` back in on the next tick."""
    if state in ACTIVE_STATES:
        return True, 0
    idle_count += 1
    if prev_armed and idle_count < max(1, idle_needed):
        return True, idle_count  # tolerate a transient non-active read while armed
    return False, idle_count

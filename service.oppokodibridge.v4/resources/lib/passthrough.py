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

Kodi's keyboard button code for a virtual key is ``0xF000 | <Windows virtual-key code>``. The VK codes
below were captured from the operator's actual remote with the Windows key-capture tool, so the button
codes are derived, not guessed -- and the first on-device run logs any miss, which a config override
(``passthrough_key_overrides``) can correct without a code change.
"""
from __future__ import annotations

import json
from typing import Dict, Optional

VKEY_BASE = 0xF000  # Kodi keyboard button code = 0xF000 | Windows virtual-key code

# (label, kodi_action_id_or_None, windows_vk, oppo_code). Operator's remote + chosen OPPO codes:
#   arrows/enter -> matched by action id (no collision); everything else by button code.
_KEYS = (
    ("Up",         3,    0x26, "NUP"),
    ("Down",       4,    0x28, "NDN"),
    ("Left",       1,    0x25, "NLT"),
    ("Right",      2,    0x27, "NRT"),
    ("Enter/OK",   7,    0x0D, "SEL"),
    ("Play/Pause", None, 0x08, "PAU"),  # BackSpace -- OPPO PAU toggles play<->pause itself
    ("Stop",       None, 0x2E, "STP"),  # Delete
    ("Back",       None, 0xA6, "RET"),  # browser-back
    ("Subtitle",   None, 0x5D, "SUB"),  # Apps / menu key
    ("Audio",      None, 0xAC, "AUD"),  # browser-home
    ("Volume+",    None, 0xAF, "VUP"),
    ("Volume-",    None, 0xAE, "VDN"),
    ("Info",       None, 0xAD, "OSD"),  # mute key -> Info/OSD overlay (operator's choice)
)

CODE_BY_ACTION: Dict[int, str] = {a: c for (_l, a, _vk, c) in _KEYS if a is not None}
CODE_BY_BUTTONCODE: Dict[int, str] = {VKEY_BASE | vk: c for (_l, _a, vk, c) in _KEYS}
LABEL_BY_CODE: Dict[str, str] = {c: label for (label, _a, _vk, c) in _KEYS}


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
        try:
            out[int(key)] = str(value)
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

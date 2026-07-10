"""Map a Kodi remote action to an OPPO OREMOTE key -- the pure core of the remote-passthrough feature.

While a handed-off disc plays on the OPPO, an in-Kodi passthrough dialog forwards each keypress to the
OPPO via ``OppoClient.send_remote_key``. This module holds only the mapping (no Kodi imports) so it is
unit-testable off-box.

Resolution has two layers, checked in order (overrides -> button code -> action id):

* by Kodi **action id** -- reliable for the standard navigation keys, which never collide
  (Up/Down/Left/Right/Select).
* by Kodi **button code** -- only for buttons that COLLIDE on an action id. This remote's Play/Pause and
  Back buttons BOTH raise ACTION_NAV_BACK (button codes 61448 vs 61616), and Stop raises ACTION_NONE --
  an action-id map cannot tell those apart, so they are matched by button code (checked first).

The maps below were CAPTURED from the operator's remote via the live add-on log (2026-07-09), not
predicted. This remote double-fires -- one press emits a keyboard ``0xF0xx`` code AND a big
``0x0101xxxx`` code -- but Kodi normalises both to one action id, which is why action id is the robust
key. The on-device UNMAPPED log line + a ``passthrough_key_overrides`` JSON entry (button-code keyed)
fix any remaining miss with no code change.
"""
from __future__ import annotations

import json
from typing import Dict, Optional

# Maps CAPTURED from the operator's remote via the live add-on log (2026-07-09), not predicted.
#
# Kodi ACTION id -> OPPO code: the reliable path. These actions are stable and unique per button on this
# remote. A single press can emit two raw button codes (a keyboard 0xF0xx code AND a big 0x0101xxxx one),
# but Kodi normalises both to the same action id -- so action-id mapping is robust where a raw button
# code is not. (VUP/VDN/OSD/SUB/AUD were UNMAPPED on the first hardware run; fixed here by action id.)
CODE_BY_ACTION: Dict[int, str] = {
    1:   "NLT",  # ACTION_MOVE_LEFT
    2:   "NRT",  # ACTION_MOVE_RIGHT
    3:   "NUP",  # ACTION_MOVE_UP
    4:   "NDN",  # ACTION_MOVE_DOWN
    7:   "SEL",  # ACTION_SELECT_ITEM  (OK / Enter)
    88:  "VUP",  # ACTION_VOLUME_UP
    89:  "VDN",  # ACTION_VOLUME_DOWN
    91:  "OSD",  # ACTION_MUTE  -> operator uses the mute button as Info/OSD
    117: "SUB",  # ACTION_CONTEXT_MENU  -> this remote's Subtitle (Apps) key
    122: "AUD",  # this remote's Audio key (best-effort -- confirm on device)
}

# Kodi BUTTON code -> OPPO code, for buttons that COLLIDE on an action id (this remote's Play/Pause and
# Back both raise ACTION_NAV_BACK=92) or that Kodi maps to ACTION_NONE=0 (Stop). Checked BEFORE the
# action map so a collision key wins.
CODE_BY_BUTTONCODE: Dict[int, str] = {
    61448: "PAU",  # BackSpace -> Play/Pause (OPPO PAU toggles play<->pause)
    61616: "RET",  # dedicated Back button (NAV_BACK, distinct from BackSpace's 61448)
    61575: "STP",  # Stop (Delete / ACTION_NONE here) -- best-effort, confirm on device
}


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


def parse_ignore_codes(raw: object) -> "set":
    """A set of int button codes the passthrough dialog should SWALLOW (never forward). Used for a
    double-firing remote whose volume keys are handled by the TV-volume takeover (keymap -> NotifyAll)
    but still deliver a second event to this dialog that would otherwise mis-resolve to another action
    (e.g. this remote's Vol+/Vol- = button 61625/61624 leak in as the Audio action). Comma/space/
    semicolon-separated; blank -> empty set. Best-effort: non-numeric tokens are skipped."""
    out = set()
    for tok in str(raw or "").replace(";", ",").replace(" ", ",").split(","):
        tok = tok.strip()
        if not tok:
            continue
        try:
            out.add(int(tok))
        except ValueError:
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

"""Detect which Kodi playback items qualify for OPPO handoff -- the single source of truth.

ONLY disc content goes to the OPPO: disc images (``.iso``) and disc folders (BDMV / VIDEO_TS /
HVDVD_TS). Everything else (MKV, MP4, loose m2ts, ...) stays in Kodi. ``pcf.py`` builds the
playercorefactory routing rules from ``PCF_RULES`` here, and the orchestrator re-checks at play time,
so the XML routing and the runtime classifier can never drift apart.
"""
from __future__ import annotations

import urllib.parse

_DISC_SEGMENTS = ("bdmv", "video_ts", "hvdvd_ts")

# The playercorefactory <rule> patterns that route a file to the OPPO external player. (kind, pattern)
# where kind is "filetypes" or "filename". One definition shared by pcf.py's XML and is_handoff_target.
PCF_RULES = (
    ("filetypes", "iso"),
    ("filename", "(?i).*/bdmv/.*"),
    ("filename", "(?i).*/video_ts/.*"),
    ("filename", r"(?i).*\.bdmv$"),
    ("filename", r"(?i).*\.iso$"),
)


def _disc_marker_index(low_path: str) -> int:
    """Index where a disc-structure segment (BDMV/VIDEO_TS/HVDVD_TS) begins as a whole path component,
    or -1. Matches the segment at the START of the path too (a disc folder at the share root)."""
    for seg in _DISC_SEGMENTS:
        if low_path.startswith(seg + "/"):
            return 0
        idx = low_path.find("/" + seg + "/")
        if idx >= 0:
            return idx + 1  # the segment starts just after the leading slash
    return -1


def is_iso(path: str) -> bool:
    """True for a disc-image file (``.iso``)."""
    return str(path).strip().lower().endswith(".iso")


def is_disc_path(path: str) -> bool:
    """True for a Blu-ray / DVD disc-folder path (BDMV / VIDEO_TS / HVDVD_TS structure)."""
    low = str(path).replace("\\", "/").lower()
    return low.endswith((".bdmv", ".ifo")) or _disc_marker_index(low) >= 0


def disc_folder(path: str) -> str:
    """The disc folder (the dir that CONTAINS BDMV/VIDEO_TS) from a disc-structure path.

    ``…/Ant-Man (2015)/BDMV/index.bdmv`` -> ``…/Ant-Man (2015)``; a disc structure at the root
    (``BDMV/index.bdmv``) -> ``""`` (the export root itself).
    """
    text = str(path).replace("\\", "/")
    idx = _disc_marker_index(text.lower())
    if idx >= 0:
        return text[:idx].rstrip("/")
    return text


def is_handoff_target(path: str) -> bool:
    """The handoff filter. Route to the OPPO ONLY for disc content: disc images (``.iso``) and disc
    folders (BDMV / VIDEO_TS / HVDVD_TS). Everything else stays in Kodi -- this is the only kind of
    file Kodi should send to the OPPO."""
    text = str(path)
    if text.lower().startswith(("nfs://", "smb://")):
        text = urllib.parse.unquote(text)
    return is_iso(text) or is_disc_path(text)


# Backwards-compatible alias (oppo_http re-exports this for existing importers/tests).
is_oppo_target = is_handoff_target

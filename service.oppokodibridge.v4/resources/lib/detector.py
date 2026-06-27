"""Detect which Kodi playback items qualify for OPPO handoff -- the single source of truth.

ONLY disc content goes to the OPPO: disc images (``.iso``) and disc folders (BDMV / VIDEO_TS).
Everything else -- MKV, MP4, loose m2ts, and HD-DVD (HVDVD_TS), which the OPPO cannot play, so routing
it would only cause a failed handoff -- stays in Kodi. The playercorefactory routing rules
(``PCF_RULES``) are DERIVED from the very same ``_DISC_SEGMENTS`` / ``_DISC_FILE_SUFFIXES`` constants
that ``is_handoff_target`` matches on (see ``_build_pcf_rules``), so the XML routing Kodi reads at boot
and the in-process runtime classifier are one definition and cannot drift apart.
"""
from __future__ import annotations

import urllib.parse

# Disc-folder structure segments: a path containing one of these as a whole component is disc content.
_DISC_SEGMENTS = ("bdmv", "video_ts")

# Disc-structure leaf-file suffixes (besides ``.iso``, handled separately): a file whose name ends with
# one of these is disc content even outside a recognised disc folder. Kept in lockstep with PCF_RULES.
_DISC_FILE_SUFFIXES = (".bdmv",)


def _build_pcf_rules():
    """The playercorefactory ``<rule>`` set, as ``(kind, pattern)`` where kind is "filetypes" or
    "filename" -- DERIVED from the segment + suffix constants above (plus the ``.iso`` image rules), so
    this XML definition and the runtime ``is_handoff_target`` are guaranteed to match the same files."""
    rules = [("filetypes", "iso"), ("filename", r"(?i).*\.iso$")]
    rules += [("filename", "(?i).*/{}/.*".format(seg)) for seg in _DISC_SEGMENTS]
    rules += [("filename", r"(?i).*\{}$".format(suffix)) for suffix in _DISC_FILE_SUFFIXES]
    return tuple(rules)


# One definition shared by pcf.py's generated XML and is_handoff_target -- see _build_pcf_rules.
PCF_RULES = _build_pcf_rules()


def _disc_marker_index(low_path: str) -> int:
    """Index where a disc-structure segment (BDMV/VIDEO_TS) begins as a whole path component,
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
    """True for a Blu-ray / DVD disc-folder path (BDMV / VIDEO_TS structure) or a disc index file."""
    low = str(path).replace("\\", "/").lower()
    return low.endswith(_DISC_FILE_SUFFIXES) or _disc_marker_index(low) >= 0


def disc_folder(path: str) -> str:
    """The disc folder (the dir that CONTAINS BDMV/VIDEO_TS) from a disc-structure path.

    ``…/Ant-Man (2015)/BDMV/index.bdmv`` -> ``…/Ant-Man (2015)``; a disc structure at the root
    (``BDMV/index.bdmv``) -> ``""`` (the export root itself).

    For a loose disc index file with NO BDMV/VIDEO_TS folder marker (e.g. a bare ``.bdmv`` sitting
    directly in a movie folder), the disc folder is the directory that CONTAINS the index file, so the
    leaf is dropped. This keeps the handoff mounting an actual folder -- never NFS-mounting the
    ``.bdmv`` FILE's own path, which hard-crashes the OPPO.
    """
    text = str(path).replace("\\", "/")
    idx = _disc_marker_index(text.lower())
    if idx >= 0:
        return text[:idx].rstrip("/")
    trimmed = text.rstrip("/")
    return trimmed.rsplit("/", 1)[0] if "/" in trimmed else ""


def is_handoff_target(path: str) -> bool:
    """The handoff filter. Route to the OPPO ONLY for disc content: disc images (``.iso``) and disc
    folders (BDMV / VIDEO_TS). Everything else stays in Kodi -- this is the only kind of
    file Kodi should send to the OPPO."""
    text = str(path)
    if text.lower().startswith(("nfs://", "smb://")):
        text = urllib.parse.unquote(text)
    return is_iso(text) or is_disc_path(text)


# Backwards-compatible alias (oppo_http re-exports this for existing importers/tests).
is_oppo_target = is_handoff_target

"""Shared IR code library — a small JSON store used by *both* bench tools.

A code captured on the RPi LIRC console can be reopened/sent from the Windows
ZJIoT console and vice-versa, because they read/write the same schema::

    {"version": 1, "codes": [
        {"label": "TV HDMI1 (OPPO)", "kind": "nec", "value": "0x57e310ef", "note": ""},
        {"label": "TV HDMI4 (Kodi)", "kind": "raw", "value": "9000 4500 560 ...", "note": ""}
    ]}

``kind`` is one of :data:`KINDS`.  Stdlib only.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, asdict

SCHEMA_VERSION = 1

# nec  : "0x<hex>" NEC scancode (protocol nec/necx per width)
# raw  : space-separated pulse/space durations in µs
# slot : a ZJIoT stored-slot index (decimal string)
KINDS = ("nec", "raw", "slot")


class CodeError(ValueError):
    """A malformed code entry or library file."""


@dataclass
class IrCode:
    label: str
    kind: str
    value: str
    note: str = ""

    def __post_init__(self) -> None:
        if not self.label:
            raise CodeError("code needs a label")
        if self.kind not in KINDS:
            raise CodeError("unknown kind {!r} (expected one of {})".format(self.kind, KINDS))
        if not self.value:
            raise CodeError("code {!r} needs a value".format(self.label))


class CodeLibrary:
    """An ordered, label-unique collection of :class:`IrCode`."""

    def __init__(self, codes=None):
        self._codes = []
        for c in codes or []:
            self.add(c)

    def __len__(self):
        return len(self._codes)

    def __iter__(self):
        return iter(self._codes)

    @property
    def codes(self):
        return list(self._codes)

    def find(self, label):
        for c in self._codes:
            if c.label == label:
                return c
        return None

    def add(self, code, replace=False):
        """Add ``code``; raise on a duplicate label unless ``replace``."""
        if not isinstance(code, IrCode):
            raise CodeError("expected IrCode, got {!r}".format(type(code).__name__))
        existing = self.find(code.label)
        if existing is not None:
            if not replace:
                raise CodeError("duplicate label {!r}".format(code.label))
            self._codes[self._codes.index(existing)] = code
        else:
            self._codes.append(code)
        return code

    def remove(self, label):
        c = self.find(label)
        if c is None:
            raise CodeError("no code labelled {!r}".format(label))
        self._codes.remove(c)

    def to_dict(self):
        return {"version": SCHEMA_VERSION, "codes": [asdict(c) for c in self._codes]}

    @classmethod
    def from_dict(cls, obj):
        if not isinstance(obj, dict):
            raise CodeError("library must be a JSON object")
        raw = obj.get("codes", [])
        if not isinstance(raw, list):
            raise CodeError("'codes' must be a list")
        lib = cls()
        for entry in raw:
            if not isinstance(entry, dict):
                raise CodeError("each code must be an object")
            lib.add(
                IrCode(
                    label=entry.get("label", ""),
                    kind=entry.get("kind", ""),
                    value=entry.get("value", ""),
                    note=entry.get("note", ""),
                )
            )
        return lib


def load(path) -> CodeLibrary:
    """Load a code library from ``path`` (empty library if the file is absent)."""
    try:
        with open(path, "r", encoding="utf-8") as fh:
            obj = json.load(fh)
    except FileNotFoundError:
        return CodeLibrary()
    except (OSError, ValueError) as exc:
        raise CodeError("cannot read code library {!r}: {}".format(path, exc)) from exc
    return CodeLibrary.from_dict(obj)


def save(path, library) -> None:
    """Write ``library`` to ``path`` as pretty JSON."""
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(library.to_dict(), fh, indent=2, ensure_ascii=False)
        fh.write("\n")

"""Tiny shared Tkinter widgets for the IR bench tools (dev-only).

Importing this module requires Tkinter; the tools guard the import so their
headless controllers stay testable without a display.
"""

from __future__ import annotations

import time
import tkinter as tk
from tkinter import ttk
from tkinter import scrolledtext


def section(parent, title):
    """A titled group box packed to fill width."""
    frame = ttk.LabelFrame(parent, text=title)
    frame.pack(fill="x", padx=8, pady=4)
    return frame


def labeled_entry(parent, label, default="", width=16):
    """Pack a ``label: [entry]`` row into ``parent``; return the ``StringVar``."""
    row = ttk.Frame(parent)
    row.pack(fill="x", padx=6, pady=2)
    ttk.Label(row, text=label, width=14, anchor="w").pack(side="left")
    var = tk.StringVar(value=default)
    ttk.Entry(row, textvariable=var, width=width).pack(side="left", fill="x", expand=True)
    return var


class LogPane(ttk.Frame):
    """A read-only, auto-scrolling, timestamped log area."""

    def __init__(self, parent, height=12):
        super().__init__(parent)
        self._text = scrolledtext.ScrolledText(
            self, height=height, state="disabled", wrap="word"
        )
        self._text.pack(fill="both", expand=True)

    def log(self, msg):
        stamp = time.strftime("%H:%M:%S")
        self._text.configure(state="normal")
        self._text.insert("end", "[{}] {}\n".format(stamp, msg))
        self._text.see("end")
        self._text.configure(state="disabled")

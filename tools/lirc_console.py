#!/usr/bin/env python3
"""Raspberry Pi 4 LIRC IR console — bench tool (Tkinter desktop).

Auto-detect the ``/dev/lirc*`` transmit/receive devices, **send any IR command**
(NEC scancode or raw waveform), learn codes (raw or decoded), and run a
**loopback self-test** (blast on TX, confirm the RX hears it) — all in a window.

Dev-only (not shipped in the add-on). Needs ``v4l-utils`` (``ir-ctl`` /
``ir-keytable``) and the ``pwm-ir-tx`` / ``gpio-ir`` overlays.

.. note::
   Tkinter needs a display — run this on **Raspberry Pi OS Desktop** or over a
   VNC / X session. It will not run on a headless LibreELEC box.

Run:
    python3 tools/lirc_console.py
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from ir import codes
from lirc import devices, ctl
from lirc.devices import LircToolError


class LircController:
    """Headless logic for the LIRC console — unit-tested without Tkinter/a Pi.

    ``run_fn(args) -> (returncode, stdout, stderr)`` is injectable so every
    ``ir-ctl`` / ``ir-keytable`` call can be mocked.
    """

    def __init__(self, run_fn=None, log=None, glob_fn=None):
        self._run = run_fn or devices._run
        self._glob = glob_fn
        self._log = log or (lambda m: None)
        self.devices = []
        self.tx = None
        self.rx = None
        self.library = codes.CodeLibrary()

    def log(self, msg):
        self._log(msg)

    def refresh(self):
        self.devices = devices.discover(run_fn=self._run, glob_fn=self._glob or devices._default_glob)
        self.tx, self.rx = devices.classify(self.devices)
        self.log(
            "found {} device(s); tx={} rx={}".format(
                len(self.devices),
                self.tx.path if self.tx else "-",
                self.rx.path if self.rx else "-",
            )
        )
        return self.devices

    def _require_tx(self):
        if self.tx is None:
            raise LircToolError("no TX device selected (Refresh first)")

    def _require_rx(self):
        if self.rx is None:
            raise LircToolError("no RX device selected (Refresh first)")

    def send_nec(self, scancode):
        self._require_tx()
        code = ctl.send_nec(self.tx.path, scancode, run_fn=self._run)
        self.log("sent nec:{} on {}".format(code, self.tx.path))
        return code

    def send_raw(self, timings):
        self._require_tx()
        ctl.send_raw(self.tx.path, timings, run_fn=self._run)
        self.log("sent {} edge(s) on {}".format(len(timings), self.tx.path))

    def learn_raw(self, timeout=10.0):
        self._require_rx()
        timings = ctl.learn_raw(self.rx.path, run_fn=self._run, timeout=timeout)
        self.log("captured {} raw edge(s)".format(len(timings)))
        return timings

    def learn_decoded(self, protocol="nec", timeout=10.0):
        seen = ctl.learn_decoded(protocol, run_fn=self._run, timeout=timeout)
        self.log("decoded: {}".format(", ".join(seen) if seen else "(none)"))
        return seen

    @staticmethod
    def check_loopback(sent_scancode, captured):
        """True if ``sent_scancode`` appears among ``captured`` scancodes."""
        want = ctl.normalize_scancode(sent_scancode)
        for c in captured:
            try:
                if ctl.normalize_scancode(c) == want:
                    return True
            except LircToolError:
                continue
        return False

    def send_code(self, code):
        """Replay a shared-library :class:`ir.codes.IrCode` via LIRC."""
        if code.kind == "nec":
            return self.send_nec(code.value)
        if code.kind == "raw":
            return self.send_raw([int(x) for x in code.value.split()])
        raise LircToolError("unsupported code kind on LIRC: {}".format(code.kind))


# --------------------------------------------------------------------------- GUI
try:
    import time
    import threading
    import tkinter as tk
    from tkinter import ttk, messagebox, filedialog
    from ir import tkutil

    _TK_OK = True
except Exception:
    _TK_OK = False


if _TK_OK:

    class LircApp(tk.Tk):
        def __init__(self):
            super().__init__()
            self.title("LIRC IR console (Raspberry Pi 4)")
            self.geometry("620x720")
            self._log_pane = tkutil.LogPane(self, height=12)
            self.ctl = LircController(log=self._log_pane.log)
            self._last_learn = []
            self._build()
            self._log_pane.pack(fill="both", expand=True, padx=8, pady=4)
            self._refresh()

        def _build(self):
            dev = tkutil.section(self, "Devices")
            row = ttk.Frame(dev)
            row.pack(fill="x", padx=6, pady=4)
            ttk.Button(row, text="↻ Refresh", command=self._refresh).pack(side="left")
            ttk.Label(row, text="TX").pack(side="left", padx=(10, 2))
            self.tx_var = tk.StringVar()
            self.tx_box = ttk.Combobox(row, textvariable=self.tx_var, width=16, state="readonly")
            self.tx_box.pack(side="left")
            ttk.Label(row, text="RX").pack(side="left", padx=(10, 2))
            self.rx_var = tk.StringVar()
            self.rx_box = ttk.Combobox(row, textvariable=self.rx_var, width=16, state="readonly")
            self.rx_box.pack(side="left")

            send = tkutil.section(self, "Send")
            self.nec_var = tkutil.labeled_entry(send, "NEC scancode", "0x57e310ef")
            ttk.Button(send, text="Send NEC", command=self._send_nec).pack(anchor="e", padx=6)
            self.raw_var = tkutil.labeled_entry(send, "raw µs list", "9000 4500 560 560")
            ttk.Button(send, text="Send raw", command=self._send_raw).pack(anchor="e", padx=6, pady=2)

            learn = tkutil.section(self, "Learn")
            lr = ttk.Frame(learn)
            lr.pack(fill="x", padx=6, pady=2)
            ttk.Button(lr, text="Learn raw", command=self._learn_raw).pack(side="left")
            ttk.Button(lr, text="Learn decoded (nec)", command=self._learn_decoded).pack(
                side="left", padx=4
            )
            self.learn_label = tkutil.labeled_entry(learn, "save as label", "TV HDMI1 (OPPO)")
            ttk.Button(learn, text="Add capture to library", command=self._add_capture).pack(
                anchor="e", padx=6, pady=2
            )

            st = tkutil.section(self, "Loopback self-test")
            self.st_var = tkutil.labeled_entry(st, "test scancode", "0x57e310ef")
            ttk.Button(st, text="Run loopback self-test", command=self._self_test).pack(
                anchor="e", padx=6, pady=2
            )

            lib = tkutil.section(self, "Code library")
            self.lib_box = tk.Listbox(lib, height=5)
            self.lib_box.pack(fill="x", padx=6, pady=2)
            lb = ttk.Frame(lib)
            lb.pack(fill="x", padx=6, pady=2)
            ttk.Button(lb, text="Open…", command=self._lib_open).pack(side="left")
            ttk.Button(lb, text="Save…", command=self._lib_save).pack(side="left", padx=2)
            ttk.Button(lb, text="Send selected", command=self._lib_send).pack(side="left")

        # -- helpers -----------------------------------------------------------
        def _guard(self, fn):
            try:
                fn()
            except Exception as exc:
                self._log_pane.log("ERROR: {}".format(exc))
                messagebox.showerror("LIRC console", str(exc))

        def _sync_selection(self):
            """Point the controller at the currently-selected TX/RX paths."""
            by_path = {d.path: d for d in self.ctl.devices}
            self.ctl.tx = by_path.get(self.tx_var.get(), self.ctl.tx)
            self.ctl.rx = by_path.get(self.rx_var.get(), self.ctl.rx)

        def _refresh(self):
            def do():
                self.ctl.refresh()
                self.tx_box["values"] = [d.path for d in self.ctl.devices if d.can_send]
                self.rx_box["values"] = [d.path for d in self.ctl.devices if d.can_receive]
                if self.ctl.tx:
                    self.tx_var.set(self.ctl.tx.path)
                if self.ctl.rx:
                    self.rx_var.set(self.ctl.rx.path)

            self._guard(do)

        def _send_nec(self):
            self._sync_selection()
            self._guard(lambda: self.ctl.send_nec(self.nec_var.get()))

        def _send_raw(self):
            self._sync_selection()
            self._guard(lambda: self.ctl.send_raw([int(x) for x in self.raw_var.get().split()]))

        def _learn_raw(self):
            self._sync_selection()

            def worker():
                try:
                    self._last_learn = ("raw", self.ctl.learn_raw())
                except Exception as exc:
                    self.after(0, lambda: self._log_pane.log("ERROR: {}".format(exc)))

            threading.Thread(target=worker, daemon=True).start()

        def _learn_decoded(self):
            self._sync_selection()

            def worker():
                try:
                    seen = self.ctl.learn_decoded("nec")
                    self._last_learn = ("nec", seen[-1] if seen else "")
                except Exception as exc:
                    self.after(0, lambda: self._log_pane.log("ERROR: {}".format(exc)))

            threading.Thread(target=worker, daemon=True).start()

        def _add_capture(self):
            def do():
                if not self._last_learn:
                    raise ValueError("nothing captured yet — Learn first")
                kind, payload = self._last_learn
                value = payload if kind == "nec" else " ".join(str(x) for x in payload)
                if not value:
                    raise ValueError("last capture was empty")
                self.ctl.library.add(
                    codes.IrCode(label=self.learn_label.get(), kind=kind, value=value), replace=True
                )
                self._reload_lib()

            self._guard(do)

        def _self_test(self):
            self._sync_selection()

            def worker():
                want = self.st_var.get()
                result = {}

                def cap():
                    try:
                        result["seen"] = self.ctl.learn_decoded("nec", timeout=7)
                    except Exception as exc:
                        result["err"] = str(exc)

                t = threading.Thread(target=cap, daemon=True)
                t.start()
                time.sleep(0.5)
                try:
                    self.ctl.send_nec(want)
                except Exception as exc:
                    self.after(0, lambda: self._log_pane.log("ERROR: {}".format(exc)))
                t.join(timeout=8)
                ok = self.ctl.check_loopback(want, result.get("seen", []))
                self.after(0, lambda: self._log_pane.log("LOOPBACK {}".format("PASS" if ok else "FAIL")))

            threading.Thread(target=worker, daemon=True).start()

        def _reload_lib(self):
            self.lib_box.delete(0, "end")
            for c in self.ctl.library:
                self.lib_box.insert("end", "{} [{}]".format(c.label, c.kind))

        def _lib_open(self):
            path = filedialog.askopenfilename(filetypes=[("JSON", "*.json")])
            if path:
                self._guard(
                    lambda: (setattr(self.ctl, "library", codes.load(path)), self._reload_lib())
                )

        def _lib_save(self):
            path = filedialog.asksaveasfilename(
                defaultextension=".json", filetypes=[("JSON", "*.json")]
            )
            if path:
                self._guard(lambda: codes.save(path, self.ctl.library))

        def _lib_send(self):
            self._sync_selection()
            sel = self.lib_box.curselection()
            if not sel:
                return
            code = self.ctl.library.codes[sel[0]]
            self._guard(lambda: self.ctl.send_code(code))


def main():
    if not _TK_OK:
        sys.stderr.write("Tkinter is not available on this host — cannot start the GUI.\n")
        return 2
    LircApp().mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

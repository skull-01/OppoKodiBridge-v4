#!/usr/bin/env python3
"""ZJIoT + USB-TTL IR console — Windows bench tool (Tkinter desktop).

Detect the serial port, connect to a ZJIoT serial IR module, and **send any IR
command**: build/raw-hex frames, synth+send NEC, drive stored slots, and
learn/capture codes into the shared code library.

Dev-only (not shipped in the add-on). Needs pyserial:
    pip install -r tools/requirements-dev.txt
Run:
    python tools/zjiot_console.py
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from ir import proto, codes
from ir.serial_win import ZjiotSerial, SerialToolError, list_ports, DEFAULT_BAUD


def parse_us(text):
    """Parse a space/comma separated list of µs durations to ``[int, ...]``."""
    return [int(x) for x in str(text).replace(",", " ").split()]


def _slot_byte(index):
    """A validated slot index as a byte (#39). Reject out-of-range instead of silently masking with
    ``& 0xFF`` -- masking addressed the WRONG slot (256 -> 0, -1 -> 255), firing the wrong stored IR code
    with no error and a log line that looked correct."""
    try:
        idx = int(index)
    except (TypeError, ValueError) as exc:
        raise SerialToolError("slot index is not an integer: {!r}".format(index)) from exc
    if not 0 <= idx <= 0xFF:
        raise SerialToolError("slot index out of range (0-255): {!r}".format(index))
    return idx


def _fmt_reply(reply):
    if reply is None:
        return "(no reply)"
    return "afn={} data=[{}]".format(
        proto.AFN_NAMES.get(reply["afn"], hex(reply["afn"])),
        reply["data"].hex(" ") or "-",
    )


class ZjiotController:
    """Headless logic for the ZJIoT console — unit-tested without Tkinter.

    ``serial_factory(port, baud) -> serial_like`` where ``serial_like`` exposes
    ``open()``, ``close()``, ``is_open`` and ``send_frame(frame, expect_reply)``
    (``ir.serial_win.ZjiotSerial`` satisfies this; tests inject a fake).
    """

    def __init__(self, serial_factory=ZjiotSerial, log=None, addr=0):
        self._factory = serial_factory
        self._log = log or (lambda m: None)
        self.addr = addr
        self._serial = None
        self.library = codes.CodeLibrary()

    @property
    def connected(self):
        return self._serial is not None and self._serial.is_open

    def log(self, msg):
        self._log(msg)

    def ports(self):
        return list_ports()

    def connect(self, port, baud=DEFAULT_BAUD):
        self.disconnect()
        self._serial = self._factory(port, baud)
        self._serial.open()
        self.log("connected {} @ {} baud".format(port, baud))

    def disconnect(self):
        if self._serial is not None:
            self._serial.close()
            self._serial = None
            self.log("disconnected")

    def _require(self):
        if not self.connected:
            raise SerialToolError("not connected")

    def send_command(self, afn, data=b"", expect_reply=False):
        self._require()
        frame = proto.build(self.addr, afn, data)
        reply = self._serial.send_frame(frame, expect_reply=expect_reply)
        self.log(
            "TX {} data=[{}] -> {}".format(
                proto.AFN_NAMES.get(afn, hex(afn)), bytes(data).hex(" ") or "-", _fmt_reply(reply)
            )
        )
        return reply

    def send_exact(self, frame, expect_reply=False):
        """Send exact bytes verbatim (poke the module with any frame)."""
        self._require()
        frame = bytes(frame)
        reply = self._serial.send_frame(frame, expect_reply=expect_reply)
        self.log("TX raw [{}] -> {}".format(frame.hex(" "), _fmt_reply(reply)))
        return reply

    def send_slot(self, index):
        return self.send_command(proto.AFN_SEND_SLOT, bytes([_slot_byte(index)]))

    def write_slot(self, index, raw):
        return self.send_command(
            proto.AFN_WRITE_SLOT, bytes([_slot_byte(index)]) + bytes(raw), expect_reply=True
        )

    def send_raw(self, raw):
        return self.send_command(proto.AFN_SEND_RAW, bytes(raw))

    def send_nec(self, address, command, extended=False):
        raw = proto.pack_raw(proto.nec_timings(address, command, extended))
        return self.send_raw(raw)

    def learn(self):
        reply = self.send_command(proto.AFN_LEARN, b"", expect_reply=True)
        if reply is None:
            raise SerialToolError("no learn reply from module")
        self.log("learned {} raw byte(s)".format(len(reply["data"])))
        return reply["data"]

    def send_code(self, code):
        """Replay a shared-library :class:`ir.codes.IrCode` via the ZJIoT module."""
        if code.kind == "slot":
            return self.send_slot(int(code.value))
        if code.kind == "raw":
            return self.send_raw(proto.pack_raw(parse_us(code.value)))
        if code.kind == "nec":
            return self.send_raw(proto.pack_raw(proto.nec_scancode_timings(int(code.value, 16))))
        raise SerialToolError("unsupported code kind: {}".format(code.kind))


# --------------------------------------------------------------------------- GUI
try:
    import threading
    import tkinter as tk
    from tkinter import ttk, messagebox, filedialog
    from ir import tkutil

    _TK_OK = True
except Exception:  # tkinter absent (headless import) — controller still usable
    _TK_OK = False


if _TK_OK:

    class ZjiotApp(tk.Tk):
        def __init__(self):
            super().__init__()
            self.title("ZJIoT IR console")
            self.geometry("620x760")
            self._log_pane = tkutil.LogPane(self, height=12)
            # #39: the controller can log from a WORKER thread (learn()), and Tk is not thread-safe, so
            # route every controller log line onto the main loop via after(0) instead of touching the
            # widget directly. (Main-thread callers are unaffected -- after(0) just defers one tick.)
            self.ctl = ZjiotController(log=self._safe_log)
            self._last_learn = b""
            self._build()
            self._log_pane.pack(fill="both", expand=True, padx=8, pady=4)
            self._refresh_ports()

        # -- layout ------------------------------------------------------------
        def _build(self):
            conn = tkutil.section(self, "Connection")
            row = ttk.Frame(conn)
            row.pack(fill="x", padx=6, pady=4)
            ttk.Label(row, text="Port", width=6).pack(side="left")
            self.port_var = tk.StringVar()
            self.port_box = ttk.Combobox(row, textvariable=self.port_var, width=22)
            self.port_box.pack(side="left", padx=2)
            ttk.Button(row, text="↻", width=3, command=self._refresh_ports).pack(side="left")
            ttk.Label(row, text="Baud").pack(side="left", padx=(8, 2))
            self.baud_var = tk.StringVar(value=str(DEFAULT_BAUD))
            ttk.Entry(row, textvariable=self.baud_var, width=8).pack(side="left")
            ttk.Button(row, text="Connect", command=self._connect).pack(side="left", padx=4)
            ttk.Button(row, text="Disconnect", command=self._disconnect).pack(side="left")

            nec = tkutil.section(self, "Send NEC (synthesised)")
            self.nec_addr = tkutil.labeled_entry(nec, "address (hex)", "0x57e3")
            self.nec_cmd = tkutil.labeled_entry(nec, "command (hex)", "0x10")
            self.nec_ext = tk.BooleanVar(value=True)
            ttk.Checkbutton(nec, text="extended (16-bit address)", variable=self.nec_ext).pack(
                anchor="w", padx=6
            )
            ttk.Button(nec, text="Send NEC", command=self._send_nec).pack(anchor="e", padx=6, pady=2)

            raw = tkutil.section(self, "Slots & raw")
            self.slot_idx = tkutil.labeled_entry(raw, "slot index", "0")
            r1 = ttk.Frame(raw)
            r1.pack(fill="x", padx=6)
            ttk.Button(r1, text="Send slot", command=self._send_slot).pack(side="left")
            self.raw_us = tkutil.labeled_entry(raw, "raw µs list", "9000 4500 560 560")
            ttk.Button(raw, text="Send raw", command=self._send_raw).pack(anchor="e", padx=6, pady=2)

            arb = tkutil.section(self, "Arbitrary frame")
            self.hex_entry = tkutil.labeled_entry(arb, "exact hex", "68 03 00 00 22 00 22 16")
            ttk.Button(arb, text="Send exact bytes", command=self._send_exact).pack(
                anchor="e", padx=6, pady=2
            )

            learn = tkutil.section(self, "Learn / capture")
            lr = ttk.Frame(learn)
            lr.pack(fill="x", padx=6, pady=2)
            ttk.Button(lr, text="● Learn (capture)", command=self._learn).pack(side="left")
            self.learn_label = tkutil.labeled_entry(learn, "save as label", "TV HDMI1 (OPPO)")
            ttk.Button(learn, text="Add capture to library", command=self._add_capture).pack(
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
        def _safe_log(self, msg):
            # thread-safe log: marshal onto the Tk main loop (#39). Safe to call from any thread.
            self.after(0, lambda m=msg: self._log_pane.log(m))

        def _guard(self, fn):
            try:
                fn()
            except Exception as exc:  # surface every failure in the log + a dialog
                self._log_pane.log("ERROR: {}".format(exc))
                messagebox.showerror("ZJIoT console", str(exc))

        def _refresh_ports(self):
            ports = self.ctl.ports()
            self.port_box["values"] = [p for p, _ in ports]
            if ports and not self.port_var.get():
                self.port_var.set(ports[0][0])
            if not ports:
                self._log_pane.log("no serial ports (is pyserial installed / adapter plugged in?)")

        def _connect(self):
            self._guard(lambda: self.ctl.connect(self.port_var.get(), int(self.baud_var.get())))

        def _disconnect(self):
            self._guard(self.ctl.disconnect)

        def _send_nec(self):
            self._guard(
                lambda: self.ctl.send_nec(
                    int(self.nec_addr.get(), 16), int(self.nec_cmd.get(), 16), self.nec_ext.get()
                )
            )

        def _send_slot(self):
            self._guard(lambda: self.ctl.send_slot(int(self.slot_idx.get())))

        def _send_raw(self):
            self._guard(lambda: self.ctl.send_raw(proto.pack_raw(parse_us(self.raw_us.get()))))

        def _send_exact(self):
            self._guard(lambda: self.ctl.send_exact(bytes.fromhex(self.hex_entry.get())))

        def _learn(self):
            def worker():
                try:
                    data = self.ctl.learn()
                    # #39: marshal the result back to the main loop; don't touch Tk (or shared state used
                    # by the UI) from the worker thread. Controller logging is already marshalled via
                    # _safe_log, so learn()'s internal log lines are safe too.
                    self.after(0, lambda d=data: setattr(self, "_last_learn", d))
                except Exception as exc:
                    self.after(0, lambda e=exc: self._log_pane.log("ERROR: {}".format(e)))

            threading.Thread(target=worker, daemon=True).start()

        def _add_capture(self):
            def do():
                if not self._last_learn:
                    raise ValueError("nothing captured yet — Learn first")
                code = codes.IrCode(
                    label=self.learn_label.get(),
                    kind="raw",
                    value=" ".join(str(x) for x in proto.unpack_raw(self._last_learn)),
                )
                self.ctl.library.add(code, replace=True)
                self._reload_lib()

            self._guard(do)

        def _reload_lib(self):
            self.lib_box.delete(0, "end")
            for c in self.ctl.library:
                self.lib_box.insert("end", "{} [{}]".format(c.label, c.kind))

        def _lib_open(self):
            path = filedialog.askopenfilename(filetypes=[("JSON", "*.json")])
            if path:
                self._guard(lambda: (setattr(self.ctl, "library", codes.load(path)), self._reload_lib()))

        def _lib_save(self):
            path = filedialog.asksaveasfilename(defaultextension=".json", filetypes=[("JSON", "*.json")])
            if path:
                self._guard(lambda: codes.save(path, self.ctl.library))

        def _lib_send(self):
            sel = self.lib_box.curselection()
            if not sel:
                return
            code = self.ctl.library.codes[sel[0]]
            self._guard(lambda: self.ctl.send_code(code))


def main():
    if not _TK_OK:
        sys.stderr.write("Tkinter is not available on this host — cannot start the GUI.\n")
        return 2
    ZjiotApp().mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Kodi-side runtime for the remote-passthrough feature (Approach A: in-service OPPO-state poll).

While a handed-off disc plays on the OPPO, this forwards the Kodi remote's keypresses to the OPPO so
the user can drive the disc menu. It is DEFAULT-OFF (``remote_passthrough_enabled``) so the proven
handoff path is byte-for-byte unchanged when the feature is not used.

Design (no JSON-RPC / web-server dependency):
* ``PassthroughRunner.tick(config)`` is called from the background service loop. It probes the OPPO's
  playback state (a fast :436 port check, then ``playback_state()``) and, via the pure
  ``passthrough.arm_decision``, opens a capture dialog when a disc starts and closes it when the disc
  ends (with a debounce so a blip doesn't drop it, and an absolute armed-time ceiling as a safety net).
* ``PassthroughDialog`` (an ``xbmcgui.WindowDialog``) captures EVERY key via ``onAction`` and forwards
  the mapped OPPO code on a worker thread (so a slow/unreachable OPPO can never freeze the UI). It does
  NOT call the base ``onAction`` -- during a disc the remote is fully "handed over" to the OPPO; Kodi
  control returns when the disc ends (or you press Stop, which sends STP -> the OPPO goes idle -> the
  dialog closes). Unmapped keys are logged (with their button code) so the first on-device run reveals
  any code that needs a ``passthrough_key_overrides`` entry.

Kodi imports are at module scope on purpose: this module is only imported (lazily) by service_cec once
it is already running inside Kodi.
"""
from __future__ import annotations

import queue
import threading

import xbmc
import xbmcgui

from . import passthrough
from .kodilog import log
from .oppo_http import OppoClient, OppoError


class PassthroughDialog(xbmcgui.WindowDialog):
    """Full-screen (invisible on the TV, which is on the OPPO's input) key-capture surface."""

    def __init__(self, config):
        super().__init__()
        self._overrides = passthrough.parse_overrides(getattr(config, "passthrough_key_overrides", ""))
        self._client = OppoClient(config)
        self._q: "queue.Queue" = queue.Queue()
        self._stop = threading.Event()
        self._worker = threading.Thread(target=self._pump, name="okb-passthrough", daemon=True)
        self._worker.start()
        try:  # a tiny status line -- only ever visible if the TV is flipped back to Kodi's input
            self.addControl(xbmcgui.ControlLabel(
                40, 30, 1200, 40,
                "OppoKodiBridge: remote passthrough active -- keys -> OPPO",
                textColor="0xFFDDDDDD"))
        except Exception as exc:  # noqa: BLE001 -- a label failure must not stop capture
            log("passthrough: label add failed (non-fatal): {!r}".format(exc))

    def onAction(self, action) -> None:  # noqa: D401 -- Kodi callback
        button = action.getButtonCode()
        code = None
        try:
            code = passthrough.resolve(action.getId(), button, self._overrides)
        except Exception as exc:  # noqa: BLE001
            log("passthrough: resolve error (non-fatal): {!r}".format(exc))
        if code:
            self._q.put(code)
            log("passthrough: action={} button={} -> {}".format(action.getId(), button, code))
        else:
            log("passthrough: UNMAPPED action={} button={} (add to passthrough_key_overrides if wanted)"
                .format(action.getId(), button))
        # No super().onAction(): every key is swallowed and (if mapped) forwarded to the OPPO. Kodi
        # control returns when the disc ends / on Stop -> OPPO idle -> the runner closes this dialog.

    def _pump(self) -> None:
        while not self._stop.is_set():
            try:
                code = self._q.get(timeout=0.5)
            except queue.Empty:
                continue
            try:
                self._client.send_remote_key(code)
            except OppoError as exc:
                log("passthrough: send {} failed: {}".format(code, exc))
            except Exception as exc:  # noqa: BLE001 -- the worker must never die
                log("passthrough: send {} error: {!r}".format(code, exc))

    def shutdown(self) -> None:
        self._stop.set()


class PassthroughRunner:
    """Owns the dialog lifecycle; driven by ``tick(config)`` from the service loop."""

    def __init__(self) -> None:
        self._dialog = None
        self._armed = False
        self._idle = 0
        self._armed_polls = 0

    def _probe(self, config) -> str:
        """``playing`` / ``paused`` / ``idle`` / ``down`` -- a fast port gate first so an asleep/absent
        OPPO costs ~1s, not a full socket timeout, per tick."""
        client = OppoClient(config)
        try:
            if not client._port_open(int(config.oppo_http_port), 1.0):  # noqa: SLF001
                return "down"
            return client.playback_state()
        except Exception as exc:  # noqa: BLE001
            log("passthrough: probe error (non-fatal): {!r}".format(exc))
            return "down"

    def tick(self, config) -> None:
        if not (getattr(config, "remote_passthrough_enabled", False) and getattr(config, "configured", False)):
            self.shutdown()
            return
        idle_needed = max(1, int(getattr(config, "idle_confirmations", 2)))
        state = self._probe(config)
        armed, self._idle = passthrough.arm_decision(self._armed, state, self._idle, idle_needed)

        # absolute armed-time ceiling: never leave the remote "handed over" forever if stop detection
        # somehow never fires (mirrors monitor.max_watch_seconds).
        interval = max(2.0, float(getattr(config, "passthrough_poll_interval", 4.0)))
        max_polls = max(1, int(float(getattr(config, "max_watch_seconds", 21600.0)) / interval))
        if armed:
            self._armed_polls += 1
            if self._armed_polls >= max_polls:
                log("passthrough: hit the armed-time ceiling; closing so Kodi control returns.")
                self.shutdown()
                return
        else:
            self._armed_polls = 0

        if armed and self._dialog is None:
            self._open(config)
        elif not armed and self._dialog is not None:
            self._close()
        self._armed = armed

    def _open(self, config) -> None:
        try:
            self._dialog = PassthroughDialog(config)
            self._dialog.show()
            log("passthrough: ARMED (disc playing) -- forwarding remote keys to the OPPO.")
        except Exception as exc:  # noqa: BLE001
            log("passthrough: open failed (non-fatal): {!r}".format(exc))
            self._dialog = None

    def _close(self) -> None:
        dlg, self._dialog = self._dialog, None
        if dlg is None:
            return
        try:
            dlg.shutdown()
            dlg.close()
            log("passthrough: DISARMED (disc stopped) -- Kodi control returned.")
        except Exception as exc:  # noqa: BLE001
            log("passthrough: close failed (non-fatal): {!r}".format(exc))

    def shutdown(self) -> None:
        self._armed = False
        self._idle = 0
        self._armed_polls = 0
        self._close()

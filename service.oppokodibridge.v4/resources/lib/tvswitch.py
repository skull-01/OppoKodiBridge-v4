"""Pluggable TV-switch strategy -- the orchestrator's single seam for switching the TV on play/stop.

``make_switcher(config, client)`` selects by ``config.tv_switch_method``:

    none  -> NullSwitcher   (no-op; switch manually)
    cec   -> CecSwitcher    (existing: OPPO One-Touch-Play grab + script.cecreclaim reclaim)
    ir    -> ZjiotSwitcher  (ir_zjiot: serial IR module, Ugoos/CoreELEC host)
    lirc  -> LircSwitcher   (ir_lirc: GPIO IR via ir-ctl, Raspberry Pi 4 host)

Every switcher exposes ``to_oppo()`` (play-side) and ``to_kodi()`` (stop-side), each an honest bool,
non-fatal, single-shot -- the same contract the CEC path has always had. The default 'cec' reproduces
the pre-tvswitch behaviour EXACTLY (zero regression). The two IR transports share no transport code.
"""
from __future__ import annotations

from . import cec
from .kodilog import log


class NullSwitcher:
    """``tv_switch_method=none``: do nothing (the user switches the TV input manually)."""

    def to_oppo(self) -> bool:
        return False

    def to_kodi(self) -> bool:
        return False


class CecSwitcher:
    """The existing HDMI-CEC path: the OPPO's own One-Touch-Play grab + Kodi's own reclaim.

    Reproduces the orchestrator's previous inline gating exactly: the grab is skipped unless
    ``grab_tv_on_play`` AND ``cec.grab_supported`` (model gate -- the M9207 has no network grab), and
    the reclaim is skipped unless ``cec_reclaim_on_stop``."""

    def __init__(self, config, client):
        self.config = config
        self.client = client

    def to_oppo(self) -> bool:
        if not getattr(self.config, "grab_tv_on_play", True):
            return False
        if not cec.grab_supported(self.config):
            log("grab_tv_on_play is on but oppo_model={!r} has no network grab (M9207/UDP-203); "
                "leaving the TV to be switched to the OPPO manually.".format(
                    getattr(self.config, "oppo_model", "")))
            return False
        log("Grabbing the TV for the OPPO (power-cycle -> its own One-Touch-Play)")
        return cec.grab_oppo(self.client)

    def to_kodi(self) -> bool:
        if not getattr(self.config, "cec_reclaim_on_stop", True):
            return False
        return cec.reclaim_kodi(self.config)


def make_switcher(config, client=None):
    """Build the TV-switch strategy selected by ``config.tv_switch_method`` (default 'cec')."""
    method = str(getattr(config, "tv_switch_method", "cec") or "cec").strip().lower()
    if method == "none":
        return NullSwitcher()
    if method == "ir":
        from .ir_zjiot import ZjiotSwitcher  # lazy: only load a transport when it is selected
        return ZjiotSwitcher(config)
    if method == "lirc":
        from .ir_lirc import LircSwitcher
        return LircSwitcher(config)
    return CecSwitcher(config, client)  # default / 'cec'

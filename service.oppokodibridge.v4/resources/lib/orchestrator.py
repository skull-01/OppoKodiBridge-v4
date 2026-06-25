"""The clean execution flow for one playback handoff (runs in the external pcf_player process):

    detector  -> is this a disc the OPPO should play?
    cec       -> grab the TV for the OPPO (its own One-Touch-Play)
    handoff   -> tell the OPPO to play the file
    monitor   -> watch until playback stops
    cec       -> reclaim the TV for Kodi (its own active source)

Every CEC assertion is single-shot, tied to an event: the OPPO grab fires once on play, the Kodi
reclaim fires once on stop (in the ``finally``, so it runs whether playback succeeded or failed). There
is NO standing re-asserter -- a manual input change must stick.
"""
from __future__ import annotations

from . import cec, detector, handoff, monitor
from .kodilog import log
from .oppo_http import OppoClient


def run(config, kodi_file: str, should_abort=None) -> bool:
    """Hand ``kodi_file`` to the OPPO and switch the TV. Returns True if playback was observed."""
    if not detector.is_handoff_target(kodi_file):
        log("Not a disc handoff target; leaving it to Kodi: {!r}".format(kodi_file))
        return False

    if not getattr(config, "configured", False):
        # No OPPO IP -- e.g. the service never published runtime_config.json (or did so under a
        # different Kodi profile). Don't power-cycle / reclaim against an empty config.
        log("No OPPO configured (runtime config missing?); leaving it to Kodi")
        return False

    client = OppoClient(config)

    # play-side: the OPPO grabs the TV via its OWN One-Touch-Play (a single power-cycle).
    if getattr(config, "grab_tv_on_play", True):
        log("Grabbing the TV for the OPPO (power-cycle -> its own One-Touch-Play)")
        cec.grab_oppo(client)

    started = False
    try:
        if not handoff.play(config, client, kodi_file, should_abort):
            return False
        started = monitor.watch_playback(config, client, should_abort)
    finally:
        # stop-side: reclaim the TV for Kodi -- ONCE, now that the handoff has ended (success OR
        # failure). Single-shot; never a standing re-asserter.
        if getattr(config, "cec_reclaim_on_stop", True):
            cec.reclaim_kodi(config)
    return started

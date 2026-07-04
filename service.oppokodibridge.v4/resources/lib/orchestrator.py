"""The clean execution flow for one playback handoff (runs in the external pcf_player process):

    detector  -> is this a disc the OPPO should play?
    tvswitch  -> switch the TV to the OPPO (to_oppo: CEC grab / IR blast, per tv_switch_method)
    handoff   -> tell the OPPO to play the file
    monitor   -> watch until playback stops
    tvswitch  -> switch the TV back to Kodi (to_kodi)

Every TV-switch is single-shot, tied to an event: to_oppo fires once on play, to_kodi once on stop (in
the ``finally``, so it runs whether playback succeeded or failed). There is NO standing re-asserter --
a manual input change must stick.
"""
from __future__ import annotations

from . import detector, handoff, monitor, tvswitch
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

    # play-side TV switch (single-shot, non-fatal). The strategy is chosen by tv_switch_method:
    # 'cec' (default) is the model-gated OPPO One-Touch-Play grab + Kodi reclaim; 'ir'/'lirc' blast the
    # stored HDMI-input code; 'none' is manual. Default 'cec' reproduces the prior behaviour exactly.
    switcher = tvswitch.make_switcher(config, client)
    switcher.to_oppo()

    started = False
    try:
        if not handoff.play(config, client, kodi_file, should_abort):
            return False
        # ISO auto-heal (#21): if the OPPO never reports playback within the grace window, re-issue the
        # play ONCE. handoff.play is idempotent (re-wake / bounded re-mount / re-play).
        started = monitor.watch_playback(
            config, client, should_abort,
            on_stall=lambda: handoff.play(config, client, kodi_file, should_abort),
        )
    finally:
        # stop-side TV switch, ONCE, whether playback succeeded or failed. Single-shot; never a
        # standing re-asserter (a manual input change must stick).
        switcher.to_kodi()
    return started

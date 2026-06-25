"""Watch OPPO playback state -- reports what is happening, asserts nothing.

Two-phase wait:
  Phase 1 (pre-playback) -- HTTP /getglobalinfo poll until playback STARTS. Verbose mode only carries
    useful info once the OPPO is actually playing, and the NFS mount + buffer can be slow, so the
    latency-tolerant HTTP poll owns the startup window.
  Phase 2 (playing) -- a fresh verbose ``#SVM 3`` connection blocks until ``@UPL STOP``, pushed the
    instant playback ends (no poll lag). Falls back to HTTP polling if the verbose channel fails.
"""
from __future__ import annotations

import time

from .kodilog import log
from .oppo_http import OppoError


def interruptible_sleep(seconds: float, should_abort) -> None:
    """Sleep up to ``seconds``, bailing early if ``should_abort()`` goes true."""
    waited = 0.0
    step = 0.5
    while waited < seconds:
        if should_abort():
            return
        time.sleep(min(step, seconds - waited))
        waited += step


def watch_playback(config, client, should_abort=None) -> bool:
    """Block until the OPPO stops playing. Returns True if playback was observed at all."""
    if should_abort is None:
        should_abort = lambda: False

    interval = max(2.0, float(config.poll_interval))
    grace = max(int(config.idle_confirmations), 10)  # NFS mount + buffer can be slow to start
    interruptible_sleep(interval, should_abort)
    idle = 0
    while not should_abort():
        if client.is_playing():
            break
        idle += 1
        if idle >= grace:
            log("OPPO never reported playback after {} HTTP polls; giving up.".format(idle))
            return False
        interruptible_sleep(interval, should_abort)
    else:
        return False

    log("Playback started; opening verbose (#SVM 3) for instant stop detection.")
    try:
        client.verbose_watch_until_stop(should_abort)
    except OppoError as exc:
        log("verbose watch failed ({}); falling back to HTTP polling".format(exc))
        _http_watch_until_idle(config, client, should_abort)
    return True


def _http_watch_until_idle(config, client, should_abort) -> None:
    """Fallback for phase 2: poll /getglobalinfo until the OPPO is idle for N consecutive reads.

    Uses the tri-state ``playback_state`` so a transport blip ("unknown") is NOT mistaken for a stop:
    only a confirmed-idle read counts toward the idle confirmations, so a brief network/HTTP failure
    can't trigger a premature reclaim mid-playback. To guarantee this loop always returns -- so the
    orchestrator's finally still reclaims the TV and the external-player process can never hang -- it
    also gives up after a run of unreadable polls (the OPPO went away) and after an absolute
    watch-time ceiling (a stuck 'still playing' flag that never clears).
    """
    interval = max(2.0, float(config.poll_interval))
    needed = max(1, int(config.idle_confirmations))
    max_unknown = max(needed, int(config.max_read_failures))
    max_polls = max(1, int(float(config.max_watch_seconds) / interval))
    idle = unknown = polls = 0
    while not should_abort():
        state = client.playback_state()
        if state == "playing":
            idle = unknown = 0
        elif state == "idle":
            unknown = 0
            idle += 1
            if idle >= needed:
                return
        else:  # "unknown" -- could not read the OPPO; never treat as a stop
            unknown += 1
            if unknown >= max_unknown:
                log("OPPO unreadable for {} polls; ending watch so the TV is reclaimed.".format(unknown))
                return
        polls += 1
        if polls >= max_polls:
            log("HTTP watch hit the {}-poll ceiling; ending so the TV is reclaimed.".format(max_polls))
            return
        interruptible_sleep(interval, should_abort)

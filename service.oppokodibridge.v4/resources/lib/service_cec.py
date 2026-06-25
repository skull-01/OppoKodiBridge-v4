"""CEC Control Experiment service: install the playercorefactory.xml and publish the resolved config.

Unlike v2 this service does NOT intercept playback -- Kodi's playercorefactory does that. The service
only:
  1. resolves the add-on settings (the one place with Kodi APIs) and dumps them to
     ``runtime_config.json`` in the add-on's data dir, so the external player (which runs outside
     Kodi) can read them without xbmcaddon;
  2. writes ``playercorefactory.xml`` into the Kodi profile so disc content routes to the external
     player;
  3. idles, re-publishing the config if settings change, and removes the playercorefactory.xml on
     stop so disabling the add-on cleanly reverts Kodi to normal playback.
"""
from __future__ import annotations

import dataclasses
import json
import os

from . import config as config_mod
from . import pcf
from .kodilog import log

ADDON_ID = "service.oppokodibridge.v4"


def _translate(path: str) -> str:
    import xbmcvfs

    return xbmcvfs.translatePath(path)


def _addon_dir() -> str:
    import xbmcaddon

    return _translate(xbmcaddon.Addon().getAddonInfo("path"))


def _profile_dir() -> str:
    return _translate("special://profile/")


def _runtime_config_path() -> str:
    # masterprofile is always <home>/userdata, which is the path pcf_player derives -- so writer and
    # reader agree even when Kodi is running a NON-master profile (special://profile would point at the
    # active profile and the external player would never find the published config).
    data_dir = _translate("special://masterprofile/addon_data/{}/".format(ADDON_ID))
    if not os.path.isdir(data_dir):
        os.makedirs(data_dir, exist_ok=True)
    return os.path.join(data_dir, "runtime_config.json")


def _publish_config() -> None:
    try:
        cfg = config_mod.from_addon()
        with open(_runtime_config_path(), "w", encoding="utf-8") as fh:
            json.dump(dataclasses.asdict(cfg), fh)
        log("published runtime config")
    except Exception as exc:  # pragma: no cover - hardware path
        log("publish config failed: {!r}".format(exc))


def _install_pcf() -> None:
    try:
        if not config_mod.from_addon().handoff_enabled:
            pcf.uninstall(_profile_dir())
            log("handoff disabled; playercorefactory.xml removed")
            return
        script = os.path.join(_addon_dir(), "pcf_player.py")
        python_bin = "/usr/bin/python3" if os.path.exists("/usr/bin/python3") else "python3"
        pcf.install(_profile_dir(), script, python_bin)
    except Exception as exc:  # pragma: no cover - hardware path
        log("install playercorefactory.xml failed: {!r}".format(exc))


def main() -> None:
    import xbmc

    log("CEC Control Experiment service starting.")
    _publish_config()
    _install_pcf()

    class _Monitor(xbmc.Monitor):
        def onSettingsChanged(self) -> None:
            log("settings changed; re-publishing config + playercorefactory.xml")
            _publish_config()
            _install_pcf()

    # The CEC reclaim is no longer the service's job: the orchestrator triggers it directly via
    # JSON-RPC (cec.reclaim_kodi -> script.cecreclaim) when the handoff ends. This service only
    # installs the playercorefactory.xml and publishes the resolved config, then idles.
    monitor = _Monitor()
    while not monitor.abortRequested():
        if monitor.waitForAbort(5):
            break
    # Do NOT remove playercorefactory.xml on shutdown: Kodi loads it at STARTUP, before this service
    # runs, so the file must already be on disk at boot -> it has to persist across restarts. It is
    # removed only when the handoff is turned off (in _install_pcf). After a fresh install, Kodi must
    # be restarted ONCE for the routing to take effect (the file is written too late for that boot).
    log("CEC Control Experiment service stopping.")

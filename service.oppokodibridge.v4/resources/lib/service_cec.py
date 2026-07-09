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
from . import volumeir
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


def _current_model() -> str:
    import xbmcaddon

    try:
        return (xbmcaddon.Addon(ADDON_ID).getSettingString("oppo_model") or "M9205").strip().upper()
    except Exception:  # pragma: no cover - hardware path
        return "M9205"


def _autofill_ip_on_model_change(prev_model: str) -> str:
    """When oppo_model changes, default the oppo_ip field from the model (M9205 -> .10, M9207 -> .228)
    unless the user typed a custom address. Returns the current model so the caller can track changes.
    The IP-resolution rule lives in config.resolve_oppo_ip, so a custom address is never clobbered."""
    import xbmcaddon

    addon = xbmcaddon.Addon(ADDON_ID)
    model = (addon.getSettingString("oppo_model") or "M9205").strip().upper()
    if model == prev_model:  # not a model change -> leave the IP alone (e.g. the user edited it)
        return model
    current_ip = (addon.getSettingString("oppo_ip") or "").strip()
    new_ip = config_mod.resolve_oppo_ip(model, current_ip)
    if new_ip and new_ip != current_ip:
        addon.setSettingString("oppo_ip", new_ip)  # re-fires onSettingsChanged, but model==prev then -> no loop
        log("oppo_model -> {}: defaulted oppo_ip to {}".format(model, new_ip))
    return model


def _maybe_launch_first_run_wizard() -> None:
    """On first run (the wizard hasn't completed / been dismissed), pop the setup wizard once. The
    wizard.py 'firstrun' guard re-checks the flag, and the wizard sets wizard_done when finished or
    dismissed, so this won't nag on every start. The Settings button re-runs it on demand."""
    import xbmc
    import xbmcaddon

    try:
        # wizard_done is a boolean setting -> read it with the typed getter. getSettingString on a
        # boolean setting returns "" in Kodi, which would make this always re-launch.
        done = bool(xbmcaddon.Addon(ADDON_ID).getSettingBool("wizard_done"))
    except Exception as exc:  # pragma: no cover - hardware path
        log("wizard flag read failed ({}); skipping auto-launch".format(exc))
        return
    if done:
        return
    log("first run: launching the setup wizard")
    xbmc.executebuiltin("RunScript(special://home/addons/{}/wizard.py,firstrun)".format(ADDON_ID))


def _passthrough_enabled() -> bool:
    """Cheap single-flag read so the DEFAULT-OFF loop stays light (no full config build per tick)."""
    import xbmcaddon

    try:
        return bool(xbmcaddon.Addon(ADDON_ID).getSettingBool("remote_passthrough_enabled"))
    except Exception:  # pragma: no cover - hardware path
        return False


def _keymaps_dir() -> str:
    return _translate("special://profile/keymaps/")


def _reload_keymaps() -> None:
    import xbmc

    xbmc.executebuiltin("Action(reloadkeymaps)")


def _sync_volume_keymap(cfg) -> None:
    """Install or remove the always-on volume-takeover keymap to match the setting, reloading keymaps
    only on a real change. Installed ONLY when the feature is on AND a blaster host is set -- otherwise
    the keys would be remapped to a NotifyAll nobody can act on (dead volume keys). Non-fatal."""
    want = bool(getattr(cfg, "tv_volume_via_ir", False)) and bool(
        str(getattr(cfg, "ir_blaster_host", "") or "").strip())
    try:
        kmdir = _keymaps_dir()
        if want:
            changed = volumeir.install_keymap(
                kmdir, getattr(cfg, "tv_volume_key_up", "volume_up"),
                getattr(cfg, "tv_volume_key_down", "volume_down"))
        else:
            changed = volumeir.remove_keymap(kmdir)
        if changed:
            _reload_keymaps()
            log("volume takeover: keymap {} + keymaps reloaded".format("installed" if want else "removed"))
    except Exception as exc:  # pragma: no cover - hardware path
        log("volume takeover keymap sync failed (non-fatal): {!r}".format(exc))


def main() -> None:
    import xbmc

    log("CEC Control Experiment service starting.")
    _publish_config()
    _install_pcf()
    _maybe_launch_first_run_wizard()

    # Always-on TV volume takeover: a keymap remaps the remote's volume keys to NotifyAll, and this
    # forwarder fires the mapped RCA command at the TV via the IR blaster. `state["cfg"]` is the latest
    # config (refreshed on settings change) so onNotification reads the current volume codes cheaply.
    state = {"cfg": config_mod.from_addon()}
    try:
        forwarder = volumeir.VolumeIrForwarder(state["cfg"])
    except Exception as exc:  # pragma: no cover - hardware path
        log("volume takeover forwarder unavailable (non-fatal): {!r}".format(exc))
        forwarder = None

    class _Monitor(xbmc.Monitor):
        def __init__(self) -> None:
            super().__init__()
            self._model = _current_model()

        def onSettingsChanged(self) -> None:
            self._model = _autofill_ip_on_model_change(self._model)
            cfg = config_mod.from_addon()
            state["cfg"] = cfg
            log("settings changed; re-publishing config + playercorefactory.xml")
            _publish_config()
            _install_pcf()
            _sync_volume_keymap(cfg)
            if forwarder is not None:
                # push the refreshed connection settings into the live pipe so first-time setup
                # (enable + fill in the blaster host) takes effect without a Kodi restart.
                forwarder.update_config(cfg)

        def onNotification(self, sender: str, method: str, data: str) -> None:
            # Fast path for the volume-takeover NotifyAll messages; every other notification is ignored.
            if forwarder is None:
                return
            try:
                cmd = volumeir.volume_command(method, state["cfg"])
            except Exception as exc:  # noqa: BLE001 -- a bad notification must never break the service
                log("volume takeover: notification parse error (non-fatal): {!r}".format(exc))
                return
            if cmd is not None:
                forwarder.submit(cmd)

    # The CEC reclaim is no longer the service's job: the orchestrator triggers it directly via
    # JSON-RPC (cec.reclaim_kodi -> script.cecreclaim) when the handoff ends. This service only
    # installs the playercorefactory.xml and publishes the resolved config, then idles.
    monitor = _Monitor()
    _sync_volume_keymap(state["cfg"])  # install/remove the keymap to match the current setting

    # Remote passthrough (default-off): when enabled, poll the OPPO's playback state and open/close a
    # key-forwarding dialog around a playing disc. Lazy import -- passthrough_ui pulls in xbmcgui, which
    # must only load inside a running Kodi. A failure here is non-fatal: the handoff service still runs.
    runner = None
    try:
        from .passthrough_ui import PassthroughRunner

        runner = PassthroughRunner()
    except Exception as exc:  # pragma: no cover - hardware path
        log("passthrough runner unavailable (non-fatal): {!r}".format(exc))

    while not monitor.abortRequested():
        interval = 5
        if runner is not None and _passthrough_enabled():
            cfg = config_mod.from_addon()
            try:
                runner.tick(cfg)
            except Exception as exc:  # pragma: no cover - hardware path
                log("passthrough tick error (non-fatal): {!r}".format(exc))
            interval = max(2, int(getattr(cfg, "passthrough_poll_interval", 4) or 4))
        elif runner is not None:
            runner.shutdown()
        if monitor.waitForAbort(interval):
            break

    if runner is not None:
        runner.shutdown()
    # Stop the volume forwarder and REVERT the keymap so disabling/stopping the add-on restores Kodi's
    # own volume keys. (Unlike playercorefactory.xml, keymaps are reloaded at runtime, so removing here
    # is safe; on the next start the service reinstalls it if the feature is still on.)
    if forwarder is not None:
        forwarder.stop()
        forwarder.join(2.0)  # let the worker close the SSH pipe before we drop the keymap
    try:
        if volumeir.remove_keymap(_keymaps_dir()):
            _reload_keymaps()
            log("volume takeover: keymap removed on stop")
    except Exception as exc:  # pragma: no cover - hardware path
        log("volume takeover keymap removal on stop failed (non-fatal): {!r}".format(exc))
    # Do NOT remove playercorefactory.xml on shutdown: Kodi loads it at STARTUP, before this service
    # runs, so the file must already be on disk at boot -> it has to persist across restarts. It is
    # removed only when the handoff is turned off (in _install_pcf). After a fresh install, Kodi must
    # be restarted ONCE for the routing to take effect (the file is written too late for that boot).
    log("CEC Control Experiment service stopping.")

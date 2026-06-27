#!/usr/bin/env python3
"""First-run setup wizard entry point -- launched inside Kodi via RunScript:

    RunScript(.../wizard.py)            -- run the guided setup
    RunScript(.../wizard.py, firstrun)  -- same, but only if it hasn't completed (auto-trigger guard)

Runs inside Kodi (xbmcgui dialogs + the add-on settings). The flow LOGIC lives in
``resources/lib/wizard.py`` (unit-tested off-box); this is just the interactive Kodi adapter.
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

from resources.lib import config as config_mod  # noqa: E402
from resources.lib import wizard as wizard_lib  # noqa: E402
from resources.lib.oppo_http import OppoClient  # noqa: E402

ADDON_ID = "service.oppokodibridge.v4"


class _KodiUI:
    """xbmcgui.Dialog adapter matching the run_wizard UI contract."""

    def __init__(self):
        import xbmcgui

        self._d = xbmcgui.Dialog()

    def ok(self, title, message):
        self._d.ok(title, message)

    def yesno(self, title, message):
        return bool(self._d.yesno(title, message))

    def input(self, title, default=""):
        import xbmcgui

        try:
            return self._d.input(title, defaultt=default, type=xbmcgui.INPUT_IPADDRESS) or ""
        except Exception:
            # INPUT_IPADDRESS can't represent a hostname; fall back to a free-text box.
            return self._d.input(title, defaultt=default) or ""

    def select(self, title, options):
        return int(self._d.select(title, list(options)))


class _KodiSettings:
    """Reads/writes the add-on settings, and resolves a full Config. Internal keys (wizard_done,
    detected_*) are read/written directly here; they are not part of the Config dataclass."""

    def __init__(self):
        import xbmcaddon

        self._a = xbmcaddon.Addon(ADDON_ID)

    def get(self, key):
        try:
            return self._a.getSettingString(key) or ""
        except Exception:
            return ""

    def get_bool(self, key):
        # wizard_done is a boolean setting -> must be read with the typed bool getter (getSettingString
        # returns "" for a boolean setting in Kodi).
        try:
            return bool(self._a.getSettingBool(key))
        except Exception:
            return False

    def set(self, key, value):
        try:
            if isinstance(value, bool):
                self._a.setSettingBool(key, value)
            else:
                self._a.setSettingString(key, str(value))
        except Exception:
            pass

    def config(self):
        return config_mod.from_addon()


def main(argv):
    only_if_unfinished = len(argv) > 1 and argv[1] == "firstrun"
    settings = _KodiSettings()
    if only_if_unfinished and settings.get_bool("wizard_done"):
        return  # already completed/dismissed -> don't auto-pop the wizard again
    wizard_lib.run_wizard(_KodiUI(), lambda cfg: OppoClient(cfg), settings)


if __name__ == "__main__":
    main(sys.argv)

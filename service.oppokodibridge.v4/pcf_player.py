#!/usr/bin/env python3
"""External player invoked by Kodi's playercorefactory: ``python3 pcf_player.py "<file>"``.

Runs OUTSIDE Kodi (its own process, no xbmc). Reads the config the service published to
``runtime_config.json``, then hands the file to the OPPO over the network and blocks until playback
ends. Because Kodi routed the file here *before* playing it, there is no "Kodi blips then hands off"
moment -- that's the whole point of this no-blip fork.
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

from resources.lib import config as config_mod  # noqa: E402
from resources.lib import orchestrator  # noqa: E402
from resources.lib.kodilog import log  # noqa: E402

ADDON_ID = "service.oppokodibridge.v4"


def _runtime_config_path() -> str:
    # HERE = <kodi>/addons/<id> ; the service writes config to <kodi>/userdata/addon_data/<id>/.
    kodi_home = os.path.dirname(os.path.dirname(HERE))
    return os.path.join(kodi_home, "userdata", "addon_data", ADDON_ID, "runtime_config.json")


def _load_config():
    path = _runtime_config_path()
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return config_mod.Config.from_dict(json.load(fh))
    except Exception as exc:
        log("pcf_player: could not load {} ({!r}); using defaults".format(path, exc))
        return config_mod.Config()


def main(argv) -> int:
    if len(argv) < 2 or not argv[1]:
        log("pcf_player: no file argument given")
        return 2
    kodi_file = argv[1]
    log("pcf_player: handling {!r}".format(kodi_file))
    cfg = _load_config()
    try:
        orchestrator.run(cfg, kodi_file)
    except Exception as exc:  # never crash the player process
        log("pcf_player: orchestrator error {!r}".format(exc))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))

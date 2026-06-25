"""Trigger the TV input switch-over -- the ONLY place this add-on asserts CEC, always single-shot.

  * ``grab_oppo(client)``  -- power-cycle the OPPO so its OWN One-Touch-Play grabs the TV (the OPPO
                             only asserts active source on a power-ON transition).
  * ``reclaim_kodi(config)`` -- ask Kodi to re-assert ITS OWN active source, via localhost JSON-RPC ->
                             the ``script.cecreclaim`` helper -> ``CECActivateSource``.

Each is fired exactly ONCE per play/stop event by the orchestrator. There is NO standing monitor that
re-asserts active source -- that would override a manual input change and make the TV un-leaveable
(CEC is open-loop; we cannot tell "the TV missed my frame" from "the user switched away"). Each device
asserts only its OWN source -- no injection, no foreign-initiator spoof.
"""
from __future__ import annotations

import base64
import json
import urllib.request

from .kodilog import log
from .oppo_http import OppoClient, OppoError  # noqa: F401 (OppoClient re-exported for callers/tests)

RECLAIM_ADDON = "script.cecreclaim"


def grab_oppo(client) -> bool:
    """Power-cycle the OPPO so its own One-Touch-Play grabs the TV. Single-shot, non-fatal on failure.

    Catches any exception, not just OppoError: grab runs BEFORE the orchestrator's try/finally, so an
    escape here would skip the stop-side reclaim and strand the TV. The control transport normally
    raises OppoError, but the serial path can surface other types -- none may abort the handoff."""
    try:
        client.power_cycle()
        return True
    except Exception as exc:  # noqa: BLE001 - non-fatal by contract (see docstring)
        log("OPPO grab (power-cycle) failed (non-fatal): {}".format(exc))
        return False


def reclaim_kodi(config) -> bool:
    """Ask Kodi to re-assert its OWN active source once, via localhost JSON-RPC -> script.cecreclaim.

    Runs from the external player process (or the in-Kodi settings test); both reach Kodi's HTTP
    JSON-RPC on 127.0.0.1. Returns True if the call was accepted (the TV switch itself is open-loop)."""
    host = "127.0.0.1"
    port = int(getattr(config, "kodi_rpc_port", 8080) or 8080)
    user = getattr(config, "kodi_rpc_user", "") or ""
    password = getattr(config, "kodi_rpc_pass", "") or ""
    payload = json.dumps(
        {"jsonrpc": "2.0", "id": 1, "method": "Addons.ExecuteAddon",
         "params": {"addonid": RECLAIM_ADDON}}
    ).encode()
    req = urllib.request.Request(
        "http://{}:{}/jsonrpc".format(host, port),
        data=payload,
        headers={"Content-Type": "application/json"},
    )
    if user:
        token = base64.b64encode("{}:{}".format(user, password).encode()).decode()
        req.add_header("Authorization", "Basic " + token)
    try:
        with urllib.request.urlopen(req, timeout=5.0) as resp:
            body = json.loads(resp.read().decode("utf-8", "replace"))
    except (OSError, ValueError) as exc:
        # OSError = unreachable / timeout / HTTPError (e.g. 401); ValueError (json.JSONDecodeError) = a
        # non-JSON 200 body, e.g. a misconfigured Kodi web server returning an HTML login/error page.
        # Either way: log and give up -- never raise into the orchestrator's finally or the settings test.
        log("Kodi reclaim failed (JSON-RPC {}:{}): {}".format(host, port, exc))
        return False
    if isinstance(body, dict) and body.get("error"):
        log("Kodi reclaim error: {}".format(body["error"]))
        return False
    log("Kodi reclaim sent (script.cecreclaim -> CECActivateSource), single-shot.")
    return True

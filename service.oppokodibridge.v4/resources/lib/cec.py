"""Trigger the TV input switch-over -- the ONLY place this add-on asserts CEC, always single-shot.

  * ``grab_oppo(client)``  -- power-cycle the OPPO so its OWN One-Touch-Play grabs the TV (the OPPO
                             only asserts active source on a power-ON transition).
  * ``reclaim_kodi(config)`` -- ask Kodi to re-assert ITS OWN active source, via localhost JSON-RPC ->
                             the ``script.cecreclaim`` helper -> ``CECActivateSource``.

Each is fired exactly ONCE per play/stop event by the orchestrator. There is NO standing monitor that
re-asserts active source -- that would override a manual input change and make the TV un-leaveable
(CEC is open-loop; we cannot tell "the TV missed my frame" from "the user switched away"). Each device
asserts only its OWN source -- no injection, no foreign-initiator spoof.

This module also hosts the add-on's localhost Kodi JSON-RPC channel (``_kodi_jsonrpc``): the CEC
reclaim above, and ``kodi_video_sources`` -- read Kodi's configured video sources so the handoff can
auto-detect ``path_from`` (it is the same 127.0.0.1 web-server channel the reclaim already needs).
"""
from __future__ import annotations

import base64
import json
import urllib.request

from .kodilog import log
from .oppo_http import (  # noqa: F401 (OppoClient re-exported for callers/tests)
    OppoClient,
    OppoError,
    unwrap_multipath,
)

RECLAIM_ADDON = "script.cecreclaim"


def grab_supported(config) -> bool:
    """True when the OPPO can grab the TV over the network -- i.e. a ``#POF`` -> ``#PON`` power-cycle
    fires its OWN One-Touch-Play (genuine OPPO; verified on a TCL Q9L).

    False for the M9207 Plus / UDP-203 clone: its ``#POF`` is a sleep and ``#PON`` is a no-op (it only
    powers on, and thus asserts active source, from an IR/remote power button). On that hardware the
    power-cycle never grabs AND wedges the unit -- it puts the box to sleep then can't wake it, which is
    the sluggish/locked IR remote during playback. So on the M9207 the grab is skipped ENTIRELY
    (regardless of ``grab_tv_on_play``) and the TV is switched to the OPPO input manually. Model-gated,
    default M9205. (Stop detection is HTTP-only for every model, so ``oppo_model`` now affects only
    this grab.)"""
    return str(getattr(config, "oppo_model", "M9205") or "M9205").strip().upper() != "M9207"


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


def _kodi_jsonrpc(config, method: str, params=None, timeout: float = 5.0):
    """POST one JSON-RPC call to Kodi's localhost web server and return the parsed response dict, or
    ``None`` on ANY transport/parse failure (unreachable, timeout, HTTPError e.g. 401, or a non-JSON
    200 body from a misconfigured web server). NEVER raises -- callers run in the orchestrator's
    ``finally`` (reclaim) or before a handoff (sources), where an escape would strand the TV/handoff."""
    host = "127.0.0.1"
    port = int(getattr(config, "kodi_rpc_port", 8080) or 8080)
    user = getattr(config, "kodi_rpc_user", "") or ""
    password = getattr(config, "kodi_rpc_pass", "") or ""
    payload = json.dumps(
        {"jsonrpc": "2.0", "id": 1, "method": method, "params": params or {}}
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
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8", "replace"))
    except (OSError, ValueError) as exc:
        log("Kodi JSON-RPC {} failed ({}:{}): {}".format(method, host, port, exc))
        return None


def reclaim_kodi(config) -> bool:
    """Ask Kodi to re-assert its OWN active source once, via localhost JSON-RPC -> script.cecreclaim.

    Runs from the external player process (or the in-Kodi settings test); both reach Kodi's HTTP
    JSON-RPC on 127.0.0.1. Returns True if the call was accepted (the TV switch itself is open-loop)."""
    body = _kodi_jsonrpc(config, "Addons.ExecuteAddon", {"addonid": RECLAIM_ADDON})
    if body is None:
        return False
    if isinstance(body, dict) and body.get("error"):
        log("Kodi reclaim error: {}".format(body["error"]))
        return False
    log("Kodi reclaim sent (script.cecreclaim -> CECActivateSource), single-shot.")
    return True


def kodi_video_sources(config) -> list:
    """Kodi's configured VIDEO source roots, for auto-detecting ``path_from`` (over the same localhost
    JSON-RPC channel as the reclaim). Best-effort: returns ``[]`` on any failure or unexpected shape --
    the handoff then falls back to the typed ``path_from``. ``multipath://`` sources (one source that
    aggregates several folders) are expanded into their member roots."""
    body = _kodi_jsonrpc(config, "Files.GetSources", {"media": "video"})
    result = body.get("result") if isinstance(body, dict) else None
    sources = result.get("sources") if isinstance(result, dict) else None
    if not isinstance(sources, list):
        return []
    roots: list = []
    for item in sources:
        if not isinstance(item, dict):
            continue
        path = item.get("file")
        if path:
            roots.extend(unwrap_multipath(str(path)))
    return roots

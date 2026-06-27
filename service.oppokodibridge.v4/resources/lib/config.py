"""Add-on configuration.

``Config`` is a plain dataclass with no Kodi dependency, so the pure-logic modules and
the test-suite can build one directly. ``from_addon()`` is the only Kodi-aware entry
point: it reads the live add-on settings.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class Config:
    oppo_ip: str = ""
    oppo_http_port: int = 436
    oppo_model: str = "M9205"
    oppo_http_broadcast: str = "255.255.255.255"
    socket_timeout: float = 4.0
    handoff_enabled: bool = True
    disc_iso_only: bool = True
    use_json_payload: bool = True
    media_type: int = 1
    app_device_type: int = 2
    path_from: str = ""
    path_to: str = ""
    cec_auto_enable: bool = True
    cec_reclaim_on_stop: bool = True
    grab_tv_on_play: bool = True
    oppo_hdmi_phys: str = "1.0.0.0"
    serial_control: bool = False
    serial_port: str = "/dev/ttyUSB0"
    serial_baud: int = 9600
    poll_interval: float = 5.0
    idle_confirmations: int = 2
    max_read_failures: int = 5
    max_watch_seconds: float = 21600.0
    kodi_rpc_port: int = 8080
    kodi_rpc_user: str = ""
    kodi_rpc_pass: str = ""

    @property
    def configured(self) -> bool:
        return bool(self.oppo_ip.strip())

    @classmethod
    def from_dict(cls, data: dict) -> "Config":
        """Build a Config from a plain dict (e.g. runtime_config.json), ignoring unknown keys."""
        import dataclasses

        names = {f.name for f in dataclasses.fields(cls)}
        return cls(**{k: v for k, v in (data or {}).items() if k in names})


# Per-model default OPPO IP. The model is the first setting; the IP field defaults from it
# (M9205 = .10, M9207 = .228) -- but a custom address the user typed is always preserved.
DEFAULT_IP_BY_MODEL = {
    "M9205": "192.168.10.10",
    "M9207": "192.168.10.228",
}


def default_ip_for_model(model: str) -> str:
    """The default OPPO IP for ``model`` (falls back to the M9205 default for an unknown model)."""
    return DEFAULT_IP_BY_MODEL.get(str(model or "").strip().upper(), DEFAULT_IP_BY_MODEL["M9205"])


def resolve_oppo_ip(model: str, raw_ip: str) -> str:
    """The effective OPPO IP for ``model``: keep a custom address the user typed, but fill a blank or a
    known per-model default with THIS model's default. So selecting the model drives the default IP
    (M9205 -> .10, M9207 -> .228) without ever clobbering a hand-entered address."""
    ip = str(raw_ip or "").strip()
    if ip and ip not in DEFAULT_IP_BY_MODEL.values():
        return ip  # a custom address -> keep it
    return default_ip_for_model(model)


def from_addon() -> "Config":
    import xbmcaddon

    # Pass the id explicitly: a no-arg xbmcaddon.Addon() raises "No valid addon id could be obtained"
    # when this runs from a RunScript (the Setup & tests buttons) rather than the background service.
    addon = xbmcaddon.Addon("service.oppokodibridge.v4")

    def s(key: str, default: str = "") -> str:
        try:
            return addon.getSettingString(key) or default
        except Exception:
            return default

    def b(key: str, default: bool) -> bool:
        try:
            if not addon.getSetting(key):  # undeclared / unset id -> use the dataclass default
                return default
            return bool(addon.getSettingBool(key))
        except Exception:
            return default

    def i(key: str, default: int) -> int:
        try:
            if not addon.getSetting(key):  # undeclared / unset id -> use the dataclass default
                return default
            return int(addon.getSettingInt(key))
        except Exception:
            return default

    oppo_model = s("oppo_model", "M9205").strip().upper()
    return Config(
        oppo_ip=resolve_oppo_ip(oppo_model, s("oppo_ip")),
        oppo_http_port=i("oppo_http_port", 436),
        oppo_model=oppo_model,
        socket_timeout=float(i("socket_timeout", 4)),
        handoff_enabled=b("handoff_enabled", True),
        disc_iso_only=b("disc_iso_only", True),
        use_json_payload=b("use_json_payload", True),
        media_type=i("media_type", 1),
        app_device_type=i("app_device_type", 2),
        path_from=s("path_from").strip(),
        path_to=s("path_to").strip(),
        cec_auto_enable=b("cec_auto_enable", True),
        cec_reclaim_on_stop=b("cec_reclaim_on_stop", True),
        grab_tv_on_play=b("grab_tv_on_play", True),
        oppo_hdmi_phys=s("oppo_hdmi_phys") or "1.0.0.0",
        serial_control=b("serial_control", False),
        serial_port=s("serial_port") or "/dev/ttyUSB0",
        serial_baud=i("serial_baud", 9600),
        poll_interval=float(i("poll_interval", 5)),
        kodi_rpc_port=i("kodi_rpc_port", 8080),
        kodi_rpc_user=s("kodi_rpc_user").strip(),
        kodi_rpc_pass=s("kodi_rpc_pass").strip(),
    )

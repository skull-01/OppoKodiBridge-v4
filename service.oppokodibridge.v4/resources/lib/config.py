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
    socket_timeout: float = 8.0
    handoff_enabled: bool = True
    disc_iso_only: bool = True
    use_json_payload: bool = True
    media_type: int = 1
    app_device_type: int = 2
    path_from: str = ""
    path_to: str = ""
    path_from_autodetect: bool = True
    # OPPO mount directory under /mnt (the play path is /mnt/<oppo_mount>/<leaf>). The real mount point
    # can't be read back from the app API (#14), so it's a configurable override; default nfs1 keeps the
    # proven /mnt/nfs1 path unchanged.
    oppo_mount: str = "nfs1"
    cec_auto_enable: bool = True
    cec_reclaim_on_stop: bool = True
    grab_tv_on_play: bool = True
    # TV-switch transport (tvswitch.py). Default 'cec' = the existing HDMI-CEC path (zero regression).
    tv_switch_method: str = "cec"          # none | cec | ir | lirc | ir_remote
    ir_serial_port: str = "/dev/ttyUSB0"   # ZJIoT serial IR module (method 'ir', Ugoos/CoreELEC)
    ir_serial_baud: int = 9600
    ir_lirc_device: str = "/dev/lirc0"     # kernel IR TX device (method 'lirc', Raspberry Pi 4)
    ir_code_oppo: str = ""                 # HDMI-input NEC code to switch the TV to the OPPO
    ir_code_kodi: str = ""                 # HDMI-input NEC code to switch the TV back to Kodi
    # method 'ir_remote': an IR blaster on a SEPARATE host, reached over SSH. Play = drive the TV's input
    # picker to the OPPO's HDMI port (RCA menu-nav; the panel has no discrete HDMI codes); stop = CEC
    # reclaim. All hardware-validated 2026-07-08.
    ir_blaster_host: str = ""              # blaster host (e.g. the Pi's IP); blank = transport disabled
    ir_blaster_user: str = ""              # SSH user on the blaster (blank = ssh default)
    ir_blaster_ssh_key: str = ""           # SSH identity file (blank = ssh default key)
    oppo_hdmi_port: int = 1                # which HDMI input the OPPO is on (1-4)
    tv_blaster_lirc_device: str = "/dev/lirc0"  # /dev/lirc TX node on the blaster
    tv_menu_anchor_ups: int = 4            # UP presses to park the picker on the top entry (anchor)
    # Entries ABOVE HDMI1 in the input picker (e.g. a "Live TV"/"Antenna" row): the OPPO's port is then
    # DOWN x (port-1+offset) from the anchored top, not DOWN x (port-1). 0 = the top entry IS HDMI1.
    tv_menu_top_offset: int = 0
    tv_ir_key_delay: float = 0.7           # seconds between keypresses (TV must register each)
    tv_ir_reps: int = 3                    # frames per keypress (burst, like a held remote button)
    tv_ir_carrier: int = 38000             # RCA carrier (Hz)
    tv_rca_device: int = 15                # RCA device/address (TCL = 15)
    tv_code_input: int = 163               # RCA command: INPUT (open the input picker)
    tv_code_up: int = 89                   # RCA command: UP
    tv_code_down: int = 88                 # RCA command: DOWN
    tv_code_ok: int = 244                  # RCA command: OK / ENTER
    ir_blaster_timeout: float = 20.0       # overall SSH+sequence timeout (s)
    ir_blaster_connect_timeout: int = 8    # SSH ConnectTimeout (s)
    oppo_hdmi_phys: str = "1.0.0.0"
    serial_control: bool = False
    serial_port: str = "/dev/ttyUSB0"
    serial_baud: int = 9600
    poll_interval: float = 5.0
    idle_confirmations: int = 2
    max_read_failures: int = 5
    max_watch_seconds: float = 21600.0
    # Reference ISO patience (#21): wait ~90s for playback to start before giving up (large UHD ISOs
    # buffer slowly), and auto-heal once (re-issue the play) if it stalls. Internal tunables.
    playback_start_grace_seconds: float = 90.0
    # Bounded transient-retry for idempotent OPPO reads on a slow/fragile proxy (#22). Internal.
    http_retries: int = 1
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

    def f(key: str, default: float) -> float:
        try:
            raw = addon.getSetting(key)  # stored as a string; number control gives e.g. "0.7"
            return float(raw) if raw else default
        except Exception:
            return default

    oppo_model = s("oppo_model", "M9205").strip().upper()
    return Config(
        oppo_ip=resolve_oppo_ip(oppo_model, s("oppo_ip")),
        oppo_http_port=i("oppo_http_port", 436),
        oppo_model=oppo_model,
        handoff_enabled=b("handoff_enabled", True),
        disc_iso_only=b("disc_iso_only", True),
        use_json_payload=b("use_json_payload", True),
        media_type=i("media_type", 1),
        app_device_type=i("app_device_type", 2),
        path_from=s("path_from").strip(),
        path_to=s("path_to").strip(),
        path_from_autodetect=b("path_from_autodetect", True),
        oppo_mount=(s("oppo_mount", "nfs1").strip() or "nfs1"),
        cec_auto_enable=b("cec_auto_enable", True),
        cec_reclaim_on_stop=b("cec_reclaim_on_stop", True),
        grab_tv_on_play=b("grab_tv_on_play", True),
        tv_switch_method=(s("tv_switch_method", "cec") or "cec").strip().lower(),
        ir_serial_port=s("ir_serial_port") or "/dev/ttyUSB0",
        ir_serial_baud=i("ir_serial_baud", 9600),
        ir_lirc_device=s("ir_lirc_device") or "/dev/lirc0",
        ir_code_oppo=s("ir_code_oppo").strip(),
        ir_code_kodi=s("ir_code_kodi").strip(),
        ir_blaster_host=s("ir_blaster_host").strip(),
        ir_blaster_user=s("ir_blaster_user").strip(),
        ir_blaster_ssh_key=s("ir_blaster_ssh_key").strip(),
        oppo_hdmi_port=i("oppo_hdmi_port", 1),
        tv_blaster_lirc_device=s("tv_blaster_lirc_device") or "/dev/lirc0",
        tv_menu_anchor_ups=i("tv_menu_anchor_ups", 4),
        tv_menu_top_offset=i("tv_menu_top_offset", 0),
        tv_ir_key_delay=f("tv_ir_key_delay", 0.7),
        tv_ir_reps=i("tv_ir_reps", 3),
        tv_rca_device=i("tv_rca_device", 15),
        tv_code_input=i("tv_code_input", 163),
        tv_code_up=i("tv_code_up", 89),
        tv_code_down=i("tv_code_down", 88),
        tv_code_ok=i("tv_code_ok", 244),
        oppo_hdmi_phys=s("oppo_hdmi_phys") or "1.0.0.0",
        serial_control=b("serial_control", False),
        serial_port=s("serial_port") or "/dev/ttyUSB0",
        serial_baud=i("serial_baud", 9600),
        kodi_rpc_port=i("kodi_rpc_port", 8080),
        kodi_rpc_user=s("kodi_rpc_user").strip(),
        kodi_rpc_pass=s("kodi_rpc_pass").strip(),
    )

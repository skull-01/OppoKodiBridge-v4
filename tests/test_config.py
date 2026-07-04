"""from_addon() must publish the dataclass DEFAULTS for setting ids that aren't declared in
settings.xml, not Kodi's getSettingBool/Int falsy 0/False for an unknown id."""
import sys
import types

from resources.lib import config as config_mod


def _fake_xbmcaddon(settings, captured=None):
    mod = types.ModuleType("xbmcaddon")

    class Addon:
        def __init__(self, addon_id=None):  # real Kodi needs the id; a no-arg call fails under RunScript
            if captured is not None:
                captured["id"] = addon_id

        def getSetting(self, key):  # raw STRING (like real Kodi); '' for an undeclared/unset id
            if key not in settings:
                return ""
            value = settings[key]
            if isinstance(value, bool):
                return "true" if value else "false"
            return str(value)

        def getSettingString(self, key):
            return settings.get(key, "")

        def getSettingBool(self, key):
            return bool(settings.get(key, False))

        def getSettingInt(self, key):
            return int(settings.get(key, 0) or 0)

    mod.Addon = Addon
    return mod


def test_from_addon_uses_dataclass_defaults_for_undeclared_ids(monkeypatch):
    # only oppo_ip is "declared"; media_type/disc_iso_only/... are absent -> dataclass defaults
    monkeypatch.setitem(sys.modules, "xbmcaddon", _fake_xbmcaddon({"oppo_ip": "1.2.3.4"}))
    cfg = config_mod.from_addon()
    assert cfg.oppo_ip == "1.2.3.4"
    assert cfg.media_type == 1            # not 0
    assert cfg.app_device_type == 2       # not 0
    assert cfg.disc_iso_only is True      # not False
    assert cfg.cec_auto_enable is True    # not False
    assert cfg.oppo_http_port == 436      # declared-default path still works
    assert cfg.kodi_rpc_port == 8080


def test_from_addon_honours_declared_falsy_values(monkeypatch):
    # a declared boolean explicitly set false must come through as False, not the default True
    monkeypatch.setitem(
        sys.modules, "xbmcaddon",
        _fake_xbmcaddon({"oppo_ip": "1.2.3.4", "grab_tv_on_play": False, "handoff_enabled": False}),
    )
    cfg = config_mod.from_addon()
    assert cfg.grab_tv_on_play is False
    assert cfg.handoff_enabled is False


def test_default_ip_for_model():
    assert config_mod.default_ip_for_model("M9205") == "192.168.10.10"
    assert config_mod.default_ip_for_model("M9207") == "192.168.10.228"
    assert config_mod.default_ip_for_model("m9207") == "192.168.10.228"   # case-insensitive
    assert config_mod.default_ip_for_model("") == "192.168.10.10"          # unknown -> M9205 default
    assert config_mod.default_ip_for_model(None) == "192.168.10.10"


def test_resolve_oppo_ip_defaults_from_model_but_keeps_custom():
    # blank -> this model's default
    assert config_mod.resolve_oppo_ip("M9207", "") == "192.168.10.228"
    assert config_mod.resolve_oppo_ip("M9205", "") == "192.168.10.10"
    # a known per-model default (the OTHER model's) is treated as non-custom -> swapped to this model's
    assert config_mod.resolve_oppo_ip("M9207", "192.168.10.10") == "192.168.10.228"
    assert config_mod.resolve_oppo_ip("M9205", "192.168.10.228") == "192.168.10.10"
    # this model's own default stays
    assert config_mod.resolve_oppo_ip("M9207", "192.168.10.228") == "192.168.10.228"
    # a custom address is NEVER clobbered
    assert config_mod.resolve_oppo_ip("M9207", "192.168.1.50") == "192.168.1.50"
    assert config_mod.resolve_oppo_ip("M9205", "10.0.0.9") == "10.0.0.9"


def test_from_addon_defaults_ip_from_model(monkeypatch):
    # M9207 with no IP set -> the M9207 default; M9205 -> the M9205 default; a custom IP is preserved.
    monkeypatch.setitem(sys.modules, "xbmcaddon", _fake_xbmcaddon({"oppo_model": "M9207"}))
    assert config_mod.from_addon().oppo_ip == "192.168.10.228"
    monkeypatch.setitem(sys.modules, "xbmcaddon", _fake_xbmcaddon({"oppo_model": "M9205"}))
    assert config_mod.from_addon().oppo_ip == "192.168.10.10"
    monkeypatch.setitem(sys.modules, "xbmcaddon", _fake_xbmcaddon({"oppo_model": "M9207", "oppo_ip": "192.168.1.50"}))
    assert config_mod.from_addon().oppo_ip == "192.168.1.50"


def test_from_addon_ignores_removed_tuning_settings(monkeypatch):
    # poll_interval & socket_timeout are no longer user settings (the Advanced tab now holds only the
    # Kodi JSON-RPC settings) -> from_addon must NOT read them; they take the dataclass defaults.
    monkeypatch.setitem(
        sys.modules, "xbmcaddon",
        _fake_xbmcaddon({"oppo_ip": "1.2.3.4", "poll_interval": 99, "socket_timeout": 25, "kodi_rpc_port": 9090}),
    )
    cfg = config_mod.from_addon()
    assert cfg.poll_interval == 5.0       # dataclass default, NOT the stray 99
    assert cfg.socket_timeout == 8.0      # dataclass default (raised for slow-proxy tolerance, #22)
    assert cfg.kodi_rpc_port == 9090      # Kodi JSON-RPC settings are still read


def test_from_addon_passes_explicit_addon_id(monkeypatch):
    # a no-arg xbmcaddon.Addon() raises "No valid addon id" when launched via RunScript (the Setup &
    # tests buttons) -- from_addon must pass the explicit id so those scripts don't crash.
    captured = {}
    monkeypatch.setitem(sys.modules, "xbmcaddon", _fake_xbmcaddon({"oppo_ip": "1.2.3.4"}, captured))
    cfg = config_mod.from_addon()
    assert captured["id"] == "service.oppokodibridge.v4"
    assert cfg.oppo_ip == "1.2.3.4"


def test_from_addon_reads_tv_switch_settings(monkeypatch):
    monkeypatch.setitem(sys.modules, "xbmcaddon", _fake_xbmcaddon({
        "oppo_ip": "1.2.3.4", "tv_switch_method": "LIRC", "ir_code_oppo": " 0x1 ",
        "ir_code_kodi": "0x2", "ir_lirc_device": "/dev/lirc1", "ir_serial_port": "/dev/ttyUSB9",
        "ir_serial_baud": 115200,
    }))
    cfg = config_mod.from_addon()
    assert cfg.tv_switch_method == "lirc"     # normalised to lowercase
    assert cfg.ir_code_oppo == "0x1"          # stripped
    assert cfg.ir_code_kodi == "0x2"
    assert cfg.ir_lirc_device == "/dev/lirc1"
    assert cfg.ir_serial_port == "/dev/ttyUSB9"
    assert cfg.ir_serial_baud == 115200


def test_from_addon_defaults_tv_switch_to_cec(monkeypatch):
    # an old install with no tv_switch_method set -> 'cec' (zero regression); IR codes blank.
    monkeypatch.setitem(sys.modules, "xbmcaddon", _fake_xbmcaddon({"oppo_ip": "1.2.3.4"}))
    cfg = config_mod.from_addon()
    assert cfg.tv_switch_method == "cec"
    assert cfg.ir_code_oppo == "" and cfg.ir_code_kodi == ""


def test_from_addon_path_from_autodetect_defaults_true(monkeypatch):
    # undeclared/unset -> dataclass default True (auto-detect path_from from Kodi sources is on)
    monkeypatch.setitem(sys.modules, "xbmcaddon", _fake_xbmcaddon({"oppo_ip": "1.2.3.4"}))
    assert config_mod.from_addon().path_from_autodetect is True


def test_from_addon_path_from_autodetect_honours_false(monkeypatch):
    # explicitly disabled -> False (use the typed path_from only; no per-play Kodi JSON-RPC)
    monkeypatch.setitem(
        sys.modules, "xbmcaddon",
        _fake_xbmcaddon({"oppo_ip": "1.2.3.4", "path_from_autodetect": False}),
    )
    assert config_mod.from_addon().path_from_autodetect is False

from resources.lib import passthrough as pt

VKEY = pt.VKEY_BASE  # 0xF000


def test_nav_and_select_by_action_id():
    assert pt.resolve(3, 0) == "NUP"
    assert pt.resolve(4, 0) == "NDN"
    assert pt.resolve(1, 0) == "NLT"
    assert pt.resolve(2, 0) == "NRT"
    assert pt.resolve(7, 0) == "SEL"


def test_repurposed_keys_by_button_code():
    assert pt.resolve(0, VKEY | 0x08) == "PAU"    # BackSpace -> Play/Pause
    assert pt.resolve(0, VKEY | 0x7F) == "STP"    # Delete (XBMCK_DELETE 0x7F) -> Stop
    assert pt.resolve(0, VKEY | 0xA6) == "RET"    # browser-back -> Return
    assert pt.resolve(0, VKEY | 0x13F) == "SUB"   # Apps/menu (XBMCK_MENU 0x13F) -> Subtitle
    assert pt.resolve(0, VKEY | 0xAC) == "AUD"    # browser-home -> Audio
    assert pt.resolve(0, VKEY | 0xAF) == "VUP"    # volume up
    assert pt.resolve(0, VKEY | 0xAE) == "VDN"    # volume down
    assert pt.resolve(0, VKEY | 0xAD) == "OSD"    # mute key -> Info


def test_old_windows_vk_codes_are_not_mapped():
    # Kodi button code is 0xF000|XBMCKey, not 0xF000|Windows-VK: the VK-derived Delete(0x2E)/Apps(0x5D)
    # must NOT resolve (they were the audit-caught bug).
    assert pt.resolve(0, VKEY | 0x2E) is None
    assert pt.resolve(0, VKEY | 0x5D) is None


def test_button_code_beats_colliding_action():
    # BackSpace and browser-back both resolve to ACTION_NAV_BACK (92) in Kodi; the button code must
    # still distinguish Play/Pause from Back.
    assert pt.resolve(92, VKEY | 0x08) == "PAU"
    assert pt.resolve(92, VKEY | 0xA6) == "RET"


def test_unknown_key_returns_none():
    assert pt.resolve(0, 0) is None
    assert pt.resolve(999, 4242) is None


def test_overrides_take_precedence():
    ov = pt.parse_overrides('{"%d": "STP"}' % (VKEY | 0x08))
    assert pt.resolve(0, VKEY | 0x08, ov) == "STP"  # remap BackSpace to Stop


def test_parse_overrides_is_lenient():
    assert pt.parse_overrides("") == {}
    assert pt.parse_overrides(None) == {}
    assert pt.parse_overrides("not json") == {}
    assert pt.parse_overrides("[1,2,3]") == {}
    assert pt.parse_overrides('{"61448": "PAU"}') == {61448: "PAU"}
    # null / numeric / empty override VALUES are skipped (no bogus "None"/"5"/"" forwarded key)
    assert pt.parse_overrides('{"61448": null, "100": 5, "200": ""}') == {}
    assert pt.parse_overrides('{"61448": " PAU ", "9": null}') == {61448: "PAU"}


def test_maps_are_consistent():
    # every configured button has an OPPO code, and codes are 2-3 uppercase letters/digits
    for code in list(pt.CODE_BY_ACTION.values()) + list(pt.CODE_BY_BUTTONCODE.values()):
        assert 2 <= len(code) <= 3 and code.isupper()


def test_arm_decision_arms_on_active():
    assert pt.arm_decision(False, "playing", 0, 2) == (True, 0)
    assert pt.arm_decision(False, "paused", 3, 2) == (True, 0)
    assert pt.arm_decision(True, "playing", 5, 2) == (True, 0)


def test_arm_decision_disarms_only_after_debounce():
    # first non-active read while armed is tolerated (a blip), second disarms
    assert pt.arm_decision(True, "idle", 0, 2) == (True, 1)
    assert pt.arm_decision(True, "idle", 1, 2) == (False, 2)
    assert pt.arm_decision(True, "down", 0, 2) == (True, 1)
    assert pt.arm_decision(True, "unknown", 1, 2) == (False, 2)


def test_arm_decision_stays_disarmed_when_never_armed():
    assert pt.arm_decision(False, "idle", 0, 2) == (False, 1)
    assert pt.arm_decision(False, "down", 5, 2) == (False, 6)


def test_arm_decision_needed_floor():
    # idle_needed<=0 must still disarm (never trap): treated as 1
    assert pt.arm_decision(True, "idle", 0, 0) == (False, 1)


def test_config_defaults_are_off_and_present():
    from resources.lib.config import Config

    c = Config()
    assert c.remote_passthrough_enabled is False  # zero-regression default
    assert c.passthrough_key_overrides == ""
    assert c.passthrough_poll_interval == 4.0
    # round-trips through runtime_config.json (from_dict ignores unknowns, keeps knowns)
    c2 = Config.from_dict({"remote_passthrough_enabled": True, "passthrough_poll_interval": 6.0})
    assert c2.remote_passthrough_enabled is True and c2.passthrough_poll_interval == 6.0

from resources.lib import passthrough as pt


def test_nav_and_select_by_action_id():
    assert pt.resolve(3, 0) == "NUP"   # ACTION_MOVE_UP
    assert pt.resolve(4, 0) == "NDN"   # ACTION_MOVE_DOWN
    assert pt.resolve(1, 0) == "NLT"   # ACTION_MOVE_LEFT
    assert pt.resolve(2, 0) == "NRT"   # ACTION_MOVE_RIGHT
    assert pt.resolve(7, 0) == "SEL"   # ACTION_SELECT_ITEM


def test_media_keys_by_action_id():
    # captured from the operator's remote: these resolve by their stable action id
    assert pt.resolve(88, 0) == "VUP"    # ACTION_VOLUME_UP
    assert pt.resolve(89, 0) == "VDN"    # ACTION_VOLUME_DOWN
    assert pt.resolve(91, 0) == "OSD"    # ACTION_MUTE -> Info/OSD
    assert pt.resolve(117, 0) == "SUB"   # ACTION_CONTEXT_MENU -> Subtitle
    assert pt.resolve(122, 0) == "AUD"   # Audio
    # action id wins even if the raw button code varies (this remote double-fires: both codes carry 88)
    assert pt.resolve(88, 61625) == "VUP"
    assert pt.resolve(88, 16838841) == "VUP"


def test_collision_keys_by_button_code():
    # Play/Pause and Back BOTH raise ACTION_NAV_BACK (92); the button code distinguishes them.
    assert pt.resolve(92, 61448) == "PAU"   # BackSpace -> Play/Pause
    assert pt.resolve(92, 61616) == "RET"   # dedicated Back button
    # Stop raises ACTION_NONE (0) -> matched by button code
    assert pt.resolve(0, 61575) == "STP"


def test_button_code_checked_before_action():
    # a mapped button code wins over the action-id map, so a collision key can't be mis-resolved
    assert pt.resolve(92, 61448) == "PAU"  # not RET, not None


def test_unknown_key_returns_none():
    assert pt.resolve(0, 0) is None
    assert pt.resolve(999, 4242) is None
    # the earlier (wrong) VK-derived button-code guesses must NOT map
    assert pt.resolve(0, 0xF000 | 0x7F) is None    # old Delete guess (61567)
    assert pt.resolve(0, 0xF000 | 0x13F) is None   # old Menu guess


def test_overrides_take_precedence():
    ov = pt.parse_overrides('{"%d": "STP"}' % 61448)
    assert pt.resolve(92, 61448, ov) == "STP"  # override remaps BackSpace to Stop


def test_parse_overrides_is_lenient():
    assert pt.parse_overrides("") == {}
    assert pt.parse_overrides(None) == {}
    assert pt.parse_overrides("not json") == {}
    assert pt.parse_overrides("[1,2,3]") == {}
    assert pt.parse_overrides('{"61616": "RET"}') == {61616: "RET"}
    # null / numeric / empty override VALUES are skipped (no bogus forwarded key)
    assert pt.parse_overrides('{"61616": null, "100": 5, "200": ""}') == {}
    assert pt.parse_overrides('{"61616": " RET ", "9": null}') == {61616: "RET"}


def test_parse_ignore_codes():
    assert pt.parse_ignore_codes("") == set()
    assert pt.parse_ignore_codes(None) == set()
    assert pt.parse_ignore_codes("61625,61624") == {61625, 61624}
    assert pt.parse_ignore_codes(" 61625 , 61624 ") == {61625, 61624}
    assert pt.parse_ignore_codes("61625;61624") == {61625, 61624}
    assert pt.parse_ignore_codes("61625,junk,61624") == {61625, 61624}   # skip non-numeric


def test_maps_are_consistent():
    for code in list(pt.CODE_BY_ACTION.values()) + list(pt.CODE_BY_BUTTONCODE.values()):
        assert 2 <= len(code) <= 3 and code.isupper()


# --- arm/disarm transition (unchanged) ---

def test_arm_decision_arms_on_active():
    assert pt.arm_decision(False, "playing", 0, 2) == (True, 0)
    assert pt.arm_decision(False, "paused", 3, 2) == (True, 0)
    assert pt.arm_decision(True, "playing", 5, 2) == (True, 0)


def test_arm_decision_disarms_only_after_debounce():
    assert pt.arm_decision(True, "idle", 0, 2) == (True, 1)
    assert pt.arm_decision(True, "idle", 1, 2) == (False, 2)
    assert pt.arm_decision(True, "down", 0, 2) == (True, 1)
    assert pt.arm_decision(True, "unknown", 1, 2) == (False, 2)


def test_arm_decision_stays_disarmed_when_never_armed():
    assert pt.arm_decision(False, "idle", 0, 2) == (False, 1)
    assert pt.arm_decision(False, "down", 5, 2) == (False, 6)


def test_arm_decision_needed_floor():
    assert pt.arm_decision(True, "idle", 0, 0) == (False, 1)


def test_config_defaults_are_off_and_present():
    from resources.lib.config import Config

    c = Config()
    assert c.remote_passthrough_enabled is False
    assert c.passthrough_key_overrides == ""
    assert c.passthrough_poll_interval == 4.0
    c2 = Config.from_dict({"remote_passthrough_enabled": True, "passthrough_poll_interval": 6.0})
    assert c2.remote_passthrough_enabled is True and c2.passthrough_poll_interval == 6.0

"""The TV-switch selector: make_switcher dispatch (default cec = zero regression) and CecSwitcher
reproducing the orchestrator's previous grab/reclaim gating exactly."""
from resources.lib import tvswitch
from resources.lib.config import Config
from resources.lib.ir_lirc import LircSwitcher
from resources.lib.ir_zjiot import ZjiotSwitcher


def test_make_switcher_dispatch():
    assert isinstance(tvswitch.make_switcher(Config(tv_switch_method="none")), tvswitch.NullSwitcher)
    assert isinstance(tvswitch.make_switcher(Config(tv_switch_method="cec"), object()), tvswitch.CecSwitcher)
    assert isinstance(tvswitch.make_switcher(Config(tv_switch_method="ir")), ZjiotSwitcher)
    assert isinstance(tvswitch.make_switcher(Config(tv_switch_method="lirc")), LircSwitcher)


def test_make_switcher_defaults_to_cec_for_old_or_unknown_config():
    # a Config with no tv_switch_method set (old runtime_config.json) and an unknown value both fall
    # back to cec -- the zero-regression default.
    assert isinstance(tvswitch.make_switcher(Config(), object()), tvswitch.CecSwitcher)
    assert isinstance(tvswitch.make_switcher(Config(tv_switch_method="BOGUS"), object()), tvswitch.CecSwitcher)


def test_null_switcher_is_noop():
    n = tvswitch.NullSwitcher()
    assert n.to_oppo() is False and n.to_kodi() is False


def test_cec_switcher_reproduces_grab_and_reclaim(monkeypatch):
    order = []
    monkeypatch.setattr(tvswitch.cec, "grab_oppo", lambda c: order.append("grab") or True)
    monkeypatch.setattr(tvswitch.cec, "reclaim_kodi", lambda c: order.append("reclaim") or True)
    sw = tvswitch.CecSwitcher(
        Config(oppo_model="M9205", grab_tv_on_play=True, cec_reclaim_on_stop=True), object())
    assert sw.to_oppo() is True
    assert sw.to_kodi() is True
    assert order == ["grab", "reclaim"]


def test_cec_switcher_skips_grab_on_m9207(monkeypatch):
    order = []
    monkeypatch.setattr(tvswitch.cec, "grab_oppo", lambda c: order.append("grab") or True)
    sw = tvswitch.CecSwitcher(Config(oppo_model="M9207", grab_tv_on_play=True), object())
    assert sw.to_oppo() is False  # M9207 has no network grab -- skipped, wedge avoided
    assert order == []


def test_cec_switcher_respects_toggles(monkeypatch):
    order = []
    monkeypatch.setattr(tvswitch.cec, "grab_oppo", lambda c: order.append("grab") or True)
    monkeypatch.setattr(tvswitch.cec, "reclaim_kodi", lambda c: order.append("reclaim") or True)
    sw = tvswitch.CecSwitcher(Config(grab_tv_on_play=False, cec_reclaim_on_stop=False), object())
    assert sw.to_oppo() is False and sw.to_kodi() is False
    assert order == []

"""The orchestrator wires the flow: detect -> grab -> play -> watch -> reclaim. The reclaim fires once
in a finally (success OR failure); non-disc targets do nothing; never re-asserts."""
from resources.lib import orchestrator
from resources.lib.config import Config


def _wire(monkeypatch, order):
    monkeypatch.setattr(orchestrator, "OppoClient", lambda cfg: object())
    # default tv_switch_method='cec' -> CecSwitcher, which calls cec.grab_oppo/reclaim_kodi.
    monkeypatch.setattr(orchestrator.tvswitch.cec, "grab_oppo", lambda c: order.append("grab") or True)
    monkeypatch.setattr(orchestrator.tvswitch.cec, "reclaim_kodi", lambda c: order.append("reclaim") or True)
    monkeypatch.setattr(orchestrator.monitor, "watch_playback", lambda *a, **k: order.append("watch") or True)


def test_run_skips_non_disc_targets(monkeypatch):
    order = []
    _wire(monkeypatch, order)
    monkeypatch.setattr(orchestrator.handoff, "play", lambda *a, **k: order.append("play") or True)
    assert orchestrator.run(Config(oppo_ip="x"), "02TV/Show/S01E01.mkv") is False
    assert order == []  # not a disc -> nothing fires, no grab, no reclaim


def test_run_full_flow_in_order(monkeypatch):
    order = []
    _wire(monkeypatch, order)
    monkeypatch.setattr(orchestrator.handoff, "play", lambda *a, **k: order.append("play") or True)
    cfg = Config(oppo_ip="x", grab_tv_on_play=True, cec_reclaim_on_stop=True,
                 path_from="nfs://h/s", path_to="srv")
    assert orchestrator.run(cfg, "nfs://h/s/01Movies/Dune (2021).iso") is True
    assert order == ["grab", "play", "watch", "reclaim"]


def test_run_reclaims_even_when_play_fails(monkeypatch):
    order = []
    _wire(monkeypatch, order)
    monkeypatch.setattr(orchestrator.handoff, "play", lambda *a, **k: order.append("play") or False)
    cfg = Config(oppo_ip="x", path_from="nfs://h/s", path_to="srv")
    assert orchestrator.run(cfg, "nfs://h/s/x.iso") is False
    assert "reclaim" in order and "watch" not in order  # reclaim fires in finally; watch skipped


def test_run_skips_grab_on_m9207_even_when_enabled(monkeypatch):
    # M9207 hard-disables the OPPO CEC grab regardless of grab_tv_on_play (its power-cycle is a no-op
    # that wedges the unit). play/watch/reclaim still run; the TV is switched to the OPPO manually.
    order = []
    _wire(monkeypatch, order)
    monkeypatch.setattr(orchestrator.handoff, "play", lambda *a, **k: order.append("play") or True)
    cfg = Config(oppo_ip="x", grab_tv_on_play=True, oppo_model="M9207", cec_reclaim_on_stop=True,
                 path_from="nfs://h/s", path_to="srv")
    assert orchestrator.run(cfg, "nfs://h/s/01Movies/Dune (2021).iso") is True
    assert "grab" not in order
    assert order == ["play", "watch", "reclaim"]


def test_run_grabs_on_m9205_when_enabled(monkeypatch):
    # the grab-capable model still grabs when grab_tv_on_play is on.
    order = []
    _wire(monkeypatch, order)
    monkeypatch.setattr(orchestrator.handoff, "play", lambda *a, **k: order.append("play") or True)
    cfg = Config(oppo_ip="x", grab_tv_on_play=True, oppo_model="M9205",
                 path_from="nfs://h/s", path_to="srv")
    assert orchestrator.run(cfg, "nfs://h/s/x.iso") is True
    assert order == ["grab", "play", "watch", "reclaim"]


def test_run_no_grab_when_disabled(monkeypatch):
    order = []
    _wire(monkeypatch, order)
    monkeypatch.setattr(orchestrator.handoff, "play", lambda *a, **k: order.append("play") or True)
    cfg = Config(oppo_ip="x", grab_tv_on_play=False, cec_reclaim_on_stop=False,
                 path_from="nfs://h/s", path_to="srv")
    assert orchestrator.run(cfg, "nfs://h/s/x.iso") is True
    assert "grab" not in order and "reclaim" not in order


def test_run_none_method_does_no_tv_switch(monkeypatch):
    # tv_switch_method='none' -> NullSwitcher: play/watch still run, but no grab and no reclaim.
    order = []
    _wire(monkeypatch, order)
    monkeypatch.setattr(orchestrator.handoff, "play", lambda *a, **k: order.append("play") or True)
    cfg = Config(oppo_ip="x", tv_switch_method="none", path_from="nfs://h/s", path_to="srv")
    assert orchestrator.run(cfg, "nfs://h/s/x.iso") is True
    assert order == ["play", "watch"]


def test_run_stops_the_oppo_on_giveup(monkeypatch):
    # #28: playback never confirmed (watch returns False) -> a best-effort STOP so a late-starting play
    # isn't left in flight, THEN reclaim. stop() must precede the reclaim; both run in the finally.
    order = []

    class _Client:
        def __init__(self, cfg):
            pass

        def stop(self):
            order.append("stop")

    monkeypatch.setattr(orchestrator, "OppoClient", _Client)
    monkeypatch.setattr(orchestrator.tvswitch.cec, "grab_oppo", lambda c: order.append("grab") or True)
    monkeypatch.setattr(orchestrator.tvswitch.cec, "reclaim_kodi", lambda c: order.append("reclaim") or True)
    monkeypatch.setattr(orchestrator.handoff, "play", lambda *a, **k: order.append("play") or True)
    monkeypatch.setattr(orchestrator.monitor, "watch_playback", lambda *a, **k: order.append("watch") or False)
    cfg = Config(oppo_ip="x", path_from="nfs://h/s", path_to="srv")
    assert orchestrator.run(cfg, "nfs://h/s/x.iso") is False
    assert order == ["grab", "play", "watch", "stop", "reclaim"]


def test_run_no_giveup_stop_when_playback_confirmed(monkeypatch):
    # the STOP is give-up-only: when playback IS observed (watch True), no STOP is sent (single-shot).
    order = []

    class _Client:
        def __init__(self, cfg):
            pass

        def stop(self):
            order.append("stop")

    monkeypatch.setattr(orchestrator, "OppoClient", _Client)
    monkeypatch.setattr(orchestrator.tvswitch.cec, "grab_oppo", lambda c: order.append("grab") or True)
    monkeypatch.setattr(orchestrator.tvswitch.cec, "reclaim_kodi", lambda c: order.append("reclaim") or True)
    monkeypatch.setattr(orchestrator.handoff, "play", lambda *a, **k: order.append("play") or True)
    monkeypatch.setattr(orchestrator.monitor, "watch_playback", lambda *a, **k: order.append("watch") or True)
    cfg = Config(oppo_ip="x", path_from="nfs://h/s", path_to="srv")
    assert orchestrator.run(cfg, "nfs://h/s/x.iso") is True
    assert order == ["grab", "play", "watch", "reclaim"]  # no "stop"


def test_run_no_giveup_stop_when_play_never_accepted(monkeypatch):
    # #28 audit refinement: handoff.play returns False (unmappable / mount-abort / wake-fail) -> nothing
    # was ever put in flight, so NO give-up STOP is sent (avoids a wasted round-trip to a maybe-asleep
    # OPPO). The STOP is gated on play_accepted, not merely `not started`.
    order = []

    class _Client:
        def __init__(self, cfg):
            pass

        def stop(self):
            order.append("stop")

    monkeypatch.setattr(orchestrator, "OppoClient", _Client)
    monkeypatch.setattr(orchestrator.tvswitch.cec, "grab_oppo", lambda c: order.append("grab") or True)
    monkeypatch.setattr(orchestrator.tvswitch.cec, "reclaim_kodi", lambda c: order.append("reclaim") or True)
    monkeypatch.setattr(orchestrator.handoff, "play", lambda *a, **k: order.append("play") or False)
    monkeypatch.setattr(orchestrator.monitor, "watch_playback", lambda *a, **k: order.append("watch") or True)
    cfg = Config(oppo_ip="x", path_from="nfs://h/s", path_to="srv")
    assert orchestrator.run(cfg, "nfs://h/s/x.iso") is False
    assert order == ["grab", "play", "reclaim"]  # no "stop", no "watch"


def test_run_skips_when_no_oppo_configured(monkeypatch):
    order = []
    _wire(monkeypatch, order)
    monkeypatch.setattr(orchestrator.handoff, "play", lambda *a, **k: order.append("play") or True)
    # a disc target but an empty config (oppo_ip="") -> not configured -> nothing fires
    cfg = Config(path_from="nfs://h/s", path_to="srv")
    assert orchestrator.run(cfg, "nfs://h/s/x.iso") is False
    assert order == []

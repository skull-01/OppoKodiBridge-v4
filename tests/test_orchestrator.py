"""The orchestrator wires the flow: detect -> grab -> play -> watch -> reclaim. The reclaim fires once
in a finally (success OR failure); non-disc targets do nothing; never re-asserts."""
from resources.lib import orchestrator
from resources.lib.config import Config


def _wire(monkeypatch, order):
    monkeypatch.setattr(orchestrator, "OppoClient", lambda cfg: object())
    monkeypatch.setattr(orchestrator.cec, "grab_oppo", lambda c: order.append("grab"))
    monkeypatch.setattr(orchestrator.cec, "reclaim_kodi", lambda c: order.append("reclaim"))
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


def test_run_no_grab_when_disabled(monkeypatch):
    order = []
    _wire(monkeypatch, order)
    monkeypatch.setattr(orchestrator.handoff, "play", lambda *a, **k: order.append("play") or True)
    cfg = Config(oppo_ip="x", grab_tv_on_play=False, cec_reclaim_on_stop=False,
                 path_from="nfs://h/s", path_to="srv")
    assert orchestrator.run(cfg, "nfs://h/s/x.iso") is True
    assert "grab" not in order and "reclaim" not in order


def test_run_skips_when_no_oppo_configured(monkeypatch):
    order = []
    _wire(monkeypatch, order)
    monkeypatch.setattr(orchestrator.handoff, "play", lambda *a, **k: order.append("play") or True)
    # a disc target but an empty config (oppo_ip="") -> not configured -> nothing fires
    cfg = Config(path_from="nfs://h/s", path_to="srv")
    assert orchestrator.run(cfg, "nfs://h/s/x.iso") is False
    assert order == []

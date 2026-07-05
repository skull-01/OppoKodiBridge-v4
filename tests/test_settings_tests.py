"""The interactive settings tests. cmd_cec must NOT gate a serial-control user on the network :23
port (irrelevant in serial mode), while still gating network-mode users on it."""
from resources.lib.config import Config

import settings_tests as st


class _Dialog:
    def __init__(self, yesno=True):
        self._yesno = yesno
        self.oks = []

    def ok(self, *a):
        self.oks.append(a)

    def yesno(self, *a):
        return self._yesno


def _patch_cec(monkeypatch):
    calls = {"grab": 0, "reclaim": 0}
    monkeypatch.setattr(st.cec, "grab_oppo", lambda client: calls.__setitem__("grab", calls["grab"] + 1))
    monkeypatch.setattr(st.cec, "reclaim_kodi", lambda cfg: calls.__setitem__("reclaim", calls["reclaim"] + 1))
    return calls


def test_cmd_cec_serial_not_blocked_by_closed_23(monkeypatch):
    # b8: a serial-control user whose :23 is closed must still be able to run the guided CEC test.
    calls = _patch_cec(monkeypatch)
    monkeypatch.setattr(st, "_tcp_open", lambda host, port, timeout=4.0: False)  # :23 closed
    st.cmd_cec(Config(oppo_ip="1.2.3.4", serial_control=True), _Dialog(yesno=True))
    assert calls["grab"] == 1


def test_cmd_cec_network_still_gated_on_23(monkeypatch):
    # the network-mode guard is preserved: a closed :23 short-circuits before the grab.
    calls = _patch_cec(monkeypatch)
    monkeypatch.setattr(st, "_tcp_open", lambda host, port, timeout=4.0: False)
    st.cmd_cec(Config(oppo_ip="1.2.3.4", serial_control=False), _Dialog(yesno=True))
    assert calls["grab"] == 0


def test_cmd_cec_m9207_skips_grab(monkeypatch):
    # #20: the M9207 power-cycle wedges the unit -> the guided CEC test must skip the grab ENTIRELY and
    # explain that the input is switched manually (mirrors the orchestrator's cec.grab_supported gate).
    calls = _patch_cec(monkeypatch)
    dlg = _Dialog(yesno=True)
    st.cmd_cec(Config(oppo_ip="1.2.3.4", oppo_model="M9207"), dlg)
    assert calls["grab"] == 0 and calls["reclaim"] == 0
    assert dlg.oks  # an explanatory dialog was shown instead of power-cycling the box


class _SelDialog(_Dialog):
    def __init__(self, select_idx=0, yesno=True):
        super().__init__(yesno=yesno)
        self._sel = select_idx
        self.selects = []

    def select(self, title, options):
        self.selects.append((title, list(options)))
        return self._sel


class _FakeAddon:
    def __init__(self):
        self.written = {}

    def setSettingString(self, key, value):
        self.written[key] = value


def _fake_client(monkeypatch, *, reachable=True, info=None, raises=None):
    captured = {"wake": 0, "attempts": None, "interval": None}

    class _C:
        def __init__(self, cfg):
            pass

        def wake_and_wait(self, attempts=18, interval=3.0):
            captured["wake"] += 1
            captured["attempts"] = attempts
            captured["interval"] = interval
            return reachable

        def get_global_info(self):
            if raises is not None:
                raise raises
            return {} if info is None else info

    monkeypatch.setattr(st, "OppoClient", _C)
    return captured


# --- #29: cmd_ping must OREMOTE-wake the sleeping :436 API before probing ----------------------------

def test_cmd_ping_wakes_the_oppo_before_probing(monkeypatch):
    captured = _fake_client(monkeypatch, reachable=True)
    monkeypatch.setattr(st, "_tcp_open", lambda host, port, timeout=4.0: True)
    dlg = _Dialog()
    st.cmd_ping(Config(oppo_ip="1.2.3.4"), dlg)
    assert captured["wake"] == 1                       # woke rather than raw-probing the sleeping API
    # pin the bounded wake timing (~19s worst case) so an accidental bump to a multi-minute stall fails CI
    assert captured["attempts"] == 4 and captured["interval"] == 1.0
    assert "HTTP API :436  ->  OK" in dlg.oks[-1][1]


def test_cmd_ping_reports_unreachable_when_wake_fails(monkeypatch):
    _fake_client(monkeypatch, reachable=False)
    monkeypatch.setattr(st, "_tcp_open", lambda host, port, timeout=4.0: False)
    dlg = _Dialog()
    st.cmd_ping(Config(oppo_ip="1.2.3.4"), dlg)
    assert "HTTP API :436  ->  UNREACHABLE" in dlg.oks[-1][1]


# --- #10: detect path_from from Kodi's own video sources, on demand ----------------------------------

def test_cmd_detectpath_writes_chosen_source_to_path_from(monkeypatch):
    monkeypatch.setattr(st.cec, "kodi_video_sources", lambda cfg: ["nfs://h/share/", "nfs://h/other"])
    addon = _FakeAddon()
    monkeypatch.setattr(st, "_addon", lambda: addon)
    dlg = _SelDialog(select_idx=0)
    st.cmd_detectpath(Config(oppo_ip="x"), dlg)
    assert addon.written["path_from"] == "nfs://h/share"   # chosen root, trailing slash stripped
    assert dlg.oks


def test_cmd_detectpath_no_sources_explains_and_writes_nothing(monkeypatch):
    monkeypatch.setattr(st.cec, "kodi_video_sources", lambda cfg: [])
    addon = _FakeAddon()
    monkeypatch.setattr(st, "_addon", lambda: addon)
    dlg = _SelDialog()
    st.cmd_detectpath(Config(oppo_ip="x"), dlg)
    assert "path_from" not in addon.written
    assert dlg.oks


def test_cmd_detectpath_cancel_writes_nothing(monkeypatch):
    monkeypatch.setattr(st.cec, "kodi_video_sources", lambda cfg: ["nfs://h/share"])
    addon = _FakeAddon()
    monkeypatch.setattr(st, "_addon", lambda: addon)
    st.cmd_detectpath(Config(oppo_ip="x"), _SelDialog(select_idx=-1))  # user cancelled the picker
    assert "path_from" not in addon.written


# --- #12 / #13: ISO / BDMV playback capability checks (report all flags) -----------------------------

def test_cmd_playback_reports_every_active_flag(monkeypatch):
    cap = _fake_client(monkeypatch, reachable=True, info={"is_bdmv_playing": True, "is_disc_playing": True})
    dlg = _Dialog(yesno=True)
    st.cmd_playback(Config(oppo_ip="x"), dlg, "bdmv")
    msg = dlg.oks[-1][1]
    assert "is_bdmv_playing" in msg and "is_disc_playing" in msg
    assert cap["attempts"] == 4 and cap["interval"] == 1.0   # same bounded wake as cmd_ping (#29)


def test_cmd_playback_reports_status_only_playback(monkeypatch):
    # #12/#13 audit: firmware that signals playback via a status token ONLY (no booleans) must still be
    # reported as playing -- honour info_is_playing so the check can't disagree with the stop-monitor.
    _fake_client(monkeypatch, reachable=True, info={"status": "PLAY"})
    dlg = _Dialog(yesno=True)
    st.cmd_playback(Config(oppo_ip="x"), dlg, "iso")
    assert "reports playback" in dlg.oks[-1][1]


def test_cmd_playback_read_error_shows_dialog(monkeypatch):
    # the OppoError branch: a mid-read transport failure shows a dialog, never crashes or hangs.
    from resources.lib.oppo_http import OppoError

    _fake_client(monkeypatch, reachable=True, raises=OppoError("boom"))
    dlg = _Dialog(yesno=True)
    st.cmd_playback(Config(oppo_ip="x"), dlg, "iso")
    assert "Couldn't read the OPPO" in dlg.oks[-1][1]


def test_cmd_playback_reports_no_playback(monkeypatch):
    _fake_client(monkeypatch, reachable=True, info={"is_video_playing": False})
    dlg = _Dialog(yesno=True)
    st.cmd_playback(Config(oppo_ip="x"), dlg, "iso")
    assert "did NOT report playback" in dlg.oks[-1][1]


def test_cmd_playback_unreachable_tells_to_ping(monkeypatch):
    _fake_client(monkeypatch, reachable=False)
    dlg = _Dialog(yesno=True)
    st.cmd_playback(Config(oppo_ip="x"), dlg, "iso")
    assert "Run Ping first" in dlg.oks[-1][1]


def test_cmd_playback_declined_does_nothing(monkeypatch):
    _fake_client(monkeypatch, reachable=True, info={"is_video_playing": True})
    dlg = _Dialog(yesno=False)   # operator declines the "start playback now?" prompt
    st.cmd_playback(Config(oppo_ip="x"), dlg, "iso")
    assert dlg.oks == []          # bailed before reading playback

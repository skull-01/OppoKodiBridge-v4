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

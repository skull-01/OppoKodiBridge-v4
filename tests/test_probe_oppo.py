"""#36: probe_oppo.py must drive the CURRENT OppoClient API (it used to call activate()/wake/_get(query=)
which never existed and crashed on first use). This smoke test runs main() against a fake client whose
methods mirror the real OppoClient surface, so any API drift (a missing/renamed method or bad kwarg)
fails here instead of at runtime on the OPPO."""
import probe_oppo


class _FakeClient:
    """Mirrors exactly the OppoClient surface probe_oppo touches."""

    def __init__(self, cfg):
        self.cfg = cfg

    def wake_and_wait(self):
        return True

    def get_firmware_version(self):
        return "v1"

    def get_setup_menu(self):
        return ""

    def signin(self, app_ip):
        return ""

    def get_global_info(self):
        return {"is_playing": False}

    def _get_json(self, endpoint, timeout=None):
        return {"devicelist": [{"sub_type": "nfs", "name": "192.168.10.20"}]}

    def _get(self, endpoint, timeout=None, retries=0):
        assert endpoint.startswith("/getfilelist?")  # querystring folded into the endpoint, not query=
        return "{\"files\": []}"


def test_probe_main_uses_current_oppoclient_api(monkeypatch, capsys):
    monkeypatch.setattr(probe_oppo, "OppoClient", _FakeClient)
    monkeypatch.setattr(probe_oppo, "local_ip_toward", lambda ip, port: "127.0.0.1")
    rc = probe_oppo.main(["probe_oppo.py", "192.168.10.5", "/mnt"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "getglobalinfo" in out and "getdevicelist" in out and "getfilelist" in out


def test_probe_main_usage_without_ip():
    assert probe_oppo.main(["probe_oppo.py"]) == 2  # usage guard, no crash

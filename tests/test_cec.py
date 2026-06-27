"""The cec module: grab_oppo power-cycles the OPPO; reclaim_kodi posts the right localhost JSON-RPC to
trigger script.cecreclaim. Single-shot, never-reassert (the orchestrator calls each once)."""
import json

from resources.lib import cec
from resources.lib.config import Config


class _FakeClient:
    def __init__(self):
        self.cycled = 0

    def power_cycle(self):
        self.cycled += 1


def test_grab_oppo_power_cycles():
    client = _FakeClient()
    assert cec.grab_oppo(client) is True
    assert client.cycled == 1


def test_grab_supported_is_model_gated():
    # M9205 (and anything that is not M9207) can grab the TV via the network power-cycle.
    assert cec.grab_supported(Config(oppo_model="M9205")) is True
    assert cec.grab_supported(Config(oppo_model="m9205")) is True  # case-insensitive
    assert cec.grab_supported(Config()) is True                    # default model = M9205
    # M9207 Plus / UDP-203: no network grab (its #PON is a no-op + the #POF sleep wedges the unit).
    assert cec.grab_supported(Config(oppo_model="M9207")) is False
    assert cec.grab_supported(Config(oppo_model=" m9207 ")) is False


def test_grab_oppo_nonfatal_on_error():
    class Boom:
        def power_cycle(self):
            raise cec.OppoError("nope")

    assert cec.grab_oppo(Boom()) is False


class _Resp:
    def __init__(self, body):
        self._b = body.encode()

    def read(self):
        return self._b

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_reclaim_kodi_posts_executeaddon(monkeypatch):
    cap = {}

    def fake_urlopen(req, timeout=None):
        cap["url"] = req.full_url
        cap["data"] = json.loads(req.data.decode())
        return _Resp(json.dumps({"jsonrpc": "2.0", "id": 1, "result": "OK"}))

    monkeypatch.setattr(cec.urllib.request, "urlopen", fake_urlopen)
    assert cec.reclaim_kodi(Config(oppo_ip="x", kodi_rpc_port=8080)) is True
    assert cap["url"] == "http://127.0.0.1:8080/jsonrpc"
    assert cap["data"]["method"] == "Addons.ExecuteAddon"
    assert cap["data"]["params"]["addonid"] == "script.cecreclaim"


def test_reclaim_kodi_false_on_unreachable(monkeypatch):
    def boom(req, timeout=None):
        raise OSError("no route to Kodi")

    monkeypatch.setattr(cec.urllib.request, "urlopen", boom)
    assert cec.reclaim_kodi(Config(oppo_ip="x")) is False


def test_reclaim_kodi_false_on_rpc_error(monkeypatch):
    monkeypatch.setattr(
        cec.urllib.request, "urlopen",
        lambda req, timeout=None: _Resp(json.dumps({"jsonrpc": "2.0", "id": 1, "error": {"message": "no"}})),
    )
    assert cec.reclaim_kodi(Config(oppo_ip="x")) is False


def test_reclaim_kodi_false_on_non_json_body(monkeypatch):
    # a misconfigured Kodi web server can return HTTP 200 with an HTML login/error page; json.loads
    # raises a ValueError, which must be caught -> False, never propagate (would abort the settings test).
    monkeypatch.setattr(
        cec.urllib.request, "urlopen",
        lambda req, timeout=None: _Resp("<html>401 Unauthorized</html>"),
    )
    assert cec.reclaim_kodi(Config(oppo_ip="x")) is False


def test_grab_oppo_nonfatal_on_non_oppoerror():
    # the serial transport can surface non-OppoError types; grab runs before the orchestrator's
    # try/finally, so any escape would skip the reclaim. grab_oppo must absorb them all.
    class Boom:
        def power_cycle(self):
            raise ImportError("No module named termios")

    assert cec.grab_oppo(Boom()) is False


# --- kodi_video_sources: read Kodi's video source roots for path_from auto-detection (#9) ----------

def test_kodi_video_sources_posts_getsources_and_returns_roots(monkeypatch):
    cap = {}

    def fake_urlopen(req, timeout=None):
        cap["url"] = req.full_url
        cap["data"] = json.loads(req.data.decode())
        body = {"jsonrpc": "2.0", "id": 1, "result": {"sources": [
            {"file": "nfs://192.168.1.177/share/", "label": "Movies"},
            {"file": "smb://nas/media/", "label": "TV"},
        ]}}
        return _Resp(json.dumps(body))

    monkeypatch.setattr(cec.urllib.request, "urlopen", fake_urlopen)
    roots = cec.kodi_video_sources(Config(oppo_ip="x", kodi_rpc_port=8080))
    assert cap["url"] == "http://127.0.0.1:8080/jsonrpc"
    assert cap["data"]["method"] == "Files.GetSources"
    assert cap["data"]["params"] == {"media": "video"}
    assert roots == ["nfs://192.168.1.177/share/", "smb://nas/media/"]


def test_kodi_video_sources_expands_multipath(monkeypatch):
    import urllib.parse as up

    a = up.quote("nfs://h/A/", safe="")
    b = up.quote("nfs://h/B/", safe="")
    body = {"result": {"sources": [{"file": "multipath://" + a + "/" + b + "/", "label": "Both"}]}}
    monkeypatch.setattr(cec.urllib.request, "urlopen",
                        lambda req, timeout=None: _Resp(json.dumps(body)))
    assert cec.kodi_video_sources(Config(oppo_ip="x")) == ["nfs://h/A/", "nfs://h/B/"]


def test_kodi_video_sources_empty_on_unreachable(monkeypatch):
    def boom(req, timeout=None):
        raise OSError("no route to Kodi")

    monkeypatch.setattr(cec.urllib.request, "urlopen", boom)
    assert cec.kodi_video_sources(Config(oppo_ip="x")) == []


def test_kodi_video_sources_empty_on_odd_shape(monkeypatch):
    # a result without a 'sources' list, a non-dict result, or an error reply -> [] (best-effort)
    for body in ({"result": {}}, {"result": {"sources": "nope"}}, {"result": None},
                 {"error": {"message": "x"}}):
        monkeypatch.setattr(cec.urllib.request, "urlopen",
                            lambda req, timeout=None, b=body: _Resp(json.dumps(b)))
        assert cec.kodi_video_sources(Config(oppo_ip="x")) == []


def test_kodi_video_sources_skips_items_without_a_file(monkeypatch):
    body = {"result": {"sources": [{"label": "no file"}, {"file": ""}, {"file": "nfs://h/ok/"}, "junk"]}}
    monkeypatch.setattr(cec.urllib.request, "urlopen",
                        lambda req, timeout=None: _Resp(json.dumps(body)))
    assert cec.kodi_video_sources(Config(oppo_ip="x")) == ["nfs://h/ok/"]


def test_kodi_video_sources_empty_on_non_json(monkeypatch):
    monkeypatch.setattr(cec.urllib.request, "urlopen",
                        lambda req, timeout=None: _Resp("<html>401</html>"))
    assert cec.kodi_video_sources(Config(oppo_ip="x")) == []

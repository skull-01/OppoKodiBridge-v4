"""handoff.play() returns False when the OPPO play call itself fails (no reply) -- so the orchestrator
does not poll for playback that will never start (~grace*interval seconds) -- and when the path can't
be mapped. Returns True only when the OPPO accepted the file."""
from resources.lib import handoff
from resources.lib.config import Config


class _FakeClient:
    def __init__(self, play_reply):
        self._reply = play_reply

    def wake_and_wait(self):
        return True

    def get_firmware_version(self):
        return ""

    def get_setup_menu(self):
        return ""

    def signin(self, app_ip):
        return ""

    def get_global_info(self):
        return {}

    def get_device_list(self):
        return {"devicelist": [{"sub_type": "nfs", "name": "192.168.10.20"}]}

    def get_nfs_share_list(self):
        return ""

    def login_nfs(self, server):
        return {}

    def mount_nfs(self, server, folder):
        return {}

    def play_file(self, server, name):
        return self._reply

    def play_bdmv(self, name):
        return self._reply

    def stop(self):
        return {}


def _cfg():
    return Config(oppo_ip="x", path_from="nfs://h/s", path_to="srv")


def test_play_returns_false_when_play_call_failed(monkeypatch):
    monkeypatch.setattr(handoff, "interruptible_sleep", lambda *a, **k: None)
    # None reply == the play HTTP call raised (caught by _best_effort) -> not "accepted"
    assert handoff.play(_cfg(), _FakeClient(play_reply=None), "nfs://h/s/01Movies/x.iso") is False


def test_play_returns_true_on_accepted(monkeypatch):
    monkeypatch.setattr(handoff, "interruptible_sleep", lambda *a, **k: None)
    assert handoff.play(_cfg(), _FakeClient(play_reply={"success": True}), "nfs://h/s/01Movies/x.iso") is True


def test_play_returns_false_when_unmappable(monkeypatch):
    monkeypatch.setattr(handoff, "interruptible_sleep", lambda *a, **k: None)
    # path_from doesn't match -> can't map -> False before the client is ever used
    assert handoff.play(_cfg(), _FakeClient(play_reply={"success": True}), "nfs://other/x.iso") is False


class _RecordingClient(_FakeClient):
    def __init__(self, play_reply=None):
        super().__init__(play_reply if play_reply is not None else {"success": True})
        self.calls = []

    def mount_nfs(self, server, folder):
        self.calls.append(("mount", folder))
        return {}

    def play_file(self, server, name):
        self.calls.append(("play_file", name))
        return self._reply

    def play_bdmv(self, name):
        self.calls.append(("play_bdmv", name))
        return self._reply


def test_play_trailing_slash_disc_folder_routes_to_bdmv(monkeypatch):
    monkeypatch.setattr(handoff, "interruptible_sleep", lambda *a, **k: None)
    client = _RecordingClient()
    assert handoff.play(_cfg(), client, "nfs://h/s/Movies/Dune/VIDEO_TS/") is True
    assert ("play_bdmv", "Dune") in client.calls       # the disc folder, not the bare basename
    assert ("mount", "srv/Movies") in client.calls      # mounts the disc folder's parent


def test_play_iso_under_a_bdmv_dir_uses_the_file_branch(monkeypatch):
    monkeypatch.setattr(handoff, "interruptible_sleep", lambda *a, **k: None)
    client = _RecordingClient()
    assert handoff.play(_cfg(), client, "nfs://h/s/Movies/BDMV/backup.iso") is True
    assert ("play_file", "backup.iso") in client.calls
    assert not any(kind == "play_bdmv" for kind, _ in client.calls)


def _cfg_model(model):
    return Config(oppo_ip="x", path_from="nfs://h/s", path_to="srv/nfs/media", oppo_model=model)


def test_play_m9205_mounts_file_folder_and_plays_bare_name(monkeypatch):
    # default M9205: mount the file's folder, play the bare leaf name (/mnt/nfs1/<leaf>).
    monkeypatch.setattr(handoff, "interruptible_sleep", lambda *a, **k: None)
    client = _RecordingClient()
    assert handoff.play(_cfg_model("M9205"), client, "nfs://h/s/06pr0n/clip.mp4") is True
    assert ("mount", "srv/nfs/media/06pr0n") in client.calls
    assert ("play_file", "clip.mp4") in client.calls


def test_play_m9207_mounts_export_root_and_plays_subpath(monkeypatch):
    # M9207 Plus / UDP-203: mount the export ROOT, play the full sub-path (hardware-verified).
    monkeypatch.setattr(handoff, "interruptible_sleep", lambda *a, **k: None)
    client = _RecordingClient()
    assert handoff.play(_cfg_model("M9207"), client, "nfs://h/s/06pr0n/clip.mp4") is True
    assert ("mount", "srv/nfs/media") in client.calls
    assert ("play_file", "06pr0n/clip.mp4") in client.calls


def test_play_m9207_disc_mounts_export_root_and_plays_subpath(monkeypatch):
    # the disc-folder case follows the same root-mount rule on the M9207 Plus.
    monkeypatch.setattr(handoff, "interruptible_sleep", lambda *a, **k: None)
    client = _RecordingClient()
    assert handoff.play(_cfg_model("M9207"), client, "nfs://h/s/Movies/Dune/BDMV/index.bdmv") is True
    assert ("mount", "srv/nfs/media") in client.calls
    assert ("play_bdmv", "Movies/Dune") in client.calls

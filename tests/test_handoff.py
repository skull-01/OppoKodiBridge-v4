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

    def stop(self):
        self.calls.append(("stop", None))
        return {}


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


def test_play_iso_sends_stp_then_play_file(monkeypatch):
    # Reference-aligned ISO: STP to clear prior playback, settle, then open the image -- and never
    # routes through play_bdmv.
    monkeypatch.setattr(handoff, "interruptible_sleep", lambda *a, **k: None)
    client = _RecordingClient()
    assert handoff.play(_cfg(), client, "nfs://h/s/Movies/Dune (2021).iso") is True
    assert ("stop", None) in client.calls
    assert ("play_file", "Dune (2021).iso") in client.calls
    assert client.calls.index(("stop", None)) < client.calls.index(("play_file", "Dune (2021).iso"))
    assert not any(kind == "play_bdmv" for kind, _ in client.calls)


def test_play_bdmv_sends_no_stp(monkeypatch):
    # Reference-aligned BDMV: checkfolderhasBDMV starts the disc directly -- no STP/settle ahead of it.
    monkeypatch.setattr(handoff, "interruptible_sleep", lambda *a, **k: None)
    client = _RecordingClient()  # default reply {"success": True}
    assert handoff.play(_cfg(), client, "nfs://h/s/Movies/Dune/BDMV/index.bdmv") is True
    assert ("play_bdmv", "Dune") in client.calls
    assert ("stop", None) not in client.calls
    assert not any(kind == "play_file" for kind, _ in client.calls)  # success -> no fallback


def test_play_bdmv_falls_back_to_play_file_on_failure(monkeypatch):
    # When checkfolderhasBDMV reports failure, fall back to /playnormalfile (reference behaviour).
    monkeypatch.setattr(handoff, "interruptible_sleep", lambda *a, **k: None)
    client = _RecordingClient(play_reply={"success": False})
    assert handoff.play(_cfg(), client, "nfs://h/s/Movies/Dune/BDMV/index.bdmv") is False
    assert ("play_bdmv", "Dune") in client.calls
    assert ("play_file", "Dune") in client.calls
    assert client.calls.index(("play_bdmv", "Dune")) < client.calls.index(("play_file", "Dune"))
    assert ("stop", None) not in client.calls


def test_play_returns_false_on_non_bool_failure(monkeypatch):
    # A firmware that rejects the file with a non-bool success (e.g. 0 / "false") must be honored as a
    # rejection -- previously `success is False` missed it and play() wrongly returned True (accepted),
    # leaving the monitor polling for playback that never starts.
    monkeypatch.setattr(handoff, "interruptible_sleep", lambda *a, **k: None)
    assert handoff.play(_cfg(), _FakeClient(play_reply={"success": 0}), "nfs://h/s/01Movies/x.iso") is False
    assert handoff.play(_cfg(), _FakeClient(play_reply={"success": "false"}), "nfs://h/s/01Movies/x.iso") is False


def test_play_bdmv_falls_back_on_non_bool_failure(monkeypatch):
    # checkfolderhasBDMV failure reported as a non-bool also triggers the play_file fallback.
    monkeypatch.setattr(handoff, "interruptible_sleep", lambda *a, **k: None)
    client = _RecordingClient(play_reply={"success": 0})
    assert handoff.play(_cfg(), client, "nfs://h/s/Movies/Dune/BDMV/index.bdmv") is False
    assert ("play_bdmv", "Dune") in client.calls
    assert ("play_file", "Dune") in client.calls  # fell back despite the non-bool failure


def test_play_loose_bdmv_mounts_containing_dir_not_the_file(monkeypatch):
    # A bare .bdmv NOT under a BDMV/ folder must mount the dir that CONTAINS it (a real folder) and
    # open that folder -- never NFS-mount the .bdmv FILE's own path, which hard-crashes the OPPO.
    monkeypatch.setattr(handoff, "interruptible_sleep", lambda *a, **k: None)
    client = _RecordingClient()
    assert handoff.play(_cfg(), client, "nfs://h/s/01Movies/Film/index.bdmv") is True
    assert ("mount", "srv/01Movies") in client.calls       # the disc folder's parent, a real folder
    assert ("play_bdmv", "Film") in client.calls           # opens the containing folder, not the file
    # the file path is NEVER mounted (the documented OPPO hard-crash)
    assert not any(kind == "mount" and folder.endswith("index.bdmv") for kind, folder in client.calls)
    assert not any(name == "" for kind, name in client.calls if kind in ("play_bdmv", "play_file"))


def _cfg_model(model):
    return Config(oppo_ip="x", path_from="nfs://h/s", path_to="srv/nfs/media", oppo_model=model)


def test_play_m9205_mounts_file_folder_and_plays_bare_name(monkeypatch):
    # default M9205: mount the file's folder, play the bare leaf name (/mnt/nfs1/<leaf>).
    monkeypatch.setattr(handoff, "interruptible_sleep", lambda *a, **k: None)
    client = _RecordingClient()
    assert handoff.play(_cfg_model("M9205"), client, "nfs://h/s/06pr0n/clip.mp4") is True
    assert ("mount", "srv/nfs/media/06pr0n") in client.calls
    assert ("play_file", "clip.mp4") in client.calls


def test_play_m9207_uses_same_layout_as_m9205(monkeypatch):
    # The M9207 NFS layout now matches the M9205 (mount the file's folder, play the bare leaf) -- the
    # earlier export-root/sub-path mode was dropped (unverified on hardware, and the platform won't
    # play sub-paths of a mount). oppo_model selects the stop-monitor transport and the TV grab now, not the
    # play path.
    monkeypatch.setattr(handoff, "interruptible_sleep", lambda *a, **k: None)
    client = _RecordingClient()
    assert handoff.play(_cfg_model("M9207"), client, "nfs://h/s/06pr0n/clip.mp4") is True
    assert ("mount", "srv/nfs/media/06pr0n") in client.calls
    assert ("play_file", "clip.mp4") in client.calls


def test_play_m9207_disc_uses_same_layout_as_m9205(monkeypatch):
    # the disc-folder case likewise mounts the parent and plays the bare disc-folder name on the M9207.
    monkeypatch.setattr(handoff, "interruptible_sleep", lambda *a, **k: None)
    client = _RecordingClient()
    assert handoff.play(_cfg_model("M9207"), client, "nfs://h/s/Movies/Dune/BDMV/index.bdmv") is True
    assert ("mount", "srv/nfs/media/Movies") in client.calls
    assert ("play_bdmv", "Dune") in client.calls


def test_oppo_model_does_not_affect_play_path(monkeypatch):
    # Invariant: oppo_model no longer changes the mount/play path -- M9205 and M9207 issue an identical
    # call sequence. The model selects the stop-monitor transport and the TV grab, NOT the play path
    # (see test_monitor.py and test_cec.py).
    monkeypatch.setattr(handoff, "interruptible_sleep", lambda *a, **k: None)
    a, b = _RecordingClient(), _RecordingClient()
    handoff.play(_cfg_model("M9205"), a, "nfs://h/s/Movies/Dune/BDMV/index.bdmv")
    handoff.play(_cfg_model("M9207"), b, "nfs://h/s/Movies/Dune/BDMV/index.bdmv")
    assert a.calls == b.calls and a.calls  # identical, and non-empty (actually exercised the path)

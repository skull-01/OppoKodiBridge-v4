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
        return {"success": True}  # an AFFIRMATIVE mount (a bare {} is no longer treated as success -- #18)

    def play_file(self, server, name):
        return self._reply

    def play_bdmv(self, name):
        return self._reply

    def stop(self):
        return {}


def _cfg():
    # autodetect off: these tests exercise the mapping/routing with the typed path_from. The
    # detect-from-Kodi-sources seam (a localhost JSON-RPC) is covered by the dedicated tests below.
    return Config(oppo_ip="x", path_from="nfs://h/s", path_to="srv", path_from_autodetect=False)


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
        return {"success": True}  # affirmative mount (#18)

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
    return Config(oppo_ip="x", path_from="nfs://h/s", path_to="srv/nfs/media", oppo_model=model,
                  path_from_autodetect=False)


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
    # play sub-paths of a mount). oppo_model selects only the TV grab now (cec.grab_supported), not the
    # play path; stop detection is HTTP-only for every model.
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
    # call sequence. The model selects only the TV grab, NOT the play path (see test_cec.py; stop
    # detection is HTTP-only for every model).
    monkeypatch.setattr(handoff, "interruptible_sleep", lambda *a, **k: None)
    a, b = _RecordingClient(), _RecordingClient()
    handoff.play(_cfg_model("M9205"), a, "nfs://h/s/Movies/Dune/BDMV/index.bdmv")
    handoff.play(_cfg_model("M9207"), b, "nfs://h/s/Movies/Dune/BDMV/index.bdmv")
    assert a.calls == b.calls and a.calls  # identical, and non-empty (actually exercised the path)


# --- path_from auto-detection from Kodi sources (#9): detect-as-FALLBACK ---------------------------
# The typed path_from is AUTHORITATIVE (it pairs with path_to at the same depth): when it maps the
# file it is used as-is and Kodi is never queried. Detection only runs when the typed prefix is blank
# or doesn't map, re-deriving path_from from Kodi's own video sources (longest-prefix).

def _cfg_auto(path_from="", path_to="srv"):
    return Config(oppo_ip="x", path_from=path_from, path_to=path_to, path_from_autodetect=True)


def test_play_typed_path_from_wins_and_skips_kodi_when_it_maps(monkeypatch):
    # A correctly-typed path_from maps the file -> Kodi is NEVER queried (no per-play JSON-RPC) and the
    # typed value (paired with path_to) is used. The no-regression / no-override guarantee.
    monkeypatch.setattr(handoff, "interruptible_sleep", lambda *a, **k: None)
    called = {"n": 0}

    def _sources(cfg):
        called["n"] += 1
        return ["nfs://kodi/would/override/Movies"]  # a different, deeper root that must NOT win

    monkeypatch.setattr(handoff.cec, "kodi_video_sources", _sources)
    client = _RecordingClient()
    assert handoff.play(_cfg_auto(path_from="nfs://h/s"), client, "nfs://h/s/Movies/clip.mp4") is True
    assert called["n"] == 0                           # typed mapped -> Kodi never consulted
    assert ("mount", "srv/Movies") in client.calls    # mapped via the typed path_from, not detection


def test_play_typed_depth_wins_even_when_a_deeper_source_also_maps(monkeypatch):
    # The exact H2 mis-mount scenario, made explicit: the typed path_from maps AND a deeper Kodi source
    # (the per-library-subfolder layout) would ALSO map the same file. detect-FIRST would have picked
    # the deeper root -> empty in-share folder -> mount the path_to ROOT 'srv' (wrong, file not there).
    # detect-as-fallback keeps the typed depth: mount stays 'srv/Movies', and Kodi is never queried.
    monkeypatch.setattr(handoff, "interruptible_sleep", lambda *a, **k: None)
    called = {"n": 0}

    def _deeper(cfg):
        called["n"] += 1
        return ["nfs://h/s/Movies"]  # a deeper source that genuinely maps the played file

    monkeypatch.setattr(handoff.cec, "kodi_video_sources", _deeper)
    client = _RecordingClient()
    assert handoff.play(_cfg_auto(path_from="nfs://h/s"), client, "nfs://h/s/Movies/clip.mp4") is True
    assert called["n"] == 0                            # typed mapped -> the deeper source is never consulted
    assert ("mount", "srv/Movies") in client.calls     # typed depth preserved, NOT 'srv' (path_to root)


def test_play_autodetects_when_typed_path_from_is_blank(monkeypatch):
    # No typed path_from -> it can't map -> detection from Kodi's sources supplies it.
    monkeypatch.setattr(handoff, "interruptible_sleep", lambda *a, **k: None)
    monkeypatch.setattr(handoff.cec, "kodi_video_sources", lambda cfg: ["nfs://192.168.1.177/share/"])
    client = _RecordingClient()
    assert handoff.play(_cfg_auto(), client, "nfs://192.168.1.177/share/Movies/Dune (2021).iso") is True
    assert ("mount", "srv/Movies") in client.calls
    assert ("play_file", "Dune (2021).iso") in client.calls


def test_play_autodetects_when_typed_path_from_does_not_match(monkeypatch):
    # A stale/wrong typed path_from fails to map -> detection picks the real Kodi source root.
    monkeypatch.setattr(handoff, "interruptible_sleep", lambda *a, **k: None)
    monkeypatch.setattr(handoff.cec, "kodi_video_sources", lambda cfg: ["nfs://192.168.1.177/share"])
    client = _RecordingClient()
    cfg = _cfg_auto(path_from="nfs://wrong/root")
    assert handoff.play(cfg, client, "nfs://192.168.1.177/share/Movies/clip.mp4") is True
    assert ("mount", "srv/Movies") in client.calls
    assert ("play_file", "clip.mp4") in client.calls


def test_play_longest_prefix_source_selected_end_to_end(monkeypatch):
    # Blank typed -> detection; with nested sources the LONGEST-prefix (deepest) root is chosen, so the
    # in-share folder is relative to it. Pins longest-prefix selection through the real handoff wiring.
    monkeypatch.setattr(handoff, "interruptible_sleep", lambda *a, **k: None)
    monkeypatch.setattr(handoff.cec, "kodi_video_sources",
                        lambda cfg: ["nfs://h/share", "nfs://h/share/Movies"])
    client = _RecordingClient()
    assert handoff.play(_cfg_auto(path_to="srv"), client, "nfs://h/share/Movies/Dune.iso") is True
    assert ("mount", "srv") in client.calls            # deepest root -> empty in-share folder -> path_to root
    assert ("play_file", "Dune.iso") in client.calls


def test_play_exact_equal_source_does_not_strand_the_handoff(monkeypatch):
    # Regression (#9 audit HIGH): a source EQUAL to the played path (a folder/file exposed as its own
    # Kodi source) alongside the broad share source must NOT be selected -- split_share_relative would
    # reject the empty remainder and strand the handoff. The broader, mappable source is chosen instead.
    monkeypatch.setattr(handoff, "interruptible_sleep", lambda *a, **k: None)
    played = "nfs://h/share/Movies/Dune (2021).iso"
    monkeypatch.setattr(handoff.cec, "kodi_video_sources", lambda cfg: ["nfs://h/share", played])
    client = _RecordingClient()
    assert handoff.play(_cfg_auto(path_to="srv"), client, played) is True
    assert ("mount", "srv/Movies") in client.calls
    assert ("play_file", "Dune (2021).iso") in client.calls


def test_play_cannot_map_when_neither_typed_nor_detection_match(monkeypatch):
    # typed fails AND no source contains the file -> detection returns None -> Cannot map -> False.
    monkeypatch.setattr(handoff, "interruptible_sleep", lambda *a, **k: None)
    monkeypatch.setattr(handoff.cec, "kodi_video_sources", lambda cfg: ["nfs://other/root"])
    client = _RecordingClient()
    cfg = _cfg_auto(path_from="nfs://wrong/root")
    assert handoff.play(cfg, client, "nfs://h/share/Movies/clip.mp4") is False


def test_play_autodetect_off_never_queries_even_when_typed_fails(monkeypatch):
    # With autodetect off there is NO per-play Kodi JSON-RPC, even when the typed path_from can't map.
    monkeypatch.setattr(handoff, "interruptible_sleep", lambda *a, **k: None)
    called = {"n": 0}

    def _record(cfg):
        called["n"] += 1
        return ["nfs://h/share"]

    monkeypatch.setattr(handoff.cec, "kodi_video_sources", _record)
    client = _RecordingClient()
    cfg = Config(oppo_ip="x", path_from="nfs://wrong/root", path_to="srv", path_from_autodetect=False)
    assert handoff.play(cfg, client, "nfs://h/share/Movies/clip.mp4") is False
    assert called["n"] == 0


# --- NFS mount hardening: reference-faithful retry + corruption-safety ------------------------------
# skull-01/emby-chinoppo-bridge-ri: <=2 mount attempts, re-login (NEVER unmount) between, abort before
# play if both fail; a timeout/None reply is a failure, not a silent success.

class _MountClient(_FakeClient):
    """Per-attempt mount replies + records play calls. A reply that IS an Exception is raised
    (simulates a mount timeout -> OppoError -> _best_effort returns None)."""

    def __init__(self, mount_replies, play_reply=None):
        super().__init__(play_reply if play_reply is not None else {"success": True})
        self._mounts = list(mount_replies)
        self.mount_attempts = 0
        self.calls = []

    def mount_nfs(self, server, folder):
        self.mount_attempts += 1
        r = self._mounts.pop(0) if self._mounts else {"success": False}
        if isinstance(r, Exception):
            raise r
        return r

    def play_file(self, server, name):
        self.calls.append(("play_file", name)); return self._reply

    def play_bdmv(self, name):
        self.calls.append(("play_bdmv", name)); return self._reply

    def stop(self):
        self.calls.append(("stop", None)); return {}


def _cfg_mount():
    return Config(oppo_ip="x", path_from="nfs://h/s", path_to="srv", path_from_autodetect=False)


def test_play_aborts_when_mount_fails_both_attempts(monkeypatch):
    # Both mount attempts fail -> abort BEFORE any play (never fire a play into a bad mount), and never
    # unmount (unmount-when-empty corrupts the OPPO NFS client). Returns False.
    monkeypatch.setattr(handoff, "interruptible_sleep", lambda *a, **k: None)
    client = _MountClient([{"success": False, "retInfo": "failed"},
                           {"success": False, "retInfo": "failed"}])
    assert handoff.play(_cfg_mount(), client, "nfs://h/s/Movies/x.iso") is False
    assert client.mount_attempts == 2                                  # bounded: exactly 2
    assert not any(k in ("play_file", "play_bdmv") for k, _ in client.calls)  # never played


def test_play_mount_succeeds_on_retry(monkeypatch):
    # First mount fails, retry succeeds -> play proceeds (the normal hardware path: the OPPO's first
    # mount reliably returns 'failed').
    monkeypatch.setattr(handoff, "interruptible_sleep", lambda *a, **k: None)
    client = _MountClient([{"success": False, "retInfo": "failed"}, {"success": True}])
    assert handoff.play(_cfg_mount(), client, "nfs://h/s/Movies/clip.mp4") is True
    assert client.mount_attempts == 2
    assert ("play_file", "clip.mp4") in client.calls


def test_play_aborts_when_mount_times_out(monkeypatch):
    # A mount timeout (OppoError -> _best_effort None) counts as a FAILURE, not a silent success ->
    # abort, no play. (Previously a None reply slipped through reply_failed and played into nothing.)
    monkeypatch.setattr(handoff, "interruptible_sleep", lambda *a, **k: None)
    client = _MountClient([handoff.OppoError("timeout"), handoff.OppoError("timeout")])
    assert handoff.play(_cfg_mount(), client, "nfs://h/s/Movies/x.iso") is False
    assert client.mount_attempts == 2
    assert not any(k in ("play_file", "play_bdmv") for k, _ in client.calls)


# --- #18: require an AFFIRMATIVE mount/play reply (tightens #17's abort-before-play) ----------------

def test_play_aborts_when_mount_reply_is_empty(monkeypatch):
    # A bare {} (or the non-JSON sentinel) is NOT an affirmative mount -- it slipped through the old
    # `not reply_failed` gate and fired a play into a bad mount. Now both attempts fail -> abort.
    monkeypatch.setattr(handoff, "interruptible_sleep", lambda *a, **k: None)
    client = _MountClient([{}, {}])
    assert handoff.play(_cfg_mount(), client, "nfs://h/s/Movies/x.iso") is False
    assert client.mount_attempts == 2
    assert not any(k in ("play_file", "play_bdmv") for k, _ in client.calls)


def test_play_aborts_when_mount_reply_is_non_json(monkeypatch):
    # A non-JSON body surfaces as {"raw": ...} -> not a parsed mount confirmation -> abort, no play.
    monkeypatch.setattr(handoff, "interruptible_sleep", lambda *a, **k: None)
    client = _MountClient([{"raw": "<html>500</html>"}, {"raw": "<html>500</html>"}])
    assert handoff.play(_cfg_mount(), client, "nfs://h/s/Movies/x.iso") is False
    assert not any(k in ("play_file", "play_bdmv") for k, _ in client.calls)


def test_play_aborts_when_play_reply_is_empty(monkeypatch):
    # Mount succeeds but the play returns {} -> not an affirmative accept -> False (don't wait for
    # playback that will never start).
    monkeypatch.setattr(handoff, "interruptible_sleep", lambda *a, **k: None)
    client = _MountClient([{"success": True}], play_reply={})
    assert handoff.play(_cfg_mount(), client, "nfs://h/s/Movies/x.iso") is False

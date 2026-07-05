import os
import socket
import sys
import types
import urllib.parse
import urllib.request

import pytest

from resources.lib import oppo_http as oh
from resources.lib.config import Config

KODI = (
    "nfs://192.168.1.177/mnt/Super3/Super3Share/02TV/01Series/02-MKV/"
    "3 Body Problem (2024)/Season 1/3 Body Problem - S01E01 - Countdown.mkv"
)
FROM = "nfs://192.168.1.177/mnt/Super3/Super3Share"


def test_split_share_relative():
    folder, base = oh.split_share_relative(KODI, FROM)
    assert folder == "02TV/01Series/02-MKV/3 Body Problem (2024)/Season 1"
    assert base == "3 Body Problem - S01E01 - Countdown.mkv"


def test_split_share_relative_urlencoded():
    enc = "nfs://192.168.1.177/mnt/Super3/Super3Share/A%20B/file%20(2024).mkv"
    folder, base = oh.split_share_relative(enc, FROM)
    assert folder == "A B"
    assert base == "file (2024).mkv"


def test_split_share_relative_no_match():
    assert oh.split_share_relative("nfs://other/x/y.mkv", FROM) == (None, None)


def test_split_share_relative_sibling_share_not_matched():
    # a sibling share whose name EXTENDS the prefix must NOT match (path-boundary check)
    assert oh.split_share_relative(FROM + "-4K/Dune.iso", FROM) == (None, None)
    # the exact prefix (no file under it) also does not produce a bogus mapping
    assert oh.split_share_relative(FROM, FROM) == (None, None)


def test_split_share_relative_root_file():
    folder, base = oh.split_share_relative(FROM + "/movie.mkv", FROM)
    assert folder == ""
    assert base == "movie.mkv"


def test_oppo_mount_folder():
    assert (
        oh.oppo_mount_folder("02TV/01Series/Season 1", "srv/nfs/media")
        == "srv/nfs/media/02TV/01Series/Season 1"
    )
    assert oh.oppo_mount_folder("", "srv/nfs/media") == "srv/nfs/media"
    assert oh.oppo_mount_folder("A/B", "/srv/nfs/media/") == "srv/nfs/media/A/B"


def test_unwrap_multipath_passthrough_for_plain_source():
    assert oh.unwrap_multipath("nfs://192.168.1.177/share/") == ["nfs://192.168.1.177/share/"]
    assert oh.unwrap_multipath("  smb://host/movies  ") == ["smb://host/movies"]
    assert oh.unwrap_multipath("") == []
    assert oh.unwrap_multipath(None) == []


def test_unwrap_multipath_expands_members_urldecoded():
    # Kodi encodes each member fully (its own scheme/slashes percent-encoded) and joins with '/'.
    a = urllib.parse.quote("nfs://192.168.1.177/share/A/", safe="")
    b = urllib.parse.quote("nfs://192.168.1.177/share/B/", safe="")
    src = "multipath://" + a + "/" + b + "/"
    assert oh.unwrap_multipath(src) == [
        "nfs://192.168.1.177/share/A/",
        "nfs://192.168.1.177/share/B/",
    ]


def test_detect_path_from_shallowest_prefix_wins():
    # #16: the SHALLOWEST (broadest) matching source wins. path_to is the OPPO export ROOT, so path_from
    # must anchor at the same share-root depth; picking the deeper per-library source would strip the
    # 'Movies' segment and mis-anchor path_to.
    sources = ["nfs://192.168.1.177/share", "nfs://192.168.1.177/share/Movies"]
    got = oh.detect_path_from("nfs://192.168.1.177/share/Movies/Dune (2021).iso", sources)
    assert got == "nfs://192.168.1.177/share"  # the broad share root, not the per-library subfolder


def test_detect_path_from_shallowest_preserves_full_in_share_subpath():
    # #16 made explicit via the split round-trip: the shallow root keeps the WHOLE sub-path so path_to
    # (the export root) + sub-path maps correctly. The deep source would have dropped 'Movies'.
    sources = ["nfs://h/share/Movies", "nfs://h/share"]  # order must not matter
    media = "nfs://h/share/Movies/Dune/BDMV/index.bdmv"
    root = oh.detect_path_from(media, sources)
    assert root == "nfs://h/share"
    folder, base = oh.split_share_relative(media, root)
    assert folder == "Movies/Dune/BDMV" and base == "index.bdmv"


def test_detect_path_from_strips_trailing_slash_and_feeds_split():
    src = "nfs://192.168.1.177/share/"
    detected = oh.detect_path_from("nfs://192.168.1.177/share/Movies/clip.mp4", [src])
    assert detected == "nfs://192.168.1.177/share"  # trailing slash normalised
    # round-trip: the detected value maps the same file through split_share_relative
    assert oh.split_share_relative("nfs://192.168.1.177/share/Movies/clip.mp4", detected) == (
        "Movies", "clip.mp4")


def test_detect_path_from_sibling_share_boundary():
    # a source whose name merely EXTENDS the share root must not match (same rule as split)
    assert oh.detect_path_from("nfs://h/Super3Share-4K/x.iso", ["nfs://h/Super3Share"]) is None


def test_detect_path_from_no_match_returns_none():
    assert oh.detect_path_from("nfs://h/share/x.mkv", ["nfs://other/root", "smb://nas/media"]) is None
    assert oh.detect_path_from("nfs://h/share/x.mkv", []) is None
    assert oh.detect_path_from("nfs://h/share/x.mkv", None) is None


def test_detect_path_from_matches_urlencoded_media_against_decoded_source():
    # the played path arrives percent-encoded; the source root is literal -- both are decoded to match.
    media = "nfs://192.168.1.177/share/A%20B/file%20(2024).mkv"
    assert oh.detect_path_from(media, ["nfs://192.168.1.177/share/A B"]) == "nfs://192.168.1.177/share/A B"


def test_detect_path_from_skips_exact_equal_source():
    # an exact-equal source has no in-share remainder -> split_share_relative would reject it (strand),
    # so it must NOT be selected; longest-prefix falls through to the broader, mappable source.
    sources = ["nfs://h/share", "nfs://h/share/Movies/Dune"]
    assert oh.detect_path_from("nfs://h/share/Movies/Dune", sources) == "nfs://h/share"
    assert oh.detect_path_from("nfs://h/share/Movies/Dune/", sources) == "nfs://h/share"  # trailing slash
    # only the exact-equal source present -> None (so the typed fallback runs, never a strand)
    assert oh.detect_path_from("nfs://h/share/Movies/Dune", ["nfs://h/share/Movies/Dune"]) is None


def test_detect_path_from_skips_pathless_host_source():
    # a bare scheme://host with no share path can't be a root (the host/share name would fold into the
    # in-share folder) -> skipped; a host + at least one path segment IS a valid root.
    assert oh.detect_path_from("nfs://192.168.1.177/share/x.iso", ["nfs://192.168.1.177"]) is None
    assert oh.detect_path_from("nfs://192.168.1.177/share/x.iso", ["nfs://192.168.1.177/"]) is None
    assert oh.detect_path_from("nfs://192.168.1.177/share/x.iso", ["nfs://192.168.1.177/share"]) == \
        "nfs://192.168.1.177/share"


def test_nfs_server_from_devices():
    devices = {
        "devicelist": [
            {"sub_type": "cifs", "name": "OPPO-PROXY"},
            {"sub_type": "nfs", "name": "192.168.10.20"},
        ]
    }
    assert oh.nfs_server_from_devices(devices) == "192.168.10.20"
    assert oh.nfs_server_from_devices({"devicelist": []}) is None


def test_status_is_idle():
    assert oh.status_is_idle("STOP")
    assert oh.status_is_idle("")
    assert not oh.status_is_idle("PLAY")


def test_status_is_idle_unknown_and_falsy_tokens_are_idle():
    for token in ("NODISC", "STANDBY", "CLOSE", "0", "false", "off", "READY"):
        assert oh.status_is_idle(token), token
    assert not oh.status_is_idle("BUFFERING")


def test_info_is_playing_false_on_unknown_status():
    assert not oh.info_is_playing({"is_video_playing": False, "state": "NODISC"})
    assert not oh.info_is_playing({"status": "STANDBY"})
    assert oh.info_is_playing({"status": "PLAY"})


def test_send_tcp_command_raises_oppoerror_on_mid_send_reset(monkeypatch):
    class _Conn:
        def sendall(self, data):
            raise ConnectionResetError("reset")

        def settimeout(self, t):
            pass

        def recv(self, n):
            return b""

        def close(self):
            pass

    monkeypatch.setattr(oh.socket, "create_connection", lambda addr, timeout=None: _Conn())
    monkeypatch.setattr(oh.time, "sleep", lambda *a, **k: None)
    with pytest.raises(oh.OppoError):
        _client().send_tcp_command("#QPW")


def test_info_is_playing_real_fields():
    idle = {"success": True, "is_video_playing": False, "is_audio_playing": False, "activeapp": "scrn_svr"}
    assert not oh.info_is_playing(idle)
    assert oh.info_is_playing({**idle, "is_video_playing": True})
    assert oh.info_is_playing({"is_bdmv_playing": True})
    assert not oh.info_is_playing({})


def test_reply_failed_coerces_non_bool_success():
    # explicit failure in every shape the loosely-typed app API can return
    assert oh.reply_failed({"success": False})
    assert oh.reply_failed({"success": 0})
    assert oh.reply_failed({"success": "false"})
    assert oh.reply_failed({"success": "0"})
    assert oh.reply_failed({"success": "no"})
    assert oh.reply_failed({"success": "OFF"})
    # success (any truthy shape) is NOT a failure
    assert not oh.reply_failed({"success": True})
    assert not oh.reply_failed({"success": 1})
    assert not oh.reply_failed({"success": "true"})
    # a missing success / non-dict is NOT a failure (the device omits it on success)
    assert not oh.reply_failed({})
    assert not oh.reply_failed({"retInfo": "ok"})
    assert not oh.reply_failed(None)
    assert not oh.reply_failed("raw text")


def test_reply_succeeded_requires_affirmative():
    # #18: the abort-before-play gate -- only an AFFIRMATIVE reply counts as success.
    assert oh.reply_succeeded({"success": True})
    assert oh.reply_succeeded({"success": 1})
    assert oh.reply_succeeded({"retInfo": "ok"})       # device omits `success` on a genuine success
    # NOT affirmative: None/non-dict, empty {}, the non-JSON sentinel {"raw":...}, or explicit failure
    assert not oh.reply_succeeded(None)
    assert not oh.reply_succeeded("raw text")
    assert not oh.reply_succeeded({})
    assert not oh.reply_succeeded({"raw": "<html>500</html>"})
    assert not oh.reply_succeeded({"success": False})
    assert not oh.reply_succeeded({"success": 0})
    assert not oh.reply_succeeded({"success": "false"})


def _client():
    return oh.OppoClient(Config(oppo_ip="1.2.3.4"))


def test_play_file_endpoint(monkeypatch):
    client = _client()
    cap = {}
    monkeypatch.setattr(client, "_get_json", lambda ep, timeout=None: cap.update(ep=ep) or {})
    client.play_file("192.168.10.20", "3 Body Problem - S01E01.mkv")
    ep = cap["ep"]
    assert ep.startswith("/playnormalfile?{") and ep.endswith("}")
    inner = urllib.parse.unquote(ep[len("/playnormalfile?{") : -1])
    assert '"path":"/mnt/nfs1/3 Body Problem - S01E01.mkv"' in inner
    assert '"extraNetPath":"192.168.10.20"' in inner


def test_mount_nfs_endpoint(monkeypatch):
    client = _client()
    cap = {}
    monkeypatch.setattr(client, "_get_json", lambda ep, timeout=None: cap.update(ep=ep) or {})
    client.mount_nfs("192.168.10.20", "srv/nfs/media/Season 1")
    ep = cap["ep"]
    assert ep.startswith('/mountNfsSharedFolder?{"server":"192.168.10.20","folder":"')
    assert "Season%201" in ep


def test_login_and_signin_endpoints(monkeypatch):
    client = _client()
    caps = []
    monkeypatch.setattr(client, "_get_json", lambda ep, timeout=None: caps.append(ep) or {})
    monkeypatch.setattr(client, "_get", lambda ep, timeout=None: caps.append(ep) or "ok")
    client.login_nfs("192.168.10.20")
    client.signin("192.168.1.100")
    assert any(c.startswith("/loginNfsServer?") and "192.168.10.20" in urllib.parse.unquote(c) for c in caps)
    assert any(c.startswith("/signin?") and "appIconType" in urllib.parse.unquote(c) for c in caps)


def test_get_raises_oppoerror_on_timeout(monkeypatch):
    client = _client()

    def boom(*a, **k):
        raise socket.timeout("timed out")

    monkeypatch.setattr(urllib.request, "urlopen", boom)
    with pytest.raises(oh.OppoError):
        client._get("/getglobalinfo")


def test_get_retries_transient_transport_failure(monkeypatch):
    # #22: a single transient transport failure is retried (bounded) instead of failing the read.
    client = _client()
    calls = {"n": 0}

    class _OK:
        status = 200

        def read(self):
            return b"ok"

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def flaky(url, timeout=None):
        calls["n"] += 1
        if calls["n"] == 1:
            raise socket.timeout("transient")
        return _OK()

    monkeypatch.setattr(urllib.request, "urlopen", flaky)
    monkeypatch.setattr(oh.time, "sleep", lambda *a, **k: None)
    assert client._get("/getglobalinfo", retries=1) == "ok"
    assert calls["n"] == 2  # failed once, retried, succeeded


def test_get_raises_oppoerror_on_httpexception(monkeypatch):
    # #19: http.client.HTTPException is NOT an OSError -- it must be caught and surfaced as OppoError,
    # not escape and crash the caller mid-playback.
    import http.client as hc

    client = _client()
    monkeypatch.setattr(urllib.request, "urlopen",
                        lambda url, timeout=None: (_ for _ in ()).throw(hc.BadStatusLine("garbage")))
    with pytest.raises(oh.OppoError):
        client._get("/getglobalinfo")


def test_send_control_command_tcp_by_default(monkeypatch):
    client = _client()
    calls = []
    monkeypatch.setattr(client, "send_tcp_command", lambda cmd, timeout=5.0: calls.append(cmd) or "@OK ON")
    assert client.send_control_command("#QPW") == "@OK ON"
    assert calls == ["#QPW"]


def test_send_control_command_serial_when_configured(monkeypatch):
    client = oh.OppoClient(Config(oppo_ip="1.2.3.4", serial_control=True, serial_port="/dev/ttyUSB9", serial_baud=9600))
    cap = {}
    monkeypatch.setattr(oh, "serial_command", lambda port, baud, cmd, read_timeout=2.0: cap.update(port=port, baud=baud, cmd=cmd) or "@OK OFF")
    assert client.send_control_command("#QPW") == "@OK OFF"
    assert cap == {"port": "/dev/ttyUSB9", "baud": 9600, "cmd": "#QPW"}


def test_power_cycle_uses_control_transport(monkeypatch):
    client = _client()
    sent = []
    monkeypatch.setattr(client, "send_control_command", lambda cmd, timeout=5.0: sent.append(cmd) or "")
    monkeypatch.setattr(oh.time, "sleep", lambda *a, **k: None)
    client.power_cycle(delay=0)
    assert sent == ["#POF", "#PON"]


def test_is_disc_path():
    assert oh.is_disc_path("01Movies/Ant-Man (2015)/BDMV/index.bdmv")
    assert oh.is_disc_path("x/VIDEO_TS/VIDEO_TS.IFO")
    assert not oh.is_disc_path("02TV/Show/S01E01.mkv")
    assert not oh.is_disc_path("01Movies/Crouching Tiger (2000).iso")


def test_is_iso():
    assert oh.is_iso("01Movies/Crouching Tiger (2000).iso")
    assert oh.is_iso("X/Y.ISO")
    assert not oh.is_iso("02TV/Show/S01E01.mkv")
    assert not oh.is_iso("X/disc/BDMV/index.bdmv")


def test_is_oppo_target():
    # Disc images and disc folders -> OPPO.
    assert oh.is_oppo_target(FROM + "/01Movies/Dune (2021).iso")
    assert oh.is_oppo_target("01Movies/Ant-Man (2015)/BDMV/index.bdmv")
    assert oh.is_oppo_target(FROM + "/01Movies/Ant-Man (2015)/BDMV/STREAM/00800.m2ts")
    assert oh.is_oppo_target("X/VIDEO_TS/VIDEO_TS.IFO")
    assert oh.is_oppo_target(FROM + "/01Movies/Dune%20(2021).iso")  # url-encoded
    # Everything else stays in Kodi.
    assert not oh.is_oppo_target(KODI)
    assert not oh.is_oppo_target(FROM + "/01Movies/film.mp4")
    assert not oh.is_oppo_target("Movies/looseclip/STREAM/0080.m2ts")  # no BDMV folder


def test_disc_folder():
    assert (
        oh.disc_folder("01Movies/01-4kDisc/Ant-Man (2015)/BDMV/index.bdmv")
        == "01Movies/01-4kDisc/Ant-Man (2015)"
    )
    assert oh.disc_folder("x/VIDEO_TS/VIDEO_TS.IFO") == "x"


def test_play_bdmv_endpoint(monkeypatch):
    client = _client()
    cap = {}
    monkeypatch.setattr(client, "_get_json", lambda ep, timeout=None: cap.update(ep=ep) or {})
    client.play_bdmv("Ant-Man (2015)")
    ep = cap["ep"]
    assert ep.startswith('/checkfolderhasBDMV?{"folderpath":"/mnt/nfs1/')
    assert "Ant-Man" in urllib.parse.unquote(ep)


# --- serial transport hardening (b9 / b12): every failure must surface as OppoError ---
def _install_fake_termios(monkeypatch, tcgetattr):
    """Inject a fake POSIX termios + os flags so serial_command runs on any host (the Windows test
    runner has no termios)."""
    fake = types.ModuleType("termios")

    class error(Exception):
        pass

    fake.error = error
    for name in ("CSIZE", "PARENB", "CSTOPB", "CS8", "CREAD", "CLOCAL", "TCSANOW", "TCIOFLUSH", "B9600"):
        setattr(fake, name, 0)
    fake.tcgetattr = tcgetattr
    fake.tcsetattr = lambda *a, **k: None
    fake.tcflush = lambda *a, **k: None
    monkeypatch.setitem(sys.modules, "termios", fake)
    for flag in ("O_RDWR", "O_NOCTTY", "O_NONBLOCK"):
        monkeypatch.setattr(os, flag, getattr(os, flag, 0), raising=False)
    monkeypatch.setattr(os, "open", lambda *a, **k: 7)
    monkeypatch.setattr(os, "close", lambda fd: None)
    return fake


def test_serial_command_missing_termios_becomes_oppoerror(monkeypatch):
    # b9: serial control on a non-POSIX host (no termios) must raise OppoError, not ImportError.
    monkeypatch.setitem(sys.modules, "termios", None)  # forces `import termios` -> ImportError
    with pytest.raises(oh.OppoError):
        oh.serial_command("/dev/ttyUSB0", 9600, "#PON")


def test_serial_command_termios_error_becomes_oppoerror(monkeypatch):
    # b12: termios.error (NOT an OSError) from configuring a non-tty fd must surface as OppoError.
    def boom(fd):
        import termios  # the injected fake

        raise termios.error("Inappropriate ioctl for device")

    _install_fake_termios(monkeypatch, tcgetattr=boom)
    with pytest.raises(oh.OppoError):
        oh.serial_command("/dev/ttyUSB0", 9600, "#PON")


def test_serial_command_bad_baud_becomes_oppoerror(monkeypatch):
    # b12 (secondary): a non-numeric baud must not escape as a raw ValueError.
    _install_fake_termios(monkeypatch, tcgetattr=lambda fd: [0, 0, 0, 0, 0, 0, 0])
    with pytest.raises(oh.OppoError):
        oh.serial_command("/dev/ttyUSB0", "not-a-number", "#PON")


# --- power_cycle must still fire #PON when #POF fails (b6) ---
def test_power_cycle_sends_pon_when_poff_fails(monkeypatch):
    client = _client()
    sent = []

    def ctrl(cmd, timeout=5.0):
        if cmd == "#POF":
            raise oh.OppoError("transient :23 reset on #POF")
        sent.append(cmd)
        return ""

    monkeypatch.setattr(client, "send_control_command", ctrl)
    monkeypatch.setattr(oh.time, "sleep", lambda *a, **k: None)
    client.power_cycle(delay=0)
    assert sent == ["#PON"]  # #PON fired despite the #POF leg failing


def test_play_bdmv_root_disc_no_trailing_slash(monkeypatch):
    # a disc structure at the export root yields an empty name -> the folderpath must be the bare mount
    # (/mnt/nfs1), never a dangling /mnt/nfs1/ (which has no disc-folder identity for the OPPO).
    client = _client()
    cap = {}
    monkeypatch.setattr(client, "_get_json", lambda ep, timeout=None: cap.update(ep=ep) or {})
    client.play_bdmv("")
    assert cap["ep"] == '/checkfolderhasBDMV?{"folderpath":"/mnt/nfs1"}'


# --- #11: parse the OPPO NFS export root from /getNfsShareFolderlist ---------------------------------

def test_parse_nfs_share_root_extracts_export_root():
    # the reply decoded utf-8/errors=replace: length/control bytes bracket the export path
    assert oh.parse_nfs_share_root("\r\x00\x00\x00srv/nfs/media\x01") == "srv/nfs/media"
    assert oh.parse_nfs_share_root("\x0dsrv/nfs/media\x01\x08more/stuff") == "srv/nfs/media"  # first path
    assert oh.parse_nfs_share_root("/srv/nfs/media/") == "srv/nfs/media"                       # trimmed


def test_parse_nfs_share_root_none_when_nothing_pathlike():
    assert oh.parse_nfs_share_root("") is None
    assert oh.parse_nfs_share_root(None) is None
    assert oh.parse_nfs_share_root("\x00\x01\x02") is None
    assert oh.parse_nfs_share_root("media") is None   # a bare single segment is too ambiguous to trust


def test_parse_nfs_share_root_rejects_non_export_bodies():
    # #11 audit hardening: a fragile SMB->NFS proxy's HTTP/header/error/version body must NOT be mistaken
    # for an export root. The no-space/no-colon token rule + the >=2-slash bar reject all of these.
    for junk in ("HTTP/1.1 200 OK", "Server: nginx/1.18.0", "Content-Type: text/html",
                 "nfs version 4.1/opt", "\x08\x00error: not/found", "a/b"):
        assert oh.parse_nfs_share_root(junk) is None, junk


def test_parse_nfs_share_root_skips_short_noise_before_root():
    # a 1-slash noise token preceding the real >=2-slash export root must be skipped, not latched.
    assert oh.parse_nfs_share_root("v1/2\x00srv/nfs/media") == "srv/nfs/media"


def test_parse_nfs_share_root_deep_noise_before_root_is_a_known_limitation():
    # KNOWN best-effort limitation (audit LOW, contained): a >=2-slash noise token BEFORE the real root
    # IS latched, not skipped. Fail-safe -- a wrong path_to makes the OPPO mount fail so the handoff
    # aborts before play (never a wrong-file play). Pinned so any future parser change is intentional.
    assert oh.parse_nfs_share_root("a/b/c\x00srv/nfs/media") == "a/b/c"


# --- #14: configurable OPPO mount directory (default nfs1 = zero regression) -------------------------

def test_mount_dir_default_and_override():
    assert oh.OppoClient(Config(oppo_ip="x"))._mount_dir(nfs=True) == "nfs1"       # default
    c = oh.OppoClient(Config(oppo_ip="x", oppo_mount=""))                          # blank -> nfs/cifs pair
    assert c._mount_dir(nfs=True) == "nfs1"
    assert c._mount_dir(nfs=False) == "cifs1"
    assert oh.OppoClient(Config(oppo_ip="x", oppo_mount="media"))._mount_dir() == "media"  # override


def test_play_file_uses_configured_oppo_mount(monkeypatch):
    client = oh.OppoClient(Config(oppo_ip="1.2.3.4", oppo_mount="media"))
    cap = {}
    monkeypatch.setattr(client, "_get_json", lambda ep, timeout=None: cap.update(ep=ep) or {})
    client.play_file("192.168.10.20", "x.iso")
    assert '"path":"/mnt/media/x.iso"' in urllib.parse.unquote(cap["ep"])


def test_play_bdmv_uses_configured_oppo_mount(monkeypatch):
    client = oh.OppoClient(Config(oppo_ip="1.2.3.4", oppo_mount="disc0"))
    cap = {}
    monkeypatch.setattr(client, "_get_json", lambda ep, timeout=None: cap.update(ep=ep) or {})
    client.play_bdmv("Dune")
    assert '"folderpath":"/mnt/disc0/Dune"' in urllib.parse.unquote(cap["ep"])


# --- #30 / #12 / #13: paused state + per-flag playback reporting -------------------------------------

def test_info_is_paused():
    assert oh.info_is_paused({"status": "PAUSE"})
    assert oh.info_is_paused({"state": "paused"})
    assert not oh.info_is_paused({"status": "PLAY"})
    assert not oh.info_is_paused({"is_video_playing": True})
    assert not oh.info_is_paused({})


def test_playback_state_four_states(monkeypatch):
    client = _client()
    monkeypatch.setattr(client, "get_global_info", lambda: {"is_video_playing": True, "status": "PAUSE"})
    assert client.playback_state() == "paused"
    monkeypatch.setattr(client, "get_global_info", lambda: {"is_video_playing": True, "status": "PLAY"})
    assert client.playback_state() == "playing"
    monkeypatch.setattr(client, "get_global_info", lambda: {"is_video_playing": False})
    assert client.playback_state() == "idle"


def test_playback_state_unknown_on_transport_error(monkeypatch):
    client = _client()
    monkeypatch.setattr(client, "get_global_info",
                        lambda: (_ for _ in ()).throw(oh.OppoError("boom")))
    assert client.playback_state() == "unknown"


def test_playing_flags_reports_every_active_flag():
    flags = oh.playing_flags({"is_video_playing": True, "is_bdmv_playing": False, "is_disc_playing": "1"})
    assert flags["is_video_playing"] is True
    assert flags["is_bdmv_playing"] is False
    assert flags["is_disc_playing"] is True
    assert "is_audio_playing" not in flags   # absent flags are not reported
    assert oh.playing_flags({}) == {}

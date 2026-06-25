import itertools
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


def test_upl_status():
    assert oh.upl_status("@UPL PLAY") == "PLAY"
    assert oh.upl_status("@UPL STOP\r") == "STOP"
    assert oh.upl_status("@UPL HOME") == "HOME"
    assert oh.upl_status("@UTC 000 002 C 00:57:15") is None
    assert oh.upl_status("garbage") is None


def test_upl_means_ended():
    assert oh.upl_means_ended("@UPL STOP")
    assert oh.upl_means_ended("@UPL HOME")
    assert not oh.upl_means_ended("@UPL PLAY")
    assert not oh.upl_means_ended("@UPL PAUS")
    assert not oh.upl_means_ended("@UTC 000 002 C 00:57:15")


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


# --- verbose heartbeat: a transient HTTP failure is NOT a stop (b1) ---
def test_verbose_heartbeat_transient_failure_is_not_a_stop(monkeypatch):
    client = _client()

    class _Sock:
        def sendall(self, data):
            pass

        def settimeout(self, t):
            pass

        def recv(self, n):
            raise socket.timeout()  # always quiet -> hit the heartbeat branch every loop

        def close(self):
            pass

    monkeypatch.setattr(oh.socket, "create_connection", lambda addr, timeout=None: _Sock())
    clock = itertools.count(0, 100)  # each read delta > 6s, so the heartbeat fires every iteration
    monkeypatch.setattr(oh.time, "time", lambda: next(clock))
    states = iter(["unknown", "unknown", "idle"])
    calls = {"n": 0}

    def fake_state():
        calls["n"] += 1
        return next(states)

    monkeypatch.setattr(client, "playback_state", fake_state)
    assert client.verbose_watch_until_stop(lambda: False) is True
    assert calls["n"] == 3  # ended only on the confirmed 'idle'; the two 'unknown's did not stop it


def test_play_bdmv_root_disc_no_trailing_slash(monkeypatch):
    # a disc structure at the export root yields an empty name -> the folderpath must be the bare mount
    # (/mnt/nfs1), never a dangling /mnt/nfs1/ (which has no disc-folder identity for the OPPO).
    client = _client()
    cap = {}
    monkeypatch.setattr(client, "_get_json", lambda ep, timeout=None: cap.update(ep=ep) or {})
    client.play_bdmv("")
    assert cap["ep"] == '/checkfolderhasBDMV?{"folderpath":"/mnt/nfs1"}'

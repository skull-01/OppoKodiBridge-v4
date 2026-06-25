"""Pure-HTTP handoff to the OPPO/M9205 app API.

Verified live against the operator's M9205 (2026-06-07). The exact sequence the device needs:

  1. wake   -- UDP b"NOTIFY OREMOTE LOGIN" to :7624 starts the :436 app API (it sleeps after boot)
  2. init   -- /getmainfirmwareversion, /getsetupmenu, /signin (appIconType/appIpAddress), /getglobalinfo
  3. login  -- /loginNfsServer for the OPPO's OWN NFS server (read from /getdevicelist)
  4. mount  -- /mountNfsSharedFolder of the FILE'S FOLDER -> the OPPO mounts it at /mnt/nfs1
  5. play   -- /playnormalfile?{...} with path "/mnt/nfs1/<basename>" and the server in extraNetPath

Two hard rules learned on hardware: mount the file's folder and play the bare filename (the OPPO
won't play sub-paths of a mount), and NEVER mount a non-exported folder (it hard-crashes the OPPO).
Mirrors the working emby-chinoppo-bridge; community-reverse-engineered, not an official protocol.
"""
from __future__ import annotations

import json
import socket
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Optional, Tuple

# Disc detection is the single source of truth in detector.py (shared with pcf.py). Re-exported here
# for backwards-compatible importers/tests (``oppo_http.is_disc_path`` etc.).
from .detector import (  # noqa: F401
    disc_folder,
    is_disc_path,
    is_handoff_target,
    is_iso,
    is_oppo_target,
)
from .kodilog import log

MOUNT_TIMEOUT = 25.0
PLAY_TIMEOUT = 15.0
OREMOTE_PORT = 7624

IDLE_STATUSES = {"STOP", "STOPPED", "IDLE", "END", "ENDED", "NO_MEDIA", "NO MEDIA", "HOME"}
PLAY_STATUSES = {
    "PLAY",
    "PLAYING",
    "PAUSE",
    "PAUSED",
    "LOADING",
    "BUFFER",
    "BUFFERING",
    "FFWD",
    "FREV",
    "SFWD",
    "SREV",
}

# The M9205 /getglobalinfo reports playback via these booleans (confirmed on hardware).
_PLAYING_FLAGS = (
    "is_playing",
    "is_video_playing",
    "is_audio_playing",
    "is_bdmv_playing",
    "is_disc_playing",
)


class OppoError(RuntimeError):
    pass


def parse_media_path(media_file: str) -> Tuple[str, str, str]:
    """Split a Kodi/network path into ``(server, folder, filename)``."""
    movie = str(media_file).replace("\\\\", "\\").replace("\\", "/")
    if "://" in movie:
        movie = movie.split("://", 1)[1]
    parts = [p for p in movie.split("/") if p]
    if not parts:
        return ("", "", "")
    server = parts[0]
    filename = parts[-1] if len(parts) > 1 else ""
    folder = "/".join(parts[1:-1]) if len(parts) > 2 else ""
    return (server, folder, filename)


def split_share_relative(media_file: str, path_from: str) -> Tuple[Optional[str], Optional[str]]:
    """``(folder, basename)`` for the file's location WITHIN the share, after stripping ``path_from``.

    ``nfs://192.168.1.177/mnt/Super3/Super3Share/A/B/file.mkv`` with path_from
    ``nfs://192.168.1.177/mnt/Super3/Super3Share`` -> ``("A/B", "file.mkv")``; ``(None, None)`` if the
    prefix doesn't match. Network URLs are URL-decoded so spaces/parens come through literal.
    """
    text = str(media_file)
    if text.lower().startswith(("nfs://", "smb://")):
        text = urllib.parse.unquote(text)
    prefix = (path_from or "").strip().rstrip("/")
    # Require a path boundary after the prefix, so a sibling share whose name EXTENDS the configured
    # one (e.g. ".../Super3Share-4K" vs path_from ".../Super3Share") is not mis-matched and mis-mapped.
    if not prefix or not (text == prefix or text.startswith(prefix + "/")):
        return (None, None)
    rel = text[len(prefix):].lstrip("/")
    if not rel:
        return (None, None)
    if "/" in rel:
        folder, basename = rel.rsplit("/", 1)
    else:
        folder, basename = "", rel
    return (folder, basename)


def oppo_mount_folder(folder: Optional[str], path_to: str) -> str:
    """The OPPO export folder to mount = the OPPO export root (``path_to``) + the in-share folder."""
    base = (path_to or "").strip().strip("/")
    rel = (folder or "").strip("/")
    if base and rel:
        return base + "/" + rel
    return base or rel


def local_ip_toward(host: str, port: int = 436) -> str:
    """The local source IP the box uses to reach ``host`` — for signin's ``appIpAddress``."""
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            sock.connect((host, int(port)))
            return sock.getsockname()[0]
        finally:
            sock.close()
    except OSError:
        return "127.0.0.1"


def nfs_server_from_devices(devices: Any) -> Optional[str]:
    """The OPPO's own NFS server name from ``/getdevicelist`` (the address it can reach)."""
    if isinstance(devices, dict):
        for dev in devices.get("devicelist", []) or []:
            if isinstance(dev, dict) and dev.get("sub_type") == "nfs":
                return dev.get("name") or dev.get("path")
    return None


def _containers(info: Any) -> list:
    out: list = []
    if isinstance(info, dict):
        out.append(info)
        for key in ("result", "playinfo", "data"):
            value = info.get(key)
            if isinstance(value, dict):
                out.append(value)
    return out


def status_is_idle(status: object) -> bool:
    # Only an explicit PLAY token counts as "not idle". Everything else -- "", "0"/"false"/"off",
    # STOP/HOME, and any UNKNOWN / no-disc token (NODISC, STANDBY, CLOSE, ...) -- is idle, so the HTTP
    # fallback watcher terminates instead of looping forever on a status string it does not recognise.
    return str(status).strip().upper() not in PLAY_STATUSES


def info_is_playing(info: Any) -> bool:
    """True when a ``/getglobalinfo`` payload indicates active (or loading) playback."""
    for container in _containers(info):
        for key in _PLAYING_FLAGS:
            flag = container.get(key)
            if isinstance(flag, bool):
                if flag:
                    return True
            elif flag is not None and str(flag).strip().lower() in ("1", "true", "yes", "playing"):
                return True
        for key in ("status", "state", "play_status", "e_play_status", "playStatus"):
            value = container.get(key)
            if value is not None and not status_is_idle(value):
                return True
    return False


_PLAYBACK_ENDED_STATUSES = {"STOP", "HOME", "NONE", "NODISC", "NO_DISC", "CLOSE"}


def upl_status(line):
    """Parse an OPPO verbose-mode ``@UPL`` line -> its status token (PLAY / STOP / HOME / PAUS / ...),
    or None if the line is not an ``@UPL`` message. e.g. ``@UPL PLAY`` -> ``"PLAY"``."""
    text = str(line).strip()
    if not text.startswith("@UPL"):
        return None
    rest = text[4:].strip()
    return rest.split()[0].upper() if rest else ""


def upl_means_ended(line):
    """True when an ``@UPL`` line reports playback has ended (STOP / HOME / no disc)."""
    status = upl_status(line)
    return status is not None and status in _PLAYBACK_ENDED_STATUSES


_BAUD_CONSTS = {
    2400: "B2400", 4800: "B4800", 9600: "B9600", 19200: "B19200",
    38400: "B38400", 57600: "B57600", 115200: "B115200",
}


def serial_command(port: str, baud: int, command: str, read_timeout: float = 2.0) -> str:
    """Send an OPPO #-control command (e.g. #PON) over an RS-232 serial port; return the reply.

    Stdlib only (termios) -- no pyserial dependency. termios is imported lazily so this module still
    imports on non-POSIX hosts (the Windows test runner). Same CR framing as send_tcp_command.

    EVERY failure surfaces as OppoError so callers (grab_oppo, the settings tests) stay non-fatal: a
    missing termios (serial enabled on a non-POSIX host), absent POSIX open-flags, a bad baud value,
    or a termios.error (the fd is not a usable tty) -- none of which are OSError -- must not escape as
    a raw exception that crashes the handoff or the RunScript.
    """
    import os
    import select

    try:
        import termios
    except ImportError as exc:
        raise OppoError("serial control needs POSIX termios (unavailable here): {}".format(exc)) from exc
    try:
        baud_const = getattr(termios, _BAUD_CONSTS.get(int(baud), "B9600"))
        open_flags = os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK
    except (ValueError, AttributeError) as exc:
        raise OppoError("serial config invalid for {} (baud={!r}): {}".format(port, baud, exc)) from exc

    try:
        fd = os.open(port, open_flags)
    except OSError as exc:
        raise OppoError("serial open {} failed: {}".format(port, exc)) from exc
    try:
        attrs = termios.tcgetattr(fd)
        attrs[0] = 0  # iflag
        attrs[1] = 0  # oflag
        attrs[3] = 0  # lflag -> raw
        attrs[2] = (attrs[2] & ~termios.CSIZE & ~termios.PARENB & ~termios.CSTOPB) | termios.CS8 | termios.CREAD | termios.CLOCAL
        attrs[4] = baud_const
        attrs[5] = baud_const
        termios.tcsetattr(fd, termios.TCSANOW, attrs)
        termios.tcflush(fd, termios.TCIOFLUSH)
        os.write(fd, (command.strip() + "\r").encode("ascii"))
        time.sleep(0.3)
        ready, _, _ = select.select([fd], [], [], read_timeout)
        if ready:
            try:
                return os.read(fd, 128).decode("ascii", errors="replace")
            except OSError:
                return ""
        return ""
    except (OSError, termios.error) as exc:
        raise OppoError("serial I/O on {} failed: {}".format(port, exc)) from exc
    finally:
        os.close(fd)


class OppoClient:
    """Live HTTP client for one OPPO. All calls raise OppoError on a transport failure."""

    def __init__(self, config: Any) -> None:
        self.cfg = config

    def _base(self) -> str:
        return "http://{}:{}".format(self.cfg.oppo_ip, int(self.cfg.oppo_http_port))

    def _get(self, endpoint: str, timeout: Optional[float] = None) -> str:
        url = self._base() + endpoint
        request_timeout = float(timeout if timeout is not None else self.cfg.socket_timeout)
        try:
            with urllib.request.urlopen(url, timeout=request_timeout) as response:
                body = response.read().decode("utf-8", errors="replace")
                status = getattr(response, "status", 200) or 200
                if status >= 400:
                    raise OppoError("OPPO HTTP {} for {}".format(status, url))
                return body
        except OppoError:
            raise
        except OSError as exc:
            raise OppoError("OPPO HTTP request failed for {}: {}".format(url, exc)) from exc

    def _get_json(self, endpoint: str, timeout: Optional[float] = None) -> dict:
        body = self._get(endpoint, timeout=timeout)
        try:
            parsed = json.loads(body)
            return parsed if isinstance(parsed, dict) else {"raw": body}
        except ValueError:
            return {"raw": body}

    def _port_open(self, port: int, timeout: float = 3.0) -> bool:
        try:
            conn = socket.create_connection((self.cfg.oppo_ip, int(port)), timeout=timeout)
            conn.close()
            return True
        except OSError:
            return False

    def send_oremote_notify(self) -> None:
        """The wake packet that starts the :436 app API."""
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            try:
                sock.sendto(b"NOTIFY OREMOTE LOGIN", (self.cfg.oppo_ip, OREMOTE_PORT))
            finally:
                sock.close()
        except OSError:
            pass

    def wake_and_wait(self, attempts: int = 18, interval: float = 3.0) -> bool:
        """Send the OREMOTE notify until the :436 API answers. Returns True if it came up."""
        port = int(self.cfg.oppo_http_port)
        for _ in range(max(1, attempts)):
            self.send_oremote_notify()
            if self._port_open(port):
                return True
            time.sleep(interval)
        return self._port_open(port)

    def get_firmware_version(self) -> str:
        return self._get("/getmainfirmwareversion")

    def get_setup_menu(self) -> str:
        return self._get("/getsetupmenu")

    def signin(self, app_ip: str = "127.0.0.1") -> str:
        payload = urllib.parse.quote('{"appIconType":1,"appIpAddress":"%s"}' % app_ip)
        return self._get("/signin?" + payload, timeout=15)

    def get_device_list(self) -> dict:
        return self._get_json("/getdevicelist")

    def get_global_info(self) -> dict:
        return self._get_json("/getglobalinfo")

    def login_nfs(self, server: str) -> dict:
        return self._get_json("/loginNfsServer?" + urllib.parse.quote('{"serverName":"%s"}' % server))

    def get_nfs_share_list(self) -> str:
        return self._get("/getNfsShareFolderlist", timeout=12)

    def mount_nfs(self, server: str, folder: str) -> dict:
        endpoint = '/mountNfsSharedFolder?{"server":"%s","folder":"%s"}' % (
            server,
            urllib.parse.quote(folder),
        )
        return self._get_json(endpoint, timeout=MOUNT_TIMEOUT)

    def play_file(self, server: str, rel_path: str, index: str = "0", nfs: bool = True) -> dict:
        mount_path = "nfs1" if nfs else "cifs1"
        inner = (
            '"path":"/mnt/%s/%s","index":%s,"type":1,"appDeviceType":2,"extraNetPath":"%s","playMode":0'
            % (mount_path, rel_path, index, server)
        )
        endpoint = "/playnormalfile?{" + urllib.parse.quote(inner) + "}"
        return self._get_json(endpoint, timeout=PLAY_TIMEOUT)

    def stop(self) -> dict:
        """Send STOP (clears a stuck 'bd_is_playing' before loading a new disc)."""
        return self._get_json("/sendremotekey?" + urllib.parse.quote('{"key":"STP"}'))

    def play_bdmv(self, disc_folder_name: str, nfs: bool = True) -> dict:
        """Play a Blu-ray disc FOLDER (one containing BDMV). On this OPPO ``/checkfolderhasBDMV``
        doesn't just check -- it starts the disc. ``disc_folder_name`` is relative to the mount; when
        the disc structure IS the mount root (a disc folder sitting at the export root) it is empty, so
        the folderpath is the bare mount (``/mnt/nfs1``) -- never a dangling ``/mnt/nfs1/``."""
        mount_path = "nfs1" if nfs else "cifs1"
        folderpath = "/mnt/%s" % mount_path
        if disc_folder_name:
            folderpath += "/" + urllib.parse.quote(disc_folder_name)
        endpoint = '/checkfolderhasBDMV?{"folderpath":"%s"}' % folderpath
        return self._get_json(endpoint, timeout=PLAY_TIMEOUT)

    def is_playing(self) -> bool:
        try:
            return info_is_playing(self.get_global_info())
        except OppoError:
            return False

    def playback_state(self) -> str:
        """Tri-state playback probe for the stop-watcher: ``"playing"`` / ``"idle"`` / ``"unknown"``.

        Unlike is_playing() (which collapses a transport failure to False), this distinguishes a
        confirmed-idle read from an unreadable one, so the monitor never mistakes a network blip for a
        stop (a premature mid-playback reclaim) nor loops forever on an unreachable OPPO."""
        try:
            info = self.get_global_info()
        except OppoError:
            return "unknown"
        return "playing" if info_is_playing(info) else "idle"

    def verbose_watch_until_stop(self, should_abort=None) -> bool:
        """Hold a verbose-mode (``#SVM 3``) connection on :23 and block until the OPPO reports
        playback stopped (``@UPL STOP`` / ``HOME``). Push-based, so stop is detected within line
        latency, not a poll interval. If the push stream goes quiet for a few seconds it cross-checks
        HTTP /getglobalinfo as a fallback. Call only AFTER playback has started (use HTTP for the
        pre-playback wait). Sends ``#SVM 0`` and closes on exit. Returns True when playback ended."""
        if should_abort is None:
            should_abort = lambda: False
        try:
            conn = socket.create_connection((self.cfg.oppo_ip, 23), timeout=5.0)
        except OSError as exc:
            raise OppoError("verbose :23 connect failed: {}".format(exc)) from exc
        try:
            try:
                conn.sendall(b"#SVM 3\r")
                conn.settimeout(1.0)
                buf = ""
                last_data = time.time()
                while not should_abort():
                    try:
                        chunk = conn.recv(512).decode("ascii", errors="replace")
                    except socket.timeout:
                        if time.time() - last_data > 6.0:  # heartbeat quiet -> confirm via HTTP
                            if self.playback_state() == "idle":
                                return True  # only a CONFIRMED idle read ends the watch
                            # "playing" or "unknown" (a transient HTTP blip during the stall) -> keep
                            # watching; never reclaim the TV on an unconfirmed read.
                            last_data = time.time()
                        continue
                    if not chunk:
                        return True  # connection dropped -> treat as ended
                    last_data = time.time()
                    buf += chunk
                    while "\r" in buf:
                        line, buf = buf.split("\r", 1)
                        if upl_means_ended(line):
                            return True
                return True
            except OSError as exc:  # a dropped :23 connection -> OppoError so the monitor's HTTP fallback runs
                raise OppoError("verbose :23 dropped: {}".format(exc)) from exc
        finally:
            try:
                conn.sendall(b"#SVM 0\r")
            except OSError:
                pass
            conn.close()

    def send_tcp_command(self, command: str, timeout: float = 5.0) -> str:
        """Send an OPPO IP-control command on :23 (e.g. #PON / #POF) and return the reply."""
        try:
            conn = socket.create_connection((self.cfg.oppo_ip, 23), timeout=timeout)
        except OSError as exc:
            raise OppoError("OPPO :23 connect failed: {}".format(exc)) from exc
        try:
            try:
                conn.sendall((command.strip() + "\r").encode("ascii"))
                time.sleep(0.5)
                conn.settimeout(2.0)
            except OSError as exc:  # a mid-send reset must surface as OppoError (grab_oppo catches it)
                raise OppoError("OPPO :23 send failed: {}".format(exc)) from exc
            try:
                return conn.recv(128).decode("ascii", errors="replace")
            except OSError:
                return ""
        finally:
            conn.close()

    def send_control_command(self, command: str, timeout: float = 5.0) -> str:
        """Send an OPPO #-control command over the configured transport: the RS-232 serial cable when
        ``serial_control`` is set, otherwise the network IP-control port (:23). The OPPO speaks the
        same #-command protocol on both wires (file playback stays on the HTTP app API regardless)."""
        if getattr(self.cfg, "serial_control", False):
            return serial_command(
                getattr(self.cfg, "serial_port", "/dev/ttyUSB0") or "/dev/ttyUSB0",
                getattr(self.cfg, "serial_baud", 9600) or 9600,
                command,
                read_timeout=min(float(timeout), 3.0),
            )
        return self.send_tcp_command(command, timeout)

    def power_cycle(self, delay: float = 5.0) -> None:
        """Standby then power on (#POF -> #PON) over the configured control transport. The power-ON
        is what fires the OPPO's CEC One-Touch-Play, so the TV follows -- on hardware whose network
        power-on actually boots the unit (genuine OPPO; verified on a TCL Q9L).

        NOTE: the M9207 Plus / UDP-203 clone does NOT support a network-triggered grab -- its #POF is a
        sleep and #PON is a no-op (the unit only does a full power-on, and thus One-Touch-Play, from an
        IR/remote power button). On that hardware the grab is manual/IR; disable it via grab_tv_on_play.
        The OPPO model (oppo_model) only affects the NFS playback layout, not this grab."""
        try:
            self.send_control_command("#POF")
        except OppoError as exc:
            # A transient first-send failure must NOT skip #PON -- the power-ON is the leg that fires
            # the OPPO's One-Touch-Play and grabs the TV. (#PON's own failure still raises.)
            log("OPPO #POF failed (continuing to #PON): {}".format(exc))
        time.sleep(delay)
        self.send_control_command("#PON")

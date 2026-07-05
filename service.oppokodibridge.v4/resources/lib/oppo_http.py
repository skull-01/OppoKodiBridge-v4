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

import http.client
import json
import re
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

# Reference-aligned (skull-01/emby-chinoppo-bridge): the OPPO's mount/play can be slow on a fragile
# SMB->NFS proxy; a too-short deadline turns a slow-but-fine handoff into a false failure (#22).
MOUNT_TIMEOUT = 60.0
PLAY_TIMEOUT = 60.0
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


def unwrap_multipath(source: str) -> list:
    """Expand a Kodi ``multipath://`` source into its member paths (URL-decoded); a non-multipath
    source returns ``[source]``. Kodi encodes a multipath as ``multipath://`` followed by each member
    path percent-encoded and joined with ``/`` (so a member's own slashes survive as ``%2f``)."""
    text = str(source or "").strip()
    if not text:
        return []
    if not text.lower().startswith("multipath://"):
        return [text]
    body = text[len("multipath://"):]
    return [urllib.parse.unquote(part) for part in body.split("/") if part]


def _decode_share(text: str) -> str:
    """URL-decode an ``nfs://``/``smb://`` path so it compares literally (matches split_share_relative)."""
    text = str(text or "")
    if text.lower().startswith(("nfs://", "smb://")):
        return urllib.parse.unquote(text)
    return text


def _has_share_path(prefix: str) -> bool:
    """A ``scheme://authority`` with NO path component can't be a share root (the host/share name would
    fold into the in-share path). Require at least one ``/`` after the ``://`` authority."""
    if "://" in prefix:
        return "/" in prefix.split("://", 1)[1]
    return True


def detect_path_from(media_file: str, sources) -> Optional[str]:
    """The Kodi source root the played ``media_file`` lives under -- i.e. ``path_from`` derived from
    Kodi's OWN configured sources instead of the typed setting. The SHALLOWEST (broadest) matching
    source wins (#16). ``path_to`` is the OPPO EXPORT ROOT, and ``path_from`` pairs with it at the SAME
    depth -- the share root. So the auto-detected ``path_from`` must anchor at that broadest level: for a
    share with nested sub-sources (e.g. both ``.../Super3Share`` and ``.../Super3Share/Movies``), picking
    the DEEPER source strips too much and mis-anchors ``path_to`` (the ``Movies`` segment is lost, so the
    OPPO mounts ``<path_to>/<disc>`` instead of ``<path_to>/Movies/<disc>``). Shallowest keeps the full
    in-share sub-path.

    Same accept rule as ``split_share_relative``: a candidate matches only when the file sits strictly
    BELOW it (a non-empty in-share remainder), so a source that EQUALS the played path -- which
    ``split_share_relative`` would reject as unmappable, stranding the handoff -- is never selected;
    shortest-prefix then still picks the broadest MAPPABLE source. Boundary-safe (``Super3Share-4K`` never
    matches ``Super3Share``); a path-less ``scheme://host`` source is skipped. Returns the matching root
    (trailing slash stripped) or ``None``. Pure: pass the result to ``split_share_relative`` as
    ``path_from``."""
    text = _decode_share(media_file)
    best: Optional[str] = None
    for source in sources or []:
        prefix = _decode_share(source).strip().rstrip("/")
        if not prefix or not _has_share_path(prefix):
            continue
        if not text.startswith(prefix + "/"):
            continue
        if not text[len(prefix):].lstrip("/"):
            continue  # exact-equal / prefix-plus-slashes: no in-share path -> split would reject it
        if best is None or len(prefix) < len(best):  # shallowest/broadest mappable source wins (#16)
            best = prefix
    return best


def oppo_mount_folder(folder: Optional[str], path_to: str) -> str:
    """The OPPO export folder to mount = the OPPO export root (``path_to``) + the in-share folder."""
    base = (path_to or "").strip().strip("/")
    rel = (folder or "").strip("/")
    if base and rel:
        return base + "/" + rel
    return base or rel


# A /getNfsShareFolderlist entry's export path sits in a run of printable ASCII between the reply's
# length/control bytes. Match a path-safe printable run: letters/digits + . _ - / -- NO space and NO
# colon, so an HTTP status line / header / error prose ('HTTP/1.1 200 OK', 'Server: nginx/1.18.0') from a
# fragile SMB->NFS proxy splits apart instead of being latched as an export root (#11 audit).
_SHARE_TOKEN_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._\-/]*")


def parse_nfs_share_root(raw: Any) -> Optional[str]:
    r"""Best-effort: the OPPO NFS export ROOT from a ``/getNfsShareFolderlist`` reply -- used to
    auto-detect ``path_to`` when the operator leaves it blank (mirrors ``detect_path_from`` for
    ``path_from``). The reply is a length-prefixed binary blob; decoded ``utf-8 errors=replace`` it reads
    like ``"\r\x00\x00\x00srv/nfs/media\x01..."``. Scan the printable-ASCII runs and return the first that
    looks like an export root: at least THREE path segments (``a/b/c``, i.e. >=2 slashes -- the known
    ``srv/nfs/media`` shape), trailing slash stripped. The >=2-slash bar plus the no-space/no-colon token
    rule reject a proxy's HTTP/header/error/version fragment (which would otherwise be mistaken for the
    root); a simpler 1-slash root is not auto-detected -- type ``path_to`` for those. ``None`` if nothing
    plausible is present.

    Best-effort by design: the exact framing is unconfirmed on hardware (a long root's length prefix can
    itself be a printable byte and merge into the token), so this is validated against a real capture at
    verify time and NEVER overrides a typed ``path_to`` -- it only fills a blank one. KNOWN LIMITATION: a
    >=2-slash NOISE token appearing BEFORE the real root (e.g. a proxy error body that echoes a request
    path) is still latched, not skipped. This is CONTAINED, not a mis-play: a wrong ``path_to`` yields a
    mount folder the OPPO rejects, so ``reply_succeeded`` is false and the handoff ABORTS before play --
    the worst case is a failed handoff, never a wrong-file play or NFS-client corruption."""
    if not raw:
        return None
    for match in _SHARE_TOKEN_RE.finditer(str(raw)):
        token = match.group(0).strip("/")
        if token.count("/") >= 2:  # >=3 segments -> the export root; rejects 1-slash proxy fragments
            return token
    return None


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


_STATUS_KEYS = ("status", "state", "play_status", "e_play_status", "playStatus")
_PAUSE_STATUSES = {"PAUSE", "PAUSED"}


def _flag_truthy(flag: Any) -> bool:
    if isinstance(flag, bool):
        return flag
    return flag is not None and str(flag).strip().lower() in ("1", "true", "yes", "playing")


def info_is_playing(info: Any) -> bool:
    """True when a ``/getglobalinfo`` payload indicates active (or loading) playback."""
    for container in _containers(info):
        for key in _PLAYING_FLAGS:
            if _flag_truthy(container.get(key)):
                return True
        for key in _STATUS_KEYS:
            value = container.get(key)
            if value is not None and not status_is_idle(value):
                return True
    return False


def info_is_paused(info: Any) -> bool:
    """True when ``/getglobalinfo`` reports a PAUSED transport state. Distinguished from general playing
    so the monitor can treat a long pause specially (#30): a paused disc is alive but not progressing, so
    it must not burn the absolute watch ceiling and get the TV reclaimed out from under it."""
    for container in _containers(info):
        for key in _STATUS_KEYS:
            value = container.get(key)
            if value is not None and str(value).strip().upper() in _PAUSE_STATUSES:
                return True
    return False


def playing_flags(info: Any) -> dict:
    """Each known playback flag present in the payload -> its truthiness (across all containers). For the
    Setup & tests ISO/BDMV capability checks (#12/#13), which REPORT every flag the OPPO raised rather
    than collapse to one boolean -- the clone's per-flag ISO/BDMV behaviour is unverified."""
    out: dict = {}
    for container in _containers(info):
        for key in _PLAYING_FLAGS:
            flag = container.get(key)
            if flag is not None:
                out[key] = bool(out.get(key)) or _flag_truthy(flag)
    return out


_FALSE_TOKENS = ("0", "false", "no", "off")


def reply_failed(reply: Any) -> bool:
    """True when an OPPO JSON reply's ``success`` field reports failure.

    The app API is community-reverse-engineered and loosely typed -- ``success`` can come back as a
    real bool ``false``, an int ``0``, or a string ``"false"`` / ``"0"`` depending on firmware (the
    same module already coerces such variants for the playback flags in ``info_is_playing``). A plain
    ``reply.get("success") is False`` only matches a genuine bool, so a non-bool failure slipped
    through and was treated as success. A MISSING ``success`` is NOT a failure (the device often omits
    it on success), preserving the prior "only an explicit failure counts" behaviour."""
    if not isinstance(reply, dict) or "success" not in reply:
        return False
    value = reply.get("success")
    if isinstance(value, bool):
        return not value
    if isinstance(value, (int, float)):
        return value == 0
    return str(value).strip().lower() in _FALSE_TOKENS


def reply_succeeded(reply: Any) -> bool:
    """True only when an OPPO mount/play reply AFFIRMATIVELY confirms success.

    Stricter than ``not reply_failed(...)`` -- this is the abort-before-play gate (#18, tightening
    #17). A missing/None reply, an empty ``{}``, or the non-JSON sentinel ``{"raw": ...}`` (an
    unparseable body from a fragile proxy) is NOT a confirmed success, so the handoff aborts instead of
    firing a play into a bad mount. A parsed JSON object with real content that is not an explicit
    failure counts as success (the device often omits ``success`` on a genuine success -- see
    ``reply_failed``)."""
    if not isinstance(reply, dict) or not reply:
        return False
    if set(reply.keys()) == {"raw"}:  # _get_json's non-JSON sentinel -- body did not parse as an object
        return False
    return not reply_failed(reply)


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

    @property
    def _read_retries(self) -> int:
        """Bounded transient-retry count for idempotent READS only (mount/play/stop never retry, so a
        lost response can't double-fire a side effect). Default 1 (see config.http_retries) -- #22."""
        return max(0, int(getattr(self.cfg, "http_retries", 1) or 0))

    def _get(self, endpoint: str, timeout: Optional[float] = None, retries: int = 0) -> str:
        url = self._base() + endpoint
        request_timeout = float(timeout if timeout is not None else self.cfg.socket_timeout)
        attempts = max(1, int(retries) + 1)
        last: Optional[Exception] = None
        for attempt in range(attempts):
            try:
                with urllib.request.urlopen(url, timeout=request_timeout) as response:
                    body = response.read().decode("utf-8", errors="replace")
                    status = getattr(response, "status", 200) or 200
                    if status >= 400:
                        raise OppoError("OPPO HTTP {} for {}".format(status, url))
                    return body
            except OppoError:
                raise  # a real >=400 status -- not a transport blip, never retried
            except (OSError, http.client.HTTPException) as exc:
                # http.client.HTTPException (BadStatusLine / IncompleteRead / ...) is NOT an OSError, so
                # it previously escaped this handler and crashed the caller mid-playback (#19).
                last = exc
                if attempt + 1 < attempts:
                    time.sleep(0.3)
                    continue
                raise OppoError("OPPO HTTP request failed for {}: {}".format(url, exc)) from exc
        raise OppoError("OPPO HTTP request failed for {}: {}".format(url, last))  # pragma: no cover

    def _get_json(self, endpoint: str, timeout: Optional[float] = None, retries: int = 0) -> dict:
        body = self._get(endpoint, timeout=timeout, retries=retries)
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
        return self._get("/getmainfirmwareversion", retries=self._read_retries)

    def get_setup_menu(self) -> str:
        return self._get("/getsetupmenu", retries=self._read_retries)

    def signin(self, app_ip: str = "127.0.0.1") -> str:
        payload = urllib.parse.quote('{"appIconType":1,"appIpAddress":"%s"}' % app_ip)
        return self._get("/signin?" + payload, timeout=15)

    def get_device_list(self) -> dict:
        return self._get_json("/getdevicelist", retries=self._read_retries)

    def get_global_info(self) -> dict:
        return self._get_json("/getglobalinfo", retries=self._read_retries)

    def get_playing_time(self) -> str:
        """Raw ``/getplayingtime`` reply -- progress/position info for the current item. Used by the
        first-run wizard's best-effort mount-path probe (the response format is unconfirmed; returned
        raw so the wizard can scan it for a file path)."""
        return self._get("/getplayingtime", timeout=8)

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

    def _mount_dir(self, nfs: bool = True) -> str:
        """The OPPO mount directory under ``/mnt`` for the play path (``/mnt/<dir>``). The real mount
        point can't be read back from the app API (#14), so it's a configurable OVERRIDE (``oppo_mount``,
        default ``nfs1``); when unset it falls back to the historic ``nfs1``/``cifs1`` pair so the proven
        ``/mnt/nfs1`` path is unchanged."""
        configured = str(getattr(self.cfg, "oppo_mount", "") or "").strip().strip("/")
        return configured or ("nfs1" if nfs else "cifs1")

    def play_file(self, server: str, rel_path: str, index: str = "0", nfs: bool = True) -> dict:
        mount_path = self._mount_dir(nfs)
        inner = (
            '"path":"/mnt/%s/%s","index":%s,"type":1,"appDeviceType":2,"extraNetPath":"%s","playMode":0'
            % (mount_path, rel_path, index, server)
        )
        endpoint = "/playnormalfile?{" + urllib.parse.quote(inner) + "}"
        return self._get_json(endpoint, timeout=PLAY_TIMEOUT)

    def stop(self) -> dict:
        """Send STOP via the app API (/sendremotekey STP) -- clears any prior playback before loading
        new media (sent ahead of an ISO open, reference-aligned)."""
        return self._get_json("/sendremotekey?" + urllib.parse.quote('{"key":"STP"}'))

    def play_bdmv(self, disc_folder_name: str, nfs: bool = True) -> dict:
        """Play a Blu-ray disc FOLDER (one containing BDMV). On this OPPO ``/checkfolderhasBDMV``
        doesn't just check -- it starts the disc. ``disc_folder_name`` is relative to the mount; when
        the disc structure IS the mount root (a disc folder sitting at the export root) it is empty, so
        the folderpath is the bare mount (``/mnt/nfs1``) -- never a dangling ``/mnt/nfs1/``."""
        mount_path = self._mount_dir(nfs)
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
        """Four-state playback probe for the stop-watcher: ``"playing"`` / ``"paused"`` / ``"idle"`` /
        ``"unknown"``.

        Unlike is_playing() (which collapses a transport failure to False), this distinguishes a
        confirmed-idle read from an unreadable one, so the monitor never mistakes a network blip for a
        stop (a premature mid-playback reclaim) nor loops forever on an unreachable OPPO. ``"paused"`` is
        split out from ``"playing"`` so a long pause doesn't burn the absolute watch ceiling (#30)."""
        try:
            info = self.get_global_info()
        except OppoError:
            return "unknown"
        if info_is_playing(info):
            return "paused" if info_is_paused(info) else "playing"
        return "idle"

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
        IR/remote power button). On that hardware the grab is manual/IR. The orchestrator skips this
        power-cycle entirely on the M9207 (see cec.grab_supported), so oppo_model now gates ONLY whether
        the network grab is attempted (stop detection is HTTP-only for every model)."""
        try:
            self.send_control_command("#POF")
        except OppoError as exc:
            # A transient first-send failure must NOT skip #PON -- the power-ON is the leg that fires
            # the OPPO's One-Touch-Play and grabs the TV. (#PON's own failure still raises.)
            log("OPPO #POF failed (continuing to #PON): {}".format(exc))
        time.sleep(delay)
        self.send_control_command("#PON")

"""Hand a disc file to the OPPO over the HTTP app API -- pure OPPO playback.

No TV/CEC switching (that is ``cec.py``) and no playback monitoring (that is ``monitor.py``); the
orchestrator wires the three together. The exact device sequence (verified live on the M9205):
wake (UDP NOTIFY -> :7624) -> init (firmware/setupmenu/signin/globalinfo) -> login the OPPO's own NFS
server -> mount the FILE'S FOLDER -> play the bare basename (or ``checkfolderhasBDMV`` for a disc
folder). Mount the file's folder and play the bare name; never mount a non-exported folder.
"""
from __future__ import annotations

from . import cec, detector
from .kodilog import log
from .monitor import interruptible_sleep
from .oppo_http import (
    OppoError,
    detect_path_from,
    local_ip_toward,
    nfs_server_from_devices,
    oppo_mount_folder,
    parse_media_path,
    reply_failed,
    split_share_relative,
)


def _best_effort(fn, label: str):
    try:
        return fn()
    except OppoError as exc:
        log("{} step skipped (non-fatal): {}".format(label, exc))
        return None


def play(config, client, kodi_file: str, should_abort=None) -> bool:
    """Wake the OPPO, run the init dance, mount the file's folder, and start playback.

    Returns True if the OPPO accepted the file (playback not yet confirmed -- ``monitor`` does that)."""
    if should_abort is None:
        should_abort = lambda: False

    # path_from (the Kodi-side share root we strip) pairs with path_to (the OPPO export root) at the
    # SAME depth, so a correctly-typed path_from is AUTHORITATIVE: use it whenever it maps the file --
    # never override it (a detected root at a different depth would mis-anchor path_to and mis-mount).
    # Only when the typed path_from does NOT map (blank or stale) do we consult Kodi's OWN configured
    # sources and re-derive path_from (longest-prefix). Kodi-side only -- no OPPO contact, best-effort.
    target = kodi_file.rstrip("/")
    path_from = config.path_from
    folder, basename = split_share_relative(target, path_from)
    if not basename and getattr(config, "path_from_autodetect", True):
        detected = detect_path_from(target, cec.kodi_video_sources(config))
        if detected:
            log("path_from auto-detected from Kodi sources: {!r} (typed prefix did not map)".format(detected))
            path_from = detected
            folder, basename = split_share_relative(target, path_from)
    if not basename:
        log("Cannot map {!r} with path_from={!r}".format(kodi_file, path_from))
        return False

    # The in-share path to what the OPPO should open: the disc FOLDER (BDMV / VIDEO_TS) for a disc,
    # else the file itself. Detect on the slash-bearing form too, so a disc FOLDER path (e.g.
    # .../VIDEO_TS, trailing slash stripped above) is still recognised. An .iso always takes the file
    # branch, even under a BDMV/VIDEO_TS directory.
    rel = (folder + "/" + basename) if folder else basename
    is_disc = (detector.is_disc_path(rel) or detector.is_disc_path(rel + "/")) and not detector.is_iso(rel)
    is_iso_file = detector.is_iso(rel)
    target = detector.disc_folder(rel + "/") if is_disc else rel
    # Mount the target's PARENT folder, play its bare leaf name (/mnt/nfs1/<leaf>). The OPPO won't play
    # sub-paths of a mount, so this is the only layout proven on the UDP-203 platform (live on the
    # M9205; the M9207 Plus uses it too). oppo_model does not change the play path -- it now gates only
    # the TV grab (cec.grab_supported); stop detection is HTTP-only for every model.
    mount_rel, play_name = target.rsplit("/", 1) if "/" in target else ("", target)

    if not client.wake_and_wait():
        log("OPPO app API ({}:{}) did not wake".format(config.oppo_ip, config.oppo_http_port))
        return False

    # Init dance (without it, signin/mount fail on a fresh API session).
    _best_effort(client.get_firmware_version, "firmware")
    _best_effort(client.get_setup_menu, "setup")
    app_ip = local_ip_toward(config.oppo_ip, config.oppo_http_port)
    _best_effort(lambda: client.signin(app_ip), "signin")
    _best_effort(client.get_global_info, "global info")

    # Dual-homed NAS: use the OPPO's own NFS server from its device list, not Kodi's address.
    server = nfs_server_from_devices(_best_effort(client.get_device_list, "device list"))
    if not server:
        server = parse_media_path(kodi_file)[0]

    _best_effort(lambda: client.login_nfs(server), "login")
    _best_effort(client.get_nfs_share_list, "share list")
    interruptible_sleep(2.0, should_abort)
    _best_effort(client.get_setup_menu, "setup")

    mount_folder = oppo_mount_folder(mount_rel, config.path_to)
    log("Handoff: server={} disc={} mount={!r} play={!r}".format(server, is_disc, mount_folder, play_name))
    # Mount the file's folder -- reference-faithful (skull-01/emby-chinoppo-bridge-ri playback.py):
    # at most TWO attempts, a fresh login (NEVER an unmount) between them, and ABORT before play if both
    # fail. Corruption-safety learned on hardware: hammering login+mount or issuing an unmount when
    # nothing is mounted drives the OPPO's NFS client into a ~20s-blocking, corrupted state (and can take
    # down a fragile SMB->NFS proxy) -- so this path never unmounts, treats a timeout/None reply as a
    # failure (not a silent success), and stops instead of firing a play into a bad mount. The mount
    # folder is share-relative with NO leading slash (oppo_mount_folder strips it), which the OPPO NFS
    # client requires -- a leading slash makes it return 'failed'.
    mounted = False
    for attempt in range(2):
        reply = _best_effort(lambda: client.mount_nfs(server, mount_folder), "mount")
        if reply is not None and not reply_failed(reply):
            mounted = True
            break
        if attempt == 0:
            why = (reply.get("retInfo") or reply.get("msg")) if isinstance(reply, dict) else "no reply/timeout"
            log("mount attempt 1 failed ({}); re-login and retry (no unmount -- avoids NFS-client corruption)"
                .format(why))
            _best_effort(lambda: client.login_nfs(server), "re-login")
            interruptible_sleep(2.0, should_abort)
    if not mounted:
        log("mount failed after 2 attempts; aborting handoff -- not sending a play into a bad mount")
        return False

    if is_disc:
        # BDMV / VIDEO_TS disc folder. Reference-aligned (emby-chinoppo-bridge): no STP/settle --
        # checkfolderhasBDMV starts the disc directly. If it does not start it, fall back to opening
        # the folder as a file (mirrors the reference's check_folder_has_bdmv -> play_file fallback).
        reply = _best_effort(lambda: client.play_bdmv(play_name), "play-bdmv")
        if reply is None or reply_failed(reply):
            log("checkfolderhasBDMV did not start the disc; falling back to play_file")
            reply = _best_effort(lambda: client.play_file(server, play_name), "play-bdmv-fallback")
    elif is_iso_file:
        # Disc image. Reference-aligned: send STP to clear any prior playback, settle ~4s, then open
        # the .iso. (Earlier v4 builds sent the STP before BDMV instead; the proven reference does the
        # opposite -- STP+settle for the ISO, nothing before the disc folder -- so this matches it.)
        _best_effort(client.stop, "stop")
        interruptible_sleep(4.0, should_abort)
        reply = _best_effort(lambda: client.play_file(server, play_name), "play-iso")
    else:
        reply = _best_effort(lambda: client.play_file(server, play_name), "play")
    log("Play reply: {!r}".format(reply))
    if reply is None:
        # the play HTTP call itself failed (OppoError -> _best_effort returned None); don't claim the
        # OPPO accepted it, or the monitor would poll for ~grace*interval seconds for playback that
        # will never start.
        log("OPPO play call failed (no reply); not waiting for playback")
        return False
    if reply_failed(reply):
        log("OPPO rejected the file: {}".format(reply.get("retInfo") or reply.get("msg") or ""))
        return False
    return True

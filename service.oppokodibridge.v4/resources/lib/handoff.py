"""Hand a disc file to the OPPO over the HTTP app API -- pure OPPO playback.

No TV/CEC switching (that is ``cec.py``) and no playback monitoring (that is ``monitor.py``); the
orchestrator wires the three together. The exact device sequence (verified live on the M9205):
wake (UDP NOTIFY -> :7624) -> init (firmware/setupmenu/signin/globalinfo) -> login the OPPO's own NFS
server -> mount the FILE'S FOLDER -> play the bare basename (or ``checkfolderhasBDMV`` for a disc
folder). Mount the file's folder and play the bare name; never mount a non-exported folder.
"""
from __future__ import annotations

from . import detector
from .kodilog import log
from .monitor import interruptible_sleep
from .oppo_http import (
    OppoError,
    local_ip_toward,
    nfs_server_from_devices,
    oppo_mount_folder,
    parse_media_path,
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

    folder, basename = split_share_relative(kodi_file.rstrip("/"), config.path_from)
    if not basename:
        log("Cannot map {!r} with path_from={!r}".format(kodi_file, config.path_from))
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
    # M9205; the M9207 Plus uses it too). oppo_model no longer changes the play path -- it only selects
    # the stop-monitor transport (see monitor._verbose_monitor_supported).
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
    mount = _best_effort(lambda: client.mount_nfs(server, mount_folder), "mount")
    if isinstance(mount, dict) and mount.get("success") is False:
        log("mount failed ({}); re-login and retry".format(mount.get("retInfo") or mount.get("msg")))
        _best_effort(lambda: client.login_nfs(server), "re-login")
        interruptible_sleep(2.0, should_abort)
        _best_effort(lambda: client.mount_nfs(server, mount_folder), "mount retry")

    if is_disc:
        # BDMV / VIDEO_TS disc folder. Reference-aligned (emby-chinoppo-bridge): no STP/settle --
        # checkfolderhasBDMV starts the disc directly. If it does not start it, fall back to opening
        # the folder as a file (mirrors the reference's check_folder_has_bdmv -> play_file fallback).
        reply = _best_effort(lambda: client.play_bdmv(play_name), "play-bdmv")
        if reply is None or (isinstance(reply, dict) and reply.get("success") is False):
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
    if isinstance(reply, dict) and reply.get("success") is False:
        log("OPPO rejected the file: {}".format(reply.get("retInfo") or reply.get("msg") or ""))
        return False
    return True

# Playing ISO & Blu-ray (BDMV) discs to an OPPO / M9205 over the network

The OPPO UDP-203/205 — and M9205-style clones — can play **disc images (`.iso`)** and **full Blu-ray
disc folders (`BDMV`)** straight from a network share, keeping disc menus, lossless audio and
bitstreaming that a transcoding player can't. This guide shows how, two ways:

- **[The easy way](#the-easy-way--oppokodibridge-add-on)** — the *OppoKodiBridge* Kodi add-on: browse
  in Kodi, it hands the file to the OPPO automatically.
- **[The manual way](#the-manual-way--http-api)** — the raw HTTP commands the add-on sends, so you
  can drive it from `curl` or your own script.

> The OPPO's HTTP "app API" (port **436**) is what the official phone app uses. It is
> community-reverse-engineered, **not** an official protocol, and behaviour varies on clones.

---

## What you need

- The OPPO and your NAS on the same network, with the OPPO able to mount the share over **NFS**
  (SMB also works; this guide uses NFS).
- The NAS **exporting** the folder that holds your ISO / BDMV.
- The OPPO's IP address (e.g. `192.168.10.10`).
- The NFS server address **as the OPPO sees it** — which may not be the address your PC uses (see
  next section).
- CEC turned on at the TV and the OPPO if you want the TV to switch inputs automatically.

### The dual-homed NAS gotcha (read this first)

The most common mistake is using the wrong server address. Your NAS may be reachable at **two**
addresses — one your PC/Kodi uses, and a different one on the subnet the OPPO is on. You must use the
address/export **the OPPO itself can reach**. Ask the OPPO directly:

- `GET http://<oppo-ip>:436/getdevicelist` → find the entry with `"sub_type":"nfs"`; its `name` is the
  NFS server to use (e.g. `192.168.10.20`).
- `GET http://<oppo-ip>:436/getNfsShareFolderlist` → lists the export roots the OPPO sees
  (e.g. `srv/nfs/media`).

So a file Kodi sees at `nfs://192.168.1.177/mnt/Super3/Super3Share/Movies/Dune (2021).iso` might live,
from the OPPO's side, on server `192.168.10.20`, export `srv/nfs/media`, path `Movies/Dune (2021).iso`.

---

## The manual way — HTTP API

All calls are HTTP **GET** to `http://<oppo-ip>:436/...`. The `:436` API **sleeps after boot**, so you
wake it first, run a short init handshake, then log in / mount / play.

> **Encoding note:** the query format is quirky and inconsistent between endpoints. Some take the whole
> `{...}` JSON percent-encoded after the `?`; others take literal braces with only the path value
> encoded. Percent-encode spaces and punctuation in paths (` ` → `%20`, `(` → `%28`). The reference
> implementation with exact encoding is `resources/lib/oppo_http.py` in this repo.

### Step 0 — Wake the API
Send the UDP datagram `NOTIFY OREMOTE LOGIN` to **port 7624** on the OPPO, then poll until `:436`
answers. (It is **not** an HTTP call and **not** a broadcast.)

```sh
# one-shot wake from a shell with python:
python3 -c "import socket; socket.socket(socket.AF_INET,socket.SOCK_DGRAM).sendto(b'NOTIFY OREMOTE LOGIN',('192.168.10.10',7624))"
```

### Step 1 — Init handshake (required, or sign-in/mount fail)
```sh
curl "http://192.168.10.10:436/getmainfirmwareversion"
curl "http://192.168.10.10:436/getsetupmenu"
# signin: the WHOLE JSON is percent-encoded. appIpAddress = this machine's IP.
#   {"appIconType":1,"appIpAddress":"192.168.10.50"}
curl "http://192.168.10.10:436/signin?%7B%22appIconType%22%3A1%2C%22appIpAddress%22%3A%22192.168.10.50%22%7D"
curl "http://192.168.10.10:436/getglobalinfo"
```

### Step 2 — Find the OPPO's NFS server (once)
```sh
curl "http://192.168.10.10:436/getdevicelist"          # -> "sub_type":"nfs" => server, e.g. 192.168.10.20
curl "http://192.168.10.10:436/getNfsShareFolderlist"  # -> export root, e.g. srv/nfs/media
```

### Step 3 — Log in and mount the file's FOLDER
Mount the **folder that contains** your file (not the file itself, and never a folder the NAS does not
export). It mounts at `/mnt/nfs1`.
```sh
# loginNfsServer: whole JSON percent-encoded -> {"serverName":"192.168.10.20"}
curl "http://192.168.10.10:436/loginNfsServer?%7B%22serverName%22%3A%22192.168.10.20%22%7D"
# mountNfsSharedFolder: literal braces, encode the folder value. Mounts at /mnt/nfs1
curl "http://192.168.10.10:436/mountNfsSharedFolder?{\"server\":\"192.168.10.20\",\"folder\":\"srv/nfs/media/Movies\"}"
```

### Step 4a — Play an ISO (or any single file)
Mount the file's folder (above), then play the **bare filename** from `/mnt/nfs1` — the OPPO won't play
sub-paths of a mount.
```sh
# /playnormalfile? then {  <percent-encoded inner JSON>  }
# inner: "path":"/mnt/nfs1/Dune (2021).iso","index":0,"type":1,"appDeviceType":2,"extraNetPath":"192.168.10.20","playMode":0
curl "http://192.168.10.10:436/playnormalfile?{%22path%22%3A%22%2Fmnt%2Fnfs1%2FDune%20(2021).iso%22%2C%22index%22%3A0%2C%22type%22%3A1%2C%22appDeviceType%22%3A2%2C%22extraNetPath%22%3A%22192.168.10.20%22%2C%22playMode%22%3A0}"
```

### Step 4b — Play a Blu-ray disc folder (BDMV)
A BDMV disc is a **folder** (containing a `BDMV/` subfolder). You don't play `index.bdmv`; you point the
OPPO at the disc folder. Mount the disc folder's **parent**, send a STOP to clear any stuck disc, then
call `checkfolderhasBDMV` — which on this OPPO **starts the disc**, it doesn't merely check.
```sh
# mount the PARENT of the disc folder, e.g. folder "srv/nfs/media/Movies"
curl "http://192.168.10.10:436/sendremotekey?{\"key\":\"STP\"}"
# then start the disc folder (relative to the mount):
curl "http://192.168.10.10:436/checkfolderhasBDMV?{\"folderpath\":\"/mnt/nfs1/Movies/Dune (2021)\"}"
```
(DVD `VIDEO_TS` folders work the same way.)

### Checking state / stopping
```sh
curl "http://192.168.10.10:436/getglobalinfo"   # booleans: is_video_playing / is_bdmv_playing / is_disc_playing ...
curl "http://192.168.10.10:436/sendremotekey?{\"key\":\"STP\"}"   # stop
```
Power/transport can also go over the OPPO's IP-control port **:23** (or RS-232): `#PON`, `#POF`, `#QPW`.

---

## The easy way — OppoKodiBridge add-on

The add-on automates everything above: you browse your library in Kodi, press play on an ISO or BDMV,
and it hands the file to the OPPO and switches the TV.

1. **Install** `service.oppokodibridge` in Kodi (Add-ons → Install from zip file → the release zip).
2. **Configure** (add-on settings):
   - **OPPO IP** — e.g. `192.168.10.10`.
   - **Kodi path prefix** (`path_from`) — the share prefix Kodi uses, e.g.
     `nfs://192.168.1.177/mnt/Super3/Super3Share`.
   - **OPPO path prefix** (`path_to`) — the OPPO's export root, e.g. `srv/nfs/media`.
   - The OPPO's NFS **server** is auto-detected from `/getdevicelist` — you don't set it.
3. **Play**: browse to an ISO or a BDMV folder in Kodi and press play. The add-on intercepts it, maps
   the path, mounts the folder on the OPPO, and starts the file (ISO) or disc (BDMV).

By default the add-on **only** hands **disc content** to the OPPO — `.iso` files and `BDMV`/`VIDEO_TS`
disc folders. Everything else (MKV, MP4, …) keeps playing in Kodi as normal. (Toggle: *"Only hand discs
& ISO files to the OPPO"*.)

**TV switching** is by HDMI-CEC — see the next section for how it works and the sharp edges.

---

## TV / HDMI switching — how it works, and what NOT to do

Getting the TV to switch to the OPPO (and back to Kodi) is done with **HDMI-CEC**, and it has some
genuinely dangerous pitfalls. The short version: **let the OPPO switch the TV itself; never try to force
the switch from the Kodi box.**

### How it works (the safe way)
- **To the OPPO:** the OPPO declares itself the HDMI **active source** when it **powers on** (CEC "One
  Touch Play"). So the add-on briefly **power-cycles** the OPPO (`#POF` → `#PON` over `:23`) at the start
  of a handoff, and the TV follows it. The OPPO only asserts on a power-**on** transition — *not* when it
  starts playing while already on — so the power-cycle is what triggers the switch. Cost: ~20–24 s,
  which is mostly the OPPO's boot time (it has to boot to play anyway).
- **Back to Kodi:** when playback stops, Kodi reclaims the TV with the `CECActivateSource` builtin.
- Both need **CEC enabled** on the TV and the OPPO — hardware toggles the add-on can't flip:
  - **TCL:** Settings → System → **CEC / T-Link → On**
  - **OPPO:** Setup → HDMI → **CEC → On**

### ⚠️ Do NOT inject CEC to "force" the switch
It is tempting to make the switch instant by having the **Kodi box** broadcast a CEC `<Active Source>`
for the OPPO's HDMI input. **Don't** — two different ways of doing this were tried and both broke things:

- **`cec-client`** opens a *second* libCEC client, which corrupts Kodi's own CEC: the stop-reclaim stops
  working and the input starts fighting.
- **Writing the frame to the Amlogic driver** (`/sys/class/aocec/cmd`) avoids a second client, but the
  injected `<Active Source>` carries a **spoofed initiator logical address** that collides with other
  devices on the shared CEC bus — it **cross-controlled an unrelated streaming box** (a Mi Box on another
  HDMI input started responding to the TV remote).

The rule behind both failures: **only a device may announce its own active source.** Spoofing it from
somewhere else corrupts CEC logical-address allocation and remote-control routing across the **whole**
bus, not just your two devices. So the OPPO's own One-Touch-Play (the power-cycle) is the only safe
switch — and since the ~24 s is mostly unavoidable boot time, there's little to gain anyway.

### Recovering from a stuck / confused CEC bus
If devices start getting cross-controlled, or inputs fight:
1. Put the OPPO in **standby** (`#POF`) so it stops asserting active source.
2. **Restart Kodi** — it re-allocates CEC logical addresses cleanly.
3. If a *sibling* device (a streaming stick on another input) is **still** misbehaving, its CEC state is
   cached inside it — **cold-start everything**, and crucially **unplug the TV from mains for ~30–60 s**.
   CEC state often survives *standby*; only a real power-off flushes the TV's routing table. Then power
   the TV on first, and the rest after.

### Faster, CEC-free alternative (optional, not built in yet)
A **network IR blaster** (e.g. a Broadlink RM4 mini) can switch the TV's input by sending the TV's own
**IR** codes — **instant** and completely **off the CEC bus**, so it can't disturb other devices. If the
~24 s power-cycle bothers you, that's the path: switch by IR and drop the CEC dance entirely. (A USB
*Flirc* won't do this — it's an IR *receiver*, not a blaster.)

### Note on `input.enablecec`
Kodi's `input.enablecec` setting is **invalid on CoreELEC** (JSON-RPC returns `-32602`); the add-on's
"auto-enable Kodi CEC" is a graceful no-op there. CEC is on by default on CoreELEC — you only need to
enable it on the **TV** and the **OPPO**.

---

## Gotchas & troubleshooting

- **Mount the folder, play the bare filename.** The OPPO won't play a sub-path of a mount, so mount the
  file's folder and play just the basename.
- **BDMV plays the disc folder, not `index.bdmv`** — via `checkfolderhasBDMV`, after a `STP` to clear a
  stuck disc.
- **Never mount a folder the NAS doesn't actually export.** Doing so can **hard-crash the OPPO** (both
  the `:436` and `:23` ports die) and it needs a mains power-cycle to recover. Only mount real exports.
- **Use the address the OPPO can reach**, from `/getdevicelist` — not necessarily your PC's NAS address.
- **Percent-encode** spaces and punctuation in paths.
- **Nothing plays / API silent?** The `:436` API sleeps after boot — (re)send the `NOTIFY OREMOTE LOGIN`
  wake to `:7624` and re-run the init handshake.
- **Mount fails the first time?** Log in again (`loginNfsServer`) and retry the mount.

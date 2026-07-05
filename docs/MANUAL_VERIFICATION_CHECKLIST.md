# Manual verification checklist

Hardware-in-the-loop checks the operator runs to verify implemented work. Code being implemented and
software-tested is **not** the same as verified — items here stay open until you confirm on hardware and
close the linked issue.

**Reply protocol:** reply with the row numbers + `PASS` / `FAIL` (e.g. "1 PASS, 3 FAIL: remote still
locks"). Software-only status is noted per row; everything below is `software-verified, hardware-pending`
until you run it.

---

## Bug-sweep fixes — branch `fix/bug-sweep-confirmed` (→ v4.1.1)

Off-box suite: **90 passed** (`PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest -q`).

| # | Issue / SHA | Check | What you should see |
|---|-------------|-------|---------------------|
| 1 | [#1](https://github.com/skull-01/OppoKodiBridge-v4/issues/1) `de7545a` | **M9207 (your unit):** play an ISO/BDMV disc, let it run, press Stop. Watch the IR remote responsiveness throughout. | Remote stays responsive during and after playback; Kodi reclaims the TV input on Stop. (M9207 already uses HTTP-only, so this is a no-regression check.) |
| 2 | [#1](https://github.com/skull-01/OppoKodiBridge-v4/issues/1) `de7545a` (superseded by v4.1.3) | **M9205 (if available):** same play→stop cycle. | Stop is detected within a few seconds over HTTP `/getglobalinfo`; the remote never wedges. **Note:** v4.1.3 removed the verbose `#SVM 3` watch entirely (the `:23` socket is never opened on any model), so #1's hang is now structurally eliminated — this row is just a no-regression check. |
| 3 | [#2](https://github.com/skull-01/OppoKodiBridge-v4/issues/2) `2f02deb` | Play a **loose `.bdmv`** that sits directly in a movie folder (no `BDMV/` subdir), e.g. `…/Movies/Film/index.bdmv`. | The OPPO mounts the **containing folder** and starts the disc (or cleanly falls back to file play). The OPPO does **not** hard-crash / reboot. |
| 4 | [#2](https://github.com/skull-01/OppoKodiBridge-v4/issues/2) `2f02deb` | Regression: play a **normal** Blu-ray (`…/Title/BDMV/index.bdmv`) and a DVD (`…/Title/VIDEO_TS/…`). | Both still hand off and play exactly as before. |
| 5 | [#3](https://github.com/skull-01/OppoKodiBridge-v4/issues/3) `2dd13e2` | Play a disc normally (happy path). | Playback starts; no spurious "rejected"/"failed" handling. (The non-bool `success` path is firmware-dependent and may not be observable on your unit — a clean happy path is sufficient.) |
| 6 | [#4](https://github.com/skull-01/OppoKodiBridge-v4/issues/4) `8e18956` | Regression: confirm normal disc routing still works (covered by rows 3–4). No separate hardware step. | No change in routing for real `nfs://`-prefixed paths. |

> Only the operator closes issues. When a row passes, close the linked issue (the implementing SHA is
> commented on it and it carries `status:awaiting-verify`).

---

## Model-gated OPPO grab — branch `fix/model-gated-grab` (→ v4.1.2)

Off-box suite: **93 passed**.

| # | Issue / SHA | Check | What you should see |
|---|-------------|-------|---------------------|
| 7 | [#5](https://github.com/skull-01/OppoKodiBridge-v4/issues/5) | **M9207 (your unit), `oppo_model=M9207`, leave `grab_tv_on_play` ON:** start a disc. | The OPPO is **not** power-cycled (no `#POF`/`#PON`); the box is not put to sleep; the IR remote stays responsive. You switch the TV to the OPPO input manually. Playback + the Kodi reclaim on stop still work. |
| 8 | [#5](https://github.com/skull-01/OppoKodiBridge-v4/issues/5) | **M9205 (if available), `oppo_model=M9205`, `grab_tv_on_play` ON:** start a disc. | The OPPO power-cycles and grabs the TV (its own One-Touch-Play) exactly as before — no regression. |

---

## HTTP-only stop monitor — branch `refactor/http-only-monitor` (→ v4.1.3)

Off-box suite: **86 passed**. The verbose `#SVM 3` watch on `:23` is removed; stop detection is HTTP
`/getglobalinfo` polling for **every** model (the reference-faithful behavior).

| # | Issue / SHA | Check | What you should see |
|---|-------------|-------|---------------------|
| 9 | [#6](https://github.com/skull-01/OppoKodiBridge-v4/issues/6) | **M9205 (if available), `oppo_model=M9205`:** play a disc, let it run, press Stop. | Playback is detected, the stop is detected within a few seconds over HTTP, and Kodi reclaims the TV. The OPPO's `:23` port is **never** opened for monitoring (only `#POF`/`#PON` on the grab). No remote sluggishness. |
| 10 | [#6](https://github.com/skull-01/OppoKodiBridge-v4/issues/6) | **M9207 (your unit):** play→stop cycle. | No change from v4.1.2 — HTTP-only stop detection, remote stays responsive. Regression check only. |

---

## Auto-detect `path_from` from Kodi sources — v4.1.7

Off-box suite: **124 passed** (`PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest -q`). Detection is
**detect-as-fallback**: the typed *Kodi path prefix* (`path_from`, NAS path mapping) stays authoritative
and is used whenever it maps the played file — Kodi is queried only when that field is blank or doesn't
match. Requires Kodi's JSON-RPC / web server enabled (already needed for the CEC reclaim). 5-lens
adversarial re-audit + a clean recursion pass; two HIGH issues found pre-release and fixed
(detect-first → detect-as-fallback; exact-equal source strand).

| # | Issue / SHA | Check | What you should see |
|---|-------------|-------|---------------------|
| 11 | [#9](https://github.com/skull-01/OppoKodiBridge-v4/issues/9) `cd9f7f7` | **Regression (most important):** leave your **existing, correctly-typed** `path_from` as-is, "Auto-detect the Kodi path prefix" ON (default), and play a disc/file you normally play. | Plays **exactly as before** — the typed value is authoritative and is NOT overridden. The Kodi log shows **no** `path_from auto-detected…` line for this play (Kodi isn't even queried when the typed prefix maps). |
| 12 | [#9](https://github.com/skull-01/OppoKodiBridge-v4/issues/9) `cd9f7f7` `bfb9d59` | **Zero-config:** blank the *Kodi path prefix* field (or set it deliberately wrong), keep auto-detect ON, then play a file that lives under one of your Kodi **video sources**. | The add-on auto-fills `path_from` from Kodi's sources and the file hands off + plays. The Kodi log shows `path_from auto-detected from Kodi sources: '…' (typed prefix did not map)`. (Needs the file to be under a configured Kodi video source; UPnP/plugin paths won't map — that's expected.) |
| 13 | [#9](https://github.com/skull-01/OppoKodiBridge-v4/issues/9) `bfb9d59` | **Toggle off:** with the *Kodi path prefix* still blank/wrong, turn "Auto-detect the Kodi path prefix" **OFF**, and play. | Playback is **not** handed off (the log shows `Cannot map …`); no Kodi sources query is made. Turning the toggle back ON (or fixing the typed field) restores the handoff. |

> Detects `path_from` only — `path_to` (the OPPO export root) and the mount point still need the
> on-device probe (issues #11/#14). Only the operator closes issues; each row's SHA is commented on #9
> and it carries `status:awaiting-verify`.

---

## NFS mount hardening (corruption-safety) — issue #17

Off-box suite: **127 passed**. `handoff.play` mount is now reference-faithful (skull-01/emby-chinoppo-bridge-ri):
≤2 attempts, re-login (never unmount) between, timeout/None counts as failure, abort-before-play if both
fail. Prevents the OPPO NFS-client corruption / ~20s-block that crashed the SMB→NFS proxy this session.
The abort / timeout / retry paths are **software-verified off-box** (hard to force on hardware).

| # | Issue / SHA | Check | What you should see |
|---|-------------|-------|---------------------|
| 14 | [#17](https://github.com/skull-01/OppoKodiBridge-v4/issues/17) `8990cf5` | **No-regression (after the proxy is restored):** play a normal ISO and a BDMV via Kodi handoff, as you did before. | Both still mount and play exactly as before — the first `mountNfsSharedFolder` may log `failed` once then the re-login retry succeeds (normal), and it plays. No new failures. |
| 15 | [#17](https://github.com/skull-01/OppoKodiBridge-v4/issues/17) `8990cf5` | **Corruption-safety (observational):** over repeated handoffs, watch that the OPPO/proxy stays healthy (no ~20s hangs, no proxy crash). | The add-on never issues an unmount and caps mounts at 2 attempts, so it should not drive the NFS client into the corrupted/blocking state. Report if any hang/crash recurs. |

---

## IR bench/dev tools — branch `feat/ir-dev-tools` (#24, #25)

Off-box suite: **182 passed** (`PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest -q --basetemp=<writable>`).
Two Tkinter **dev tools** (NOT shipped in the add-on): a Windows ZJIoT-serial console (#24) and a
Raspberry Pi 4 LIRC console (#25). All logic is unit-tested off-box; end-to-end needs the hardware you
**don't have yet** (ZJIoT module + USB-TTL; a Pi with a wired IR blaster/receiver on a Desktop/VNC
session). Two independent audits (one per app), confirmed fixes folded in. Rows 16/18/19 are blocked on
hardware; row 17 runs now on any Windows box.

| # | Issue / SHA | Check | What you should see |
|---|-------------|-------|---------------------|
| 16 | [#24](https://github.com/skull-01/OppoKodiBridge-v4/issues/24) `48d180a` | **ZJIoT console (needs the module + USB-TTL):** `pip install -r tools/requirements-dev.txt`, plug the USB-TTL, `python tools/zjiot_console.py`, pick the COM port + baud, Connect. | The port lists and Connect succeeds (log: "connected COMx @ …"); Send NEC / Send slot / Send exact draw module ACKs; **Learn** captures a code from a remote into the library. |
| 17 | [#24](https://github.com/skull-01/OppoKodiBridge-v4/issues/24) `48d180a` | **ZJIoT no-hardware smoke (any Windows, runnable now):** run it with nothing plugged in. | The window opens; the log notes "no serial ports (is pyserial installed…)"; buttons show a friendly error dialog rather than crashing. |
| 18 | [#25](https://github.com/skull-01/OppoKodiBridge-v4/issues/25) `48d180a` | **LIRC console (needs a Pi + wired IR, RPi OS Desktop/VNC):** `sudo apt install v4l-utils`, enable the `pwm-ir-tx`/`gpio-ir` overlays, `python3 tools/lirc_console.py`, Refresh. | TX and RX auto-classify into the dropdowns; Send NEC blasts (phone-camera flicker); **Loopback self-test** logs PASS when TX→RX are wired + aimed. |
| 19 | [#25](https://github.com/skull-01/OppoKodiBridge-v4/issues/25) `48d180a` | **LIRC learn (needs a Pi + RX):** point the TCL remote's HDMI/Source button at the receiver, Learn decoded (nec). | The real scancode is captured and can be saved to the shared library + replayed via Send NEC — this is how you get the true code instead of the disputed `0x57E3`. |

---

## v4.2.0 — reference-parity hardening (#18–#22) + pluggable TV-switch (#23/#26/#27)

Off-box suite: **227 passed**. Theme A (bug-sweep) got a standard independent re-audit (reference-aligned to
`emby-chinoppo-bridge`, clean); Theme C (tvswitch) got a **deep zero-regression fan-out** (cec-default confirmed
byte-for-byte identical). The IR transports ship **default-off** and are **software-verified only** (no hardware).

| # | Issue / SHA | Check | What you should see |
|---|-------------|-------|---------------------|
| 20 | [#18](https://github.com/skull-01/OppoKodiBridge-v4/issues/18) `f6bcb5a` | **No-regression:** play a normal ISO and a BDMV via Kodi handoff. | Both mount + play exactly as before. (A bare `{}` / non-JSON / timeout mount reply now ABORTS before play instead of firing into a bad mount — the intended change.) |
| 21 | [#19](https://github.com/skull-01/OppoKodiBridge-v4/issues/19) `f6bcb5a` | **Stability (observational):** over many handoffs, watch for any uncaught crash of the external player mid-playback. | No mid-playback crash from a transport hiccup; the TV reclaim always runs (HTTPException is now caught). |
| 22 | [#20](https://github.com/skull-01/OppoKodiBridge-v4/issues/20) `f6bcb5a` | **M9207:** Settings → "CEC switch-over test" on an M9207. | It shows "this model has no network CEC grab — skipped; switch manually" and does **not** power-cycle / wedge the unit. |
| 23 | [#21](https://github.com/skull-01/OppoKodiBridge-v4/issues/21) `f6bcb5a` | **Large UHD ISO:** play a big ISO that buffers slowly. | The TV is **not** reclaimed mid-load (~90s patience). ⚠️ **M2 note:** a genuinely >90s *silent* load could trigger the one-shot auto-heal (which sends STP then re-plays) — report if a big ISO ever gets interrupted at ~90s. |
| 24 | [#22](https://github.com/skull-01/OppoKodiBridge-v4/issues/22) `f6bcb5a` | **Slow proxy (after the proxy is restored):** handoff over the fragile SMB→NFS proxy. | Fewer false handoff failures. ⚠️ **M1 note:** on an *unreachable* OPPO the stop-watch now takes longer (~40–80s) to give up and reclaim the TV — bounded and expected (raised timeouts + one retry). |
| 25 | [#26](https://github.com/skull-01/OppoKodiBridge-v4/issues/26) `94ef41a` | **cec default no-regression:** leave `tv_switch_method` = `cec` (the default). | Grab/reclaim behave identically to v4.1.7 — the zero-regression guarantee (audited). |
| 26 | [#23](https://github.com/skull-01/OppoKodiBridge-v4/issues/23)/[#26](https://github.com/skull-01/OppoKodiBridge-v4/issues/26) `94ef41a` | **lirc (needs RPi4 host + wired IR):** set `tv_switch_method=lirc` + `ir_code_oppo`/`ir_code_kodi` (captured via `tools/lirc_console.py`), `ir_lirc_device`. Play then stop. | TV switches to the OPPO on play and back to Kodi on stop via `ir-ctl` — no OPPO power-cycle. |
| 27 | [#26](https://github.com/skull-01/OppoKodiBridge-v4/issues/26) `94ef41a` | **ir (needs Ugoos host + ZJIoT module):** set `tv_switch_method=ir` + `ir_serial_port` + codes. Play/stop. | TV switches via the serial IR module. ⚠️ Confirm the ZJIoT wire format on real hardware (the codec carries a "confirm-on-hardware" warning). |
| 28 | [#27](https://github.com/skull-01/OppoKodiBridge-v4/issues/27) `94ef41a` | **Provisioner (needs a Pi):** `python3 tools/setup_rpi4_lirc.py` (dry-run), then `sudo … --apply`. Re-run `--apply`. | Dry-run prints the plan and writes nothing; `--apply` installs v4l-utils + adds the overlay to the correct `config.txt` (a `.okb-bak` backup is made) and reports a reboot is needed; the re-run is a no-op (idempotent). |

---

## detect-cluster + sweep fixes — branch `feat/detect-cluster` (#10–#16, #28–#31)

Off-box suite: **262 passed**. Built proxy-unblocked; two independent adversarial audit rounds (11-agent +
4-agent re-audit) — a HIGH termination regression (#30) and a MED share-root parser false-positive were
caught and fixed; remaining items are LOW/contained + documented. **Default `cec`/`nfs1`/typed-path
behaviour is unchanged (zero regression).** Detection is **software-verified only** — the OPPO checks need
the box + the (now-restored) proxy. All rows implemented in `16c3cb1`. #32 (orchestrator early-switch) is a
design decision, intentionally **not** built.

| # | Issue / SHA | Check | What you should see |
|---|-------------|-------|---------------------|
| 29 | [#16](https://github.com/skull-01/OppoKodiBridge-v4/issues/16) `16c3cb1` | **path_from auto-detect (nested sources):** leave `path_from` blank with two overlapping Kodi video sources (a broad share + a nested sub-source); play a disc under the nested one. | Mounts under the FULL sub-path (anchored at the broad share root), not the truncated deep-source path. A typed `path_from` still wins, untouched. |
| 30 | [#11](https://github.com/skull-01/OppoKodiBridge-v4/issues/11) `16c3cb1` | **path_to auto-detect:** blank `path_to`, then play a disc. | The add-on parses the OPPO NFS share list for the export root and mounts under it (log: "path_to auto-detected …"). A TYPED path_to is never overridden. ⚠️ Best-effort — confirm the detected root matches your real export. |
| 31 | [#14](https://github.com/skull-01/OppoKodiBridge-v4/issues/14) `16c3cb1` | **Mount override / no-regression:** leave `oppo_mount` = `nfs1` (default) and play a disc; then set a custom value if your OPPO mounts elsewhere. | Default plays byte-identical `/mnt/nfs1/<leaf>`; a custom value changes the mount dir to `/mnt/<value>/…`. |
| 32 | [#10](https://github.com/skull-01/OppoKodiBridge-v4/issues/10) `16c3cb1` | **Detect-from-Kodi button:** Settings → NAS path mapping → "Detect the Kodi path prefix …". | Lists your Kodi video sources; picking one writes it to `path_from`. Empty sources → a friendly "add a source first" message. |
| 33 | [#12](https://github.com/skull-01/OppoKodiBridge-v4/issues/12) `16c3cb1` | **ISO capability check:** Settings → Setup & tests → "ISO playback check"; start an ISO on the OPPO, confirm. | Wakes the OPPO, then reports the playback flags it raised. If nothing plays it says so (also honours a status-only PLAY token). |
| 34 | [#13](https://github.com/skull-01/OppoKodiBridge-v4/issues/13) `16c3cb1` | **BDMV capability check:** "BDMV playback check"; start a Blu-ray folder on the OPPO, confirm. | Same as #33 for a BDMV disc — reports all raised flags (doesn't gate on `is_bdmv_playing` alone). |
| 35 | [#29](https://github.com/skull-01/OppoKodiBridge-v4/issues/29) `16c3cb1` | **Ping wakes the OPPO:** put the OPPO in standby (:436 asleep), then Settings → "Ping the OPPO". | HTTP API reports **OK** (the ping now OREMOTE-wakes first) instead of a false UNREACHABLE. |
| 36 | [#28](https://github.com/skull-01/OppoKodiBridge-v4/issues/28) `16c3cb1` | **Give-up STOP (observational):** if a handoff ever gives up (slow/never-starting ISO), watch the OPPO. | The OPPO isn't left playing to itself after Kodi reclaims the TV. No STOP is sent on an unmappable-file abort. |
| 37 | [#30](https://github.com/skull-01/OppoKodiBridge-v4/issues/30) `16c3cb1` | **Pause tolerance:** pause a disc on the OPPO for a long time. | The TV isn't reclaimed out from under the pause at the ~6h mark (pause has its own budget; the watch still terminates, bounded ~2×). |
| 38 | [#31](https://github.com/skull-01/OppoKodiBridge-v4/issues/31) `16c3cb1` | **Wizard IP display:** run the wizard, pick M9207 but type the M9205 default IP (`192.168.10.10`) with the OPPO off. | The "cannot reach" dialog names the **resolved** `192.168.10.228` (actually pinged), not the typed `.10`. |

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

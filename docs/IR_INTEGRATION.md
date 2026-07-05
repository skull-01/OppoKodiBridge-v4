# IR integration — Broadlink RM4 mini TV input switching (superseded — historical IR design)

> ⚠️ **SUPERSEDED — historical reference, not the shipped v4 design.** This document describes a
> **Broadlink RM4 IR** approach to TV-input switching that was explored on the v3 line. **v4 does not
> ship IR**: it switches the TV with pure, spec-legitimate **HDMI-CEC** (the OPPO's own One-Touch-Play
> grab + Kodi's own `script.cecreclaim` reclaim) — see [README](../README.md) and
> [ARCHITECTURE.md](ARCHITECTURE.md). The implementation files referenced below
> (`resources/lib/broadlink_rm4.py`, `resources/lib/ir.py`) were **never merged into this tree**, so
> nothing here reflects the current codebase. Kept only as a design record should IR be revisited.
> The one Broadlink artifact that *was* in the tree, `tools/learn_ir.py`, imported the never-merged
> `broadlink_rm4` module and so crashed on import; it has been **removed** (#37). v4's IR path is the
> ZJIoT / LIRC transports (`ir_zjiot.py` / `ir_lirc.py`) with their own bench tools under `tools/`.

> **Historical status (IR branch — not in this build):** the wiring fix, the timing change, the
> hand-rolled Broadlink client, the sequencing layer, the learn tool, and the off-box test suite were
> **landed and green** *in the IR branch* (47 tests, no hardware) at the time of this write-up.
> What remained then was **hardware validation** — confirming the protocol against a real RM4 + TCL and
> capturing the actual input codes — tracked in §10. Until an RM4 is configured, `ir.configured()`
> is False and the handoff falls back to the interim OPPO power-cycle exactly as before.

This was designed by a verified multi-agent pass: 24 load-bearing facts were each adversarially
checked (22 confirmed, **2 refuted** — see §1), and a completeness critic's findings are folded into
the shipped code and §9–§10.

---

## 1. Settled decisions (and two verified corrections)

- **HDMI-CEC is abandoned for switching.** v4 is CEC-free by design (`handoff.py`: no
  `CECActivateSource`). The RM4 sends the TV's *own* input-select code — one command, one direction,
  no CEC bus, no Mi-Box bleed, no OPPO power-cycle.
  - **Correction (verified):** the Mi-Box cross-control came from the **Kodi box spoofing a *foreign*
    `<Active Source>` CEC frame** via the Amlogic driver (`/sys/class/aocec/cmd`) — an *abandoned*
    approach. The OPPO's own One-Touch-Play power-cycle is documented as **the only safe CEC switch**
    (a device announcing *its own* active source is legitimate). This changes the *reasoning* in the
    failure matrix (§9), not the conclusion.
- **IR is for TV INPUT SWITCHING ONLY** — never to trigger the OPPO. The OPPO is loaded+played
  atomically over HTTP (`/playnormalfile`, `/checkfolderhasBDMV`); there is no separate "load then
  press play," so there is nothing for IR to do on the OPPO side.
- **The interim method (when no RM4 is configured) is the OPPO power-cycle** (`#POF`/`#PON`, whose
  power-ON fires One-Touch-Play). The RM4 replaces it.
  - **Correction (verified):** the TCL Q9L Pro runs TCL's proprietary **Lingxi / LingControl OS 3.0**
    and is a **China-only** model — *not* Google TV (the global Google-TV QD-Mini equivalent is the
    separate QM-series). This doesn't change the implementation — we capture raw codes on the real TV,
    not a platform code set — but it means **capture-on-hardware is the only safe path**; trust no
    published platform code set.

### Hardware chain

| Role | Device | Notes |
|------|--------|-------|
| Kodi host | Ugoos AM6B+, CoreELEC (kernel 4.9) | Kodi's bundled python3 runs the add-on |
| Player | M9205 V1 (OPPO UDP-203 clone) @ 192.168.10.10 | HTTP app API on :436 |
| TV | TCL Q9L Pro | OPPO = HDMI 1, Mi Box S = HDMI 2, Ugoos/Kodi = HDMI 4 |
| Blaster | Broadlink RM4 mini (WiFi/LAN IR) | **not yet in hand** |

### Runtime constraints that shaped the design

- The IR send runs **inside `pcf_player`'s external process** (`handoff.play_on_oppo`): plain
  python3, **no Kodi/xbmc/CEC APIs**, network/stdlib only.
- The add-on ships as a **runtime-only zip with no pip** — every dependency is **vendored**.
- The Broadlink protocol uses **AES-128-CBC**, which the stdlib lacks and CoreELEC ships only as
  pycryptodome (`Crypto` namespace, not `cryptography`). The design does not bet on that being
  visible to the player process (see §4).
- **IR is open-loop:** no readback of the TV's input. Reliability comes from idempotent codes +
  bounded resend + an honest failure path (§9).

---

## 2. Architecture: a hand-rolled client, not a vendored library

The implementation is one new stdlib-only file,
[`resources/lib/broadlink_rm4.py`](../service.oppokodibridge.v4/resources/lib/broadlink_rm4.py)
(~330 lines incl. a vendored AES), implementing **only the four verbs we need** — discover-by-IP,
auth, send-IR, learn — plus a pure-Python AES-128 floor.

**Why not vendor upstream `python-broadlink`:** it hard-imports the `cryptography` C-extension, which
is **absent on CoreELEC**. Vendoring it verbatim would `ModuleNotFoundError` on the box, or force a
fragile `cryptography`-shim package + `sys.path` games — more moving parts than reimplementing a tiny,
frozen protocol. The blaster is a single confirmed RM4 mini at a known IP, so there is no
`SUPPORTED_TYPES` table, no device subclasses, and no RF sweep: the discovered device-type id is
**logged as a sanity check, never a gate**, and RM4 framing is hardcoded.

The client is a dumb "send one `0x26` blob" primitive. **Sequencing and reliability live one layer up**
in [`ir.py`](../service.oppokodibridge.v4/resources/lib/ir.py), so the client stays minimal and the
TCL nav-sequence logic is testable in isolation.

---

## 3. Byte-level Broadlink protocol (as implemented)

Constants and offsets are the canonical `mjg59/python-broadlink` values, confirmed in the verified
research.

### 3.1 Fixed key / IV

```
INIT_KEY  = 097628343fe99e23765c1513accf8b02   # AES-128, used until auth returns a session key
INIT_VECT = 562e17996d093d28ddb3ba695a2e6f58   # IV — stays FIXED for auth AND all session traffic
```
Only the **key** changes after auth (init key → per-session key); the IV never changes.

### 3.2 Discovery by known IP (unicast hello, marker `0x06`)

The RM4's IP is known, so the 48-byte hello is sent **unicast** to `ip:80` (no broadcast). From the
reply: **device type = `u16le(resp[0x34:0x36])`**, **MAC = `resp[0x3a:0x40]`** (stored as-is;
reversed only into later headers and for display). The devtype is logged, never gated on.

### 3.3 Packet framing (0x38 header + AES payload)

Magic `5a a5 aa 55 5a a5 aa 55` @ 0x00. Then:

| Offset | Field |
|--------|-------|
| 0x24–0x25 | device type (LE) |
| 0x26–0x27 | command (LE): `0x0065` auth, `0x006a` encrypted send/recv |
| 0x28–0x29 | packet counter (LE) |
| 0x2a–0x2f | MAC, **reversed** |
| 0x30–0x33 | session id (from auth; zeros before) |
| 0x34–0x35 | **payload checksum** (LE, over the *unencrypted* payload) |
| 0x20–0x21 | **whole-packet checksum** (LE) |
| 0x38+ | AES-128-CBC payload |

Order of operations: build header with both checksums zeroed → payload checksum over the unencrypted
payload → **zero-pad to a 16-byte multiple (NOT PKCS#7)** and AES-CBC encrypt with the current key +
`INIT_VECT` → whole-packet checksum. Both checksums are `(sum(bytes) + 0xBEAF) & 0xFFFF`, LE. On
receive, decrypt `reply[0x38:]` and **parse the inner length field — never depad** (replies are
zero-padded too).

### 3.4 Auth (command 0x65)

80-byte payload, mostly zeros, with a fake CID at `[0x04:0x13]`, `[0x1e]=1`, `[0x2d]=1`, and the app
name at `[0x30:]`. Encrypt with `INIT_KEY`. From the decrypted reply: **session id = `[0x00:0x04]`**,
**session key = `[0x04:0x14]`** (16 bytes).

### 3.5 Send-IR & learn (RM4 length-prefixed framing)

```
inner = struct.pack("<HI", len(data) + 4, command) + data
```

| Verb | command | inner data |
|------|---------|------------|
| send IR | `0x02` | the raw `0x26…` blob |
| enter learning | `0x03` | empty |
| check learned | `0x04` | empty; reply: `p_len=u16le(dec[0:2])`, body=`dec[0x06:p_len+2]` |

The `0x02` ack only confirms the **blaster emitted** — the TV never acks (open-loop).

### 3.6 The `0x26` IR blob & repeat byte

`[0x26][repeat][len_lo len_hi][ pulse bytes … ][0x0d 0x05]`. Byte 1 is the **repeat** field
(0 = once, 1 = twice in-packet). `send_ir()` **rejects any blob whose lead byte ≠ `0x26`** (so an RF
`0xb2`/`0xd7` capture can't be blasted) and only mutates the repeat byte after that guard.

### 3.7 Storage format (base64, hex-tolerant, **hex-first**)

`decode_ir_code()` auto-detects: an **even-length pure-hex** string is decoded as hex *first*, else
base64 (`validate=True`). Hex-first avoids the trap where a pure-hex code (e.g. `2600…`) is also valid
base64 and would silently decode to the wrong bytes. The learn tool emits base64 (the ecosystem
default); hex is a manual fallback.

---

## 4. AES sourcing — try-chain with a pure-Python floor

`_aes_cbc(key, iv, data, *, encrypt)` tries **pycryptodome** (`from Crypto.Cipher import AES`,
`MODE_CBC`, no padding) and falls through on *any* import/runtime failure to a **vendored pure-Python
AES-128** (`_Aes128`). Raw / no-pad CBC only (Broadlink zero-pads manually). The floor guarantees
correctness whether or not the player process can see CoreELEC's site-packages, so the
visibility question (§10) is a *performance* note, not a correctness risk. The floor is pinned by a
**FIPS-197 known-answer test** and a try-chain-vs-floor parity test (§8).

---

## 5. TCL Q9L Pro code-capture strategy

The bundled remote has **no discrete-HDMI button** (TCL's flow is Source → arrows → OK over an
on-screen list; protocol RCA addr `0x0F`). So `ir_code_oppo` / `ir_code_kodi` carry an **ordered nav
sequence** of learned codes, stored as a **comma-delimited** list of base64 in the existing string
settings (a single discrete code is just a 1-element list — forward/backward compatible,
`configured()` unchanged; `_send` splits on `,`/newline).

**Capture** (with [`tools/learn_ir.py`](../tools/learn_ir.py) + the RM4):
1. `learn` Source, Up, Down, OK from the TV's own remote (aim each at the RM4).
2. On the TV Source list, note row order and where HDMI 1 (OPPO) / HDMI 4 (Kodi) land.
3. Build a **start-position-independent, end-stop-anchored** sequence per target —
   `Source → run one direction to the list end → step back N → OK` — so it self-corrects from any
   cursor start. `ir_code_oppo` lands on HDMI 1; `ir_code_kodi` lands on HDMI 4.
4. Verify with `learn_ir.py … sequence "<b64>,<b64>,…"`; paste the comma-joined base64 into settings.

**Preferred fast path:** if the Q9L Pro *receiver* honours a **discrete RCA-`0x0F` 'HDMI N' code**
(attested codes like `D15F180`/`D15F181` exist) even though the bundled remote never emits one, use
it — idempotent, safe to double-send, no list drift — with the anchored sequence as fallback. **Do
NOT** ship the circulated NECext-`EAC7` (Roku) or NEC-`0x57E3` discrete sets — verified WRONG protocol
for this RCA-`0x0F` panel.

**Reliability rule (open-loop):** discrete OR end-stop-anchored sequences only. A bare Source/Input
**toggle is non-idempotent** — any repeat advances the input twice and lands wrong, unrecoverable.

---

## 6. The config wiring fix (the load-bearing, hardware-free change)

`config.from_addon()` previously built `Config(...)` **without** reading `broadlink_ip` /
`ir_code_oppo` / `ir_code_kodi`, so they fell back to `""` on a real install → `ir.configured()` was
**permanently False even when the user filled the UI**, leaving the entire IR path dark. The fix is
three reads added to the `Config(...)` call:

```python
broadlink_ip=s("broadlink_ip").strip(),
ir_code_oppo=s("ir_code_oppo").strip(),
ir_code_kodi=s("ir_code_kodi").strip(),
```

That is the only change needed for settings to reach the player: `service_v4._publish_config()`
serializes via `dataclasses.asdict` (already field-generic) → `runtime_config.json` →
`pcf_player` → `Config.from_dict()`. `settings.xml` already defines all three (category `switching`).
The reliability tunables (`IR_REPEAT`, `IR_KEY_GAP_MS`, `IR_SETTLE_MS`) are **module constants**, not
settings — deliberately, so there is no settings.xml id without a matching dataclass field (which
would re-introduce the same silent-drop bug in miniature).

---

## 7. Lifecycle integration (the timing change)

- **Play side — moved.** The IR switch no longer fires before wake (which would aim the TV at a
  no-signal HDMI 1 during the OPPO's sign-in + mount + buffer). It now fires the **instant the OPPO
  first reports PLAYING** (the Phase 1 → Phase 2 boundary in `_watch_playback`), via an `on_started`
  callback, so the TV lands on a **live** input and the blackout collapses to one handshake. It runs
  in a **daemon thread** so a multi-key nav sequence (a second or two) **cannot delay opening the
  `#SVM 3` verbose stop-watch** — a very short clip is still caught when it stops.
- **Stop side — unchanged placement.** `switch_to_kodi` fires after playback ends and before the
  player exits. HDMI 4 never stopped outputting, so it's just the TV's own input handshake.
- **Interim power-cycle** stays up front but is now reached **only when IR is not configured**.
- Both IR calls return an **honest bool**; the handoff logs on False and **continues** — the OPPO
  still plays regardless, and a missed switch is correctable with the physical remote.

---

## 8. Test coverage (off-box, no RM4) — 47 tests green

| Area | What's pinned |
|------|---------------|
| AES | FIPS-197 known-answer (encrypt + decrypt), CBC round-trip, **no-PKCS#7** (1 block → 16 bytes), try-chain == floor |
| Framing | magic/command/checksum offsets, RM4 length-prefixed `0x02` send framing, reversed MAC |
| Decoding | base64 ⇄ hex equivalence, **hex-first** for ambiguous pure-hex, bad input raises |
| send_ir | repeat byte mutates only byte 1, **rejects RF (`0xb2`) and empty** |
| Round-trip | full discover → auth → send → learn against a **fake in-process RM4 UDP server** |
| ir._send | single-code repeat vs sequence no-repeat, inter-key gaps, empty no-op, unreachable→False, bad/RF code→False, **one** re-auth retry, `ir.configured` vs `cfg.configured` |
| Wiring | `from_addon` reads the IR settings; survives the real `asdict → json file → from_dict` boundary |
| Handoff | IR fires **after play, not before wake**; before the verbose watch opens; power-cycle **only** when IR not configured |

Run: `cd D:\Git\OppoKodiBridge-v4 && python -m pytest -q`.

---

## 9. Failure / fallback matrix

The handoff never crashes (`pcf_player` wraps it) and **never** bridges a `configured()`-true IR
failure into the OPPO power-cycle.

| Condition | `ir.configured()` | Behaviour | Why |
|-----------|:---:|-----------|-----|
| No `broadlink_ip`/`ir_code_oppo` | False | interim OPPO power-cycle (if `grab_tv_on_play` **and** `oppo_model != M9207` — the M9207 grab is skipped, switch the TV manually), else rely on Kodi reasserting on exit | the only legitimate power-cycle path |
| `ir_code_kodi` empty | True | play-side switches to OPPO; on stop `_send` no-ops, **TV left on HDMI 1** | `configured()` needs only `ir_code_oppo`; see §10 gap |
| RM4 unreachable | True | log + leave the TV; OPPO keeps playing | **not** Mi-Box-bleed (corrected §1); real reasons: ~20–24 s OPPO reboot mid-handoff + silently downgrading the chosen IR path |
| Stale session | True | one re-discover+re-auth+resend per packet, then give up | bounded — no storm |
| LAN packet drop | True | client resends the datagram once on timeout | network reliability is the client's job |
| Missed IR burst | True | discrete code: in-packet `repeat=1` (idempotent) | per-packet repeat for **discrete codes only** |
| Toggle code misconfigured | True | dangerous (double-advance); learn-tool + help steer to discrete/anchored only | idempotency is the whole reliability argument |
| pycryptodome not visible | n/a | falls through to the pure-Python AES floor | why the floor is mandatory |
| No discrete TCL code | True | `ir_code_*` is a sequence; `_send` replays with `IR_KEY_GAP_MS`; end-stop anchor removes drift | the central TCL constraint |
| Bad code (not base64/hex) | True | `_send` logs + returns False, leaves the TV | misconfig surfaced, not fatal |

In every IR-failure row the OPPO still plays the file (IR is TV-switch-only); worst case is "OPPO
playing, TV didn't follow," fixable with the remote — strictly safer than touching the CEC bus.

---

## 10. Known gaps & hardware-validation checklist

These need the real RM4 + TCL before they can be trusted/closed (surfaced by the completeness critic):

**Open design gaps (deliberately not designed around):**
- **TV-off:** an input code does nothing on a powered-off TV, and CEC-free means no power lever. Either
  learn a discrete TV-power code (open-loop power toggle is non-idempotent — risky) or accept that the
  user powers the TV on. Decide on hardware.
- **OPPO-already-on:** the interim power-cycle is a **no-op** when the OPPO is already on (One-Touch-Play
  only fires out of standby — per `oppo_http.power_cycle`'s "Verified on a TCL Q9L"). So
  `configured()==False` + already-on ⇒ the TV never switches. **IR actually fixes this** (it switches
  regardless of OPPO power state) — a genuine win worth stating.
- **Asymmetric codes:** if `ir_code_kodi` is left empty, the TV is stranded on HDMI 1 after every stop.
  Consider requiring both, or document it in the `ir_code_kodi` help string.

**Must validate on hardware:**
1. Whether the Q9L Pro **receiver honours a discrete RCA-`0x0F` 'HDMI N' code** — if yes, prefer it.
2. Whether the **Source/Up/Down/OK buttons emit IR at all** (vs Bluetooth/RF) — point each at the RM4
   in learn mode; `check_learned` must return a `0x26`-lead blob, or the RM4 can't drive nav.
3. The **exact Source-list order/positions** on this TV (where HDMI 1 / HDMI 4 sit, which arrow moves,
   wrap vs end-stop) — sets the arrow counts; observe, never hardcode.
4. The **RM4 protocol** end-to-end: discover-by-IP, auth, the `0x02` send framing, zero-pad (not
   PKCS#7), both `0xBEAF` checksums — confirm a learned-then-replayed code actually fires the TV.
5. **Post-`is_playing` timing**: that firing at the Phase 1→2 boundary collapses the blackout to one
   handshake, the daemon IR thread doesn't starve `#SVM 3`, and whether a small `IR_SETTLE_MS` helps.
6. Whether the player process sees pycryptodome (`from Crypto.Cipher import AES`) — determines which
   AES backend runs (correctness is covered by the floor; this is a latency note).
7. That a double-emitted **anchored sequence** mis-lands (confirming repeat stays restricted to single
   discrete codes).

**Maintenance note:** with the hand-rolled client we own protocol correctness — mitigated because the
device is a single confirmed RM4 mini, the protocol is frozen, and the AES + fake-RM4 round-trip tests
pin it. Do **not** later "simplify" by pip-vendoring upstream `python-broadlink` — it imports the
`cryptography` C-extension CoreELEC lacks and would break the on-box import.

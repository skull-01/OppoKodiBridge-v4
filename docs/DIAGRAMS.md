# OppoKodiBridge v4 — design diagrams

Two views of the same pipeline:

1. **Architecture & CEC handoff** — the modules, the devices, and the two single-shot CEC assertions
   (green = the OPPO grabs the TV on play; blue = Kodi reclaims the TV on stop, via the helper).
2. **User journey** — what happens, in order, from pressing Play to the TV returning to Kodi on Stop.

---

## 1 · Architecture & CEC handoff

![OppoKodiBridge v4 architecture and CEC handoff](diagrams/oppokodibridge-v4-architecture.svg)

> If the image above doesn't render in your Markdown viewer, open the SVG directly:
> [`docs/diagrams/oppokodibridge-v4-architecture.svg`](diagrams/oppokodibridge-v4-architecture.svg)

**How the CEC helper is used.** The whole pipeline runs in `pcf_player.py`, which Kodi spawns as an
**external player — a separate OS process outside Kodi** (so disc content never touches Kodi's own
player → no pre-play blip). That process has **no `xbmc` API**, so it cannot call
`xbmc.executebuiltin("CECActivateSource")` itself. On stop, `orchestrator.run`'s `finally` calls
`cec.reclaim_kodi`, which reaches back into Kodi over **localhost JSON-RPC** (`Addons.ExecuteAddon`,
`addonid: script.cecreclaim`); the tiny in-Kodi **`script.cecreclaim`** helper runs the
`CECActivateSource` builtin so Kodi re-announces **its own** active source and the TV returns to Kodi.
The grab side does **not** use the helper — the OPPO grabs the TV via its own One-Touch-Play, forced by
a `#POF`→`#PON` power-cycle. **The grab is model-gated** (`cec.grab_supported`): on the M9207 Plus /
UDP-203 the network power-cycle is a no-op that also wedges the unit, so the grab is skipped entirely
and the TV is switched to the OPPO input manually. Each device asserts only its **own** HDMI source
(no IR, no spoofed initiator), and both assertions are **single-shot** (tied to play/stop events — no
standing re-asserter, so a manual input change sticks).

---

## 2 · User journey

```mermaid
sequenceDiagram
    autonumber
    actor You as You (remote)
    participant TV
    participant Kodi
    participant PCF as playercorefactory.xml
    participant Player as pcf_player.py (orchestrator)
    participant OPPO
    participant Helper as script.cecreclaim

    Note over Kodi,PCF: Setup (once): the service publishes runtime_config.json and installs playercorefactory.xml

    You->>Kodi: Play a disc (.iso / BDMV / VIDEO_TS)
    Kodi->>PCF: match the disc routing rule
    PCF->>Player: spawn external player (no blip)
    Player->>Player: read runtime_config.json

    rect rgb(231, 244, 238)
    Note over Player,TV: Play side — switch the TV to the OPPO (model-gated grab)
    alt oppo_model = M9205 (grab-capable)
        Player->>OPPO: cec.grab_oppo — power-cycle (#POF then #PON)
        OPPO-->>TV: HDMI-CEC One-Touch-Play (OPPO's own source)
        Note over TV: TV switches to the OPPO input
    else oppo_model = M9207 / UDP-203 (no network grab)
        Note over You,TV: grab skipped — switch the TV to the OPPO input manually
    end
    Player->>OPPO: handoff.play — wake, init, NFS mount, play
    OPPO-->>You: disc plays on the TV
    end

    loop until playback stops
        Player->>OPPO: monitor (M9205: #SVM 3 push + HTTP; M9207: HTTP only)
    end

    You->>OPPO: press Stop (or the disc ends)
    OPPO-->>Player: playback ended

    rect rgb(233, 240, 254)
    Note over Player,TV: Stop side — Kodi reclaims the TV via the CEC helper
    Player->>Kodi: cec.reclaim_kodi — JSON-RPC Addons.ExecuteAddon(script.cecreclaim)
    Kodi->>Helper: run the helper add-on
    Helper->>Kodi: xbmc.executebuiltin("CECActivateSource")
    Kodi-->>TV: HDMI-CEC active source (Kodi's own source)
    Note over TV: TV returns to Kodi
    end
```

### Step notes

- **No blip:** Kodi's `playercorefactory.xml` routes disc content to the external player *before*
  Kodi's own player ever opens the file — there is no momentary Kodi playback before the handoff.
- **The ~20–24 s cost:** the OPPO only asserts active source on a power-**ON** transition, so the grab
  is a deliberate `#POF`→`#PON` power-cycle.
- **`oppo_model` gates BOTH sides (since v4.1.2):** on the **M9205** the OPPO grabs the TV (power-cycle)
  and stop is detected with a verbose `#SVM 3` push watch on `:23` (HTTP cross-check + absolute ceiling,
  since v4.1.1). On the **M9207 Plus / UDP-203** the grab is skipped entirely (its `#PON` is a no-op
  that wedges the unit) — switch the TV manually — and stop is detected by HTTP `/getglobalinfo`
  polling only (it never opens `:23`). Selecting `M9207` is now the single knob; you no longer also
  have to turn off `grab_tv_on_play`.
- **Reclaim always runs:** `cec.reclaim_kodi` is in the orchestrator's `finally`, so the TV is
  reclaimed whether playback succeeded or failed — once, never re-asserted.

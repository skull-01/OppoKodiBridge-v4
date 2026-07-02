# Build plan v1 — IR TV-input switching transport

**Status:** Proposed · 2026-07-03 · not yet approved (awaiting operator "Go")
**Reference docs:** `IR_Learning_Module_Manual_EN.md` (ZJIoT serial IR module protocol, verified 28/28 test frames)
+ operator's TCL TV IR-codes gist (extended-NEC, address `0x57E3`).

## Goal
A gated `ir` control transport that fires **discrete TCL HDMI-select NEC codes** over the ZJIoT serial IR
module (USB-TTL into the Ugoos/CoreELEC box) to switch the TV's HDMI input on play/stop — the clean
alternative to the CEC power-cycle grab that wedges the M9207 (issue #5 / the remote-lockup). **All OPPO
control stays on the network `:436` API** (unchanged).

## Operator decisions (proposed — confirm/amend on Go)
- **Q1 Scope:** IR switches the **TV HDMI input only**; all OPPO control stays on the network `:436` API.
- **Q2 Encoding path:** **synthesize** NEC → ZJIoT raw and write into the module (no learning) as primary;
  keep **learn-from-remote** as a drop-in fallback. Both produce the same stored raw bytes → identical runtime.
- **Q3 Trigger:** play → "to OPPO input"; stop → "to Kodi input", via a new `tv_switch_method`
  (`none | cec | ir`). `ir` works on all models (esp. M9207 where the CEC grab is disabled/harmful).
- **Q4 Default OFF:** ships inert (`tv_switch_method` default = today's behavior) → the whole software feature
  lands and is testable **without hardware**.
- **Q5 Codes are config:** TCL addr `0x57E3`; exact HDMI command set + which inputs are OPPO/Kodi are values
  set after a one-time on-TV test.

## Dependency chain (locked)
```
PR1.1 ZJIoT frame codec (offline; 28/28 already proven)             [Session 1]
   +-> PR1.2 NEC->raw synthesis + validator  (offline, GO/NO-GO)     [Session 1]
          +-> PR1.3 ir.py transport + binary termios serial (mocked) [Session 1]
                 +-> PR1.4 config/settings + orchestrator wiring     [Session 1]
                        +-> PR1.5 docs + verification checklist + issue [Session 1]
   ...> HW-bring-up: device node + learn/test wizard + TV verify      [Session 2 - needs hardware]
```

## Session 1 — offline software (buildable + testable now, no hardware) (5 PRs)
Theme: *"the whole IR feature, gated and default-off, validated off-box."*

### PR 1.1 — ZJIoT protocol frame codec (~120 LOC)
- New `resources/lib/ir_proto.py`: `build(addr, afn, data)`, `parse(frame)`, `checksum`, AFN constants,
  ACK/report decode. (Header `0x68`, LE length, `(addr+afn+sum(data))&0xFF`, tail `0x16`.)
- **Tests:** round-trip all 28 manual frames (checksum+length), build reproduces them byte-for-byte,
  malformed-frame rejection.

### PR 1.2 — NEC -> ZJIoT raw-waveform synthesis + validator (~150 LOC) — GO/NO-GO spike
- Reverse the module's raw byte encoding from the manual `0x17`/`0x18`/`0x22` example streams + NEC timing
  (9 ms / 4.5 ms lead, 32x 560 us bits, 38 kHz). Implement `nec_to_raw(addr16, cmd8)`.
- **Decision gate:** if the encoding can't be confidently pinned offline, mark path B "confirm-on-hardware"
  and ship using path A (learn) — no rework; both feed the same `raw` bytes.
- **Tests:** synthesized frame matches expected NEC bit/timing structure; documents decoded format + confidence.

### PR 1.3 — `ir.py` control module + binary serial transport (~200 LOC)
- `resources/lib/ir.py`: `IrBlaster` with `send_slot(i)` (`0x12`), `write_slot(i, raw)` (`0x17`),
  `send_raw(raw)` (`0x22`), `learn_external()` (`0x20`->`0x22`); `switch_to_oppo()` / `switch_to_kodi()`.
- Binary termios send/recv reusing `oppo_http.serial_command`'s open/configure pattern (stdlib only, no
  pyserial), with ACK read + bounded retry.
- **Tests:** fake/loopback serial — framing per command, ACK handling, timeout/retry, non-fatal on missing
  termios (mirrors the serial transport's error contract).

### PR 1.4 — config + settings + orchestrator wiring (~150 LOC)
- `config.py`: `tv_switch_method` (`none|cec|ir`), `ir_serial_port` (`/dev/ttyUSB0`), `ir_oppo_input`/
  `ir_kodi_input` (1-3), `ir_module_addr` (`0x00`), stored codes (raw bytes or slot idx). `settings.xml` +
  `strings.po`.
- `orchestrator.run`: play -> `if method=='ir': ir.switch_to_oppo()` else current cec grab; stop-side
  symmetric. Gated; default keeps today's behavior.
- **Tests:** orchestrator dispatches the right transport per setting; `from_addon` round-trip; existing
  127 tests unaffected.

### PR 1.5 — docs + verification checklist + issue (~docs)
- `docs/IR_TV_SWITCHING.md`: wiring (J1<->USB-TTL cross), TCL code sets, synthesize-vs-learn, CoreELEC
  driver notes. Verification-checklist rows (hardware). SHA-comment the feature issue + `status:awaiting-verify`.

## Session 2 — hardware bring-up (deferred until module + USB-TTL adapter in hand)
- CoreELEC device enumeration (CP2102/FT232 -> `/dev/ttyUSB0`, `dmesg`); a Settings/wizard "learn or test TV
  switch" action; confirm which TCL code set the TV honours + the OPPO/Kodi input numbers; real end-to-end
  play->OPPO-input, stop->Kodi-input; close the hardware rows.

## Plan rollup
| Session | Delivered | PRs | Cumulative tests | Status |
|---|---|---|---|---|
| S1 - offline software | frame codec · NEC synth · `ir` transport · config+wiring · docs | 5 | ~127 -> ~150 | proposed |
| S2 - hardware | device node · learn/test wizard · TV verify | (HW) | - | deferred (needs hardware) |
| **Total** | **full gated IR TV-switch feature** | **5 (+HW)** | **~150** | **proposed** |

## Risk callouts
1. **NEC->raw decode may not fully pin offline** (highest) — proprietary raw format from few examples.
   *Mitigation:* PR 1.2 is an explicit go/no-go; failure just defers the codes to the learn path (A) with
   zero rework — runtime is identical.
2. **Which discrete code set works** — the gist has two HDMI sets; only a live TV test confirms.
   *Mitigation:* config-selectable; a 30-sec HW step.
3. **CoreELEC USB-serial driver** — CH340 may not enumerate. *Mitigation:* recommend CP2102/FT232;
   documented bring-up check.
4. **Toggle-only fallback** — if no discrete set works (unlikely given the gist), `tv_switch_method` still
   offers `cec`/`none`; LIRC-native TX is a documented alternative.
5. **Additive & gated** — default-off, can't regress existing handoff/CEC behavior; no destructive changes.

## Decision
Reply with any of:
- "Go"        — file the feature issue and start PR 1.1 (all offline; nothing needs hardware)
- "Wait"      + questions / decisions to change (e.g. lock which inputs are OPPO/Kodi)
- "Replan X"  — narrow, reorder, or drop a PR (e.g. "codec + synthesis only for now")

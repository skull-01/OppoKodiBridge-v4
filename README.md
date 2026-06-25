# OppoKodiBridge v4

**An experiment: no-blip OPPO playback with pure, spec-legitimate HDMI-CEC switching -- no IR, no spoofing.**

The playercorefactory no-blip
variant of [OppoKodiBridge / v2](https://github.com/skull-01/OppoKodiBridge). It keeps v3's external-player
handoff (so Kodi never starts the file and there is no pre-play "blip") but strips the IR blaster and switches
the TV using **only legitimate CEC**.

## The hypothesis

Clean two-way TV input switching with **zero extra hardware and zero CEC-bus corruption**, by using CEC the
way the spec intends -- each device announcing its *own* active source -- and accepting the OPPO power-cycle
as the cost.

| Leg | How |
|-----|-----|
| OPPO grabs its HDMI input | the OPPO's **own** One-Touch-Play, forced by power-cycling it (`#POF`->`#PON`). It only asserts active source on a power-**ON** transition, so an already-on OPPO is power-cycled to re-grab. |
| Kodi reclaims its HDMI input | Kodi re-asserts its **own** active source (libCEC `SetActiveSource`). |

**Never:** an IR blaster, a CEC `<Active Source>` / `<Set Stream Path>` injected with a *foreign* initiator,
or a second `cec-client` / `cec-ctl` owner -- those corrupt the shared CEC bus (the verified root cause of the
Mi Box cross-control that motivated this fork).

### Assert once per event -- never re-assert

Each TV-input assertion is **single-shot, tied to an event**: the OPPO grabs HDMI-1 once on play, Kodi
reclaims once on stop. There is **no standing monitor** re-asserting active source -- that would override a
manual input change and make the TV un-leaveable (CEC is open-loop; it can't tell "the TV missed my frame"
from "the user switched away"). So if you manually switch the TV input, your choice **stays**. The orchestrator
fires the grab once on play and the reclaim once on stop -- and nothing else ever touches the TV.

### Why this is the *legitimate* path (primary sources)

HDMI-CEC has no "give-back" and only two routing primitives: `<Active Source>` (a device announces **its own**
source) and `<Set Stream Path>` (**TV-only**). So no third party may drive the TV to the OPPO's input -- the
only in-spec lever is the OPPO's own One-Touch-Play. See the Linux kernel CEC framework docs and libCEC's own
enforcement (`"only the TV is allowed to send CEC_OPCODE_SET_STREAM_PATH"`).

## Trade-off

The OPPO power-cycle costs **~20-24 s** on every handoff -- the deliberate price for no IR hardware and a clean
bus. (v2 = CEC but blips; v3 = no blip but needs IR; v4 = no blip, pure CEC, slow grab.)

## Architecture

Five single-responsibility modules under `resources/lib/`, wired by the orchestrator (which runs in the
playercorefactory external-player process, so there is still no blip):

| Module | Responsibility |
|--------|----------------|
| `detector` | which files qualify for handoff (ISO + BDMV/VIDEO_TS) -- one source of truth; `pcf` builds its routing rules from it |
| `handoff` | tell the OPPO to play (wake → init → mount → play); pure OPPO HTTP |
| `cec` | trigger the switch-over -- `grab_oppo` (power-cycle) + `reclaim_kodi` (JSON-RPC → `script.cecreclaim` → `CECActivateSource`) |
| `monitor` | watch playback state (HTTP poll → playing; `#SVM 3` → stop) |
| `orchestrator` | the flow: detect → grab → play → watch → reclaim |

The in-Kodi service only installs `playercorefactory.xml` and publishes config. The Kodi reclaim goes
straight from the orchestrator to Kodi over **localhost JSON-RPC**, so there is no flag and no stale-flag
bug -- still single-shot, still never re-asserting.

## Setup

1. **OPPO:** enable IP/serial control (TCP `:23`); set the OPPO IP + NAS paths in the add-on settings.
2. **Kodi:** enable **Settings → Services → Control → Allow remote control via HTTP** (port `8080`), and
   install the [`script.cecreclaim`](desktop/kodi-helper/script.cecreclaim) helper (the reclaim target).
3. **Settings → Setup & tests:** **Ping the OPPO**, then **Control test** (`#QPW`), then the guided
   **CEC switch-over test** — it grabs the OPPO, asks if the TV switched, reclaims Kodi, and asks again.

## Status

Experimental, software-verified only (83 off-box tests: `python -m pytest -q`). Installs alongside v2 and v3
(add-on id `service.oppokodibridge.v4`) for direct A/B/C comparison. Hardware validation pending.

## License

MIT -- see [LICENSE](LICENSE).

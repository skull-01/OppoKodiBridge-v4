# ZJIoT IR console — Windows dev tool

Detect a **ZJIoT serial IR module** on a **USB-TTL** adapter and **send it any IR command**.
Dev-only bench tool (not shipped in the add-on). Companion to
[`../docs/IR_TV_SWITCHING_BUILD_PLAN.md`](../docs/IR_TV_SWITCHING_BUILD_PLAN.md) — tracks issue #24.

## Install & run

```bat
pip install -r tools/requirements-dev.txt      :: pyserial
python tools/zjiot_console.py
```

## Wiring (USB-TTL ↔ ZJIoT module J1)

| USB-TTL | ZJIoT module | note |
|---|---|---|
| **TXD** | **RXD** | cross over |
| **RXD** | **TXD** | cross over |
| **VCC** | **VCC** | 5 V or 3.3 V per your module |
| **GND** | **GND** | common ground |

Set the baud in the app (default **9600** — confirm against the module manual).

## What it does

- Connect to a COM port (auto-listed via pyserial).
- **Send NEC** synthesised from address + command (extended/16-bit supported).
- **Send** a stored slot, a raw µs waveform, or **any exact hex frame** (poke the module with anything).
- **Learn**: capture a code from a remote into the shared **code library** (JSON).
- The code library is interchangeable with the Raspberry Pi LIRC console.

## Caveat

The ZJIoT frame layout is transcribed from the build plan and **must be confirmed against the real
module/manual**. The frame codec is self-consistent and unit-tested, but end-to-end behaviour is
unverified until run on hardware. The **learn** path captures the module's raw bytes verbatim and does
not depend on the synthesis being exact.

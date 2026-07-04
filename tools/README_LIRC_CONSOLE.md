# LIRC IR console — Raspberry Pi 4 dev tool

Auto-detect the `/dev/lirc*` **TX/RX** devices, **send any IR command**, learn codes, and run a
**loopback self-test** — in a Tkinter window. Dev-only bench tool (not shipped in the add-on).
Companion to [`../docs/IR_LIRC_RPI4.md`](../docs/IR_LIRC_RPI4.md) — tracks issue #25.

## ⚠️ Needs a display

Tkinter requires **Raspberry Pi OS Desktop** or a **VNC / X** session. It will **not** run on a headless
LibreELEC box — there, drive `ir-ctl` over SSH instead (see the parent doc), or add a CLI front-end (the
`LircController` logic is display-free).

## Install & run

```bash
sudo apt install v4l-utils            # ir-ctl + ir-keytable
# enable the overlays (see ../docs/IR_LIRC_RPI4.md), then reboot:
#   dtoverlay=pwm-ir-tx,gpio_pin=18
#   dtoverlay=gpio-ir,gpio_pin=17
python3 tools/lirc_console.py
```

## What it does

- **Refresh** — enumerate `/dev/lirc*` and classify **TX vs RX** via `ir-ctl --features` (probe order is
  not trusted).
- **Send** a NEC scancode (`ir-ctl -S nec:0x…`) or a raw µs waveform.
- **Learn** raw (`ir-ctl -r`) or decoded (`ir-keytable -p nec -t`).
- **Loopback self-test** — blast a code on TX and confirm the RX hears it (fastest wiring check).
- **Code library** (JSON) interchangeable with the Windows ZJIoT console.

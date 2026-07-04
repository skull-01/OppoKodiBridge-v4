import pytest

from lirc_console import LircController
from ir import codes


def make(feats, cmd_rc=0, cmd_out="", cmd_err=""):
    calls = []

    def run(args, timeout=5.0):
        calls.append(list(args))
        if "--features" in args:
            return 0, feats.get(args[-1], ""), ""
        return cmd_rc, cmd_out, cmd_err

    c = LircController(run_fn=run, glob_fn=lambda: sorted(feats))
    return c, calls


def test_refresh_classifies():
    feats = {"/dev/lirc0": "Device can send", "/dev/lirc1": "Device can receive"}
    c, _ = make(feats)
    c.refresh()
    assert c.tx.path == "/dev/lirc0" and c.rx.path == "/dev/lirc1"


def test_send_nec_uses_tx():
    c, calls = make({"/dev/lirc0": "Device can send"})
    c.refresh()
    c.send_nec("0x20df")
    assert ["ir-ctl", "-d", "/dev/lirc0", "-S", "nec:0x20df"] in calls


def test_send_without_tx_raises():
    c, _ = make({})
    with pytest.raises(Exception):
        c.send_nec("0x1")


def test_learn_decoded():
    c, _ = make({"/dev/lirc1": "Device can receive"}, cmd_out="scancode = 0x20df10ef\n")
    c.refresh()
    assert c.learn_decoded("nec") == ["0x20df10ef"]


def test_check_loopback():
    assert LircController.check_loopback("0x20df", ["0x20DF"]) is True
    assert LircController.check_loopback("0x1", ["0x2"]) is False


def test_send_code_nec():
    c, calls = make({"/dev/lirc0": "Device can send"})
    c.refresh()
    c.send_code(codes.IrCode("x", "nec", "0x20df"))
    assert ["ir-ctl", "-d", "/dev/lirc0", "-S", "nec:0x20df"] in calls

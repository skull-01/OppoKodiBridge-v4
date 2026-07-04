"""The LIRC TV-switch transport (resources/lib/ir_lirc.py): builds the right ir-ctl args, is non-fatal,
and drives to_oppo/to_kodi from the stored codes."""
from resources.lib import ir_lirc
from resources.lib.config import Config


def test_send_nec_builds_ir_ctl_args():
    calls = {}

    def run(args, timeout):
        calls["args"] = args
        return 0, ""

    assert ir_lirc.send_nec("/dev/lirc0", "0x20df", run=run) is True
    assert calls["args"] == ["ir-ctl", "-d", "/dev/lirc0", "-S", "nec:0x20df"]


def test_send_nec_normalizes_bare_hex():
    calls = {}
    ir_lirc.send_nec("/dev/lirc0", "20DF", run=lambda a, t: (calls.setdefault("a", a), (0, ""))[1])
    assert calls["a"][-1] == "nec:0x20df"


def test_send_nec_rejects_empty_or_invalid():
    assert ir_lirc.send_nec("/dev/lirc0", "", run=lambda a, t: (0, "")) is False
    assert ir_lirc.send_nec("/dev/lirc0", "zzz", run=lambda a, t: (0, "")) is False


def test_send_nec_nonzero_rc_is_false():
    assert ir_lirc.send_nec("/dev/lirc0", "0x1", run=lambda a, t: (1, "boom")) is False


def test_send_nec_nonfatal_on_exception():
    def boom(a, t):
        raise FileNotFoundError("ir-ctl not installed")

    assert ir_lirc.send_nec("/dev/lirc0", "0x1", run=boom) is False


def test_switcher_to_oppo_then_kodi():
    sent = []

    def run(args, timeout):
        sent.append(args[-1])
        return 0, ""

    cfg = Config(tv_switch_method="lirc", ir_lirc_device="/dev/lirc0", ir_code_oppo="0x1", ir_code_kodi="0x2")
    sw = ir_lirc.LircSwitcher(cfg, run=run)
    assert sw.to_oppo() is True and sw.to_kodi() is True
    assert sent == ["nec:0x1", "nec:0x2"]

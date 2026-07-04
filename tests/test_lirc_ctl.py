import pytest

from lirc import ctl
from lirc.devices import LircToolError


def rec_run(rc=0, out="", err=""):
    calls = []

    def run(args, timeout=5.0):
        calls.append(list(args))
        return rc, out, err

    return run, calls


def test_normalize_scancode():
    assert ctl.normalize_scancode(0x1F) == "0x1f"
    assert ctl.normalize_scancode("1F") == "0x1f"
    assert ctl.normalize_scancode("0x20df") == "0x20df"
    with pytest.raises(LircToolError):
        ctl.normalize_scancode("zzz")


def test_send_nec_args():
    run, calls = rec_run()
    assert ctl.send_nec("/dev/lirc0", "0x20df", run_fn=run) == "0x20df"
    assert calls[0] == ["ir-ctl", "-d", "/dev/lirc0", "-S", "nec:0x20df"]


def test_send_nec_failure_raises():
    run, _ = rec_run(rc=1, err="nope")
    with pytest.raises(LircToolError):
        ctl.send_nec("/dev/lirc0", "0x1", run_fn=run)


def test_raw_file_text():
    txt = ctl.raw_file_text([9000, 4500, 560])
    assert "carrier 38000" in txt
    assert "pulse 9000" in txt and "space 4500" in txt and "pulse 560" in txt


def test_send_raw_writes_file_and_args(tmp_path):
    seen = {}

    def run(args, timeout=5.0):
        seen["args"] = list(args)
        with open(args[-1]) as fh:
            seen["content"] = fh.read()
        return 0, "", ""

    ctl.send_raw("/dev/lirc0", [9000, 4500], run_fn=run, tempdir=str(tmp_path))
    assert seen["args"][:4] == ["ir-ctl", "-d", "/dev/lirc0", "--send"]
    assert "pulse 9000" in seen["content"]


def test_parse_raw_capture_tokens():
    assert ctl.parse_raw_capture("+9000 -4500 +560") == [9000, 4500, 560]


def test_parse_raw_capture_lines():
    assert ctl.parse_raw_capture("carrier 38000\npulse 9000\nspace 4500") == [9000, 4500]


def test_parse_keytable_scancodes():
    out = "1.1: lirc protocol(nec): scancode = 0x20df10ef\n"
    assert ctl.parse_keytable_scancodes(out) == ["0x20df10ef"]


def test_learn_decoded_args():
    run, calls = rec_run(out="scancode = 0x1\n")
    assert ctl.learn_decoded("nec", run_fn=run) == ["0x1"]
    assert calls[0] == ["ir-keytable", "-p", "nec", "-t"]


def test_learn_decoded_passes_timeout():
    seen = {}

    def run(args, timeout=5.0):
        seen["timeout"] = timeout
        return 0, "scancode = 0x1\n", ""

    ctl.learn_decoded("nec", run_fn=run, timeout=30.0)
    assert seen["timeout"] == 30.0


def test_learn_raw_passes_timeout_and_parses():
    seen = {}

    def run(args, timeout=5.0):
        seen["timeout"] = timeout
        return 0, "+9000 -4500", ""

    assert ctl.learn_raw("/dev/lirc1", run_fn=run, timeout=12.0) == [9000, 4500]
    assert seen["timeout"] == 12.0


def test_learn_rc124_is_non_fatal():
    assert ctl.learn_decoded("nec", run_fn=lambda a, timeout=5.0: (124, "scancode = 0x1\n", "t/o")) == ["0x1"]
    assert ctl.learn_raw("/dev/lirc0", run_fn=lambda a, timeout=5.0: (124, "+9000 -4500", "t/o")) == [9000, 4500]


def test_parse_raw_capture_rejects_malformed_token():
    assert ctl.parse_raw_capture("+-9000 +560") == [560]

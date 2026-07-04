from lirc import devices


def fake_run(feats):
    def run(args, timeout=5.0):
        return 0, feats.get(args[-1], ""), ""

    return run


def test_parse_features():
    assert devices.parse_features("Device can send raw IR\nDevice cannot receive") == (True, False)
    assert devices.parse_features("Device can receive\nDevice can send") == (True, True)
    assert devices.parse_features("Device cannot send\nDevice cannot receive") == (False, False)


def test_discover_and_classify():
    feats = {"/dev/lirc0": "Device can send raw IR", "/dev/lirc1": "Device can receive raw IR"}
    calls = []

    def run(args, timeout=5.0):
        calls.append(list(args))
        return 0, feats.get(args[-1], ""), ""

    devs = devices.discover(run_fn=run, glob_fn=lambda: sorted(feats))
    tx, rx = devices.classify(devs)
    assert tx.path == "/dev/lirc0" and rx.path == "/dev/lirc1"
    assert tx.role == "tx" and rx.role == "rx"
    assert calls[0][:2] == ["ir-ctl", "--features"]  # classified via ir-ctl --features


def test_discover_survives_feature_failure():
    def run(args, timeout=5.0):
        return 1, "", "boom"

    devs = devices.discover(run_fn=run, glob_fn=lambda: ["/dev/lirc0"])
    assert devs[0].can_send is False and devs[0].can_receive is False


def test_classify_none_when_empty():
    assert devices.classify([]) == (None, None)

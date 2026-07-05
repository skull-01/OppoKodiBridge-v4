"""The RPi4 LIRC provisioner (tools/setup_rpi4_lirc.py): OS detection, idempotent overlay planning,
and the backed-up apply -- all via injected fns (no real /boot writes)."""
import setup_rpi4_lirc as prov
from setup_rpi4_lirc import (
    RX_OVERLAY,
    TX_OVERLAY,
    _backup,
    _run,
    _write,
    apply_overlays,
    build_plan,
    detect_config_path,
    missing_overlays,
    wanted_overlays,
)


def test_detect_config_path_prefers_bookworm():
    present = {"/boot/firmware/config.txt", "/flash/config.txt"}
    assert detect_config_path(exists=lambda p: p in present) == "/boot/firmware/config.txt"


def test_detect_config_path_libreelec_and_none():
    assert detect_config_path(exists=lambda p: p == "/flash/config.txt") == "/flash/config.txt"
    assert detect_config_path(exists=lambda p: False) is None


def test_wanted_overlays_receiver_is_optin():
    assert wanted_overlays(with_receiver=False) == [TX_OVERLAY]
    assert wanted_overlays(with_receiver=True) == [TX_OVERLAY, RX_OVERLAY]


def test_missing_overlays_is_idempotent():
    text = "# comment\n" + TX_OVERLAY + "\n"
    assert missing_overlays(text, with_receiver=False) == []            # already present
    assert missing_overlays(text, with_receiver=True) == [RX_OVERLAY]   # only RX missing
    assert missing_overlays("", with_receiver=False) == [TX_OVERLAY]


def test_build_plan_install_logic():
    plan = build_plan(exists=lambda p: p == "/boot/firmware/config.txt", read=lambda p: "",
                      ir_ctl_present=False)
    assert plan["config_path"] == "/boot/firmware/config.txt"
    assert plan["install_v4l_utils"] is True
    assert plan["overlays_to_add"] == [TX_OVERLAY]
    # LibreELEC bundles ir-ctl -> never apt, even when ir-ctl is (reported) absent.
    plan2 = build_plan(exists=lambda p: p == "/flash/config.txt", read=lambda p: "", ir_ctl_present=False)
    assert plan2["libreelec"] is True and plan2["install_v4l_utils"] is False


def test_apply_overlays_appends_and_backs_up():
    store = {"cfg": "# existing\ndtoverlay=vc4-kms-v3d\n"}
    backups = {}
    added = apply_overlays(
        "/boot/config.txt", with_receiver=True,
        read=lambda p: store["cfg"], write=lambda p, t: store.__setitem__("cfg", t),
        backup=lambda p, t: backups.__setitem__("t", t),
    )
    assert added == [TX_OVERLAY, RX_OVERLAY]
    assert TX_OVERLAY in store["cfg"] and RX_OVERLAY in store["cfg"]
    assert backups["t"] == "# existing\ndtoverlay=vc4-kms-v3d\n"  # pre-edit content backed up first


def test_apply_overlays_idempotent_noop():
    calls = {"write": 0, "backup": 0}
    added = apply_overlays(
        "/boot/config.txt", with_receiver=False, read=lambda p: TX_OVERLAY + "\n",
        write=lambda p, t: calls.__setitem__("write", calls["write"] + 1),
        backup=lambda p, t: calls.__setitem__("backup", calls["backup"] + 1),
    )
    assert added == [] and calls == {"write": 0, "backup": 0}  # no write / no backup when nothing to add


def test_write_is_atomic_and_leaves_no_temp(tmp_path):
    # #38: _write must produce exactly the new content and leave no temp file (temp -> os.replace).
    p = tmp_path / "config.txt"
    p.write_text("old contents\n", encoding="utf-8")
    _write(str(p), "new contents\n")
    assert p.read_text(encoding="utf-8") == "new contents\n"
    assert [f.name for f in tmp_path.iterdir()] == ["config.txt"]  # the .okb-tmp-* was renamed away


def test_backup_preserves_the_pristine_original(tmp_path):
    # #38: a second apply must NOT overwrite the first (pristine) .okb-bak.
    p = tmp_path / "config.txt"
    bak = tmp_path / "config.txt.okb-bak"
    _backup(str(p), "PRISTINE")
    assert bak.read_text(encoding="utf-8") == "PRISTINE"
    _backup(str(p), "ALREADY-PATCHED")  # second --apply run
    assert bak.read_text(encoding="utf-8") == "PRISTINE"  # unchanged -- original preserved


def test_run_returns_rc_out_err(monkeypatch):
    # #38: _run returns (returncode, stdout, stderr) so callers can surface the real apt/reboot error.
    class _P:
        returncode = 7
        stdout = "OUT"
        stderr = "ERR"

    monkeypatch.setattr(prov.subprocess, "run", lambda *a, **k: _P())
    assert _run(["anything"]) == (7, "OUT", "ERR")

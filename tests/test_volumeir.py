"""Always-on TV volume takeover: keymap generation/install, NotifyAll matching, config defaults, and
the thread-safe forwarder that fires RCA commands at the TV via the persistent IR pipe."""
import threading
import time

from resources.lib import volumeir
from resources.lib.config import Config


# --- keymap generation + install/remove ---------------------------------------------------------------

def test_keymap_xml_maps_the_volume_keys_to_notifyall():
    xml = volumeir.keymap_xml("volume_up", "volume_down")
    assert "<volume_up>NotifyAll(service.oppokodibridge.v4,okb_volup)</volume_up>" in xml
    assert "<volume_down>NotifyAll(service.oppokodibridge.v4,okb_voldown)</volume_down>" in xml
    assert "<global>" in xml and "<keyboard>" in xml


def test_keymap_xml_honours_custom_key_names():
    xml = volumeir.keymap_xml("VolumeUp", "VolumeDown")
    assert "<VolumeUp>NotifyAll(service.oppokodibridge.v4,okb_volup)</VolumeUp>" in xml
    assert "<VolumeDown>NotifyAll(service.oppokodibridge.v4,okb_voldown)</VolumeDown>" in xml


def test_install_keymap_writes_then_is_idempotent(tmp_path):
    kmdir = str(tmp_path / "keymaps")
    assert volumeir.install_keymap(kmdir) is True          # created -> changed
    path = tmp_path / "keymaps" / volumeir.KEYMAP_FILENAME
    assert path.exists()
    assert volumeir.install_keymap(kmdir) is False         # same content -> no change (no reload)
    assert volumeir.install_keymap(kmdir, "kb_volup", "kb_voldown") is True  # different keys -> changed


def test_remove_keymap_reports_removal(tmp_path):
    kmdir = str(tmp_path / "keymaps")
    assert volumeir.remove_keymap(kmdir) is False          # nothing to remove
    volumeir.install_keymap(kmdir)
    assert volumeir.remove_keymap(kmdir) is True           # removed -> caller should reload
    assert volumeir.remove_keymap(kmdir) is False          # already gone


# --- NotifyAll matching -------------------------------------------------------------------------------

def test_volume_command_maps_messages_to_rca_codes():
    c = Config(tv_code_volume_up=16, tv_code_volume_down=17)
    assert volumeir.volume_command("okb_volup", c) == 16
    assert volumeir.volume_command("okb_voldown", c) == 17
    # robust to a sender-prefixed delivery form
    assert volumeir.volume_command("service.oppokodibridge.v4.okb_volup", c) == 16


def test_volume_command_ignores_unrelated_notifications():
    c = Config()
    assert volumeir.volume_command("Player.OnPlay", c) is None
    assert volumeir.volume_command("", c) is None
    assert volumeir.volume_command(None, c) is None


def test_volume_command_honours_custom_codes():
    c = Config(tv_code_volume_up=200, tv_code_volume_down=201)
    assert volumeir.volume_command("okb_volup", c) == 200
    assert volumeir.volume_command("okb_voldown", c) == 201


# --- config defaults ----------------------------------------------------------------------------------

def test_config_defaults_off_and_present():
    c = Config()
    assert c.tv_volume_via_ir is False
    assert c.tv_code_volume_up == 47 and c.tv_code_volume_down == 46  # HW-confirmed TCL RCA-15
    assert c.tv_volume_key_up == "volume_up" and c.tv_volume_key_down == "volume_down"
    c2 = Config.from_dict({"tv_volume_via_ir": True, "tv_code_volume_up": 20,
                           "tv_volume_key_up": "kb_volup"})
    assert c2.tv_volume_via_ir is True and c2.tv_code_volume_up == 20
    assert c2.tv_volume_key_up == "kb_volup"


def test_keymap_xml_falls_back_on_a_blank_key_name():
    # from_dict does no coercion, so a corrupted runtime_config could carry "" -- keymap_xml must never
    # emit an empty <> tag; it falls back to the media-key default.
    xml = volumeir.keymap_xml("", "")
    assert "<volume_up>" in xml and "<volume_down>" in xml
    assert "<>" not in xml


# --- forwarder (thread-safe worker owning the persistent pipe) ----------------------------------------

class _FakeBlaster:
    def __init__(self):
        self.sent = []
        self.closed = 0
        self._got = threading.Event()

    def send(self, cmd):
        self.sent.append(cmd)
        self._got.set()
        return True

    def close(self):
        self.closed += 1

    def wait_for(self, n, timeout=2.0):
        deadline = time.monotonic() + timeout
        while len(self.sent) < n and time.monotonic() < deadline:
            self._got.wait(0.05)
            self._got.clear()
        return len(self.sent) >= n


def test_forwarder_worker_fires_submitted_commands_then_closes_on_stop():
    fb = _FakeBlaster()
    f = volumeir.VolumeIrForwarder(Config(), make_blaster=lambda cfg: fb, idle_seconds=30)
    f.submit(16)
    f.submit(17)
    assert fb.wait_for(2), "worker should have fired both commands"
    assert fb.sent == [16, 17]
    f.stop()
    f.join(2.0)
    assert fb.closed >= 1  # pipe closed when the forwarder stops


def test_forwarder_drops_the_pipe_after_an_idle_gap():
    fb = _FakeBlaster()
    f = volumeir.VolumeIrForwarder(Config(), make_blaster=lambda cfg: fb, idle_seconds=0.2)
    f.submit(16)
    assert fb.wait_for(1)
    # after ~idle seconds with no further presses, the worker closes the pipe (reopens on next send)
    deadline = time.monotonic() + 2.0
    while fb.closed == 0 and time.monotonic() < deadline:
        time.sleep(0.05)
    assert fb.closed >= 1
    f.stop()
    f.join(2.0)


def test_forwarder_rebuilds_the_pipe_on_a_refreshed_config():
    # regression for the audit HIGH: after a settings change (e.g. first-time setup fills in the blaster
    # host), the worker must rebuild the pipe on the NEW config, not keep using the startup snapshot.
    fb = _FakeBlaster()
    seen = []

    def make(cfg):
        seen.append(cfg)
        return fb

    c1 = Config(ir_blaster_host="")           # startup: blank host (feature off / not yet set up)
    f = volumeir.VolumeIrForwarder(c1, make_blaster=make, idle_seconds=30)
    f.submit(16)
    assert fb.wait_for(1)
    assert seen == [c1]
    c2 = Config(ir_blaster_host="192.168.1.143")   # user enables + sets the host
    f.update_config(c2)
    f.submit(17)
    assert fb.wait_for(2)
    assert seen[-1] is c2 and len(seen) == 2  # rebuilt on the new config
    assert fb.closed >= 1                       # the stale pipe was closed on rebuild
    f.stop()
    f.join(2.0)


def test_forwarder_submit_is_non_blocking_when_full():
    # a stuck worker must never make submit() (called from Kodi's notification thread) block/raise
    fb = _FakeBlaster()
    f = volumeir.VolumeIrForwarder(Config(), make_blaster=lambda cfg: fb, idle_seconds=30)
    for n in range(100):
        f.submit(n)  # capacity is 16; the rest are dropped, never raising
    f.stop()
    f.join(2.0)

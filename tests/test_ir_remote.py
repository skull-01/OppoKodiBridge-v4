"""ir_remote transport: RCA frame codec, the anchored input-menu sequence, the self-contained blaster
program, SSH arg construction, and the non-fatal to_oppo / CEC-delegating to_kodi contract."""
from resources.lib import ir_remote, tvswitch
from resources.lib.config import Config


def _decode_rca(durs):
    """Decode a frame from ir_remote.rca_frame back to (device, command); raises on a bad checksum."""
    pairs = [(durs[i], durs[i + 1]) for i in range(2, len(durs) - 1, 2)]  # skip header, drop trailer mark
    bits = "".join("1" if s > 1400 else "0" for _, s in pairs)
    assert len(bits) == 24, bits
    device = int(bits[0:4], 2)
    command = int(bits[4:12], 2)
    inv_dev = int(bits[12:16], 2)
    inv_cmd = int(bits[16:24], 2)
    assert inv_dev == (~device & 0xF), "device checksum"
    assert inv_cmd == (~command & 0xFF), "command checksum"
    return device, command


def test_rca_frame_roundtrips_with_valid_checksum():
    for dev, cmd in [(15, 163), (15, 42), (15, 244), (15, 89), (15, 88), (0, 0), (15, 255)]:
        durs = ir_remote.rca_frame(dev, cmd)
        assert durs[0] == 4000 and durs[1] == 4000  # header
        assert len(durs) == 2 + 24 * 2 + 1          # header + 24 bit-pairs + trailer mark
        assert _decode_rca(durs) == (dev, cmd)


def test_input_sequence_anchors_then_steps_per_port():
    # port 1: INPUT, UP*4, OK (no downs). port N: + (N-1) downs.
    assert ir_remote.input_sequence(Config(oppo_hdmi_port=1)) == [163, 89, 89, 89, 89, 244]
    assert ir_remote.input_sequence(Config(oppo_hdmi_port=2)) == [163, 89, 89, 89, 89, 88, 244]
    assert ir_remote.input_sequence(Config(oppo_hdmi_port=3)) == [163, 89, 89, 89, 89, 88, 88, 244]
    assert ir_remote.input_sequence(Config(oppo_hdmi_port=4)) == [163, 89, 89, 89, 89, 88, 88, 88, 244]


def test_input_sequence_clamps_port_and_honours_overrides():
    assert ir_remote.input_sequence(Config(oppo_hdmi_port=9)) == [163, 89, 89, 89, 89, 88, 88, 88, 244]
    assert ir_remote.input_sequence(Config(oppo_hdmi_port=0)) == [163, 89, 89, 89, 89, 244]
    seq = ir_remote.input_sequence(Config(
        oppo_hdmi_port=2, tv_menu_anchor_ups=2, tv_code_input=1, tv_code_up=2, tv_code_down=3, tv_code_ok=4))
    assert seq == [1, 2, 2, 3, 4]


def test_build_program_is_valid_python_and_embeds_the_commands():
    cmds = ir_remote.input_sequence(Config(oppo_hdmi_port=3))
    prog = ir_remote.build_program(Config(tv_blaster_lirc_device="/dev/lirc0"), cmds)
    compile(prog, "<blaster>", "exec")            # the doubled braces must survive .format()
    assert "CMDS = [163, 89, 89, 89, 89, 88, 88, 244]" in prog
    assert "'/dev/lirc0'" in prog and "ir-ctl" in prog


def test_ssh_args_none_without_host():
    assert ir_remote.ssh_args(Config()) is None


def test_ssh_args_include_user_key_and_python_stdin():
    args = ir_remote.ssh_args(Config(
        ir_blaster_host="192.168.1.143", ir_blaster_user="pi", ir_blaster_ssh_key="/k/id"))
    assert args[0] == "ssh"
    assert args[-2:] == ["pi@192.168.1.143", "python3 -"]
    assert "-i" in args and "/k/id" in args
    assert "BatchMode=yes" in args


def test_ssh_args_bare_host_without_user():
    args = ir_remote.ssh_args(Config(ir_blaster_host="pi.local"))
    assert args[-2:] == ["pi.local", "python3 -"]  # no "user@" prefix
    assert "-i" not in args                         # no key -> no -i flag


def test_to_oppo_success_runs_ssh_and_returns_true():
    seen = {}

    def run(args, program, timeout):
        seen["args"] = args
        seen["program"] = program
        return 0, ""

    sw = ir_remote.RemoteBlasterSwitcher(
        Config(ir_blaster_host="192.168.1.143", ir_blaster_user="pi", oppo_hdmi_port=3), run=run)
    assert sw.to_oppo() is True
    assert seen["args"][-1] == "python3 -"
    assert "CMDS = [163, 89, 89, 89, 89, 88, 88, 244]" in seen["program"]


def test_to_oppo_no_host_is_noop_false():
    sw = ir_remote.RemoteBlasterSwitcher(Config(), run=lambda *a: (0, ""))
    assert sw.to_oppo() is False


def test_to_oppo_nonzero_rc_is_false():
    sw = ir_remote.RemoteBlasterSwitcher(
        Config(ir_blaster_host="h"), run=lambda *a: (255, "connection refused"))
    assert sw.to_oppo() is False


def test_to_oppo_exception_is_non_fatal_false():
    def boom(*a):
        raise OSError("ssh not found")

    sw = ir_remote.RemoteBlasterSwitcher(Config(ir_blaster_host="h"), run=boom)
    assert sw.to_oppo() is False


def test_to_kodi_delegates_to_cec_reclaim(monkeypatch):
    calls = []
    monkeypatch.setattr(ir_remote.cec, "reclaim_kodi", lambda c: calls.append("reclaim") or True)
    sw = ir_remote.RemoteBlasterSwitcher(Config(ir_blaster_host="h", cec_reclaim_on_stop=True))
    assert sw.to_kodi() is True
    assert calls == ["reclaim"]


def test_to_kodi_respects_reclaim_toggle(monkeypatch):
    monkeypatch.setattr(ir_remote.cec, "reclaim_kodi", lambda c: (_ for _ in ()).throw(AssertionError("called")))
    sw = ir_remote.RemoteBlasterSwitcher(Config(ir_blaster_host="h", cec_reclaim_on_stop=False))
    assert sw.to_kodi() is False


def test_tvswitch_dispatches_ir_remote():
    sw = tvswitch.make_switcher(Config(tv_switch_method="ir_remote"))
    assert isinstance(sw, ir_remote.RemoteBlasterSwitcher)

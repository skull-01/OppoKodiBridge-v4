"""ir_remote transport: RCA frame codec, the anchored input-menu sequence, the self-contained blaster
program, SSH arg construction, and the non-fatal to_oppo / CEC-delegating to_kodi contract."""
import shlex

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


def test_input_sequence_top_offset_adds_downs_for_leading_menu_entry():
    # a leading entry above HDMI1 -> OPPO on HDMI3 is DOWN x (3-1+1) = DOWN x 3 (the operator's TCL menu).
    assert ir_remote.input_sequence(Config(oppo_hdmi_port=3, tv_menu_top_offset=1)) == \
        [163, 89, 89, 89, 89, 88, 88, 88, 244]
    # default offset 0 is unchanged (HDMI1 is the top entry).
    assert ir_remote.input_sequence(Config(oppo_hdmi_port=3, tv_menu_top_offset=0)) == \
        [163, 89, 89, 89, 89, 88, 88, 244]


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


def test_to_oppo_bad_config_value_is_non_fatal_false():
    # a corrupted runtime_config.json (from_dict does no coercion) with a non-numeric port makes
    # input_sequence()/build_program() int()/float() raise -- that must be caught, not propagated.
    called = []
    sw = ir_remote.RemoteBlasterSwitcher(
        Config(ir_blaster_host="h", oppo_hdmi_port="not-a-number"), run=lambda *a: called.append(1) or (0, ""))
    assert sw.to_oppo() is False   # returned False, did not raise
    assert called == []            # never even reached the ssh run


def test_ssh_args_rejects_option_shaped_values():
    assert ir_remote.ssh_args(Config(ir_blaster_host="-oProxyCommand=id")) is None
    assert ir_remote.ssh_args(Config(ir_blaster_host="h", ir_blaster_user="-x")) is None
    assert ir_remote.ssh_args(Config(ir_blaster_host="h", ir_blaster_ssh_key="-k")) is None
    # a normal host/user/key is unaffected
    assert ir_remote.ssh_args(Config(ir_blaster_host="192.168.1.143", ir_blaster_user="pi")) is not None


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


# --- persistent-pipe IR sender (volume passthrough) ---------------------------------------------------

class _FakeStdin:
    def __init__(self, fail=False):
        self.buf = b""
        self.flushed = 0
        self.closed = False
        self._fail = fail

    def write(self, b):
        if self._fail:
            raise BrokenPipeError("pipe")
        self.buf += b

    def flush(self):
        self.flushed += 1

    def close(self):
        self.closed = True


class _FakeProc:
    def __init__(self):
        self.stdin = _FakeStdin()
        self._returncode = None   # None = still running
        self.waited = False
        self.terminated = False

    def poll(self):
        return self._returncode

    def wait(self, timeout=None):
        self.waited = True
        self._returncode = 0
        return 0

    def terminate(self):
        self.terminated = True
        self._returncode = 0


def _popen_factory(procs):
    seq = iter(procs)

    def factory(args):
        factory.calls.append(args)
        return next(seq)

    factory.calls = []
    return factory


def test_build_loop_program_is_valid_python_and_reads_stdin():
    prog = ir_remote.build_loop_program(Config(tv_blaster_lirc_device="/dev/lirc0", tv_rca_device=15))
    compile(prog, "<loop>", "exec")               # doubled braces must survive .format()
    assert "for line in sys.stdin" in prog
    assert "'/dev/lirc0'" in prog and "ir-ctl" in prog


def test_pipe_ssh_args_uses_python_c_so_stdin_is_free():
    args = ir_remote.pipe_ssh_args(Config(ir_blaster_host="192.168.1.143", ir_blaster_user="pi"))
    assert args[0] == "ssh"
    assert args[-2] == "pi@192.168.1.143"
    assert args[-1].startswith("python3 -c ")     # NOT the plain "python3 -" stdin form
    assert "ServerAliveInterval=10" in args        # keep-alives on the persistent pipe
    prog = shlex.split(args[-1])[2]                 # the quoted program is one shell arg, and valid python
    compile(prog, "<loop>", "exec")


def test_pipe_ssh_args_none_without_host_and_guards_option_shaped_values():
    assert ir_remote.pipe_ssh_args(Config()) is None
    assert ir_remote.pipe_ssh_args(Config(ir_blaster_host="-oProxyCommand=id")) is None


def test_ssh_args_one_shot_form_unchanged_no_keepalives():
    # the hardware-validated one-shot switch keeps the plain-stdin form and no pipe-only keep-alives
    args = ir_remote.ssh_args(Config(ir_blaster_host="h"))
    assert args[-1] == "python3 -"
    assert "ServerAliveInterval=10" not in args


def test_persistent_blaster_opens_once_reuses_and_writes_lines():
    proc = _FakeProc()
    factory = _popen_factory([proc])
    pb = ir_remote.PersistentBlaster(Config(ir_blaster_host="h"), popen=factory)
    assert pb.send(16) is True
    assert pb.send(17) is True
    assert len(factory.calls) == 1                 # one connection, reused
    assert proc.stdin.buf == b"16\n17\n"
    assert proc.stdin.flushed == 2


def test_persistent_blaster_no_host_is_a_false_noop():
    called = []
    pb = ir_remote.PersistentBlaster(Config(), popen=lambda a: called.append(a))
    assert pb.send(16) is False
    assert called == []


def test_persistent_blaster_rejects_non_int_command():
    proc = _FakeProc()
    pb = ir_remote.PersistentBlaster(Config(ir_blaster_host="h"), popen=_popen_factory([proc]))
    assert pb.send("nope") is False
    assert proc.stdin.buf == b""                    # never even opened for a bad command


def test_persistent_blaster_broken_pipe_tears_down_then_reopens():
    dead = _FakeProc()
    dead.stdin = _FakeStdin(fail=True)
    fresh = _FakeProc()
    factory = _popen_factory([dead, fresh])
    pb = ir_remote.PersistentBlaster(Config(ir_blaster_host="h"), popen=factory)
    assert pb.send(16) is False                     # write raised -> torn down
    assert dead.stdin.closed is True
    assert pb.send(17) is True                       # transparently reopened
    assert len(factory.calls) == 2
    assert fresh.stdin.buf == b"17\n"


def test_persistent_blaster_reopens_if_process_exits_between_sends():
    dead = _FakeProc()
    fresh = _FakeProc()
    factory = _popen_factory([dead, fresh])
    pb = ir_remote.PersistentBlaster(Config(ir_blaster_host="h"), popen=factory)
    assert pb.send(16) is True
    dead._returncode = 1                             # ssh exits between presses
    assert pb.send(17) is True
    assert len(factory.calls) == 2
    assert dead.stdin.closed is True                 # the dead one was reaped
    assert fresh.stdin.buf == b"17\n"


def test_persistent_blaster_close_closes_stdin_and_is_idempotent():
    proc = _FakeProc()
    pb = ir_remote.PersistentBlaster(Config(ir_blaster_host="h"), popen=_popen_factory([proc]))
    pb.send(16)
    pb.close()
    assert proc.stdin.closed is True and proc.waited is True
    pb.close()                                       # idempotent: no raise on a closed sender

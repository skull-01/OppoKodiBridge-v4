"""The monitor: poll until playing, then hold the verbose channel until stop; give up if playback
never starts. Asserts nothing about CEC -- it only observes."""
from resources.lib import monitor
from resources.lib.config import Config


class _Client:
    def __init__(self, plays_on=1):
        self.n = 0
        self.plays_on = plays_on
        self.verbose = 0

    def is_playing(self):
        self.n += 1
        return self.n >= self.plays_on

    def verbose_watch_until_stop(self, should_abort):
        self.verbose += 1
        return True


def test_watch_playback_observes_start_then_verbose(monkeypatch):
    monkeypatch.setattr(monitor, "interruptible_sleep", lambda *a, **k: None)
    client = _Client(plays_on=1)
    cfg = Config(oppo_ip="x", poll_interval=2, idle_confirmations=2)
    assert monitor.watch_playback(cfg, client) is True
    assert client.verbose == 1


def test_watch_playback_gives_up_if_never_starts(monkeypatch):
    monkeypatch.setattr(monitor, "interruptible_sleep", lambda *a, **k: None)

    class Never:
        def is_playing(self):
            return False

        def verbose_watch_until_stop(self, should_abort):
            raise AssertionError("verbose must not open if playback never started")

    cfg = Config(oppo_ip="x", poll_interval=2, idle_confirmations=2)  # grace = max(2, 10) = 10 polls
    assert monitor.watch_playback(cfg, Never()) is False


def test_watch_playback_aborts(monkeypatch):
    monkeypatch.setattr(monitor, "interruptible_sleep", lambda *a, **k: None)
    cfg = Config(oppo_ip="x")
    assert monitor.watch_playback(cfg, _Client(plays_on=999), should_abort=lambda: True) is False


class _FallbackClient:
    """Forces the HTTP fallback (verbose raises) and serves a scripted playback_state sequence."""

    def __init__(self, states):
        self.states = list(states)
        self.i = 0
        self.state_calls = 0

    def is_playing(self):
        return True  # phase 1 sees playback immediately, so the watch advances to phase 2

    def verbose_watch_until_stop(self, should_abort):
        raise monitor.OppoError("verbose down -> HTTP fallback")

    def playback_state(self):
        self.state_calls += 1
        s = self.states[self.i] if self.i < len(self.states) else self.states[-1]
        self.i += 1
        return s


def test_http_fallback_transient_unknown_is_not_a_stop(monkeypatch):
    # b4: a swallowed transport error ("unknown") must NOT count as idle -> no premature reclaim.
    monkeypatch.setattr(monitor, "interruptible_sleep", lambda *a, **k: None)
    client = _FallbackClient(["unknown", "unknown", "playing", "idle", "idle"])
    cfg = Config(oppo_ip="x", poll_interval=2, idle_confirmations=2)
    assert monitor.watch_playback(cfg, client) is True
    # ended only after the two CONFIRMED idles (5 reads); the unknowns did not trip the stop early.
    assert client.state_calls == 5


def test_http_fallback_gives_up_when_oppo_unreadable(monkeypatch):
    # b3/b4: a permanently-unreadable OPPO must still end the watch so the reclaim fires.
    monkeypatch.setattr(monitor, "interruptible_sleep", lambda *a, **k: None)
    client = _FallbackClient(["unknown"])
    cfg = Config(oppo_ip="x", poll_interval=2, idle_confirmations=2, max_read_failures=3)
    assert monitor.watch_playback(cfg, client) is True
    assert client.state_calls == 3  # gave up after max_read_failures unreadable polls


def test_http_fallback_bounded_when_playing_flag_sticks(monkeypatch):
    # b3: if playback_state sticks "playing" forever, the wall-clock ceiling still returns so the
    # external-player process can't hang and the orchestrator's reclaim runs.
    monkeypatch.setattr(monitor, "interruptible_sleep", lambda *a, **k: None)
    client = _FallbackClient(["playing"])
    cfg = Config(oppo_ip="x", poll_interval=5, idle_confirmations=2, max_watch_seconds=20)
    assert monitor.watch_playback(cfg, client) is True
    assert client.state_calls == 4  # ceil(20/5) poll ceiling, never unbounded

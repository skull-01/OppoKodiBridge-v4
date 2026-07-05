"""The monitor: poll /getglobalinfo until playing, then poll until idle; give up if playback never
starts. HTTP-only for every model (the verbose #SVM 3 channel on :23 is never opened). Asserts nothing
about CEC -- it only observes."""
from resources.lib import monitor
from resources.lib.config import Config


class _Client:
    """Phase 1 starts playing after ``plays_on`` reads; phase 2 then serves ``states`` for playback_state.

    Defines verbose_watch_until_stop so that, if the monitor ever regressed to opening the #SVM 3
    channel, the test would fail loudly -- the HTTP-only monitor must never call it."""

    def __init__(self, plays_on=1, states=("idle", "idle")):
        self.n = 0
        self.plays_on = plays_on
        self.states = list(states)
        self.i = 0
        self.state_calls = 0

    def is_playing(self):
        self.n += 1
        return self.n >= self.plays_on

    def playback_state(self):
        self.state_calls += 1
        s = self.states[self.i] if self.i < len(self.states) else self.states[-1]
        self.i += 1
        return s

    def verbose_watch_until_stop(self, should_abort):
        raise AssertionError("HTTP-only monitor must never open the #SVM 3 verbose channel on :23")


def test_watch_playback_starts_then_http_watch(monkeypatch):
    monkeypatch.setattr(monitor, "interruptible_sleep", lambda *a, **k: None)
    client = _Client(plays_on=1, states=["idle", "idle"])
    cfg = Config(oppo_ip="x", poll_interval=2, idle_confirmations=2)
    assert monitor.watch_playback(cfg, client) is True
    assert client.state_calls == 2  # ended after two CONFIRMED idle HTTP reads; verbose never opened


def test_watch_playback_http_only_for_default_model(monkeypatch):
    # The default model (M9205) is now ALSO HTTP-only -- no verbose channel for any model.
    monkeypatch.setattr(monitor, "interruptible_sleep", lambda *a, **k: None)
    client = _Client(plays_on=1, states=["playing", "idle", "idle"])
    cfg = Config(oppo_ip="x", oppo_model="M9205", poll_interval=2, idle_confirmations=2)
    assert monitor.watch_playback(cfg, client) is True
    assert client.state_calls == 3


def test_watch_playback_gives_up_if_never_starts(monkeypatch):
    monkeypatch.setattr(monitor, "interruptible_sleep", lambda *a, **k: None)

    class Never:
        def is_playing(self):
            return False

        def playback_state(self):
            raise AssertionError("phase 2 must not run if playback never started")

    cfg = Config(oppo_ip="x", poll_interval=2, idle_confirmations=2)  # ~90s grace; no on_stall -> give up
    assert monitor.watch_playback(cfg, Never()) is False


def test_watch_playback_auto_heals_then_plays(monkeypatch):
    # #21: playback stalls past the grace -> re-issue the play ONCE (on_stall); then it starts.
    monkeypatch.setattr(monitor, "interruptible_sleep", lambda *a, **k: None)
    state = {"healed": False, "heals": 0}

    class C:
        def is_playing(self):
            return state["healed"]  # only starts playing after the heal fires

        def playback_state(self):
            return "idle"  # phase 2: immediate confirmed idle

    def heal():
        state["heals"] += 1
        state["healed"] = True

    cfg = Config(oppo_ip="x", poll_interval=2, idle_confirmations=1, playback_start_grace_seconds=4)
    assert monitor.watch_playback(cfg, C(), on_stall=heal) is True
    assert state["heals"] == 1


def test_watch_playback_heals_only_once_then_gives_up(monkeypatch):
    # #21: the auto-heal is bounded -- exactly ONE re-issue, then give up if still not playing.
    monkeypatch.setattr(monitor, "interruptible_sleep", lambda *a, **k: None)
    heals = {"n": 0}

    class Never:
        def is_playing(self):
            return False

        def playback_state(self):
            raise AssertionError("phase 2 must not run if playback never started")

    cfg = Config(oppo_ip="x", poll_interval=2, idle_confirmations=1, playback_start_grace_seconds=4)
    result = monitor.watch_playback(cfg, Never(), on_stall=lambda: heals.__setitem__("n", heals["n"] + 1))
    assert result is False
    assert heals["n"] == 1


def test_watch_playback_aborts(monkeypatch):
    monkeypatch.setattr(monitor, "interruptible_sleep", lambda *a, **k: None)
    cfg = Config(oppo_ip="x")
    assert monitor.watch_playback(cfg, _Client(plays_on=999), should_abort=lambda: True) is False


def test_http_transient_unknown_is_not_a_stop(monkeypatch):
    # b4: a swallowed transport error ("unknown") must NOT count as idle -> no premature reclaim.
    monkeypatch.setattr(monitor, "interruptible_sleep", lambda *a, **k: None)
    client = _Client(plays_on=1, states=["unknown", "unknown", "playing", "idle", "idle"])
    cfg = Config(oppo_ip="x", poll_interval=2, idle_confirmations=2)
    assert monitor.watch_playback(cfg, client) is True
    # ended only after the two CONFIRMED idles (5 reads); the unknowns did not trip the stop early.
    assert client.state_calls == 5


def test_http_gives_up_when_oppo_unreadable(monkeypatch):
    # b3/b4: a permanently-unreadable OPPO must still end the watch so the reclaim fires.
    monkeypatch.setattr(monitor, "interruptible_sleep", lambda *a, **k: None)
    client = _Client(plays_on=1, states=["unknown"])
    cfg = Config(oppo_ip="x", poll_interval=2, idle_confirmations=2, max_read_failures=3)
    assert monitor.watch_playback(cfg, client) is True
    assert client.state_calls == 3  # gave up after max_read_failures unreadable polls


def test_http_bounded_when_playing_flag_sticks(monkeypatch):
    # b3: if playback_state sticks "playing" forever, the wall-clock ceiling still returns so the
    # external-player process can't hang and the orchestrator's reclaim runs.
    monkeypatch.setattr(monitor, "interruptible_sleep", lambda *a, **k: None)
    client = _Client(plays_on=1, states=["playing"])
    cfg = Config(oppo_ip="x", poll_interval=5, idle_confirmations=2, max_watch_seconds=20)
    assert monitor.watch_playback(cfg, client) is True
    assert client.state_calls == 4  # ceil(20/5) poll ceiling, never unbounded


def test_paused_does_not_burn_the_main_watch_ceiling(monkeypatch):
    # #30: a pause must NOT count toward the absolute ceiling. With a 3-poll ceiling, two pauses then
    # real playback still reaches the idle confirmations (5 reads); if pause were counted toward the main
    # ceiling (the old behaviour -- PAUSE reads as "playing") it would have tripped at the 3rd read.
    monkeypatch.setattr(monitor, "interruptible_sleep", lambda *a, **k: None)
    client = _Client(plays_on=1, states=["paused", "paused", "playing", "idle", "idle"])
    cfg = Config(oppo_ip="x", poll_interval=2, idle_confirmations=2, max_watch_seconds=6)
    assert monitor.watch_playback(cfg, client) is True
    assert client.state_calls == 5


def test_paused_forever_terminates_on_its_own_bounded_budget(monkeypatch):
    # #30: an OPPO left paused forever must still terminate (never hang the external-player process). The
    # paused budget is bounded by the same ceiling, so the watch ends even with no idle/abort.
    monkeypatch.setattr(monitor, "interruptible_sleep", lambda *a, **k: None)
    client = _Client(plays_on=1, states=["paused"])
    cfg = Config(oppo_ip="x", poll_interval=5, idle_confirmations=2, max_watch_seconds=20)
    assert monitor.watch_playback(cfg, client) is True
    assert client.state_calls == 4  # ceil(20/5) paused-budget ceiling, never unbounded


def test_paused_playing_interleave_terminates_within_bound(monkeypatch):
    # #30 regression (audit HIGH): the pathological 1-playing : (max_polls-1)-paused pattern must NOT
    # defeat the ceiling. With a MONOTONIC paused counter the loop ends within polls+paused <= 2*max_polls
    # reads; the pre-fix (paused reset on every playing read) ran ~max_polls^2 reads (~1080 days at
    # defaults). max_watch=20/interval=5 -> max_polls=4; pattern = playing, paused, paused, paused, ...
    monkeypatch.setattr(monitor, "interruptible_sleep", lambda *a, **k: None)
    client = _Client(plays_on=1, states=(["playing"] + ["paused"] * 3) * 50)
    cfg = Config(oppo_ip="x", poll_interval=5, idle_confirmations=2, max_watch_seconds=20)
    assert monitor.watch_playback(cfg, client) is True
    assert client.state_calls <= 2 * 4  # <= 2*max_polls; the pre-fix bug ran far past this (max_polls^2)

"""The first-run wizard: pure path-detection helpers + the guided flow driven through fake UI / client /
settings adapters (the real xbmcgui adapter in wizard.py at the add-on root is Kodi-only)."""
import importlib
import sys
import types

from resources.lib import config as config_mod
from resources.lib import wizard
from resources.lib.config import Config


# --- pure helpers -------------------------------------------------------------------------------

def test_extract_playing_path_variants():
    assert wizard.extract_playing_path({"path": "/mnt/nfs1/Movies/x.iso"}) == "/mnt/nfs1/Movies/x.iso"
    assert wizard.extract_playing_path({"a": {"b": "nfs://192.168.10.20/srv/nfs/media/x.iso"}}) \
        == "nfs://192.168.10.20/srv/nfs/media/x.iso"
    # a raw JSON-ish string, with a space in the title -> captured whole, stops at the closing quote
    assert wizard.extract_playing_path('{"path":"/mnt/nfs2/01Movies/Dune (2021).iso","time":12}') \
        == "/mnt/nfs2/01Movies/Dune (2021).iso"


def test_extract_playing_path_none():
    assert wizard.extract_playing_path({"is_video_playing": True, "title": "Dune"}) is None
    assert wizard.extract_playing_path("") is None
    assert wizard.extract_playing_path({}) is None


def test_derive_mount_point():
    assert wizard.derive_mount_point("/mnt/nfs2/01Movies/x.iso") == "/mnt/nfs2"
    assert wizard.derive_mount_point("/mnt/nfs1") == "/mnt/nfs1"
    # #33: a mount whose name contains a space must be kept whole (extract_playing_path allows spaces).
    assert wizard.derive_mount_point("/mnt/nfs share/Movies/x.iso") == "/mnt/nfs share"
    assert wizard.derive_mount_point("nfs://host/srv/x.iso") is None
    assert wizard.derive_mount_point("") is None


# --- flow harness -------------------------------------------------------------------------------

class FakeUI:
    def __init__(self, *, selects=None, inputs=None, yesnos=None):
        self.selects = list(selects or [])
        self.inputs = list(inputs or [])
        self.yesnos = list(yesnos or [])
        self.oks = []

    def ok(self, title, message):
        self.oks.append((title, message))

    def yesno(self, title, message):
        return self.yesnos.pop(0) if self.yesnos else False

    def input(self, title, default=""):
        return self.inputs.pop(0) if self.inputs else default

    def select(self, title, options):
        return self.selects.pop(0) if self.selects else -1


class FakeClient:
    def __init__(self, *, reachable=True, globalinfo=None, playingtime=""):
        self.reachable = reachable
        self._gi = {} if globalinfo is None else globalinfo
        self._pt = playingtime

    def wake_and_wait(self):
        return self.reachable

    def get_global_info(self):
        return self._gi

    def get_playing_time(self):
        return self._pt


class FakeSettings:
    def __init__(self, **initial):
        self.store = dict(initial)

    def get(self, key):
        return str(self.store.get(key, ""))

    def set(self, key, value):
        self.store[key] = value

    def config(self):
        return Config(oppo_ip=str(self.store.get("oppo_ip", "")),
                      oppo_model=str(self.store.get("oppo_model", "M9205")))


def _run(ui, client, settings):
    grabs, reclaims = [], []
    summary = wizard.run_wizard(
        ui, lambda cfg: client, settings,
        sleep=lambda *a, **k: None,
        reclaim=lambda cfg: reclaims.append(cfg),
        grab=lambda c: grabs.append(c),
    )
    return summary, grabs, reclaims


PATH = "/mnt/nfs1/01Movies/Dune (2021).iso"


def test_wizard_dismissed_sets_done_and_stops_nagging():
    ui = FakeUI(yesnos=[False])  # "Run setup now?" -> No
    settings = FakeSettings()
    summary, grabs, reclaims = _run(ui, FakeClient(), settings)
    assert summary.get("dismissed") is True
    assert settings.store.get("wizard_done") is True   # so the first-run auto-launch won't nag again
    assert "model" not in summary                       # bailed before any setup
    assert grabs == [] and reclaims == []


def test_wizard_aborts_when_oppo_unreachable():
    ui = FakeUI(yesnos=[True], selects=[0], inputs=["1.2.3.4"])  # run, M9205, an IP
    settings = FakeSettings()
    summary, grabs, reclaims = _run(ui, FakeClient(reachable=False), settings)
    assert summary["ping"] is False
    assert summary["completed"] is False                # did NOT proceed past the unreachable ping
    assert "wizard_done" not in settings.store
    assert grabs == [] and reclaims == []


def test_wizard_m9205_full_flow_detects_and_captures():
    ui = FakeUI(selects=[0], inputs=["192.168.10.10"],
                yesnos=[True, True, True, True, True, True, True])  # run, cec ready/oppo/kodi, iso, bdmv, reclaim
    settings = FakeSettings()
    client = FakeClient(globalinfo={"is_video_playing": True, "path": PATH})
    summary, grabs, reclaims = _run(ui, client, settings)
    assert summary["completed"] is True
    assert summary["model"] == "M9205"
    assert summary["cec_m9205"] is True
    assert summary["iso"]["detected"] == PATH and summary["iso"]["mount"] == "/mnt/nfs1"
    assert summary["bdmv"]["detected"] == PATH
    assert summary["reclaim"] is True
    assert settings.store["wizard_done"] is True
    assert settings.store["detected_iso_path"] == PATH
    assert settings.store["detected_bdmv_path"] == PATH
    assert len(grabs) == 1               # grabbed once for the M9205 CEC test
    assert len(reclaims) == 2            # CEC test + the step-9 reclaim


def test_wizard_m9207_skips_grab():
    ui = FakeUI(selects=[1], inputs=["192.168.10.228"],
                yesnos=[True, True, True, True])  # run, iso capture, bdmv capture, reclaim (NO cec yesno)
    settings = FakeSettings()
    client = FakeClient(globalinfo={"is_disc_playing": True, "path": PATH})
    summary, grabs, reclaims = _run(ui, client, settings)
    assert summary["model"] == "M9207"
    assert "cec_m9205" not in summary    # the M9207 has no grab step
    assert grabs == []                   # never power-cycled
    assert len(reclaims) == 1            # only the step-9 reclaim
    assert summary["completed"] is True


def test_wizard_unreachable_dialog_shows_resolved_ip():
    # #31: the operator picks M9207 but types the M9205 default (.10); resolve_oppo_ip rewrites it to
    # .228, which is what the client actually pings. The failure dialog must name the RESOLVED .228, not
    # the typed .10 -- otherwise it points the operator at an address that was never contacted.
    class _ResolvingSettings(FakeSettings):
        def config(self):
            model = str(self.store.get("oppo_model", "M9205"))
            return Config(
                oppo_ip=config_mod.resolve_oppo_ip(model, str(self.store.get("oppo_ip", ""))),
                oppo_model=model,
            )

    ui = FakeUI(yesnos=[True], selects=[1], inputs=["192.168.10.10"])  # run, M9207, typed the M9205 default
    settings = _ResolvingSettings()
    summary, grabs, reclaims = _run(ui, FakeClient(reachable=False), settings)
    assert summary["ping"] is False
    msg = " ".join(m for _, m in ui.oks)
    assert "192.168.10.228" in msg and "192.168.10.10" not in msg


def test_wizard_captures_detected_mount_to_oppo_mount():
    # #33: the detected mount must be applied to oppo_mount (the setting the play path uses), not only the
    # cosmetic detected_*_path. The OPPO reports /mnt/nfs2 here, so oppo_mount must become 'nfs2'.
    p2 = "/mnt/nfs2/01Movies/Dune.iso"
    ui = FakeUI(selects=[1], inputs=["192.168.10.228"],
                yesnos=[True, True, True, True])  # run, iso capture, bdmv capture, reclaim (M9207: no cec)
    settings = FakeSettings()
    client = FakeClient(globalinfo={"is_disc_playing": True, "path": p2})
    summary, grabs, reclaims = _run(ui, client, settings)
    assert settings.store["oppo_mount"] == "nfs2"          # #33: the real knob is written
    assert settings.store["detected_iso_path"] == p2       # cosmetic record still kept


def test_wizard_no_path_detected_is_graceful():
    ui = FakeUI(selects=[1], inputs=["192.168.10.228"], yesnos=[True, True])  # run, reclaim (no capture prompts)
    settings = FakeSettings()
    client = FakeClient(globalinfo={"is_video_playing": False})  # flags only, no path
    summary, grabs, reclaims = _run(ui, client, settings)
    assert summary["iso"]["detected"] is None
    assert summary["bdmv"]["detected"] is None
    assert "detected_iso_path" not in settings.store   # nothing captured
    assert summary["completed"] is True                 # wizard still finishes


# --- the Kodi settings adapter: wizard_done must round-trip as a BOOLEAN (regression) -------------

def _typed_xbmcaddon():
    """A fake xbmcaddon modelling Kodi's TYPE-CHECKED getters: getSettingString on a boolean-typed
    setting returns "" (the trap), getSettingBool returns the stored bool."""
    mod = types.ModuleType("xbmcaddon")

    class Addon:
        def __init__(self, addon_id=None):
            self._store = {}

        def setSettingBool(self, key, value):
            self._store[key] = ("bool", bool(value))

        def setSettingString(self, key, value):
            self._store[key] = ("str", str(value))

        def getSettingBool(self, key):
            kind, val = self._store.get(key, ("bool", False))
            return val if kind == "bool" else False

        def getSettingString(self, key):
            kind, val = self._store.get(key, ("str", ""))
            return val if kind == "str" else ""   # typed mismatch -> "" (Kodi behaviour)

    mod.Addon = Addon
    return mod


def test_kodisettings_wizard_done_roundtrips_as_bool(monkeypatch):
    # wizard_done is written as a bool (setSettingBool) and MUST be read with getSettingBool. Reading it
    # via getSettingString returns "" for a boolean setting, which defeated the "don't nag again" guard.
    monkeypatch.setitem(sys.modules, "xbmcaddon", _typed_xbmcaddon())
    entry = importlib.import_module("wizard")   # the add-on-root entry (NOT resources.lib.wizard)
    s = entry._KodiSettings()
    s.set("wizard_done", True)
    assert s.get_bool("wizard_done") is True    # the fix: typed bool read sees it
    assert s.get("wizard_done") == ""           # getSettingString on a bool setting -> "" (the old bug)

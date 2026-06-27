"""The handoff detector: only disc content (ISO + BDMV/VIDEO_TS) qualifies; everything else stays in
Kodi. pcf.py builds its routing rules from the same PCF_RULES, so the two cannot drift."""
import re

from resources.lib import detector, pcf


def test_is_handoff_target_disc_content():
    assert detector.is_handoff_target("01Movies/Dune (2021).iso")
    assert detector.is_handoff_target("X/Y.ISO")
    assert detector.is_handoff_target("01Movies/Ant-Man (2015)/BDMV/index.bdmv")
    assert detector.is_handoff_target("nfs://h/s/01Movies/Ant-Man/BDMV/STREAM/00800.m2ts")
    assert detector.is_handoff_target("X/VIDEO_TS/VIDEO_TS.IFO")
    assert detector.is_handoff_target("nfs://h/s/01Movies/Dune%20(2021).iso")  # url-encoded


def test_is_handoff_target_rejects_everything_else():
    assert not detector.is_handoff_target("02TV/Show/S01E01.mkv")
    assert not detector.is_handoff_target("X/film.mp4")
    assert not detector.is_handoff_target("Movies/looseclip/STREAM/0080.m2ts")  # no BDMV folder


def test_disc_folder():
    assert detector.disc_folder("01Movies/Ant-Man (2015)/BDMV/index.bdmv") == "01Movies/Ant-Man (2015)"
    assert detector.disc_folder("x/VIDEO_TS/VIDEO_TS.IFO") == "x"


def test_disc_folder_at_share_root():
    # a disc structure directly at the export root -> "" (mount the export root itself)
    assert detector.disc_folder("BDMV/index.bdmv") == ""
    assert detector.disc_folder("VIDEO_TS/VIDEO_TS.IFO") == ""


def test_is_disc_path_detects_a_root_level_disc_segment():
    assert detector.is_disc_path("BDMV/STREAM/00800.m2ts")  # root-level BDMV stream now detected
    assert detector.is_disc_path("VIDEO_TS/VTS_01_1.VOB")


def test_pcf_rules_match_a_root_level_disc_segment():
    # The generated XML rule must match a disc segment at the path START too (no leading slash), in
    # lockstep with the runtime _disc_marker_index -- otherwise the two drift for a root-level path.
    assert _xml_would_route("BDMV/STREAM/00800.m2ts")
    assert _xml_would_route("VIDEO_TS/VTS_01_1.VOB")
    assert _xml_would_route("nfs://h/s/X/VIDEO_TS/VTS_01_1.VOB")  # the prefixed case still matches


def test_disc_folder_loose_bdmv_returns_containing_dir():
    # a bare .bdmv NOT under a BDMV/ folder -> the disc folder is the dir that CONTAINS it (drop the
    # leaf), so the handoff mounts a real folder and never the .bdmv FILE's own path (OPPO hard-crash).
    assert detector.disc_folder("01Movies/Film/index.bdmv/") == "01Movies/Film"
    assert detector.disc_folder("01Movies/Film/index.bdmv") == "01Movies/Film"
    assert detector.disc_folder("index.bdmv/") == ""  # loose .bdmv at the share root


def test_pcf_rules_drive_the_xml():
    xml = pcf.build_xml("/path/to/pcf_player.py", "python3")
    # the iso filetype rule and the bdmv/iso filename rules from detector.PCF_RULES are emitted
    assert '<rule filetypes="iso" player="OppoKodiBridge"/>' in xml
    assert r'<rule filename="(?i).*\.iso$" player="OppoKodiBridge"/>' in xml
    assert "/path/to/pcf_player.py" in xml
    for kind, pattern in detector.PCF_RULES:
        assert '{}="{}"'.format(kind, pattern) in xml


def test_hvdvd_ts_and_bare_ifo_are_not_handoff_targets():
    # Option B: HD-DVD (HVDVD_TS) is not an OPPO-playable format, and a bare .ifo on its own is not a
    # disc -- both stay in Kodi rather than triggering a failed OPPO handoff.
    assert not detector.is_handoff_target("nfs://h/s/01Movies/Film/HVDVD_TS/HV000I01.EVO")
    assert not detector.is_handoff_target("nfs://h/s/01Movies/Film/HVDVD_TS/index.ifo")
    assert not detector.is_handoff_target("nfs://h/s/01Movies/notes.ifo")  # bare .ifo, no VIDEO_TS
    # ...but a .ifo INSIDE a VIDEO_TS folder still qualifies (matched via the folder, not the suffix).
    assert detector.is_handoff_target("nfs://h/s/01Movies/Film/VIDEO_TS/VIDEO_TS.IFO")


def _xml_would_route(path):
    """Apply PCF_RULES the way Kodi's playercorefactory does: a 'filetypes' rule matches the file
    extension; a 'filename' rule is a regex (the patterns carry their own (?i)) searched against the
    whole path. Route to the external player if ANY rule matches."""
    basename = path.rsplit("/", 1)[-1]
    ext = basename.rsplit(".", 1)[-1].lower() if "." in basename else ""
    for kind, pattern in detector.PCF_RULES:
        if kind == "filetypes":
            if re.fullmatch(pattern, ext, re.IGNORECASE):
                return True
        elif re.search(pattern, path):  # filename regex
            return True
    return False


def test_pcf_rules_and_runtime_agree():
    # Single source of truth: for every realistic full path Kodi might route, the generated
    # playercorefactory.xml rules and is_handoff_target MUST give the same answer (no drift).
    corpus = [
        ("nfs://h/s/01Movies/Dune (2021).iso", True),
        ("nfs://h/s/X/Y.ISO", True),
        ("nfs://h/s/01Movies/Ant-Man (2015)/BDMV/index.bdmv", True),
        ("nfs://h/s/01Movies/Film/index.bdmv", True),  # .bdmv leaf NOT under a BDMV/ dir -> exercises the suffix rule alone
        ("BDMV/index.bdmv", True),  # root-level disc segment (no host/share prefix) -> XML & runtime must agree
        ("VIDEO_TS/VIDEO_TS.IFO", True),
        ("nfs://h/s/01Movies/Ant-Man (2015)/BDMV/STREAM/00800.m2ts", True),
        ("nfs://h/s/X/VIDEO_TS/VIDEO_TS.IFO", True),
        ("nfs://h/s/01Movies/Film/HVDVD_TS/HV000I01.EVO", False),
        ("nfs://h/s/01Movies/notes.ifo", False),
        ("nfs://h/s/02TV/Show/S01E01.mkv", False),
        ("nfs://h/s/X/film.mp4", False),
        ("nfs://h/s/Movies/looseclip/STREAM/0080.m2ts", False),
    ]
    for path, expected in corpus:
        assert _xml_would_route(path) == expected, ("xml", path)
        assert detector.is_handoff_target(path) == expected, ("runtime", path)
        assert _xml_would_route(path) == detector.is_handoff_target(path), ("drift", path)

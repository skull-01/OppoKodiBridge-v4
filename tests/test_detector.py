"""The handoff detector: only disc content (ISO + BDMV/VIDEO_TS) qualifies; everything else stays in
Kodi. pcf.py builds its routing rules from the same PCF_RULES, so the two cannot drift."""
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


def test_pcf_rules_drive_the_xml():
    xml = pcf.build_xml("/path/to/pcf_player.py", "python3")
    # the iso filetype rule and the bdmv/iso filename rules from detector.PCF_RULES are emitted
    assert '<rule filetypes="iso" player="OppoKodiBridge"/>' in xml
    assert r'<rule filename="(?i).*\.iso$" player="OppoKodiBridge"/>' in xml
    assert "/path/to/pcf_player.py" in xml
    for kind, pattern in detector.PCF_RULES:
        assert '{}="{}"'.format(kind, pattern) in xml

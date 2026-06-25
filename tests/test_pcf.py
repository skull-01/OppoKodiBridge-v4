"""playercorefactory.xml generation + the marker-guarded backup/restore (never clobber a user's own
file) + XML-escaping of substituted paths."""
import os
import tempfile
import xml.dom.minidom as minidom

from resources.lib import pcf


def test_build_xml_well_formed_and_escapes_special_chars():
    # a Kodi home dir containing '&' must not corrupt the XML
    xml = pcf.build_xml("/home/My & Stuff/addons/x/pcf_player.py", "python3")
    minidom.parseString(xml)  # raises if malformed
    assert pcf._MARKER in xml
    assert '<rule filetypes="iso" player="OppoKodiBridge"/>' in xml


def test_install_backs_up_a_foreign_file_even_if_it_mentions_the_player_name():
    with tempfile.TemporaryDirectory() as d:
        target = os.path.join(d, "playercorefactory.xml")
        # the regression case: a user's own file that mentions "OppoKodiBridge" but is NOT ours
        user = "<playercorefactory><!-- my OppoKodiBridge notes --></playercorefactory>"
        with open(target, "w", encoding="utf-8") as fh:
            fh.write(user)
        pcf.install(d, "/x/pcf_player.py", "python3")
        assert os.path.exists(target + ".okbv4-backup")  # backed up despite the substring
        with open(target, encoding="utf-8") as fh:
            assert pcf._MARKER in fh.read()  # ours is installed now
        pcf.uninstall(d)
        with open(target, encoding="utf-8") as fh:
            assert fh.read() == user  # the user's file is restored verbatim


def test_uninstall_leaves_a_foreign_file_untouched():
    with tempfile.TemporaryDirectory() as d:
        target = os.path.join(d, "playercorefactory.xml")
        user = "<playercorefactory><!-- not ours, no backup --></playercorefactory>"
        with open(target, "w", encoding="utf-8") as fh:
            fh.write(user)
        pcf.uninstall(d)  # no backup + no marker -> must NOT delete the user's file
        with open(target, encoding="utf-8") as fh:
            assert fh.read() == user

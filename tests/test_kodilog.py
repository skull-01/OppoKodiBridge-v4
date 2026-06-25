"""The log tag must carry the .cec suffix so lines aren't mislabeled as the v2 add-on being compared."""
from resources.lib import kodilog


def test_addon_id_is_the_cec_id():
    assert kodilog.ADDON_ID == "service.oppokodibridge.v4"

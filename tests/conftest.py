import os
import sys

ADDON_ROOT = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "service.oppokodibridge.v4"
)
if ADDON_ROOT not in sys.path:
    sys.path.insert(0, ADDON_ROOT)

"""Kodi service entry point (declared in addon.xml as library="service.py").

This fork runs the playercorefactory-installer service, not a playback monitor.
"""
from resources.lib.service_cec import main

if __name__ == "__main__":
    main()

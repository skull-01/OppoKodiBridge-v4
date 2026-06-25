"""script.cecreclaim -- re-assert Kodi's OWN HDMI-CEC active source.

OppoKodiBridge calls this over localhost JSON-RPC (Addons.ExecuteAddon -> script.cecreclaim) when a
disc handoff ends, so Kodi re-announces ITS OWN active source (legitimate, in-spec CEC -- a device
announcing its own source) and the TV returns to Kodi's input. Single-shot; the switch is open-loop
(CEC has no acknowledgement), so there is nothing to confirm and nothing to retry here.
"""
import xbmc

xbmc.executebuiltin("CECActivateSource")
xbmc.log("[script.cecreclaim] CECActivateSource executed", xbmc.LOGINFO)

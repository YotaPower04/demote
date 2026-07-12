"""Brand-agnostic logical remote keys and capability flags.

Drivers translate these logical names into their protocol-specific codes; the UI
and the gamepad reader emit only logical names, so nothing above the driver layer
knows about Samsung `KEY_*`, Roku key words, LG SSAP URIs, etc.
"""
from __future__ import annotations

# --- Navigation ----------------------------------------------------------
UP = "UP"
DOWN = "DOWN"
LEFT = "LEFT"
RIGHT = "RIGHT"
OK = "OK"
BACK = "BACK"
HOME = "HOME"
MENU = "MENU"
EXIT = "EXIT"
INFO = "INFO"
TOOLS = "TOOLS"

# --- Power / input -------------------------------------------------------
POWER = "POWER"
SOURCE = "SOURCE"

# --- Volume / channel ----------------------------------------------------
VOLUP = "VOLUP"
VOLDOWN = "VOLDOWN"
MUTE = "MUTE"
CHUP = "CHUP"
CHDOWN = "CHDOWN"
CH_LIST = "CH_LIST"

# --- Media transport -----------------------------------------------------
PLAY = "PLAY"
PAUSE = "PAUSE"
STOP = "STOP"
REWIND = "REWIND"
FF = "FF"

# --- Numbers / colors ----------------------------------------------------
NUM = [f"NUM{n}" for n in range(10)]        # NUM0 .. NUM9
RED = "RED"
GREEN = "GREEN"
YELLOW = "YELLOW"
BLUE = "BLUE"


class Cap:
    """Feature flags a driver advertises via ``TVDriver.capabilities()``.

    The UI shows/greys sections based on these; a driver need only implement what
    its device supports.
    """
    NAV = "nav"              # d-pad / OK / back / home / menu
    POWER = "power"          # can power off
    POWER_ON = "power_on"    # can wake the device (e.g. Wake-on-LAN)
    VOLUME = "volume"        # volume up/down/mute
    CHANNELS = "channels"    # channel up/down + list
    NUMBERS = "numbers"      # 0-9 keypad
    MEDIA = "media"          # play/pause/stop/rew/ff
    COLORS = "colors"        # red/green/yellow/blue
    SOURCE = "source"        # input switching
    APPS = "apps"            # list + launch apps
    TEXT = "text"            # send text to an on-screen keyboard
    CAST = "cast"            # push media (DLNA/etc.)

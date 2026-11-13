"""Constants and small helpers of the HA CoAP integration."""

from __future__ import annotations

import re
from datetime import timedelta

DOMAIN = "ha_coap"

MANUFACTURER = "Yann T."
MODEL = "ha-coap node"

# How often the boards are asked for their readings, and how many of those times between two
# reads of what changes rarely ('info': firmware, hardware; 'pumpdc': the pump's run time, with
# firmware whose 'diag' doesn't tell it)
UPDATE_INTERVAL = timedelta(minutes=15)
INFO_EVERY = 4  # every hour

# In a board's entry: it was said to be off (Repairs), so it isn't an issue until it has answered again
QUIET_WHILE_OFF = "quiet_while_off"

BOARD_ID = re.compile(r"[0-9a-fA-F]{8}")


def board_id(text: str | None) -> str | None:
    """A board's ID (8 hex digits, lowercase) from a name such as "ha-coap-1a2b3c4d" (its last
    dash-separated part) or "coap-1a2b3c4d" (the IDs before version 2), or the ID itself. None
    when there's none."""
    last = (text or "").strip().rsplit("-", 1)[-1]
    return last.lower() if BOARD_ID.fullmatch(last) else None


def clean_host(host: str | None) -> str:
    """An address as stored: without the brackets of a URL ("[fd11::1]" → "fd11::1")."""
    return (host or "").strip().strip("[]").strip()

"""Download diagnostics (on the board's device page): its entry and what it last said."""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from homeassistant.core import HomeAssistant

from .coordinator import HaCoapConfigEntry


async def async_get_config_entry_diagnostics(hass: HomeAssistant, entry: HaCoapConfigEntry) -> dict[str, Any]:
    coordinator = entry.runtime_data
    return {
        "entry": {"title": entry.title, "version": entry.version, "data": dict(entry.data)},
        "host": coordinator.host,
        "last_update_success": coordinator.last_update_success,
        "last_exception": repr(coordinator.last_exception) if coordinator.last_exception else None,
        "state": asdict(coordinator.data) if coordinator.data else None,
    }

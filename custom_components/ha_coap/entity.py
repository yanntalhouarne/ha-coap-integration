"""What every entity of a board shares: its device, and its coordinator."""

from __future__ import annotations

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, MANUFACTURER, MODEL
from .coordinator import HaCoapCoordinator


class HaCoapEntity(CoordinatorEntity[HaCoapCoordinator]):
    """An entity of a board. Its unique ID: "<board ID>_<key>" (the keys of version 1, so
    migrated entities keep theirs)."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: HaCoapCoordinator, key: str) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.device_id}_{key}"
        info = coordinator.data.info if coordinator.data else None
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, coordinator.device_id)},
            name=coordinator.name,
            manufacturer=MANUFACTURER,
            model=MODEL,
            sw_version=info.fw_version if info else None,
            hw_version=(info.hw_version or None) if info else None,
        )

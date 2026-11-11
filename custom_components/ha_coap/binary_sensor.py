"""Whether the board answers: on when its last reading came, off when it didn't."""

from __future__ import annotations

from homeassistant.components.binary_sensor import BinarySensorDeviceClass, BinarySensorEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .coordinator import HaCoapConfigEntry
from .entity import HaCoapEntity


async def async_setup_entry(hass: HomeAssistant, entry: HaCoapConfigEntry, async_add_entities: AddEntitiesCallback) -> None:
    async_add_entities([HaCoapConnectivity(entry.runtime_data)])


class HaCoapConnectivity(HaCoapEntity, BinarySensorEntity):
    _attr_translation_key = "connectivity"
    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator) -> None:
        super().__init__(coordinator, "connectivity")

    @property
    def available(self) -> bool:
        """Always: it's the one that says the board doesn't answer."""
        return True

    @property
    def is_on(self) -> bool | None:
        """None (unknown) until the board was first asked."""
        if self.coordinator.data is None and self.coordinator.last_update_success:
            return None
        return self.coordinator.last_update_success

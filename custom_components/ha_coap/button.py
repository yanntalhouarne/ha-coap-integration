"""The board's pump (runs it for its run time; the Water action runs it for a given time) and its
buzzer (a beep, to find it)."""

from __future__ import annotations

import voluptuous as vol
from homeassistant.components.button import ButtonDeviceClass, ButtonEntity
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import entity_platform
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .coordinator import HaCoapConfigEntry
from .entity import HaCoapEntity

SERVICE_WATER = "water"


async def async_setup_entry(hass: HomeAssistant, entry: HaCoapConfigEntry, async_add_entities: AddEntitiesCallback) -> None:
    coordinator = entry.runtime_data
    async_add_entities([HaCoapPumpButton(coordinator), HaCoapBeepButton(coordinator)])
    platform = entity_platform.async_get_current_platform()
    platform.async_register_entity_service(
        SERVICE_WATER, {vol.Optional("seconds"): vol.All(cv.positive_int, vol.Range(min=1, max=9))}, "async_water")


class HaCoapPumpButton(HaCoapEntity, ButtonEntity):
    _attr_translation_key = "pump"

    def __init__(self, coordinator) -> None:
        super().__init__(coordinator, "pump")

    @property
    def available(self) -> bool:
        """Always: pressing it is how to try a board that didn't answer the last reading."""
        return True

    async def async_press(self) -> None:
        """Raises HomeAssistantError when the board doesn't answer (the page shows it)."""
        await self.coordinator.water()

    async def async_water(self, seconds: int | None = None) -> None:
        """The Water action: for 'seconds' (1 to 9), else its run time."""
        await self.coordinator.water(seconds)


class HaCoapBeepButton(HaCoapEntity, ButtonEntity):
    """Its buzzer beeps, to find it ("identify")."""

    _attr_translation_key = "ping"
    _attr_device_class = ButtonDeviceClass.IDENTIFY

    def __init__(self, coordinator) -> None:
        super().__init__(coordinator, "ping")

    @property
    def available(self) -> bool:
        return True

    async def async_press(self) -> None:
        await self.coordinator.put("ping", b"1")  # 1: with a beep

    async def async_water(self, seconds: int | None = None) -> None:
        raise ServiceValidationError(translation_domain=DOMAIN, translation_key="not_a_pump")

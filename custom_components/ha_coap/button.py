"""The board's pump (runs it for its run time) and its buzzer (a beep, to find it)."""

from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .coordinator import HaCoapConfigEntry
from .entity import HaCoapEntity


async def async_setup_entry(hass: HomeAssistant, entry: HaCoapConfigEntry, async_add_entities: AddEntitiesCallback) -> None:
    coordinator = entry.runtime_data
    async_add_entities([HaCoapButton(coordinator, "pump", "pump", b"1"),   # runs its pump for its run time
                        HaCoapButton(coordinator, "ping", "ping", b"1")])  # 1: with a beep


class HaCoapButton(HaCoapEntity, ButtonEntity):
    def __init__(self, coordinator, key: str, path: str, payload: bytes) -> None:
        super().__init__(coordinator, key)
        self._attr_translation_key = key
        self._path = path
        self._payload = payload

    @property
    def available(self) -> bool:
        """Always: pressing it is how to try a board that didn't answer the last reading."""
        return True

    async def async_press(self) -> None:
        """Raises HomeAssistantError when the board doesn't answer (the page shows it)."""
        await self.coordinator.put(self._path, self._payload)

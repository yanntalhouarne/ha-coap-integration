"""A board's events: it restarted (and why), and its pump ran with its button (newer firmware,
seen at the next reading)."""

from __future__ import annotations

from homeassistant.components.event import EventEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import coap
from .coordinator import HaCoapConfigEntry
from .entity import HaCoapEntity
from .sensor import RESET_CAUSES


async def async_setup_entry(hass: HomeAssistant, entry: HaCoapConfigEntry, async_add_entities: AddEntitiesCallback) -> None:
    coordinator = entry.runtime_data
    async_add_entities([HaCoapRestartEvent(coordinator), HaCoapButtonEvent(coordinator)])


class HaCoapRestartEvent(HaCoapEntity, EventEntity):
    """It restarted: the event type is why (a crash, its watchdog... are worth an automation)."""

    _attr_translation_key = "restarted"
    _attr_event_types = RESET_CAUSES
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator) -> None:
        super().__init__(coordinator, "restarted")

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self.coordinator.restart_listeners.append(self._restarted)
        self.async_on_remove(lambda: self.coordinator.restart_listeners.remove(self._restarted))

    @callback
    def _restarted(self, diag: coap.Diagnostics) -> None:
        cause = diag.reset if diag.reset in RESET_CAUSES else "other"
        self._trigger_event(cause, {"restarts": diag.boots, "fault": diag.fault or None})
        self.async_write_ha_state()


class HaCoapButtonEvent(HaCoapEntity, EventEntity):
    """Its pump ran with its button (S1): seen at the next reading, with how long it ran."""

    _attr_translation_key = "button_pressed"
    _attr_event_types = ["pressed"]

    def __init__(self, coordinator) -> None:
        super().__init__(coordinator, "button_pressed")

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self.coordinator.button_listeners.append(self._pressed)
        self.async_on_remove(lambda: self.coordinator.button_listeners.remove(self._pressed))

    @callback
    def _pressed(self, seconds: int) -> None:
        self._trigger_event("pressed", {"seconds": seconds})
        self.async_write_ha_state()

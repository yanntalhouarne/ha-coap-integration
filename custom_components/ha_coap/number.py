"""The pump's run time: how long a press of the pump button runs it (1 to 9 s; the board keeps it
across restarts)."""

from __future__ import annotations

from homeassistant.components.number import NumberDeviceClass, NumberEntity, NumberMode
from homeassistant.const import EntityCategory, UnitOfTime
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .coordinator import HaCoapConfigEntry
from .entity import HaCoapEntity


async def async_setup_entry(hass: HomeAssistant, entry: HaCoapConfigEntry, async_add_entities: AddEntitiesCallback) -> None:
    async_add_entities([HaCoapPumpRunTime(entry.runtime_data)])


class HaCoapPumpRunTime(HaCoapEntity, NumberEntity):
    _attr_translation_key = "pump_run_time"
    _attr_device_class = NumberDeviceClass.DURATION
    _attr_native_unit_of_measurement = UnitOfTime.SECONDS
    _attr_native_min_value = 1
    _attr_native_max_value = 9
    _attr_native_step = 1
    _attr_mode = NumberMode.SLIDER
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, coordinator) -> None:
        super().__init__(coordinator, "pump_dc")  # its key in version 1 ("pump duty cycle")

    @property
    def native_value(self) -> int | None:
        state = self.coordinator.data
        return state.pump_s if state is not None else None

    async def async_set_native_value(self, value: float) -> None:
        await self.coordinator.set_pump_s(int(value))

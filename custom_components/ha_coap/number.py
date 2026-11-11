"""A board's numbers: its pump's run time (on the board, kept across its restarts), and two kept
here: the soil moisture its plant needs water under (Water below), and how much its pump moves a
second (Pump flow, for the water used)."""

from __future__ import annotations

from homeassistant.components.number import NumberDeviceClass, NumberEntity, NumberMode, RestoreNumber
from homeassistant.const import PERCENTAGE, EntityCategory, UnitOfTime
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .coordinator import DEFAULT_PUMP_FLOW, DEFAULT_WATER_BELOW, HaCoapConfigEntry
from .entity import HaCoapEntity


async def async_setup_entry(hass: HomeAssistant, entry: HaCoapConfigEntry, async_add_entities: AddEntitiesCallback) -> None:
    coordinator = entry.runtime_data
    async_add_entities([HaCoapPumpRunTime(coordinator), HaCoapWaterBelow(coordinator), HaCoapPumpFlow(coordinator)])


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


class _KeptHere(HaCoapEntity, RestoreNumber):
    """A number kept by Home Assistant (not the board), in the coordinator for the entities using it."""

    _attr_entity_category = EntityCategory.CONFIG
    _attr_mode = NumberMode.BOX
    _default: float
    _field: str

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        last = await self.async_get_last_number_data()
        value = last.native_value if last is not None and last.native_value is not None else self._default
        setattr(self.coordinator, self._field, value)

    @property
    def available(self) -> bool:
        return True  # it's here, not on the board

    @property
    def native_value(self) -> float:
        return getattr(self.coordinator, self._field)

    async def async_set_native_value(self, value: float) -> None:
        setattr(self.coordinator, self._field, value)
        self.coordinator.async_update_listeners()  # Needs water, Next watering, Water used use it


class HaCoapWaterBelow(_KeptHere):
    """The soil moisture its plant needs water under (0: not watched)."""

    _attr_translation_key = "water_below"
    _attr_native_unit_of_measurement = PERCENTAGE
    _attr_native_min_value = 0
    _attr_native_max_value = 100
    _attr_native_step = 1
    _default = DEFAULT_WATER_BELOW
    _field = "water_below"

    def __init__(self, coordinator) -> None:
        super().__init__(coordinator, "water_below")


class HaCoapPumpFlow(_KeptHere):
    """How much its pump moves a second (ml): measure it once with a cup."""

    _attr_translation_key = "pump_flow"
    _attr_native_unit_of_measurement = "mL/s"
    _attr_native_min_value = 0.1
    _attr_native_max_value = 1000
    _attr_native_step = 0.1
    _default = DEFAULT_PUMP_FLOW
    _field = "pump_flow"

    def __init__(self, coordinator) -> None:
        super().__init__(coordinator, "pump_flow")

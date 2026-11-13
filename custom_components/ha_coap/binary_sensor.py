"""A board's binary sensors: whether it answers, whether its plant needs water, whether its pump
runs (a run started from here), and its power (charging, plugged in: newer firmware)."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.const import CONF_URL, EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .coordinator import BoardState, HaCoapConfigEntry, HaCoapCoordinator
from .entity import HaCoapEntity
from .plants import add_plant_entities, tank_binary_sensors

ON_USB = ("usb", "charging", "charged")


@dataclass(frozen=True, kw_only=True)
class HaCoapBinaryDescription(BinarySensorEntityDescription):
    value: Callable[[BoardState, HaCoapCoordinator], bool | None]


BINARY_SENSORS = (
    HaCoapBinaryDescription(
        key="needs_water", translation_key="needs_water", device_class=BinarySensorDeviceClass.PROBLEM,
        value=lambda s, c: (s.data.soil_moisture < c.water_below) if c.water_below else None),
    HaCoapBinaryDescription(
        key="pump_running", translation_key="pump_running", device_class=BinarySensorDeviceClass.RUNNING,
        value=lambda s, c: s.pump_running),
    HaCoapBinaryDescription(
        key="charging", translation_key="charging", device_class=BinarySensorDeviceClass.BATTERY_CHARGING,
        value=lambda s, c: (s.power == "charging") if s.power else None),
    HaCoapBinaryDescription(
        key="plugged_in", translation_key="plugged_in", device_class=BinarySensorDeviceClass.PLUG,
        value=lambda s, c: (s.power in ON_USB) if s.power else None),
)


async def async_setup_entry(hass: HomeAssistant, entry: HaCoapConfigEntry, async_add_entities: AddEntitiesCallback) -> None:
    if CONF_URL in entry.data:  # the fleet manager: each plant's water tank low
        add_plant_entities(hass, entry, async_add_entities, tank_binary_sensors)
        return
    coordinator = entry.runtime_data
    async_add_entities([HaCoapConnectivity(coordinator), *(HaCoapBinarySensor(coordinator, d) for d in BINARY_SENSORS)])


class HaCoapBinarySensor(HaCoapEntity, BinarySensorEntity):
    entity_description: HaCoapBinaryDescription

    def __init__(self, coordinator: HaCoapCoordinator, description: HaCoapBinaryDescription) -> None:
        super().__init__(coordinator, description.key)
        self.entity_description = description

    @property
    def is_on(self) -> bool | None:
        state = self.coordinator.data
        return self.entity_description.value(state, self.coordinator) if state is not None else None


class HaCoapConnectivity(HaCoapEntity, BinarySensorEntity):
    """Whether the board answers: on when its last reading came, off when it didn't."""

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

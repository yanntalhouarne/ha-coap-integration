"""A board's readings (soil, battery, air humidity, temperature) and what it is (firmware,
hardware, device ID)."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import PERCENTAGE, EntityCategory, UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .coordinator import BoardState, HaCoapConfigEntry
from .entity import HaCoapEntity


@dataclass(frozen=True, kw_only=True)
class HaCoapSensorDescription(SensorEntityDescription):
    value: Callable[[BoardState], int | str | None]


# The keys are those of version 1 (in the entities' unique IDs): they don't change
SENSORS = (
    HaCoapSensorDescription(
        key="soil-humidity", translation_key="soil_moisture", device_class=SensorDeviceClass.MOISTURE,
        native_unit_of_measurement=PERCENTAGE, state_class=SensorStateClass.MEASUREMENT,
        value=lambda s: s.data.soil_moisture),
    HaCoapSensorDescription(
        key="battery", translation_key="battery", device_class=SensorDeviceClass.BATTERY,
        native_unit_of_measurement=PERCENTAGE, state_class=SensorStateClass.MEASUREMENT,
        value=lambda s: s.data.battery),
    HaCoapSensorDescription(
        key="air-humidity", translation_key="air_humidity", device_class=SensorDeviceClass.HUMIDITY,
        native_unit_of_measurement=PERCENTAGE, state_class=SensorStateClass.MEASUREMENT,
        value=lambda s: s.data.air_humidity),
    HaCoapSensorDescription(
        key="temperature", translation_key="temperature", device_class=SensorDeviceClass.TEMPERATURE,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS, state_class=SensorStateClass.MEASUREMENT,
        value=lambda s: s.data.temperature),
    HaCoapSensorDescription(
        key="sw-version", translation_key="firmware", entity_category=EntityCategory.DIAGNOSTIC,
        value=lambda s: s.info.fw_version if s.info else None),
    HaCoapSensorDescription(
        key="hw-version", translation_key="hardware", entity_category=EntityCategory.DIAGNOSTIC,
        value=lambda s: (s.info.hw_version or None) if s.info else None),
    HaCoapSensorDescription(
        key="device-id", translation_key="device_id", entity_category=EntityCategory.DIAGNOSTIC,
        value=lambda s: (s.info.device_id or None) if s.info else None),
)


async def async_setup_entry(hass: HomeAssistant, entry: HaCoapConfigEntry, async_add_entities: AddEntitiesCallback) -> None:
    async_add_entities(HaCoapSensor(entry.runtime_data, description) for description in SENSORS)


class HaCoapSensor(HaCoapEntity, SensorEntity):
    entity_description: HaCoapSensorDescription

    def __init__(self, coordinator, description: HaCoapSensorDescription) -> None:
        super().__init__(coordinator, description.key)
        self.entity_description = description

    @property
    def native_value(self) -> int | str | None:
        """None (unknown) until the board answered."""
        state = self.coordinator.data
        return self.entity_description.value(state) if state is not None else None

"""A board's sensors: its readings (soil, battery, air humidity, temperature), when its plant needs
water next and when it was last watered, the water its pump moved (for the Energy dashboard),
what it is (firmware, hardware, device ID) and its health (signal, parent, restarts)."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from homeassistant.components.sensor import (
    RestoreSensor,
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import (
    CONF_URL,
    PERCENTAGE,
    SIGNAL_STRENGTH_DECIBELS_MILLIWATT,
    EntityCategory,
    UnitOfElectricPotential,
    UnitOfTemperature,
    UnitOfVolume,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .coordinator import BoardState, HaCoapConfigEntry, HaCoapCoordinator
from .plants import add_plant_entities, battery_sensors, care_sensors, round_trip_sensors, tank_sensors
from .entity import HaCoapEntity

RESET_CAUSES = ["power", "pin", "soft", "watchdog", "lockup", "crash", "other"]
ROLES = ["disabled", "detached", "child", "router", "leader"]


def _time(value: float | None) -> datetime | None:
    return datetime.fromtimestamp(value, UTC) if value else None


@dataclass(frozen=True, kw_only=True)
class HaCoapSensorDescription(SensorEntityDescription):
    value: Callable[[BoardState, HaCoapCoordinator], Any]


def _diag(field: str) -> Callable[[BoardState, HaCoapCoordinator], Any]:
    return lambda s, c: getattr(s.diag, field) if s.diag is not None else None


# The keys of version 1 (in the entities' unique IDs) don't change
SENSORS = (
    HaCoapSensorDescription(
        key="soil-humidity", translation_key="soil_moisture", device_class=SensorDeviceClass.MOISTURE,
        native_unit_of_measurement=PERCENTAGE, state_class=SensorStateClass.MEASUREMENT,
        value=lambda s, c: s.data.soil_moisture),
    HaCoapSensorDescription(
        key="battery", translation_key="battery", device_class=SensorDeviceClass.BATTERY,
        native_unit_of_measurement=PERCENTAGE, state_class=SensorStateClass.MEASUREMENT,
        value=lambda s, c: s.data.battery),
    HaCoapSensorDescription(
        key="air-humidity", translation_key="air_humidity", device_class=SensorDeviceClass.HUMIDITY,
        native_unit_of_measurement=PERCENTAGE, state_class=SensorStateClass.MEASUREMENT,
        value=lambda s, c: s.data.air_humidity),
    HaCoapSensorDescription(
        key="temperature", translation_key="temperature", device_class=SensorDeviceClass.TEMPERATURE,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS, state_class=SensorStateClass.MEASUREMENT,
        value=lambda s, c: s.data.temperature),
    # The plant
    HaCoapSensorDescription(
        key="next_watering", translation_key="next_watering", device_class=SensorDeviceClass.TIMESTAMP,
        value=lambda s, c: _time(c.needs_water_at)),
    HaCoapSensorDescription(
        key="last_watered", translation_key="last_watered", device_class=SensorDeviceClass.TIMESTAMP,
        value=lambda s, c: _time(s.last_watered)),
    # What it is
    HaCoapSensorDescription(
        key="sw-version", translation_key="firmware", entity_category=EntityCategory.DIAGNOSTIC,
        value=lambda s, c: s.info.fw_version if s.info else None),
    HaCoapSensorDescription(
        key="hw-version", translation_key="hardware", entity_category=EntityCategory.DIAGNOSTIC,
        value=lambda s, c: (s.info.hw_version or None) if s.info else None),
    HaCoapSensorDescription(
        key="device-id", translation_key="device_id", entity_category=EntityCategory.DIAGNOSTIC,
        value=lambda s, c: (s.info.device_id or None) if s.info else None),
    # Its health ('diag', newer firmware)
    HaCoapSensorDescription(
        key="signal", translation_key="signal", device_class=SensorDeviceClass.SIGNAL_STRENGTH,
        native_unit_of_measurement=SIGNAL_STRENGTH_DECIBELS_MILLIWATT, state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC, value=_diag("rssi")),
    HaCoapSensorDescription(
        key="link_quality", translation_key="link_quality", state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC, value=_diag("link_quality")),
    HaCoapSensorDescription(
        key="parent", translation_key="parent", entity_category=EntityCategory.DIAGNOSTIC,
        value=lambda s, c: (s.diag.parent or None) if s.diag else None),
    HaCoapSensorDescription(
        key="role", translation_key="role", device_class=SensorDeviceClass.ENUM, options=ROLES,
        entity_category=EntityCategory.DIAGNOSTIC,
        value=lambda s, c: s.diag.role if s.diag and s.diag.role in ROLES else None),
    HaCoapSensorDescription(
        key="restarts", translation_key="restarts", entity_category=EntityCategory.DIAGNOSTIC, value=_diag("boots")),
    HaCoapSensorDescription(
        key="restart_cause", translation_key="restart_cause", device_class=SensorDeviceClass.ENUM, options=RESET_CAUSES,
        entity_category=EntityCategory.DIAGNOSTIC,
        value=lambda s, c: s.diag.reset if s.diag and s.diag.reset in RESET_CAUSES else None),
    HaCoapSensorDescription(
        key="soil_voltage", translation_key="soil_voltage", device_class=SensorDeviceClass.VOLTAGE,
        native_unit_of_measurement=UnitOfElectricPotential.MILLIVOLT, state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC, entity_registry_enabled_default=False, value=_diag("soil_mv")),
)


async def async_setup_entry(hass: HomeAssistant, entry: HaCoapConfigEntry, async_add_entities: AddEntitiesCallback) -> None:
    if CONF_URL in entry.data:  # the fleet manager: each plant's water tank and battery forecast
        add_plant_entities(hass, entry, async_add_entities, tank_sensors)
        add_plant_entities(hass, entry, async_add_entities, battery_sensors)
        add_plant_entities(hass, entry, async_add_entities, care_sensors)
        add_plant_entities(hass, entry, async_add_entities, round_trip_sensors)
        return
    coordinator = entry.runtime_data
    async_add_entities([*(HaCoapSensor(coordinator, d) for d in SENSORS), HaCoapLastRestart(coordinator),
                        HaCoapWaterUsed(coordinator)])


class HaCoapSensor(HaCoapEntity, SensorEntity):
    entity_description: HaCoapSensorDescription

    def __init__(self, coordinator: HaCoapCoordinator, description: HaCoapSensorDescription) -> None:
        super().__init__(coordinator, description.key)
        self.entity_description = description

    @property
    def native_value(self) -> Any:
        """None (unknown) until the board answered."""
        state = self.coordinator.data
        return self.entity_description.value(state, self.coordinator) if state is not None else None


class HaCoapLastRestart(HaCoapEntity, SensorEntity):
    """When the board last started: its uptime back from when it said it, kept until the next
    restart (so it doesn't move a little at each reading)."""

    _attr_translation_key = "last_restart"
    _attr_device_class = SensorDeviceClass.TIMESTAMP
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator: HaCoapCoordinator) -> None:
        super().__init__(coordinator, "last_restart")
        self._boots: int | None = None
        self._started: datetime | None = None

    @property
    def native_value(self) -> datetime | None:
        state = self.coordinator.data
        diag = state.diag if state else None
        if diag is None:
            return self._started
        if diag.boots != self._boots or self._started is None:
            self._boots = diag.boots
            self._started = _time(datetime.now(UTC).timestamp() - diag.uptime_s)
        return self._started


class HaCoapWaterUsed(HaCoapEntity, RestoreSensor):
    """The water its pump moved, in liters: its run time (from the board, whatever started it)
    times its pump's flow (its Pump flow number). Always growing, for the Energy dashboard."""

    _attr_translation_key = "water_used"
    _attr_device_class = SensorDeviceClass.WATER
    _attr_native_unit_of_measurement = UnitOfVolume.LITERS
    _attr_state_class = SensorStateClass.TOTAL_INCREASING
    _attr_suggested_display_precision = 2

    def __init__(self, coordinator: HaCoapCoordinator) -> None:
        super().__init__(coordinator, "water_used")
        self._total = 0.0
        self._counted_ms = 0  # the coordinator's pumped_ms already counted

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        last = await self.async_get_last_sensor_data()
        if last is not None and isinstance(last.native_value, (int, float)):
            self._total = float(last.native_value)
        self._counted_ms = self.coordinator.pumped_ms

    @callback
    def _handle_coordinator_update(self) -> None:
        ran_ms = self.coordinator.pumped_ms - self._counted_ms
        if ran_ms > 0:
            self._total += ran_ms * self.coordinator.pump_flow / 1_000_000  # ms × ml/s → l
        self._counted_ms = self.coordinator.pumped_ms
        super()._handle_coordinator_update()

    @property
    def available(self) -> bool:
        return True  # a total: it doesn't go away while the board doesn't answer

    @property
    def native_value(self) -> float:
        return round(self._total, 3)

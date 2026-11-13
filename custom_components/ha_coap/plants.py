"""From the fleet manager, on each plant's device: its water tank (how full, the days it lasts,
low, and a button for a tank refilled), its battery's days left, its photo, and its last silent ping's
round trip. And one to-do list, Plant
care, of the plants' care tasks (fertilize, repot...). The fleet manager keeps them: it counts
each pump run against the tank (Home Assistant's too), and its care tasks come back after they're
done."""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

from homeassistant.components.binary_sensor import BinarySensorEntity
from homeassistant.components.button import ButtonEntity
from homeassistant.components.image import ImageEntity
from homeassistant.components.sensor import SensorDeviceClass, SensorEntity, SensorStateClass
from homeassistant.components.todo import TodoItem, TodoItemStatus, TodoListEntity, TodoListEntityFeature
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import PERCENTAGE, EntityCategory, UnitOfTime
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity import Entity
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import dt as dt_util

from .const import DOMAIN
from .fleet import FleetCoordinator


def add_plant_entities(hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback,
                       make: Callable[[FleetCoordinator, str, dict], list[Entity]]) -> None:
    """Adds make(fleet, device_id, plant)'s entities for each plant whose board Home Assistant
    knows, as they appear (a plant gets none until 'make' gives some: a tank set up later)."""
    fleet: FleetCoordinator = entry.runtime_data
    added: set[str] = set()

    @callback
    def add_new() -> None:
        devices = dr.async_get(hass)
        new: list[Entity] = []
        for device_id, plant in fleet.plants().items():
            if device_id in added or devices.async_get_device(identifiers={(DOMAIN, device_id)}) is None:
                continue
            entities = make(fleet, device_id, plant)
            if entities:
                added.add(device_id)
                new += entities
        if new:
            async_add_entities(new)

    add_new()
    entry.async_on_unload(fleet.async_add_listener(add_new))


def tank_sensors(fleet: FleetCoordinator, device_id: str, plant: dict) -> list[Entity]:
    if plant.get("tank") is None:
        return []
    return [WaterTank(fleet, device_id), WaterTankDaysLeft(fleet, device_id)]


def battery_sensors(fleet: FleetCoordinator, device_id: str, plant: dict) -> list[Entity]:
    return [BatteryDaysLeft(fleet, device_id)]


def round_trip_sensors(fleet: FleetCoordinator, device_id: str, plant: dict) -> list[Entity]:
    # Once it has been pinged (Ping all, or the fleet manager's timed pings)
    return [RoundTrip(fleet, device_id)] if plant.get("round_trip") else []


def care_sensors(fleet: FleetCoordinator, device_id: str, plant: dict) -> list[Entity]:
    return [CareDue(fleet, device_id)] if plant.get("care") else []


def photo_images(fleet: FleetCoordinator, device_id: str, plant: dict) -> list[Entity]:
    return [PlantPhoto(fleet, device_id)] if plant.get("photo") else []


def tank_binary_sensors(fleet: FleetCoordinator, device_id: str, plant: dict) -> list[Entity]:
    return [WaterTankLow(fleet, device_id)] if plant.get("tank") is not None else []


def tank_buttons(fleet: FleetCoordinator, device_id: str, plant: dict) -> list[Entity]:
    return [WaterTankRefilled(fleet, device_id)] if plant.get("tank") is not None else []


class PlantEntity(CoordinatorEntity[FleetCoordinator]):
    """On the plant's board's device; unavailable while the fleet manager doesn't tell it."""

    _attr_has_entity_name = True

    def __init__(self, fleet: FleetCoordinator, device_id: str, key: str) -> None:
        super().__init__(fleet)
        self.device_id = device_id
        self._attr_translation_key = key
        self._attr_unique_id = f"{device_id}_{key}"
        self._attr_device_info = DeviceInfo(identifiers={(DOMAIN, device_id)})

    @property
    def plant(self) -> dict | None:
        return self.coordinator.plants().get(self.device_id)

    @property
    def available(self) -> bool:
        return super().available and self.plant is not None


class TankEntity(PlantEntity):
    """Unavailable while its tank isn't tracked (its size isn't set in the fleet manager)."""

    @property
    def tank(self) -> dict | None:
        return (self.plant or {}).get("tank")

    @property
    def available(self) -> bool:
        return super().available and self.tank is not None


class WaterTank(TankEntity, SensorEntity):
    _attr_native_unit_of_measurement = PERCENTAGE
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_icon = "mdi:cup-water"

    def __init__(self, fleet: FleetCoordinator, device_id: str) -> None:
        super().__init__(fleet, device_id, "water_tank")

    @property
    def native_value(self) -> int | None:
        return self.tank["percent"] if self.tank else None

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        if not self.tank:
            return None
        return {"left_ml": self.tank["left_ml"], "size_ml": self.tank["size_ml"], "per_day_ml": self.tank["per_day_ml"],
                "refilled": dt_util.utc_from_timestamp(self.tank["filled"]).isoformat() if self.tank["filled"] else None}


class DaysLeft(SensorEntity):
    _attr_device_class = SensorDeviceClass.DURATION
    _attr_native_unit_of_measurement = UnitOfTime.DAYS
    _attr_suggested_display_precision = 0


class WaterTankDaysLeft(TankEntity, DaysLeft):
    """Unknown until how fast it empties is known (two pump runs, over a day)."""

    _attr_icon = "mdi:calendar-clock"

    def __init__(self, fleet: FleetCoordinator, device_id: str) -> None:
        super().__init__(fleet, device_id, "water_tank_days_left")

    @property
    def native_value(self) -> float | None:
        return self.tank["days_left"] if self.tank else None


class BatteryDaysLeft(PlantEntity, DaysLeft):
    """From its battery's trend over two weeks. Unknown on USB power, or without a clear trend yet."""

    _attr_icon = "mdi:battery-clock"

    def __init__(self, fleet: FleetCoordinator, device_id: str) -> None:
        super().__init__(fleet, device_id, "battery_days_left")

    @property
    def native_value(self) -> float | None:
        return (self.plant or {}).get("battery_days_left")

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        return {"on_usb": bool((self.plant or {}).get("on_usb"))}


class RoundTrip(PlantEntity, SensorEntity):
    """Its last silent ping's round trip, from the fleet manager (Ping all on its Network tab, or
    every 'Ping every' minutes when that's on): a board getting slower shows before it drops off.
    Unknown when it didn't answer (answered: false)."""

    _attr_device_class = SensorDeviceClass.DURATION
    _attr_native_unit_of_measurement = UnitOfTime.MILLISECONDS
    _attr_suggested_unit_of_measurement = UnitOfTime.SECONDS
    _attr_suggested_display_precision = 1
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_icon = "mdi:timer-sync-outline"

    def __init__(self, fleet: FleetCoordinator, device_id: str) -> None:
        super().__init__(fleet, device_id, "round_trip")

    @property
    def _ping(self) -> dict | None:
        return (self.plant or {}).get("round_trip")

    @property
    def available(self) -> bool:
        return super().available and self._ping is not None

    @property
    def native_value(self) -> int | None:
        return self._ping["ms"] if self._ping else None

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        if not self._ping:
            return None
        return {"answered": self._ping["ms"] is not None,
                "pinged": dt_util.utc_from_timestamp(self._ping["time"]).isoformat()}


class CareDue(PlantEntity, SensorEntity):
    """How many of its care tasks are due, with all of them (for its plant card, which ticks
    them on the Plant care list)."""

    _attr_icon = "mdi:sprout"
    _unrecorded_attributes = frozenset({"tasks"})

    def __init__(self, fleet: FleetCoordinator, device_id: str) -> None:
        super().__init__(fleet, device_id, "care_due")

    def _tasks(self) -> list[dict]:
        return sorted((self.plant or {}).get("care") or [], key=lambda t: (t["due"], t["id"]))

    @property
    def native_value(self) -> int:
        now = time.time()
        return sum(1 for task in self._tasks() if task["due"] <= now)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        now = time.time()
        return {"tasks": [{"uid": str(task["id"]), "what": task["what"], "every_days": task["every_days"],
                           "due": dt_util.utc_from_timestamp(task["due"]).isoformat(), "due_now": task["due"] <= now}
                          for task in self._tasks()]}


class PlantPhoto(PlantEntity, ImageEntity):
    """Its plant's newest photo (added in the fleet manager), for its card. Read once per photo."""

    _attr_content_type = "image/jpeg"

    def __init__(self, fleet: FleetCoordinator, device_id: str) -> None:
        super().__init__(fleet, device_id, "plant_photo")
        ImageEntity.__init__(self, fleet.hass)
        self._photo: tuple[float, bytes] | None = None  # (when it was added, the picture)

    @property
    def _added(self) -> float | None:
        return (self.plant or {}).get("photo")

    @property
    def available(self) -> bool:
        return super().available and bool(self._added)

    @property
    def image_last_updated(self):
        return dt_util.utc_from_timestamp(self._added) if self._added else None

    async def async_image(self) -> bytes | None:
        added = self._added
        if not added:
            return None
        if self._photo is None or self._photo[0] != added:
            got = await self.coordinator.photo(self.device_id)
            if got is None:
                return self._photo[1] if self._photo else None
            data, self._attr_content_type = got
            self._photo = (added, data)
        return self._photo[1]


class WaterTankLow(TankEntity, BinarySensorEntity):
    """On when its tank is down to 20 % (the fleet manager's alert)."""

    _attr_icon = "mdi:water-alert"

    def __init__(self, fleet: FleetCoordinator, device_id: str) -> None:
        super().__init__(fleet, device_id, "water_tank_low")

    @property
    def is_on(self) -> bool | None:
        return self.tank["low"] if self.tank else None


class WaterTankRefilled(TankEntity, ButtonEntity):
    """Its tank is full again: counted from now."""

    _attr_icon = "mdi:water-plus"

    def __init__(self, fleet: FleetCoordinator, device_id: str) -> None:
        super().__init__(fleet, device_id, "water_tank_refilled")

    async def async_press(self) -> None:
        await self.coordinator.refilled(self.device_id)


class PlantCare(CoordinatorEntity[FleetCoordinator], TodoListEntity):
    """The plants' care tasks. One that's due is to do; done (ticked here or in the fleet
    manager), it's checked until it's due again, its number of days later. They're added, changed
    and removed in the fleet manager."""

    _attr_has_entity_name = True
    _attr_translation_key = "plant_care"
    _attr_supported_features = TodoListEntityFeature.UPDATE_TODO_ITEM
    _attr_icon = "mdi:flower"

    def __init__(self, fleet: FleetCoordinator, entry: ConfigEntry) -> None:
        super().__init__(fleet)
        self._attr_unique_id = f"{entry.unique_id or entry.entry_id}_plant_care"

    def _tasks(self) -> list[tuple[dict, dict]]:
        """(plant, task) of every care task, the soonest due first."""
        tasks = [(plant, task) for plant in self.coordinator.plants().values() for task in plant.get("care") or []]
        return sorted(tasks, key=lambda pt: (pt[1]["due"], pt[1]["id"]))

    @staticmethod
    def _due_date(task: dict):
        return dt_util.as_local(dt_util.utc_from_timestamp(task["due"])).date()

    @property
    def todo_items(self) -> list[TodoItem]:
        now = time.time()
        items = []
        for plant, task in self._tasks():
            every = task["every_days"]
            items.append(TodoItem(
                uid=str(task["id"]), summary=f"{plant['name']}: {task['what']}",
                status=TodoItemStatus.NEEDS_ACTION if task["due"] <= now else TodoItemStatus.COMPLETED,
                due=self._due_date(task), description=f"Every {every} day{'s' if every != 1 else ''}"))
        return items

    async def async_update_todo_item(self, item: TodoItem) -> None:
        """Ticked: done (the fleet manager counts its next time from now). Its name, its date: changed
        in the fleet manager."""
        found = next(((plant, task) for plant, task in self._tasks() if str(task["id"]) == item.uid), None)
        if found is None:
            raise ServiceValidationError("This care task isn't in the fleet manager any more")
        plant, task = found
        if item.summary != f"{plant['name']}: {task['what']}" or (item.due is not None and item.due != self._due_date(task)):
            raise ServiceValidationError("Care tasks are changed in the fleet manager (the plant's page, Care)")
        due = task["due"] <= time.time()
        if item.status == TodoItemStatus.COMPLETED and due:
            await self.coordinator.care_done(task["id"])
        elif item.status == TodoItemStatus.NEEDS_ACTION and not due:
            raise ServiceValidationError(f"It's done: it's next due on {self._due_date(task).isoformat()}")

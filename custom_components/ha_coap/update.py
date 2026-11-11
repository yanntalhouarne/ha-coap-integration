"""Each board's firmware, in Home Assistant's Updates: what it runs against the fleet manager's
release image, and Install to update it over the air (the fleet manager does it)."""

from __future__ import annotations

from typing import Any

from homeassistant.components.update import UpdateDeviceClass, UpdateEntity, UpdateEntityFeature
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .fleet import FleetCoordinator


def _version(image_hash: str | None, commit: str | None) -> str | None:
    """A firmware as people know it: its git commit, else the start of its image's hash."""
    return commit or (image_hash[:8] if image_hash else None)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback) -> None:
    """An update for each board both Home Assistant and the fleet manager know; the ones added
    later as they appear."""
    fleet: FleetCoordinator = entry.runtime_data
    added: set[str] = set()

    @callback
    def add_new() -> None:
        devices = dr.async_get(hass)
        new = []
        for board in (fleet.data or {}).get("devices", []):
            device_id = board["device_id"]
            if device_id not in added and devices.async_get_device(identifiers={(DOMAIN, device_id)}) is not None:
                added.add(device_id)
                new.append(HaCoapFirmwareUpdate(fleet, device_id))
        if new:
            async_add_entities(new)

    add_new()
    entry.async_on_unload(fleet.async_add_listener(add_new))


class HaCoapFirmwareUpdate(CoordinatorEntity[FleetCoordinator], UpdateEntity):
    _attr_has_entity_name = True
    _attr_translation_key = "firmware_update"
    _attr_device_class = UpdateDeviceClass.FIRMWARE
    _attr_supported_features = UpdateEntityFeature.INSTALL | UpdateEntityFeature.PROGRESS
    _attr_title = "ha-coap firmware"

    def __init__(self, fleet: FleetCoordinator, device_id: str) -> None:
        super().__init__(fleet)
        self.device_id = device_id
        self._attr_unique_id = f"{device_id}_firmware_update"
        self._attr_device_info = DeviceInfo(identifiers={(DOMAIN, device_id)})  # the board's device

    @property
    def _board(self) -> dict:
        return self.coordinator.device(self.device_id) or {}

    @property
    def available(self) -> bool:
        return super().available and bool(self._board)

    @property
    def installed_version(self) -> str | None:
        return _version(self._board.get("image_hash"), self._board.get("git_commit"))

    @property
    def latest_version(self) -> str | None:
        release = (self.coordinator.data or {}).get("release")
        if not release:
            return self.installed_version  # no release image: nothing to update to
        if self._board.get("image_hash") == release["hash"]:
            return self.installed_version
        return _version(release["hash"], release.get("git_commit"))

    @property
    def release_summary(self) -> str | None:
        release = (self.coordinator.data or {}).get("release") or {}
        notes = (release.get("notes") or "").strip()
        return notes[:255] or None

    @property
    def in_progress(self) -> bool:
        return (self._board.get("update") or {}).get("status") in ("queued", "running")

    @property
    def update_percentage(self) -> int | None:
        update = self._board.get("update") or {}
        if update.get("status") != "running":
            return None
        return round(100 * float(update.get("progress") or 0))

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        update = self._board.get("update") or {}
        return {"last_update": update.get("status"), "last_update_message": update.get("message") or None} if update else None

    async def async_install(self, version: str | None, backup: bool, **kwargs: Any) -> None:
        await self.coordinator.update(self.device_id)

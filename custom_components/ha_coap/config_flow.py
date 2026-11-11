"""Adding a board: found by zeroconf (its "_ot._udp" service, which the border router advertises
for it), or by its address; connecting the fleet manager (its firmware updates); and Reconfigure
(a board's new address, the fleet manager's new address or key)."""

from __future__ import annotations

import ipaddress
import logging
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.const import CONF_API_KEY, CONF_HOST, CONF_ID, CONF_NAME, CONF_URL
from homeassistant.helpers.aiohttp_client import async_get_clientsession

try:
    from homeassistant.helpers.service_info.zeroconf import ZeroconfServiceInfo
except ImportError:  # before Home Assistant 2025.1
    from homeassistant.components.zeroconf import ZeroconfServiceInfo

from . import coap
from .const import DOMAIN, board_id, clean_host
from .fleet import FleetError, fetch

_LOGGER = logging.getLogger(__name__)

FLEET_UNIQUE_ID = "fleet_manager"


def best_address(info: ZeroconfServiceInfo) -> str:
    """The board's address to use: a routable IPv6 one when it has one (a Thread board's mesh
    and link-local addresses aren't reachable from Home Assistant)."""
    addresses = [str(a) for a in getattr(info, "ip_addresses", []) or []]
    for address in addresses:
        parsed = ipaddress.ip_address(address)
        if parsed.version == 6 and not parsed.is_link_local:
            return address
    return addresses[0] if addresses else clean_host(info.host)


async def board_at(host: str) -> tuple[str | None, str | None]:
    """(the board's ID, error key) for the board at this address: it's asked ('info')."""
    try:
        info = coap.parse_info(await coap.get(host, "info"))
    except (TimeoutError, OSError, coap.CoapError) as err:
        _LOGGER.debug("No 'info' from %s: %s", host, err)
        return None, "cannot_connect"
    return (board_id(info.device_id), None) if board_id(info.device_id) else (None, "no_device_id")


class HaCoapConfigFlow(ConfigFlow, domain=DOMAIN):
    """A board: its name (yours), its address and its ID (the board's own, 8 hex digits). Or the
    fleet manager: its address and a key made for Home Assistant in it."""

    VERSION = 2

    def __init__(self) -> None:
        self._name = ""
        self._host = ""
        self._id = ""

    # ------------------------------------------------------------ a board, found

    async def async_step_zeroconf(self, discovery_info: ZeroconfServiceInfo) -> ConfigFlowResult:
        """A board found: its ID from its TXT record (newer firmware), else from its name
        ("ha-coap-1a2b3c4d"). One already added gets its new address, if it changed."""
        instance = discovery_info.name.split("._", 1)[0]
        properties = discovery_info.properties or {}
        device_id = board_id(properties.get("id")) or board_id(instance)
        if not device_id:
            _LOGGER.debug("Ignored %s: no board ID in its name or TXT record", discovery_info.name)
            return self.async_abort(reason="no_device_id")
        self._host = best_address(discovery_info)
        await self.async_set_unique_id(device_id)
        self._abort_if_unique_id_configured(updates={CONF_HOST: self._host})
        self._id = device_id
        self._name = instance
        self.context["title_placeholders"] = {"name": instance}
        return await self.async_step_zeroconf_confirm()

    async def async_step_zeroconf_confirm(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is None:
            return self.async_show_form(
                step_id="zeroconf_confirm",
                data_schema=vol.Schema({vol.Required(CONF_NAME, default=self._name): str}),
                description_placeholders={"name": self._name, "host": self._host, "id": self._id},
            )
        return self._create(user_input[CONF_NAME])

    # ------------------------------------------------------------ added by hand

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """A board by its address, or the fleet manager."""
        return self.async_show_menu(step_id="user", menu_options=["board", "fleet_manager"])

    async def async_step_board(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """A board by its address: it's asked for its ID ('info'), so it must answer."""
        errors: dict[str, str] = {}
        if user_input is not None:
            host = clean_host(user_input[CONF_HOST])
            device_id, error = await board_at(host)
            if error:
                errors["base"] = error
            else:
                await self.async_set_unique_id(device_id)
                self._abort_if_unique_id_configured(updates={CONF_HOST: host})
                self._host, self._id = host, device_id
                return self._create(user_input[CONF_NAME])
        return self.async_show_form(
            step_id="board",
            data_schema=self.add_suggested_values_to_schema(
                vol.Schema({vol.Required(CONF_HOST): str, vol.Required(CONF_NAME): str}), user_input or {}),
            errors=errors,
        )

    def _create(self, name: str) -> ConfigFlowResult:
        name = name.strip() or self._name or f"ha-coap-{self._id}"
        return self.async_create_entry(title=name, data={CONF_NAME: name, CONF_HOST: self._host, CONF_ID: self._id})

    async def async_step_fleet_manager(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """The fleet manager: its address (such as http://192.168.1.20:8710) and the key of a link
        made for Home Assistant in it. Each board then has a firmware update."""
        await self.async_set_unique_id(FLEET_UNIQUE_ID)
        self._abort_if_unique_id_configured()
        errors: dict[str, str] = {}
        if user_input is not None:
            url, error = await self._check_fleet(user_input)
            if error:
                errors["base"] = error
            else:
                return self.async_create_entry(title="Fleet manager",
                                               data={CONF_URL: url, CONF_API_KEY: user_input[CONF_API_KEY].strip()})
        return self.async_show_form(
            step_id="fleet_manager",
            data_schema=self.add_suggested_values_to_schema(
                vol.Schema({vol.Required(CONF_URL): str, vol.Required(CONF_API_KEY): str}), user_input or {}),
            errors=errors,
        )

    async def _check_fleet(self, user_input: dict[str, Any]) -> tuple[str, str | None]:
        url = user_input[CONF_URL].strip().rstrip("/")
        if not url.startswith(("http://", "https://")):
            url = f"http://{url}"
        try:
            await fetch(async_get_clientsession(self.hass), f"{url}/api/ha/firmware", user_input[CONF_API_KEY].strip())
        except FleetError as err:
            _LOGGER.debug("Fleet manager at %s: %s", url, err)
            return url, "invalid_auth" if err.auth else "cannot_connect"
        return url, None

    # ------------------------------------------------------------ reconfigure

    async def async_step_reconfigure(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """A board's new address (it must be the same board), or the fleet manager's address and key."""
        entry = self._get_reconfigure_entry()
        errors: dict[str, str] = {}
        if CONF_URL in entry.data:
            if user_input is not None:
                url, error = await self._check_fleet(user_input)
                if not error:
                    return self.async_update_reload_and_abort(
                        entry, data_updates={CONF_URL: url, CONF_API_KEY: user_input[CONF_API_KEY].strip()})
                errors["base"] = error
            schema = vol.Schema({vol.Required(CONF_URL): str, vol.Required(CONF_API_KEY): str})
            return self.async_show_form(step_id="reconfigure", errors=errors, data_schema=self.add_suggested_values_to_schema(
                schema, user_input or {CONF_URL: entry.data[CONF_URL], CONF_API_KEY: entry.data[CONF_API_KEY]}))
        if user_input is not None:
            host = clean_host(user_input[CONF_HOST])
            device_id, error = await board_at(host)
            if error:
                errors["base"] = error
            elif device_id != board_id(entry.data[CONF_ID]):
                errors["base"] = "another_board"
            else:
                return self.async_update_reload_and_abort(entry, data_updates={CONF_HOST: host})
        schema = vol.Schema({vol.Required(CONF_HOST): str})
        return self.async_show_form(step_id="reconfigure", errors=errors, data_schema=self.add_suggested_values_to_schema(
            schema, user_input or {CONF_HOST: entry.data[CONF_HOST]}), description_placeholders={"name": entry.title})

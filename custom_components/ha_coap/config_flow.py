"""Adding a board: found by zeroconf (its "_ot._udp" service, which the border router advertises
for it), or by its address."""

from __future__ import annotations

import ipaddress
import logging
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.const import CONF_HOST, CONF_ID, CONF_NAME

try:
    from homeassistant.helpers.service_info.zeroconf import ZeroconfServiceInfo
except ImportError:  # before Home Assistant 2025.1
    from homeassistant.components.zeroconf import ZeroconfServiceInfo

from . import coap
from .const import DOMAIN, board_id, clean_host

_LOGGER = logging.getLogger(__name__)


def best_address(info: ZeroconfServiceInfo) -> str:
    """The board's address to use: a routable IPv6 one when it has one (a Thread board's mesh
    and link-local addresses aren't reachable from Home Assistant)."""
    addresses = [str(a) for a in getattr(info, "ip_addresses", []) or []]
    for address in addresses:
        parsed = ipaddress.ip_address(address)
        if parsed.version == 6 and not parsed.is_link_local:
            return address
    return addresses[0] if addresses else clean_host(info.host)


class HaCoapConfigFlow(ConfigFlow, domain=DOMAIN):
    """A board: its name (yours), its address and its ID (the board's own, 8 hex digits)."""

    VERSION = 2

    def __init__(self) -> None:
        self._name = ""
        self._host = ""
        self._id = ""

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

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """A board by its address: it's asked for its ID ('info'), so it must answer."""
        errors: dict[str, str] = {}
        if user_input is not None:
            host = clean_host(user_input[CONF_HOST])
            try:
                info = coap.parse_info(await coap.get(host, "info"))
            except (TimeoutError, OSError, coap.CoapError) as err:
                _LOGGER.debug("No 'info' from %s: %s", host, err)
                errors["base"] = "cannot_connect"
            else:
                if not board_id(info.device_id):
                    errors["base"] = "no_device_id"
                else:
                    await self.async_set_unique_id(board_id(info.device_id))
                    self._abort_if_unique_id_configured(updates={CONF_HOST: host})
                    self._host, self._id = host, board_id(info.device_id)
                    return self._create(user_input[CONF_NAME])
        return self.async_show_form(
            step_id="user",
            data_schema=self.add_suggested_values_to_schema(
                vol.Schema({vol.Required(CONF_HOST): str, vol.Required(CONF_NAME): str}), user_input or {}),
            errors=errors,
        )

    def _create(self, name: str) -> ConfigFlowResult:
        name = name.strip() or self._name or f"ha-coap-{self._id}"
        return self.async_create_entry(title=name, data={CONF_NAME: name, CONF_HOST: self._host, CONF_ID: self._id})

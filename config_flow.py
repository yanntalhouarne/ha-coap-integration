import logging
from homeassistant import config_entries, core
from homeassistant.core import callback
from homeassistant.components import zeroconf
from homeassistant.data_entry_flow import FlowResult
from homeassistant.const import CONF_NAME, CONF_HOST

import voluptuous as vol

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

class myCoapConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """myCoap Custom config flow."""

    def __init__(self):
        """Initialize the Daikin config flow."""
        self.name = None
        self.ipaddr = None
        self.unique_id = None

    @property
    def schema(self):
        """Return current schema."""
        return vol.Schema(
            {
                vol.Required(CONF_NAME): str,
                vol.Required(CONF_HOST): str,
                vol.Required(CONF_ID): int,
            }
        )

    async def async_step_zeroconf_confirm(self, user_input=None):
        """Handle a flow initiated by zeroconf."""
        if user_input is not None:
            return self.async_create_entry(
                title=self.name,
                data={
                    CONF_NAME: self.name,
                    CONF_HOST: self.ipaddr,
                    CONF_ID: self.unique_id,
                },
            )

    async def async_step_zeroconf(
            self, discovery_info: zeroconf.ZeroconfServiceInfo
        ) -> FlowResult:
            """Prepare configuration for a discovered myCoap device."""
            self.name = discovery_info[zeroconf.ATTR_HOSTNAME].rstrip(".")
            self.ipaddr = discovery_info[zeroconf.ATTR_HOST]
            # nrf52840dk-6266d5bd
            input_string = self.name
            dash_index = input_string.find('-')
            # Check if '-' is present in the string
            if dash_index != -1:
                # Extract the substring after '-'
                result_string = input_string[dash_index + 1:]
                self.unique_id = int(result_string)
            else:
                _LOGGER.info("No '-' character found in host name.")
            _LOGGER.info("Zeroconf discovered hostname: %s, with IPv6 address: %s and unique ID: %d", self.name, self.ipaddr, self.unique_id)
            await self.async_set_unique_id(self.unique_id)
            self._abort_if_unique_id_configured({CONF_HOST: self.ipaddr})
            return await self.async_step_zeroconf_confirm()
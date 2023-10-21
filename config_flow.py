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

    @property
    def schema(self):
        """Return current schema."""
        return vol.Schema(
            {
                vol.Required(CONF_NAME): str,
                vol.Required(CONF_HOST): str,
            }
        )

    async def async_step_user(self, user_input=None):
        """User initiated config flow."""
        return self.async_create_entry(title="HA Coap Integration", data=self.data)


    async def async_step_zeroconf(
            self, discovery_info: zeroconf.ZeroconfServiceInfo
        ) -> FlowResult:
            """Prepare configuration for a discovered myCoap device."""
            _LOGGER.debug("Zeroconf user_input: %s", discovery_info)
            self.name = discovery_info.hostname
            self.ipaddr = discovery_info.host
            return await self.async_step_user()
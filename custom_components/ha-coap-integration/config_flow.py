import logging
from homeassistant import config_entries, core
from homeassistant.core import callback
from homeassistant.components import zeroconf
from homeassistant.data_entry_flow import FlowResult
from homeassistant.components.switch import PLATFORM_SCHEMA
from homeassistant.const import CONF_NAME, CONF_HOST, CONF_ID
import homeassistant.helpers.config_validation as cv
import voluptuous as vol
import asyncio
from aiocoap import *
from .const import DOMAIN

CONST_COAP_PROTOCOL = "coap://"
#protocol = ""

_LOGGER = logging.getLogger(__name__)

# for data validation
PLATFORM_SCHEMA = PLATFORM_SCHEMA.extend(
    {
        vol.Required(CONF_HOST): cv.string,
        vol.Required(CONF_NAME): cv.string,
        vol.Required(CONF_ID): cv.string,
        #vol.Required(CONF_MODEL): cv.string,
    }
)

USER_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_HOST): cv.string,
        vol.Required(CONF_NAME): cv.string,
        vol.Required(CONF_ID): cv.string,
        #vol.Required(CONF_MODEL): cv.string,
    }
)

class myCoapConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """myCoap Custom config flow."""

    name = None
    ipaddr = None
    unique_id = None
    #model = None

    async def async_step_user(self, user_input=None) -> FlowResult:
        """Handle a flow initiated by zeroconf."""
        _LOGGER.info("User node registering %s", self.name)
        if user_input is None:
            return self.async_show_form(
                step_id="user",
                data_schema=USER_SCHEMA,
            )

        self.name = user_input[CONF_NAME]
        self.ipaddr = user_input[CONF_HOST]
        self.unique_id = user_input[CONF_ID]
        #self.model = user_input[CONF_MODEL]

        return self.async_create_entry(
            title=self.name,
            data={
                CONF_NAME: self.name,
                CONF_HOST: self.ipaddr,
                CONF_ID: self.unique_id,
                #CONF_MODEL: self.model,
            },
        )

    async def async_step_zeroconf_confirm(self, user_input=None) -> FlowResult:
        """Handle a flow initiated by zeroconf."""
        _LOGGER.info("Zeroconf registering %s", self.name)
        if user_input is None:
            return self.async_show_form(
                step_id="zeroconf_confirm",
                data_schema=vol.Schema({vol.Required(CONF_NAME, default=self.name): cv.string}),
                description_placeholders={"name": self.name},
            )

        self.name = user_input[CONF_NAME]

        return self.async_create_entry(
            title=self.name,
            data={
                CONF_NAME: self.name,
                CONF_HOST: self.ipaddr,
                CONF_ID: self.unique_id,
                #CONF_MODEL: self.model,
            },
        )

    async def async_step_zeroconf(
            self, discovery_info: zeroconf.ZeroconfServiceInfo
        ) -> FlowResult:
            """Prepare configuration for a discovered myCoap device."""
            #_LOGGER.info("In async_step_zeroconf()...")
            #protocol = await Context.create_client_context()
            name_string = discovery_info.name
            to_remove = "._ot._udp.local."
            self.name = name_string.replace(to_remove, '')
            self.ipaddr = discovery_info.host
            # nrf52840dk-6266d5bd
            input_string = self.name
            dash_index = input_string.find('-')
            # Check if '-' is present in the string
            if dash_index != -1:
                # Extract the substring after '-'
                result_string = input_string[dash_index + 1:]
            else:
                _LOGGER.info("No '-' character found in host name: %s", self.name)
            self.unique_id = result_string
            _LOGGER.debug("Zeroconf discovered hostname: %s, with IPv6 address: %s and unique ID: %s", self.name, self.ipaddr, self.unique_id)

            # check if ID is already registered
            isUnique = await self.async_set_unique_id(self.unique_id)
            if isUnique == None:
                _LOGGER.info("New device ID discovered: %s", self.unique_id)
            else:
                _LOGGER.debug("Device ID already registered: %s", self.unique_id)
            # get FW version
            # try:
            #     _uri = CONST_COAP_PROTOCOL + "[" + self.ipaddr + "]" + "/" + "info"
            #     _LOGGER.info("INFO URI is: %s", _uri)
            #     request = Message(mtype=CON, code=GET, uri=_uri)
            #     response = await protocol.request(request).response
            #     _LOGGER.info("INFO payload received is: %s" % str(response.payload))
            #     #self.model = str(response.payload)
            # except Exception as e:
            #     _LOGGER.debug("Timeout reached for INFO resource. Giving up.")
            self._abort_if_unique_id_configured({CONF_HOST: self.ipaddr})
            #_LOGGER.info("Unique ID set.")
            return await self.async_step_zeroconf_confirm()
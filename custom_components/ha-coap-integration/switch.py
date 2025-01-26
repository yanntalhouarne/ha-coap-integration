"""HA CoAp Switch Interface."""
import logging
import asyncio
import os

from homeassistant.components.switch import PLATFORM_SCHEMA
from homeassistant.const import (
    CONF_HOST,
    CONF_NAME,
    CONF_ID,
)
from homeassistant.core import callback
import homeassistant.helpers.config_validation as cv
from homeassistant.helpers.entity import DeviceInfo, Entity
from homeassistant.helpers.entity import ToggleEntity
from homeassistant.helpers.event import async_track_time_interval
from homeassistant import config_entries, core

import aiocoap.defaults
aiocoap.defaults.ACK_TIMEOUT = 10.0
aiocoap.defaults.MAX_RETRANSMIT = 3
from aiocoap import Message, Context
from aiocoap.numbers.codes import Code
from aiocoap.numbers.types import Type

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

CONST_COAP_PROTOCOL = "coap://"
CONST_COAP_STRING_TRUE = "1"
CONST_COAP_STRING_FALSE = "0"

CONST_COAP_PING_URI = "ping"
CONST_COAP_NON_TIMEOUT_S = 10

async def async_setup_entry(
    hass: core.HomeAssistant,
    config_entry: config_entries.ConfigEntry,
    async_add_entities,
):
    """Set up switches from config entry."""
    config = hass.data[DOMAIN].get(config_entry.entry_id)
    _LOGGER.info("Setting up switch entities of device %s with unique ID %s", config[CONF_NAME], config[CONF_ID])
    protocol = await Context.create_client_context()
    switches = []
    switches.append(
        CoAPswitchNode(
            "["+config[CONF_HOST]+"]",
            "ping",
            CONST_COAP_PING_URI,
            protocol, 
            config[CONF_NAME], 
            False, 
            None,
            config[CONF_ID],
        )
    )

    _LOGGER.info("-> %s switch entities have been added to device %s", len(switches), config[CONF_NAME])
    async_add_entities(switches)
    for sw in switches:
        _LOGGER.info("- %s", sw._switch_type)

    # get switch states right away
    for sw in switches:
        await sw.async_update_con_switches()

class CoAPswitchNode(ToggleEntity):
    """Representation of a CoAP switch node."""

    def __init__(self, host, switch_type, uri, protocol, name, unit, invert_logic, device_id):
        """Initialize the switch."""
        self._host = host
        self._switch_type = switch_type
        self._uri = uri
        self._name = name + "." + switch_type
        self._unit = unit
        self._invert_logic = invert_logic
        self._state = False
        self._protocol = protocol
        self._device_id = device_id
        self._unique_id = device_id + switch_type

    @property
    def name(self):
        """Return the name of the switch."""
        return self._name

    @property
    def should_poll(self):
        """Return the polling state."""
        return True

    @property
    def is_on(self):
        """Return true if device is on."""
        return self._state
    
    @property
    def unique_id(self):
        """Return a unique identifier for this switch."""
        return self._unique_id

    @property
    def device_info(self) -> DeviceInfo:
        """Return the device info."""
        return DeviceInfo(
            identifiers={(DOMAIN, self._device_id)},
            name=self.name,
            manufacturer="Yann T.",    
        )

    @property
    def icon(self):
        """Return the icon of the device."""
        return "mdi:bullhorn"

    async def async_turn_on(self, **kwargs):
        """Turn the device on."""
        try:
            request = Message(
                mtype=Type.CON,
                code=Code.PUT,
                payload=CONST_COAP_STRING_TRUE.encode("ascii"),
                uri=f"{CONST_COAP_PROTOCOL}{self._host}/{self._uri}"
            )
            _LOGGER.debug(f"Sending CON PUT request with payload '1' to {self._name}/{self._uri}({self._host})")
            response = await self._protocol.request(request).response
            if response:
                response_bool = response.payload == b'\x01'
                self._state = response_bool
                self.async_write_ha_state()
                _LOGGER.debug(f"-> Switch turned ON: {self._name}/{self._uri}({self._host})")
        except Exception as e:
            _LOGGER.error(f"Failed to turn on {self._name}: {str(e)}")

    async def async_turn_off(self, **kwargs):
        """Turn the device off."""
        try:
            request = Message(
                mtype=Type.CON,
                code=Code.PUT,
                payload=CONST_COAP_STRING_FALSE.encode("ascii"),
                uri=f"{CONST_COAP_PROTOCOL}{self._host}/{self._uri}"
            )
            _LOGGER.debug(f"Sending CON PUT request with payload '0' to {self._name}/{self._uri}({self._host})")
            response = await self._protocol.request(request).response
            if response:
                response_bool = response.payload == b'\x01'
                self._state = response_bool
                self.async_write_ha_state()
                _LOGGER.debug(f"-> Switch turned OFF: {self._name}/{self._uri}({self._host})")
        except Exception as e:
            _LOGGER.error(f"Failed to turn off {self._name}: {str(e)}")

    async def async_update_con_switches(self):
        """Update switch state using confirmable request."""
        try:
            _LOGGER.debug(f"Sending CON GET request to {self._name}/{self._uri}({self._host})")
            request = Message(
                mtype=Type.CON,
                code=Code.GET,
                uri=f"{CONST_COAP_PROTOCOL}{self._host}/{self._uri}"
            )
            response = await self._protocol.request(request).response
            if response:
                response_bool = response.payload == b'\x01'
                if self._state != response_bool:
                    self._state = response_bool
                self.async_write_ha_state()
                _LOGGER.debug(f"-> Received '{response.payload}' from {self._name}/{self._uri}({self._host})")
        except Exception as e:
            _LOGGER.error(f"Failed to update {self._name}: {str(e)}")   
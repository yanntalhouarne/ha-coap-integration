"""HA CoAp Switch Interface."""
import sys
#sys.path.append("/config/custom_components/ha-coap-integration")


from datetime import timedelta
import logging
import asyncio
import os

# Bring in CoAP
from aiocoap import *

import voluptuous as vol

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

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

CONST_DEFAULT_SCAN_PERIOD_S = 1800

CONST_COAP_PROTOCOL = "coap://"
CONST_COAP_STRING_TRUE = "1"
CONST_COAP_STRING_FALSE = "0"

protocol = ""

# for data validation
PLATFORM_SCHEMA = PLATFORM_SCHEMA.extend(
    {
        vol.Required(CONF_HOST): cv.string,
        vol.Required(CONF_NAME): cv.string,
        vol.Required(CONF_ID): cv.string,
    }
)

# setup platform when new device is discovered by zeroconf
async def async_setup_entry(
    hass: core.HomeAssistant,
    config_entry: config_entries.ConfigEntry,
    async_add_entities,
):
    _LOGGER.info("In async_setup_entry()...")
    config = hass.data[DOMAIN].get(config_entry.entry_id)
    _LOGGER.info("Setting up entry for light entity of %s with unique ID %s", config[CONF_NAME], config[CONF_ID])
    protocol = await Context.create_client_context()
    hass_switches = []
    hass_switches.append(
        coap_Switch(
            "["+config[CONF_HOST]+"]",
            "light", 
            protocol, 
            config[CONF_NAME], 
            False, 
            None,
            config[CONF_ID],
        )
    )

    # Add the entities
    async_add_entities(hass_switches)

    async def async_update_switches(event):
        """Update all the coap switches."""
        # Update sensors based on scan_period set below which comes in from the config
        for sw in hass_switches:
            await sw.async_update_values()

    async_track_time_interval(hass, async_update_switches, timedelta(seconds=CONST_DEFAULT_SCAN_PERIOD_S))

class coap_Switch(ToggleEntity):
    """Representation of a Digital Output."""

    def __init__(self, host, uri, protocol, name, unit, invert_logic, device_id):
        """Initialize the pin."""

        _LOGGER.info("Adding switch " + name + " with address " + host)

        self._host = host
        self._uri = uri
        self._name = "sw."+name
        self._unit = unit
        self._invert_logic = invert_logic
        self._state = False
        self._protocol = protocol
        self._device_id = device_id
        #self.async_turn_off()

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
    def device_id(self):
        """Return the ID of this roller."""
        return self.self._device_id

    @property
    def unique_id(self):
        """Return a unique identifier for this sensor."""
        return self._device_id

    @property
    def device_info(self) -> DeviceInfo:
        """Return the device info."""
        return DeviceInfo(
            identifiers={
                # Serial numbers are unique identifiers within a specific domain
                (DOMAIN, self._device_id)
            },
            name=self.name,
            manufacturer="Yann T.",
            model="version 0.1",
        )

    @property
    def icon(self):
        """Return the icon of the device."""
        return "mdi:light-switch-off"

    async def async_turn_on(self, **kwargs):
        #_LOGGER.info("HA calling TURN_ON for " + self._host + "/" + self._uri)
        """Turn the device on."""
        try:
            #_LOGGER.info("HA calling TURN_ON for " + self._host + "/" + self._uri)
            request = Message(mtype=CON, code=PUT, payload=CONST_COAP_STRING_TRUE.encode("ascii"), uri=CONST_COAP_PROTOCOL + self._host + "/" + self._uri)
            response = await self._protocol.request(request).response
            response_bool = False
            #_LOGGER.info("Payload received is: %s" % (response.payload))
            if (response.payload == b'\x01'):
                response_bool = True
            self._state = response_bool
            self.async_write_ha_state()
        except Exception as e:
            _LOGGER.info("Failed to PUT resource: " + self._name + "/" + self._uri)
            _LOGGER.info(e)

    async def async_turn_off(self, **kwargs):
        #_LOGGER.info("HA calling TURN_OFF for " + self._host + "/" + self._uri)
        """Turn the device off."""
        #Message(code=PUT, payload=CONST_COAP_STRING_FALSE.encode("ascii"), uri=CONST_COAP_PROTOCOL + self._host + "/" + self._uri)
        try:
            #_LOGGER.info("HA calling TURN_OFF for " + self._host + "/" + self._uri)
            request = Message(mtype=CON, code=PUT, payload=CONST_COAP_STRING_FALSE.encode("ascii"), uri=CONST_COAP_PROTOCOL + self._host + "/" + self._uri)
            response = await self._protocol.request(request).response
            #_LOGGER.info("Payload received is: %s" % (response.payload))
            response_bool = False
            if (response.payload == b'\x01'):
                response_bool = True
            self._state = response_bool
            self.async_write_ha_state()
        except Exception as e:
            _LOGGER.info("Failed to PUT resource: " + self._name + "/" + + self._uri)
            _LOGGER.info(e)

    @callback
    async def async_update_values(self):
        """Update this switch."""
        try:
            #_LOGGER.info("HA calling light GET for " + self._host + "/" + self._uri)
            request = Message(mtype=NON, code=GET, uri=CONST_COAP_PROTOCOL + self._host + "/" + self._uri)
            response = await asyncio.wait_for(self._protocol.request(request).response, timeout = 1)
            #_LOGGER.info("Payload received is: %s" % (response.payload))
            response_bool = False
            if (response.payload == b'\x01'): 
                response_bool = True
            # Check for change
            if (self._state != response_bool):
                self._state = response_bool
                #_LOGGER.info("%s changed: %s - %s" % (self._uri, response.code, str(response_bool)))
                self.async_write_ha_state()
        except asyncio.TimeoutError:
            _LOGGER.debug("Timeout reached. Giving up.")
        except Exception as e:
            _LOGGER.info("Failed to GET resource: " + self._uri)
            _LOGGER.info(e)

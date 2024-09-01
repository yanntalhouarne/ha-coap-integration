"""HA CoAp Switch Interface."""
import sys
#sys.path.append("/config/custom_components/ha-coap-integration")


from datetime import timedelta
import logging
import asyncio
import os


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

# aiocoap
import aiocoap.defaults
# Set new values for ACK timeout and max retransmissions
aiocoap.defaults.ACK_TIMEOUT = 10.0  # Wait 5 seconds for an ACK
aiocoap.defaults.MAX_RETRANSMIT = 3  # Retransmit up to 5 times
# Bring in CoAP
from aiocoap import *

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

CONST_DEFAULT_SCAN_PERIOD_S = 30

CONST_COAP_PROTOCOL = "coap://"
CONST_COAP_STRING_TRUE = "1"
CONST_COAP_STRING_FALSE = "0"

protocol = ""

CONST_COAP_PUMP_URI = "light"

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
    config = hass.data[DOMAIN].get(config_entry.entry_id)
    _LOGGER.info("Setting up entry for switch entity of device %s with unique ID %s", config[CONF_NAME], config[CONF_ID])
    protocol = await Context.create_client_context()
    hass_switches = []
    hass_switches.append(
        coap_Switch(
            "["+config[CONF_HOST]+"]",
            "water-pump",
            CONST_COAP_PUMP_URI,
            protocol, 
            config[CONF_NAME], 
            False, 
            None,
            config[CONF_ID],
        )
    )

    # Add the entities
    async_add_entities(hass_switches)
    _LOGGER.info("-> %s switch entities have been added to device %s", len(hass_switches), config[CONF_NAME])
    for sw in hass_switches:
        _LOGGER.info("- %s", sw._switch_type)

    async def async_update_switches(event):
        """Update all the coap switches."""
        # Update sensors based on scan_period set below which comes in from the config
        for sw in hass_switches:
            await sw.async_update_values()

    # get switch state
    for sw in hass_switches:
        await sw.async_update_values()

    # also get switch state every CONST_DEFAULT_SCAN_PERIOD_S seconds
    async_track_time_interval(hass, async_update_switches, timedelta(seconds=CONST_DEFAULT_SCAN_PERIOD_S))
    _LOGGER.info(" -> Switch state will be updated every %s seconds", CONST_DEFAULT_SCAN_PERIOD_S)

class coap_Switch(ToggleEntity):
    """Representation of a Digital Output."""

    def __init__(self, host, switch_type, uri, protocol, name, unit, invert_logic, device_id):
        """Initialize the pin."""

        _LOGGER.info("Adding switch " + name + " with address " + host)

        self._host = host
        self._switch_type = switch_type
        self._uri = uri
        self._name = "sw."+name
        self._unit = unit
        self._invert_logic = invert_logic
        self._state = False
        self._protocol = protocol
        self._device_id = device_id

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
            #model="version 0.1",
        )

    @property
    def icon(self):
        """Return the icon of the device."""
        return "mdi:water-pump"

    async def async_turn_on(self, **kwargs):
        #_LOGGER.info("HA calling TURN_ON for " + self._host + "/" + self._uri)
        """Turn the device on."""
        try:
            request = Message(mtype=CON, code=PUT, payload=CONST_COAP_STRING_TRUE.encode("ascii"), uri=CONST_COAP_PROTOCOL + self._host + "/" + self._uri)
            _LOGGER.info("Sending CON PUT request with payload '1' to " +  self._name+"/"+self._uri+"(" + self._host +")")
            response = await self._protocol.request(request).response
        except Exception as e:
            _LOGGER.info(" -> Exception - Failed to PUT "+self._uri+" resource with payload 1 to "+self._name+"/"+self._uri)
            _LOGGER.info(e)
        else:
            response_bool = False
            if (response.payload == b'\x01'):
                response_bool = True
            self._state = response_bool
            self.async_write_ha_state()
            _LOGGER.info("-> Switch turned ON: "+self._name+"/"+self._uri+"(" + self._host +")")

    async def async_turn_off(self, **kwargs):
        #_LOGGER.info("HA calling TURN_OFF for " + self._host + "/" + self._uri)
        """Turn the device off."""
        #Message(code=PUT, payload=CONST_COAP_STRING_FALSE.encode("ascii"), uri=CONST_COAP_PROTOCOL + self._host + "/" + self._uri)
        try:
            request = Message(mtype=CON, code=PUT, payload=CONST_COAP_STRING_FALSE.encode("ascii"), uri=CONST_COAP_PROTOCOL + self._host + "/" + self._uri)
            _LOGGER.info("Sending CON PUT request with payload '0' to " + self._name+"/"+self._uri+" (" + self._host +")")
            response = await self._protocol.request(request).response
        except Exception as e:
            _LOGGER.info("-> Exception - Failed to PUT "+self._uri+" resource (token = "+request.token+") with payload 0 to "+self._name+"/"+self._uri)
            _LOGGER.info(e)
        else:    
            response_bool = False
            if (response.payload == b'\x01'):
                response_bool = True
            self._state = response_bool
            self.async_write_ha_state()
            _LOGGER.info("-> Switch turned OFF (mid = "+str(request.mid)+"): "+self._name+"/"+self._uri+" (" + self._host +")")

    @callback
    async def async_update_values(self):
        """Update this switch."""
        try:
            _LOGGER.info("Sending NON GET request to "+self._name+"/"+self._uri+"(" + self._host +")")
            request = Message(mtype=NON, code=GET, uri=CONST_COAP_PROTOCOL + self._host + "/" + self._uri)
            _LOGGER.info("URI is %: " + CONST_COAP_PROTOCOL + self._host + "/" + self._uri)
            response = await self._protocol.request(request).response
        except Exception as e:
            _LOGGER.info(" -> Exception - Failed to GET resource (mid = "+str(request.mid)+") from "+self._name+"/"+self._uri+" (" + self._host +")")
            _LOGGER.info(e)
        else:
            #_LOGGER.info("Payload received is: %s" % (response.payload))
            response_bool = False
            if (response.payload == b'\x01'): 
                response_bool = True
            # Check for change
            if (self._state != response_bool):
                self._state = response_bool
                #_LOGGER.info("%s changed: %s - %s" % (self._uri, response.code, str(response_bool)))
            self.async_write_ha_state()
            _LOGGER.info(" -> Received '"+str(response.payload)+"' (mid = "+str(request.mid)+") from "+self._name+"/"+self._uri+" (" + self._host +")")

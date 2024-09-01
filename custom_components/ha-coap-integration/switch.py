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

CONST_DEFAULT_SCAN_PERIOD_S = 300 # 5min

CONST_COAP_PROTOCOL = "coap://"
CONST_COAP_STRING_TRUE = "1"
CONST_COAP_STRING_FALSE = "0"

protocol = ""

CONST_COAP_PUMP_URI = "pump"
CONST_COAP_PING_URI = "ping"

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
    _LOGGER.info("Setting up entry for data entities of device %s with unique ID %s", config[CONF_NAME], config[CONF_ID])
    protocol = await Context.create_client_context()
    switches = []
    switches.append(
        CoAPswitchNode(
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

    # add switches to switch manager
    switch_manager = HACoApSwitchManager(protocol, "["+config[CONF_HOST]+"]", config[CONF_NAME], switches)
    _LOGGER.info("-> %s switch entities have been added to device %s", len(switches), config[CONF_NAME])
    async_add_entities(switches)
    for sw in switches:
        _LOGGER.info("- %s", sw._switch_type)

    # function to update all switches' states
    async def async_update_switches(event):
        """Update all the coap switches."""
        # Update switches based on scan_period set below which comes in from the config
        for sw in hass_switches:
            await sw.async_update_values()

    # get switch states right awat
    for sw in hass_switches:
        await sw.async_update_values()

    # also get switch state every CONST_DEFAULT_SCAN_PERIOD_S seconds
    async_track_time_interval(hass, async_update_switches, timedelta(seconds=CONST_DEFAULT_SCAN_PERIOD_S))
    _LOGGER.info(" -> Switch state will be updated every %s seconds", CONST_DEFAULT_SCAN_PERIOD_S)

class HACoApSwitchManager:
    """Manages Switches of a HA-CoAp Device"""

    def __init__(self, protocol, host, name, switches):
        """Initialize the switch manager."""
        #self._hass = hass
        self._protocol = protocol
        self._host = host
        self._name = name
        self._info = " "
        self._switches = switches



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
        """Return a unique identifier for this switch."""
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
        if self._switch_type == "pump":
            return "mdi:water-pump"
        elif self._switch_type == "ping":
            return "mdi:connection"
        else:
            return "mdi:cat"

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
        # only update 'pump' switch
        if (self._uri == CONST_COAP_PUMP_URI):
            try:
                _LOGGER.info("Sending NON GET request to "+self._name+"/"+self._uri+"(" + self._host +")")
                request = Message(mtype=NON, code=GET, uri=CONST_COAP_PROTOCOL + self._host + "/" + self._uri)
                #_LOGGER.info("URI is : " + CONST_COAP_PROTOCOL + self._host + "/" + self._uri)
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
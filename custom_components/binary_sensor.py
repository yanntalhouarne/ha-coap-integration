"""HA CoAp Binary Sensor Interface."""
import sys
#sys.path.append("/config/custom_components/ha-coap-integration")


from datetime import timedelta
import logging
import asyncio
import os


import voluptuous as vol

from homeassistant.components.binary_sensor import PLATFORM_SCHEMA
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

# aiocoap
import aiocoap.defaults
# Set new values for ACK timeout and max retransmissions
aiocoap.defaults.ACK_TIMEOUT = 10.0  # Wait 5 seconds for an ACK
aiocoap.defaults.MAX_RETRANSMIT = 3  # Retransmit up to 5 times
# Bring in CoAP
from aiocoap import *

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

CONST_DEFAULT_SCAN_PERIOD_S = 60 # 1min

CONST_COAP_PROTOCOL = "coap://"
CONST_COAP_STRING_TRUE = "1"
CONST_COAP_STRING_FALSE = "0"

protocol = ""

CONST_COAP_PING_URI = "ping"
CONST_COAP_PING_BUZZER = "1"
CONST_COAP_PING_QUIET = "0"
CONST_COAP_PING_MODE = CONST_COAP_PING_QUIET

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
    _LOGGER.info("Setting up entry for binary sensor entity of device %s with unique ID %s", config[CONF_NAME], config[CONF_ID])
    protocol = await Context.create_client_context()
    hass_binary_sensors = []
    hass_binary_sensors.append(
        coap_BinarySensor(
            "["+config[CONF_HOST]+"]",
            "connectivity",
            CONST_COAP_PING_URI,
            protocol, 
            config[CONF_NAME], 
            False, 
            None,
            config[CONF_ID],
            CONST_COAP_PING_MODE,
        )
    )

    # Add the entities
    async_add_entities(hass_binary_sensors)
    _LOGGER.info("-> %s ping entities have been added to device %s", len(hass_binary_sensors), config[CONF_NAME])
    for bn in hass_binary_sensors:
        _LOGGER.info("- %s", bn._ping_type)

    async def async_update_binary_sensors(event):
        """Send pings to the devices."""
        for bn in hass_binary_sensors:
            await bn.async_ping_device()

    # ping device
    for bn in hass_binary_sensors:
        await bn.async_ping_device()

    async_track_time_interval(hass, async_update_binary_sensors, timedelta(seconds=CONST_DEFAULT_SCAN_PERIOD_S))
    _LOGGER.info(" -> Device will be pinged every %s seconds", CONST_DEFAULT_SCAN_PERIOD_S)

class coap_BinarySensor(ToggleEntity):
    """Representation of a Digital Output."""

    def __init__(self, host, ping_type, uri, protocol, name, unit, invert_logic, device_id, sound):
        """Initialize the pin."""

        _LOGGER.info("Adding ping " + name + " with address " + host)

        self._host = host
        self._ping_type = ping_type
        self._uri = uri
        self._name = name + "." + ping_type
        self._unit = unit
        self._invert_logic = invert_logic
        self._state = False
        self._protocol = protocol
        self._device_id = device_id
        self._sound = sound
        self._unique_id = device_id + ping_type

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
        return self._unique_id

    @property
    def icon(self):
        """Return the icon of the device."""
        return "mdi:connection"

    # @property
    # def device_info(self) -> DeviceInfo:
    #     """Return the device info."""
    #     return DeviceInfo(
    #         identifiers={
    #             # Serial numbers are unique identifiers within a specific domain
    #             (DOMAIN, self._device_id)
    #         },
    #         name=self.name,
    #         manufacturer="Yann T.",
    #         #model="version 0.1",
    #    )

    # @property
    # def device_class(self):
    #     """Return the ID of this roller."""
    #     return BinarySensorDeviceClass.CONNECTIVITY

    @callback
    async def async_ping_device(self):
        """Ping the device."""
        try:
            response_bool = False
            command = CONST_COAP_STRING_TRUE.encode("ascii")
            if self._sound == CONST_COAP_PING_BUZZER:
                command = payload=CONST_COAP_STRING_TRUE.encode("ascii")
            else:
                command = payload=CONST_COAP_STRING_FALSE.encode("ascii")
            _LOGGER.info("Sending CON PUT request with payload "+str(command)+" to "+self._name+"/"+self._uri+"(" + self._host +")")
            request = Message(mtype=CON, code=PUT, payload=command,  uri=CONST_COAP_PROTOCOL + self._host + "/" + self._uri)
            #_LOGGER.info("URI is %: " + CONST_COAP_PROTOCOL + self._host + "/" + self._uri)
            response = await self._protocol.request(request).response
        except Exception as e:
            _LOGGER.info(" -> Exception - Failed to GET resource (mid = "+str(request.mid)+") from "+self._name+"/"+self._uri+" (" + self._host +")")
            _LOGGER.info(" -> Lost connection with "+self._name+"/"+self._uri+" (" + self._host +")")
            self._state = response_bool
            self.async_write_ha_state()
            _LOGGER.info(e)
        else:
            response_bool = True
            _LOGGER.info(" -> Received response (mid = "+str(request.mid)+") from "+self._name+"/"+self._uri+" (" + self._host +")")
        # Check for change
        self._state = response_bool
        self.async_write_ha_state()

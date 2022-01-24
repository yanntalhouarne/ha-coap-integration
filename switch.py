"""LEMA Off-Grid interface."""
import sys
sys.path.append("/srv/homeassistant/lib/python3.9/site-packages/homeassistant/components/my_coap")

from datetime import timedelta
from myCoapNode import CoApNode
import logging
import asyncio

# Bring in CoAP
from aiocoap import *

import voluptuous as vol

from homeassistant.components.switch import PLATFORM_SCHEMA
from homeassistant.const import (
    CONF_HOST,
    CONF_FRIENDLY_NAME,
    CONF_SCAN_INTERVAL,
)
from homeassistant.core import callback
import homeassistant.helpers.config_validation as cv
from homeassistant.helpers.entity import Entity
from homeassistant.helpers.entity import ToggleEntity
from homeassistant.helpers.event import async_track_time_interval

_LOGGER = logging.getLogger(__name__)

CONST_DEFAULT_SCAN_PERIOD_S = 60

# TODO figure out why entity_playform.py has self.scan_interval set to a string "7"
# ...seconds.  It's used once on boot but then our switch async_setup_platform
# completes and the exception never happens again.
# Seems like a race condition on init or we neet to setup something else that
# we are not doing.  See how platform is used in zwave.py
# must have to do with vol.Optional(CONF_SCAN_INTERVAL): cv.string
SCAN_INTERVAL = timedelta(seconds=CONST_DEFAULT_SCAN_PERIOD_S)

CONST_COAP_PROTOCOL = "coap://"
CONST_COAP_STRING_TRUE = "1"
CONST_COAP_STRING_FALSE = "0"

protocol = ""

PLATFORM_SCHEMA = PLATFORM_SCHEMA.extend(
    {
        vol.Required(CONF_HOST): cv.string,
        vol.Required(CONF_FRIENDLY_NAME): cv.string,
        vol.Optional(CONF_SCAN_INTERVAL): cv.string
    }
)

async def async_setup_platform(hass, config, async_add_entities, discovery_info=None):
    """Set up coap Switches """

    host = config.get(CONF_HOST)

    # Use all switches by default
    hass_switches = []

    # Setup Async CoAP
    protocol = await Context.create_client_context()

    switchList = []
    name = ""
    addr = ""
    index = 0

    with open('node_directory.txt', 'r') as f:
        while True:
            line = f.readline()
            if (not line) or (line == "#\n"):
                break
            elif line == "%\n":
                line = f.readline()
                name = line[2:-1]
                line = f.readline()
                addr = line[2:-1]
                tempNode = CoApNode(name, addr)
                switchList.append(tempNode)
                index = index + 1
    print("Loaded "+ str(index) + " MyCoap Switches from directory file.")

    # Add switches
    for node in switchList:
        hass_switches.append(
            coap_Switch(
                "["+node.ipAddr+"]", 
                "switch", 
                protocol, 
                node.deviceName, 
                False, 
                None,
            )
        )

    # Add the entities
    async_add_entities(hass_switches)

    async def async_update_switches(event):
        """Update all the coap switches."""
        # Update sensors based on scan_period set below which comes in from the config
        for sw in hass_switches:
            await sw.async_update_values()

    async_track_time_interval(hass, async_update_switches, timedelta(seconds=5))

class coap_Switch(ToggleEntity):
    """Representation of a Digital Output."""

    def __init__(self, host, uri, protocol, name, unit, invert_logic):
        """Initialize the pin."""

        print("Adding switch " + name + " with address " + host)

        self._host = host
        self._uri = uri
        self._name = name
        self._unit = unit
        self._invert_logic = invert_logic
        self._state = False
        self._protocol = protocol
        self.async_turn_off()

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

    async def async_turn_on(self, **kwargs):
        """Turn the device on."""
        try:
            _LOGGER.info("HA calling TURN_ON for " + self._uri)
            request = Message(code=PUT, payload=CONST_COAP_STRING_TRUE.encode("ascii"), uri=CONST_COAP_PROTOCOL + self._host + "/" + self._uri)
            response = await self._protocol.request(request).response
            self._state = True
            self.schedule_update_ha_state()
        except Exception as e:
            _LOGGER.info("Failed to PUT resource: " + self._uri)
            _LOGGER.info(e)

    async def async_turn_off(self, **kwargs):
        """Turn the device off."""
        try:
            _LOGGER.info("HA calling TURN_OFF for " + self._uri)
            request = Message(code=PUT, payload=CONST_COAP_STRING_FALSE.encode("ascii"), uri=CONST_COAP_PROTOCOL + self._host + "/" + self._uri)
            response = await self._protocol.request(request).response
            self._state = False
            self.schedule_update_ha_state()
        except Exception as e:
            _LOGGER.info("Failed to PUT resource: " + self._uri)
            _LOGGER.info(e)

    @callback
    async def async_update_values(self):
        """Update this switch."""
        try:
            request = Message(code=GET, uri=CONST_COAP_PROTOCOL + self._host + "/" + self._uri)
            response = await self._protocol.request(request).response
            print("Payload received is: %s" % (response.payload))
            response_bool = False
            if (response.payload == b'\x01'): # TODO: make this a character rather than boolean
                response_bool = True

            # Check for change
            if (self._state != response_bool):
                self._state = response_bool
                #_LOGGER.info("%s changed: %s - %s" % (self._uri, response.code, str(response_bool)))
                self.async_write_ha_state()
            #else:
                #_LOGGER.info("%s no change..." % (self._uri))

        except Exception as e:
            _LOGGER.info("Failed to GET resource: " + self._uri)
            _LOGGER.info(e)

    @property
    def unique_id(self):
        """Return a unique identifier for this sensor."""
        return f"{self._name}"

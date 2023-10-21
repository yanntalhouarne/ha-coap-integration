"""HA-CoAp-Integration Switch Interface."""
import sys
sys.path.append("/config/custom_components/ha-coap-integration")

from datetime import timedelta
from myCoapNode import CoApNode
import logging

# Bring in CoAP
from aiocoap import *

import voluptuous as vol

from homeassistant.components.sensor import PLATFORM_SCHEMA
from homeassistant.const import (
    CONF_HOST,
    CONF_FRIENDLY_NAME,
    CONF_SCAN_INTERVAL,
    TEMP_CELSIUS,
)
from homeassistant.core import callback
import homeassistant.helpers.config_validation as cv
from homeassistant.helpers.entity import Entity
from homeassistant.helpers.event import async_track_time_interval

_LOGGER = logging.getLogger(__name__)

CONST_DEFAULT_SCAN_PERIOD_S = 60.0

CONST_COAP_PROTOCOL = "coap://"
CONST_COAP_STRING_TRUE = "1"
CONST_COAP_STRING_FALSE = "0"

# protocol = ""

# for data validation
PLATFORM_SCHEMA = vol.All(
    PLATFORM_SCHEMA.extend(
        {
            vol.Optional(CONF_HOST): cv.string,
            vol.Optional(CONF_FRIENDLY_NAME): cv.string,
            vol.Optional(CONF_SCAN_INTERVAL): cv.string,
        },
        extra=vol.PREVENT_EXTRA,
    )
)

# setup platform
async def async_setup_platform(hass, config, async_add_entities, discovery_info=None):
    """Set up a temperature sensor."""

    print("Set up the temperature sensor")

    host = config.get(CONF_HOST)

    # Use all sensors by default
    hass_sensors = []

    # Setup Async CoAP
    protocol = await Context.create_client_context()

    temperatureList = []
    name = ""
    addr = ""
    index = 0

    _LOGGER.info("Parsing coap switches from directory ...")

    with open('/config/custom_components/ha-coap-integration/scripts/node_directory.txt', 'r') as f:
        input_text = f.read()
        blocks = input_text.strip().split("==============================\n")
        for block in blocks:
            lines = block.strip().split('\n')
            name = lines[0].split(': ')[1].replace('.local.', '')
            addr = lines[1].split(': ')[1]
            tempNode = CoApNode(name, addr)
            temperatureList.append(tempNode)
            index = index + 1
    _LOGGER.info("Loaded "+ str(index) + " MyCoap Sensors from directory file.")

    # Add sensors
    for node in temperatureList:
        hass_sensors.append(
            CoAPsensorNode(
                "["+node.ipAddr+"]",
                "temperature",
                protocol,
                node.deviceName,
                TEMP_CELSIUS,
                1,
            )
        )

    print("Adding temperature sensor done")

    async_add_entities(hass_sensors)

    print("async_add_entities done")

    async def async_update_sensors(event):
        """Update temperature sensor."""
        # Update sensors based on scan_period set below which comes in from the config
        for sensor in hass_sensors:
            await sensor.async_update_values()

    # update sensor every 5 seconds
    async_track_time_interval(hass, async_update_sensors, timedelta(seconds=60))


class CoAPsensorNode(Entity):
    """Representation of a CoAP sensor node."""

    def __init__(self, host, uri, protocol, name, unit, round_places):
        """Initialize the sensor."""

        _LOGGER.info("Adding temp sensor " + name + " with address " + host)

        self._uri = uri
        self._name = name
        self._unit = unit
        self._round_places = round_places
        self._state = "0"
        self._host = host
        self._protocol = protocol

    @property
    def name(self):
        """Return the name of the sensor."""
        return self._name

    @property
    def state(self):
        """Return the state of the sensor."""
        return self._state

    @property
    def unit_of_measurement(self):
        """Return the unit the value is expressed in."""
        return self._unit

    @property
    def poll(self):
        """Sensors are polled."""
        return True

    @callback
    async def async_update_values(self):
        """Update this sensor."""
        try:
            request = Message(mtype=NON, code=GET)
            _uri = CONST_COAP_PROTOCOL + self._host + "/" + self._uri
            request.set_request_uri(uri=_uri)
            response = await self._protocol.request(request).response
            _LOGGER.info("Received " + str(int.from_bytes(response.payload)) + " from " + self._host + "/" + self._uri)
	        # Check for change
            if self._state != round(float(int.from_bytes(response.payload)), self._round_places):
                # Round result to make the ui look nice
                self._state = round(float(int.from_bytes(response.payload)), self._round_places)
                self.async_write_ha_state()
        except Exception as e:
            _LOGGER.info("Exception - Failed to GET resource: " + self._name + "/" + self._uri)
            _LOGGER.info(e)

    @property
    def unique_id(self):
        """Return a unique identifier for this sensor."""
        return f"{self._name}"
